# SPDX-License-Identifier: Apache-2.0
"""What a generating run ended with, for a program that runs sprite-gen unattended (docs/gen.md "Outcome").

Three things a caller needs and could only guess from stderr before:

- what failed, and whether running the same command again is worth it: `errors`, a list of
  `{state, code, retryable, message}` with a code from `CODES`;
- whether the run could have cost money: `provider_submitted`, true once a request left for a provider
  (a codex/grok/claude process was started, or a POST was sent), false when the run ended before that;
- the same answer when the run is stopped: SIGTERM (and SIGHUP) end the run through `Interrupted`, so the
  report is written with `interrupted` before the process exits `INTERRUPTED_EXIT`.

`NOT_STARTED_EXIT` is the exit code of a run that failed before any provider request left: nothing was
generated and nothing could be billed. Exit 2 (an argument the parser refused) means the same. 75 is the
codex question (`base.CODEX_UNAVAILABLE_EXIT`): asked before anything was sent when codex is not signed in,
but after a `codex exec` ran when codex said its usage is used up, so its report says which. Any other
non-zero exit leaves the answer to the report's `provider_submitted`.

The record is process-wide (`Record`): a provider marks it where its request leaves (`submitted`), the verb
reads it when it writes its report. A library call with no record open marks nothing.

A stop is one event for the whole process (`STOP`): the signal sets it and runs the stop hooks (`on_stop`:
the batch verbs' children are sent SIGTERM), and nothing starts a provider request once it is set
(`NotStarted`). A report is written inside `deferred()`, where a signal waits until the write is done.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

NOT_STARTED_EXIT = 3
INTERRUPTED_EXIT = 143  # 128 + SIGTERM, what a shell reports for a process the signal ended

# The codes, each with whether running the same command again may succeed without changing anything.
PREFLIGHT = "preflight"  # refused before any provider request: a flag, an input, a login or a key is wrong or missing
OUTPUT_PATH = "output-path"  # an output's folder could not be made, found before any provider request
PROVIDER_ASKS = "provider-asks"  # a default codex cannot run here and the choice is to ask (exit 75)
PROVIDER_AUTH = "provider-auth"  # the provider refused the credential (HTTP 401/403)
PROVIDER_REFUSED = "provider-refused"  # the request itself was refused: content policy, or another HTTP 4xx
PROVIDER_USAGE_LIMIT = "provider-usage-limit"  # codex said its usage is used up
PROVIDER_RATE_LIMITED = "provider-rate-limited"  # HTTP 429 still, after the waits sprite-gen makes itself
PROVIDER_TIMEOUT = "provider-timeout"  # codex gave no answer in time and was killed after its request left: may be billed
PROVIDER_UNAVAILABLE = "provider-unavailable"  # the provider answered HTTP 5xx
PROVIDER_FAILED = "provider-failed"  # the provider answered with no image in it (codex exec failed, an empty answer)
GENERATION_UNFINISHED = "generation-unfinished"  # a video request was accepted and did not finish: video-resume it
OUTCOME_UNKNOWN = "outcome-unknown"  # whether the provider took it cannot be told (no answer read): may be billed
POSTPROCESS_FAILED = "postprocess-failed"  # the provider answered and what it made failed sprite-gen's checks
INTERRUPTED = "interrupted"  # SIGTERM or SIGHUP stopped the run
INTERNAL = "internal-error"  # sprite-gen itself failed (a bug): report it

CODES: dict[str, bool] = {
    PREFLIGHT: False,
    OUTPUT_PATH: False,
    PROVIDER_ASKS: False,
    PROVIDER_AUTH: False,
    PROVIDER_REFUSED: False,
    PROVIDER_USAGE_LIMIT: False,
    PROVIDER_RATE_LIMITED: True,
    PROVIDER_TIMEOUT: False,
    PROVIDER_UNAVAILABLE: True,
    PROVIDER_FAILED: True,
    GENERATION_UNFINISHED: False,
    OUTCOME_UNKNOWN: False,
    POSTPROCESS_FAILED: True,
    INTERRUPTED: True,  # when nothing had been sent: `error(submitted=)`, `report_fields`
    INTERNAL: False,
}


def error(code: str, message: str, *, state: str | None = None, submitted: bool | None = None, **extra: Any) -> dict[str, Any]:
    """One entry of a report's `errors`: the code's own `retryable`, the message as the run said it. A stop
    (`INTERRUPTED`) after a provider request had left (`submitted`) is not retryable: the same command sends
    that request again, and the stopped one may already be billed (2026-10-29)."""
    if code not in CODES:
        raise ValueError(f"unknown outcome code {code!r}")
    retryable = CODES[code] and not (code == INTERRUPTED and submitted)
    return {"state": state, "code": code, "retryable": retryable, "message": message, **extra}


class Stop(SystemExit):
    """A run that ends with `message` and exit `code`: `str()` is the message (what `pytest.raises(match=)`
    and a caller in-process read), `.code` the exit status. `cli.main` prints the message on stderr, as the
    interpreter does for a plain `SystemExit(message)`, and exits with the code. `outcome` is the code its
    report gives it, when the raiser knows it (`make_parent`)."""

    def __init__(self, message: str, code: int, *, outcome: str | None = None) -> None:
        super().__init__(message)
        self.code: int = code
        self.outcome = outcome


class NotStarted(SystemExit):
    """A child or item that would have sent a provider request was not begun: the run is being stopped."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what} was not begun: the run is being stopped; nothing was sent")


class Interrupted(BaseException):
    """SIGTERM or SIGHUP arrived. A BaseException that is not a SystemExit, so no `except SystemExit` of an
    item swallows it on the way to the verb that writes the report."""

    def __init__(self, signum: int) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = f"signal {signum}"
        super().__init__(f"stopped by {name}")
        self.signum = signum


@dataclass
class Record:
    """What one verb's run did with providers. `requests` names each provider a request left for, in order."""

    verb: str
    requests: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    answered: bool = False  # the provider has returned what it made: a failure from here on is sprite-gen's checks

    @property
    def provider_submitted(self) -> bool:
        return bool(self.requests)


_LOCK = threading.Lock()
_CURRENT: Record | None = None
# Set by the first SIGTERM / SIGHUP of a run (or a batch verb's own stop): no provider request starts after it.
STOP = threading.Event()
_STOP_HOOKS: list[Callable[[], None]] = []
_DEFER = 0  # >0 while a report is being written: a signal waits in `_PENDING` until the write is done
_PENDING: int | None = None


def on_stop(hook: Callable[[], None]) -> None:
    """Run `hook` when the run is stopped, from the signal handler itself (`base.stop_children`)."""
    if hook not in _STOP_HOOKS:
        _STOP_HOOKS.append(hook)


def stop() -> None:
    """Stop the run: no provider request starts from here on, and the stop hooks run."""
    STOP.set()
    for hook in list(_STOP_HOOKS):
        try:
            hook()
        except Exception:  # noqa: BLE001 — a hook that fails must not keep the others from running
            pass


@contextmanager
def deferred() -> Iterator[None]:
    """Write a report with the signals held: one that arrives meanwhile stops the run (`stop`) at once, and
    raises `Interrupted` only when the write is done, so a stopped run never leaves half a record or none."""
    global _DEFER, _PENDING
    _DEFER += 1
    try:
        yield
    finally:
        _DEFER -= 1
        if _DEFER == 0 and _PENDING is not None:
            signum, _PENDING = _PENDING, None
            raise Interrupted(signum)


def current() -> Record | None:
    return _CURRENT


def submitted(provider: str, **detail: Any) -> None:
    """A request is leaving for `provider` (a process started, a POST sent): the run may be billed from here on.
    `detail` (a request id) is kept on the record. Called by every provider where its request leaves."""
    with _LOCK:
        if _CURRENT is not None:
            _CURRENT.requests.append(provider)
            _CURRENT.detail.update(detail)


def note(**detail: Any) -> None:
    """Keep `detail` on the record (a request id an accepted request named)."""
    with _LOCK:
        if _CURRENT is not None:
            _CURRENT.detail.update(detail)


def answered() -> None:
    """The provider calls of this run have returned what they made."""
    with _LOCK:
        if _CURRENT is not None:
            _CURRENT.answered = True


def report_fields(record: Record | None, *, errors: list[dict[str, Any]], exit_status: int | None = None,
                  ) -> dict[str, Any]:
    """The outcome keys every generation report carries: `ok`, `errors`, `provider_submitted`, the providers
    requests left for (`provider_requests`), what the record kept (a request id) and, on a failure, the exit."""
    requests = list(record.requests) if record is not None else []
    # A run of one request that was stopped after it left: running it again sends it again (`error`).
    if requests:
        errors = [{**e, "retryable": False} if e.get("code") == INTERRUPTED and e.get("retryable") else e for e in errors]
    return {
        "ok": not errors,
        "errors": errors,
        "provider_submitted": bool(requests),
        "provider_requests": requests,
        **({"provider_detail": dict(record.detail)} if record is not None and record.detail else {}),
        **({"exit_code": exit_status} if exit_status is not None else {}),
    }


def _interrupt(signum: int, _frame: Any) -> None:
    global _PENDING
    # One stop is enough: a second signal while the report is being written must not cut it short.
    for name in _SIGNALS:
        try:
            signal.signal(getattr(signal, name), signal.SIG_IGN)
        except (AttributeError, ValueError, OSError):
            pass
    stop()  # before anything else: a worker about to start a child sees it now
    if _DEFER:
        _PENDING = signum
        return
    raise Interrupted(signum)


_SIGNALS = ("SIGTERM", "SIGHUP")


@contextmanager
def recording(verb: str) -> Iterator[Record]:
    """Open the run's record and turn SIGTERM / SIGHUP into `Interrupted` for as long as it is open. Signal
    handlers can only be set on the main thread; elsewhere (a test runner's worker) the record still opens."""
    global _CURRENT
    record = Record(verb)
    previous: dict[str, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for name in _SIGNALS:
            number = getattr(signal, name, None)
            if number is None:
                continue
            try:
                previous[name] = signal.signal(number, _interrupt)
            except (ValueError, OSError):
                pass
    with _LOCK:
        outer, _CURRENT = _CURRENT, record
    if outer is None:
        STOP.clear()
    try:
        yield record
    finally:
        with _LOCK:
            _CURRENT = outer
        if outer is None:
            STOP.clear()
        for name, handler in previous.items():
            try:
                signal.signal(getattr(signal, name), handler)
            except (ValueError, OSError):
                pass


def classify(exc: BaseException, *, submitted: bool, answered: bool = False) -> str:
    """The code for a run that ended with `exc`. `submitted`: a provider request had left; `answered`: the
    provider had returned what it made, so what failed came after it. Read by exception type first, then by
    the fixed words of a refusal line (`refusal`: `(HTTP <n>)` and the CONTENT_POLICY marker)."""
    from . import refusal
    from .base import CodexUnavailableAsk, GenTimeoutError

    if isinstance(exc, (Interrupted, KeyboardInterrupt)):
        return INTERRUPTED
    if isinstance(exc, CodexUnavailableAsk):
        return PROVIDER_ASKS
    if isinstance(exc, Stop) and exc.outcome in CODES:
        return exc.outcome
    type_name = type(exc).__name__
    if type_name == "GenerationUnfinished":
        return GENERATION_UNFINISHED
    if type_name == "CodexUsageLimit":
        return PROVIDER_USAGE_LIMIT
    if not isinstance(exc, SystemExit):
        return INTERNAL
    if answered:
        return POSTPROCESS_FAILED
    if not submitted:
        return PREFLIGHT
    if isinstance(exc, GenTimeoutError):
        return PROVIDER_TIMEOUT
    text = str(exc)
    if "GENERATION_OUTCOME_UNKNOWN" in text:
        return OUTCOME_UNKNOWN
    if refusal.CONTENT_POLICY in text:
        return PROVIDER_REFUSED
    if type_name == "TransportError" or MAY_HAVE_TAKEN in text or "response was not valid JSON" in text:
        # No answer came back that could be read: the server may have taken (and billed) the request.
        return OUTCOME_UNKNOWN
    for status, code in (("401", PROVIDER_AUTH), ("403", PROVIDER_AUTH), ("429", PROVIDER_RATE_LIMITED)):
        if f"(HTTP {status})" in text or f"HTTP {status}" in text:
            return code
    if "(HTTP 5" in text or "HTTP 5" in text:
        return PROVIDER_UNAVAILABLE
    if "(HTTP 4" in text:
        return PROVIDER_REFUSED  # the request itself was refused (a parameter, a size): sent again, it is again
    if text.startswith(_ANSWERED_WITHOUT_IMAGE):
        return PROVIDER_FAILED
    return OUTCOME_UNKNOWN  # nothing says the request was not taken: not sent again unseen


# The words every transport failure says after a request left (`xai.TRANSPORT_FAILED`, the openai one).
MAY_HAVE_TAKEN = "the server may have accepted it"
# Failures that are a provider's whole answer, with no image in it: the request ran and is over.
_ANSWERED_WITHOUT_IMAGE = ("codex-gen:", "grok-gen: expected", "openai-gen: expected", "gen: expected a generated PNG",
                           "gen: file at")


def exit_code(code: str, exc: BaseException) -> int:
    """The exit status a run that ended with `exc` (classified `code`) leaves: `NOT_STARTED_EXIT` before any
    provider request, 128 + the signal when stopped (`INTERRUPTED_EXIT` for SIGTERM, 129 SIGHUP, 130 Ctrl-C), the
    exception's own status otherwise (1 for a message)."""
    if code in (PREFLIGHT, OUTPUT_PATH):
        return NOT_STARTED_EXIT
    if isinstance(exc, KeyboardInterrupt):
        return 130
    if isinstance(exc, Interrupted):
        return 128 + int(exc.signum)
    status = getattr(exc, "code", 1)
    return status if isinstance(status, int) else 1


def ended(exc: BaseException, code: str) -> BaseException:
    """What the verb raises after writing its report: `Stop` with the exit `exit_code` gives, or the exception
    itself when its status is that already (the codex question keeps its line and 75, a plain message its 1)
    or it is not a SystemExit at all (a bug keeps its traceback)."""
    status = exit_code(code, exc)
    if isinstance(exc, SystemExit) and (exc.code == status or (status == 1 and not isinstance(exc.code, int))):
        return exc
    if not isinstance(exc, (SystemExit, Interrupted)):
        return exc  # Ctrl-C stays a KeyboardInterrupt
    return Stop(str(exc), status, outcome=code)


def make_parent(path: Path, flag: str, *, verb: str) -> None:
    """Make `path`'s folder now, before any provider request, or refuse with `OUTPUT_PATH`'s exit: an output a
    run cannot write must not be found out after the request was paid for."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Stop(f"{verb}: cannot make the folder of {flag} {path} ({exc.strerror or exc}); nothing was sent "
                   "to a provider", NOT_STARTED_EXIT, outcome=OUTPUT_PATH) from exc
    if path.is_dir():
        raise Stop(f"{verb}: {flag} {path} is a folder, not a file; nothing was sent to a provider",
                   NOT_STARTED_EXIT, outcome=OUTPUT_PATH)


def child_submitted(rc: int, report: Path | None) -> bool:
    """Whether a child sprite-gen run (`run_logged`) may have sent a provider request: what its report says,
    else false for an exit that means nothing was sent (2, `NOT_STARTED_EXIT`), else true — a child that ended
    any other way without a report cannot be shown to have sent nothing (a codex question asked after a
    `codex exec` ran, exit 75, among them). The caller removes the child's old report before it starts."""
    if report is not None:
        try:
            said = json.loads(report.read_text(encoding="utf-8")).get("provider_submitted")
        except (OSError, ValueError, AttributeError):
            said = None
        if isinstance(said, bool):
            return said
    return rc not in (2, NOT_STARTED_EXIT)


def gather_finished(futures: dict[Any, Any], done: set[Any], results: list[dict[str, Any]]) -> None:
    """The items of a set that sprite-gen itself failed in (a bug, not a stop): those not begun never begin, those
    under way end, and each one whose result can still be read is kept, so the set report names what was sent
    (2026-10-29: a set that raised wrote no report, and its rows' requests went unrecorded)."""
    for fut in futures:
        if fut not in done:
            fut.cancel()
    for fut in futures:
        if fut in done or fut.cancelled():
            continue
        try:
            r = fut.result()
        except Exception:  # noqa: BLE001 — the item that raised: its outcome cannot be read
            continue
        done.add(fut)
        results.append(r)


def crash_error(exc: BaseException, what: str) -> dict[str, Any]:
    """The set report's error for sprite-gen failing in the middle of a set (`INTERNAL`): what may have been sent
    is unknown, so nothing is retryable."""
    return error(INTERNAL, f"{what}: sprite-gen failed ({type(exc).__name__}: {exc}); what the unfinished items sent cannot be told")


def child_errors(report: Path | None) -> list[dict[str, Any]]:
    """A child's report's `errors`, when it wrote them."""
    if report is None:
        return []
    try:
        errors = json.loads(report.read_text(encoding="utf-8")).get("errors")
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in errors if isinstance(e, dict) and e.get("code") in CODES] if isinstance(errors, list) else []


def say(exc: BaseException) -> None:
    """Print what a `Stop` says on stderr, as the interpreter prints a plain `SystemExit(message)`."""
    text = str(exc)
    if text:
        print(text, file=sys.stderr, flush=True)


def entry(run: Callable[[], int]) -> int:
    """A command's entry (`cli.main`, a module's `main`): `run()`, and a `Stop` turned into its line on stderr and
    its exit code. The interpreter prints no message for a SystemExit whose code is a number."""
    try:
        return run()
    except Stop as exc:
        say(exc)
        return int(exc.code)
    except Interrupted as exc:  # stopped once its report was written (`deferred`)
        say(exc)
        return 128 + int(exc.signum)
