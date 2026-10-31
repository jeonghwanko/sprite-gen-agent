# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (pose-plan GPT motion; machine-readable outcomes; see NOTICE).
"""`sprite-gen gen-set` — generate every state row of a prepared run, N at a time.

The atlas pipeline's one AI step, as a command instead of prose. `prepare` already
wrote the numeric request, one prompt per state and one layout guide per state; this
runs `gen` for each state with the identity ref the run declares (`base-source.*`, or
the accepted direction anchor for a direction-contract action row — `sprite_gen.curate.anchor`
owns that choice) and writes `raw/<state>.png` where `extract` expects it.

Same contract as `video-set`: items are idempotent (an existing row is reused unless
`--force`), every item gets its own report, `table.md` names failures by stage, and the
exit code is non-zero when any item failed. There is no fallback provider: `--provider`
is honoured verbatim and the unspecified case resolves exactly like `gen` does
(`SPRITE_GEN_DEFAULT_PROVIDER` > codex; a codex that cannot run turns into what the user
chose for that, recorded per item). When that choice is to ask, every row's `gen` asks
instead of generating, and the set stops with the same question and exit code
(`base.CODEX_UNAVAILABLE_EXIT`) once the rows under way have ended, naming `--provider` as
the way to answer it.

Direction-contract runs follow the run's own `references/generation-plan.json`: stage 1
(direction anchors, base-referenced) must be generated, extracted and an anchor frame
accepted before stage 2 rows can attach it, so a stage-2 row whose anchor is not
resolvable fails by name instead of quietly re-attaching the base.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from sprite_gen import gen as gen_mod
from sprite_gen.curate import anchor as anchor_mod
from sprite_gen.gen import outcome
from sprite_gen.gen.base import (CodexUnavailableAsk, ask_codex_unavailable, child_asked, codex_unavailable_question,
                                 run_logged)
from sprite_gen.spec import layout
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.console import utf8_streams
from sprite_gen.gen.motion_contract import motion_refs, input_fingerprint

DEFAULT_CONCURRENCY = 6  # lead-verified 2026-08 (no provider throttling at 6-wide); serial one-by-one is the anti-pattern
REPORT_DIR = "reports"  # <run>/reports/gen-set/<state>.json — outside raw/ and frames/, the run contract's derived areas


def _row_states(request: dict[str, Any], wanted: list[str] | None) -> list[str]:
    """States to generate, in generation-plan order (anchors before rows), minus mirrored directions."""
    states = list(request.get("states") or {})
    if not states:
        raise SystemExit("gen-set: the request declares no states")
    directions = request.get("directions")
    mirrored = set((directions or {}).get("mirror") or {})
    ordered: list[str] = []
    if directions:
        anchors = [anchor_mod.anchor_state(request, d) for d in anchor_mod.directions(request)]
        ordered = [s for s in anchors if s in states] + [s for s in states if s not in anchors]
    else:
        ordered = states
    ordered = [s for s in ordered if not any(s.startswith(m + "_") for m in mirrored)]
    if wanted:
        unknown = [s for s in wanted if s not in states]
        if unknown:
            raise SystemExit(f"gen-set: state(s) not in the request: {', '.join(unknown)} (have: {', '.join(states)})")
        ordered = [s for s in ordered if s in wanted]
    return ordered


def _stages(request: dict[str, Any], states: list[str]) -> list[list[str]]:
    """Direction runs: anchors first (parallel), then rows. Flat runs: one stage."""
    directions = request.get("directions")
    if not directions:
        return [states]
    anchors = {anchor_mod.anchor_state(request, d) for d in anchor_mod.directions(request)}
    first = [s for s in states if s in anchors]
    second = [s for s in states if s not in anchors]
    return [stage for stage in (first, second) if stage]


def _row_image_ok(out: Path) -> str | None:
    """None when `out` is a complete, decodable image; otherwise why not."""
    if not out.is_file():
        return "row image missing"
    try:
        from PIL import Image

        with Image.open(out) as im:
            im.verify()
    except Exception as exc:  # noqa: BLE001 — any decode failure is the same verdict
        return f"row image unreadable ({exc.__class__.__name__})"
    return None


def read_report(report: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a row's gen report. Returns (report, None) or (None, why) — a missing,
    unreadable or provider-less report is never treated as a finished row."""
    if not report.is_file():
        return None, "report missing"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"report unreadable ({exc.__class__.__name__})"
    if not isinstance(data, dict) or not data.get("provider"):
        return None, "report has no provider — not a finished gen report"
    if data.get("ok") is False:  # a report written before `ok` existed has none: its provider names it finished
        return None, "report says the run failed"
    return data, None


def _auth_source(report: dict[str, Any]) -> str | None:
    """What a row's gen call ran on, as its report says (`extra.auth_source`)."""
    extra: Any = report.get("extra") if isinstance(report.get("extra"), dict) else {}
    return extra.get("auth_source")


def run_gen_cli(prompt_file: Path, out: Path, refs: list[Path], report: Path, *, provider: str | None, model: str | None, log: Path) -> int:
    cmd = [sys.executable, "-m", "sprite_gen.cli", "gen", "--prompt-file", str(prompt_file), "--out", str(out), "--report", str(report)]
    for ref in refs:
        cmd += ["--ref", str(ref)]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    return run_logged(cmd, log)


def run_item(
    *,
    run_dir: Path,
    request: dict[str, Any],
    state: str,
    provider: str | None,
    model: str | None,
    force: bool,
    gen_runner: Callable[..., int] | None = None,
) -> dict[str, Any]:
    gen_runner = gen_runner or run_gen_cli  # late-bound so a test can stand in for the provider call
    out = run_dir / layout.raw_rel(request, state)
    prompt_file = run_dir / layout.prompt_rel(request, state)
    guide = run_dir / layout.guide_rel(request, state)
    report_dir = run_dir / REPORT_DIR / "gen-set"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"{state.replace('/', '_')}.json"
    log = report_dir / f"{state.replace('/', '_')}.log"
    result: dict[str, Any] = {"state": state, "out": str(out), "report": str(report), "motion_quality": "unverified"}
    started = time.monotonic()
    child_errors: list[dict[str, Any]] = []
    rc: int | None = None  # the row's `gen` exit, once it ran
    try:
        if not prompt_file.is_file():
            raise SystemExit(f"prompt missing: {prompt_file} — run `sprite-gen prepare` first")
        if not guide.is_file():
            raise SystemExit(f"layout guide missing: {guide} — run `sprite-gen prepare` first")
        motion = request["states"][state].get("motion")
        extra_refs = motion_refs(run_dir, motion, request["states"][state]["frames"]) if motion else []
        identity = anchor_mod.identity_ref(run_dir, state, request, quiet=True)
        refs = [identity, *extra_refs, guide]
        result["refs"] = [str(r) for r in refs]
        fingerprint = input_fingerprint(prompt_file, refs, motion, provider, model) if motion else None
        attempt = report.with_suffix(".attempt.json")
        if motion and attempt.exists() and json.loads(attempt.read_text(encoding="utf-8")).get("status") != "complete":
            raise SystemExit("GENERATION_OUTCOME_UNKNOWN: inspect the previous request and outputs before any retry")
        if out.is_file() and not force:
            prior, why = read_report(report)
            bad_image = _row_image_ok(out)
            if motion and (prior is None or bad_image or prior.get("motion_input_sha256") != fingerprint):
                raise SystemExit("MOTION_INPUT_CHANGED_OR_UNPROVEN: preserve this output and inspect it; use a new run for an authorized revision")
            if prior is not None and bad_image is None:
                result["reused"] = True
                result["provider"] = prior.get("provider")
                result["provider_resolved_from"] = prior.get("provider_resolved_from")
                result["auth_source"] = _auth_source(prior)
                result["ok"] = True
                return result
            result["regenerated_because"] = why or bad_image  # the files exist but do not prove a finished row — generate again, say why
        if motion and force and out.exists():
            raise SystemExit("Preserve existing motion versions; prepare a new run for a revision")
        out.parent.mkdir(parents=True, exist_ok=True)
        # The report is the commit marker of a finished row. Drop the previous one BEFORE the
        # provider runs: a provider that half-overwrites the image and exits non-zero must not
        # leave a valid-looking report next to a broken image (validator finding 2026-09-09).
        report.unlink(missing_ok=True)
        if motion:
            atomic_write_text(attempt, json.dumps({"status": "running", "motion_input_sha256": fingerprint}) + "\n")
        rc = gen_runner(prompt_file, out, refs, report, provider=provider, model=model, log=log)
        # What the row's `gen` says it sent and why it failed, read before a failed row's report is removed.
        result["provider_submitted"] = outcome.child_submitted(rc, report)
        if result["provider_submitted"]:
            outcome.submitted("child")  # the set's record: a failure from here is never "before any request"
        child_errors = outcome.child_errors(report) if rc != 0 else []
        gen_report, why = read_report(report) if rc == 0 else (None, None)
        bad_image = _row_image_ok(out) if rc == 0 else None
        if rc != 0 or gen_report is None or bad_image is not None:
            if motion:
                if (question := child_asked(rc, log)) is not None:
                    result["asked"] = question
                raise SystemExit(f"GENERATION_OUTCOME_UNKNOWN: gen exited {rc}; preserve output, report and log for inspection")
            for partial in (out, report):  # no half state: an unproven row is removed so the next run regenerates
                if partial.exists():
                    partial.unlink()
            if rc != 0:
                if (question := child_asked(rc, log)) is not None:
                    result["asked"] = question
                    raise SystemExit(f"gen asked what to do instead of generating: codex cannot run here "
                                     f"({question['reason']})")
                tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:] if log.exists() else []
                raise SystemExit(f"gen exited {rc}; " + (" | ".join(tail) if tail else f"see {log}") + " — the row and its report were removed")
            raise SystemExit(f"gen exited 0 but its {why or bad_image}; the row and the report were removed so the next run regenerates")
        result["provider"] = gen_report.get("provider")
        result["provider_resolved_from"] = gen_report.get("provider_resolved_from")
        result["auth_source"] = _auth_source(gen_report)
        if gen_report.get("provider_fallback"):
            result["provider_fallback"] = gen_report["provider_fallback"]
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
        if motion:
            gen_report.update(motion_input_sha256=fingerprint, motion_quality="unverified", refs=result["refs"])
            atomic_write_text(report, json.dumps(gen_report, ensure_ascii=False, indent=2) + "\n")
            atomic_write_text(attempt, json.dumps({"status": "complete", "motion_input_sha256": fingerprint}) + "\n")
        result["ok"] = True
        result["errors"] = []
    except outcome.NotStarted as exc:  # the set was being stopped: this row's `gen` never started
        result["ok"] = False
        result["error"] = str(exc)
        result["errors"] = [outcome.error(outcome.INTERRUPTED, str(exc), state=state)]
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    except SystemExit as exc:
        result["ok"] = False
        result["error"] = str(exc)
        result["errors"] = _row_errors(state, str(exc), asked="asked" in result,
                                       submitted=bool(result.get("provider_submitted")),
                                       answered=rc == 0, child=child_errors)
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    result.setdefault("provider_submitted", False)  # a reused or refused row sent nothing
    return result


def _row_errors(state: str, message: str, *, asked: bool, submitted: bool, answered: bool,
                child: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A failed row's `errors`: its `gen`'s own when it wrote them (named for the row), else read off what
    gen-set refused or saw."""
    if child:
        return [{**e, "state": state} for e in child]
    if asked:
        code = outcome.PROVIDER_ASKS
    elif message.startswith("GENERATION_OUTCOME_UNKNOWN"):
        code = outcome.OUTCOME_UNKNOWN
    elif not submitted:
        code = outcome.PREFLIGHT  # a missing prompt or guide, a motion row kept as it is: nothing was sent
    elif answered:
        code = outcome.PROVIDER_FAILED  # gen said it finished and its row is not there: the provider did answer
    else:
        # gen ended after its request may have left and wrote no coded report (killed, out of memory, a crash):
        # nothing says what the provider did with it, so it is not sent again unseen.
        code = outcome.OUTCOME_UNKNOWN
    return [outcome.error(code, message, state=state)]


def write_table(results: list[dict[str, Any]], path: Path) -> str:
    lines = ["| state | provider | resolved from | seconds | status |", "|---|---|---|---|---|"]
    for r in results:
        if r.get("ok") and r.get("reused"):
            lines.append(f"| {r['state']} | - | - | - | reused |")
        elif r.get("asked"):
            lines.append(f"| {r['state']} | - | - | {r.get('elapsed_seconds', '-')} | ASKED: codex cannot run here "
                         f"({r['asked']['reason'][:80]}) |")
        elif r.get("ok"):
            lines.append(f"| {r['state']} | {r.get('provider') or '-'} | {r.get('provider_resolved_from') or '-'} | {r.get('elapsed_seconds', '-')} | OK |")
        else:
            lines.append(f"| {r['state']} | - | - | {r.get('elapsed_seconds', '-')} | FAIL: {r.get('error', '')[:100]} |")
    text = "\n".join(lines) + "\n\nOK/reused means image generation completed. Motion quality remains unverified until actual gait review.\n"
    atomic_write_text(path, text)
    return text


def run_set(
    *,
    run_dir: Path,
    states: list[str] | None,
    provider: str | None,
    model: str | None,
    concurrency: int,
    force: bool,
    gen_runner: Callable[..., int] | None = None,
) -> dict[str, Any]:
    gen_runner = gen_runner or run_gen_cli
    run_dir = run_dir.expanduser().resolve()
    if not (run_dir / "sprite-request.json").is_file():
        raise SystemExit(f"gen-set: {run_dir} is not a prepared run (no sprite-request.json) — run `sprite-gen prepare` first")
    request = anchor_mod.load_request(run_dir)
    ordered = _row_states(request, states)
    results: list[dict[str, Any]] = []
    stopped: outcome.Interrupted | None = None
    futures: dict[Any, str] = {}
    done: set[Any] = set()
    try:
        for stage in _stages(request, ordered):
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                futures = {ex.submit(run_item, run_dir=run_dir, request=request, state=s, provider=provider, model=model, force=force, gen_runner=gen_runner): s for s in stage}
                try:
                    for fut in as_completed(futures):
                        r = fut.result()
                        results.append(r)
                        done.add(fut)
                        print(json.dumps({k: r[k] for k in ("state", "ok") if k in r} | ({"error": r["error"]} if not r.get("ok") else {}), ensure_ascii=False), flush=True)
                except outcome.Interrupted as exc:
                    stopped = exc
                    _gather_stopped(futures, done, results, exc)
            if stopped is not None:
                break
            if any(r.get("asked") for r in results):
                break  # every row would ask the same: the set asks it once, below
            if any(not r.get("ok") for r in results if r["state"] in stage) and len(_stages(request, ordered)) > 1:
                # a failed anchor stage cannot feed stage 2 — stop here and say so, do not attach the base instead
                break
    except outcome.Interrupted as exc:  # stopped between two stages, or while a stage's pool was closing
        stopped = exc
        _gather_stopped(futures, done, results, exc)
    except Exception as exc:  # a bug in sprite-gen: the rows that ran are still reported, then it is raised
        outcome.gather_finished(futures, done, results)
        with outcome.deferred():
            _set_report(run_dir, ordered, results, None, crashed=exc)
        raise
    with outcome.deferred():  # the set report is written whole; a stop meanwhile ends the run after it
        return _set_report(run_dir, ordered, results, stopped)


def _gather_stopped(futures: dict[Any, str], done: set[Any], results: list[dict[str, Any]],
                    stop: outcome.Interrupted) -> None:
    """The rows of a stopped stage: those not begun never begin, those under way end (the signal already sent
    their `gen` SIGTERM, `base.stop_children`), and each is kept with the stop as its error."""
    for fut in futures:
        if fut not in done:
            fut.cancel()
    for fut in futures:
        if fut in done or fut.cancelled():
            continue
        r = fut.result()
        done.add(fut)
        if not r.get("ok"):
            # A row under way may have sent its request: unless its `gen` said it sent nothing, the stop is not retryable.
            sent = r.get("provider_submitted") is not False
            r["errors"] = [{**e, "retryable": e.get("retryable", True) and not sent} if e.get("code") == outcome.INTERRUPTED else
                           outcome.error(outcome.INTERRUPTED, f"{stop} while the row was generating", state=r["state"], submitted=sent)
                           for e in r.get("errors") or [{}]]
        results.append(r)


def _set_report(run_dir: Path, ordered: list[str], results: list[dict[str, Any]],
                stopped: outcome.Interrupted | None, *, crashed: BaseException | None = None) -> dict[str, Any]:
    results.sort(key=lambda r: ordered.index(r["state"]))
    report_dir = run_dir / REPORT_DIR / "gen-set"
    report_dir.mkdir(parents=True, exist_ok=True)
    table = write_table(results, report_dir / "table.md")
    skipped = [s for s in ordered if s not in {r["state"] for r in results}]
    # the set's own question, its flag to answer it: the same as its rows', which ran `gen` with gen-set's --provider
    asked = next((codex_unavailable_question(r["asked"]["cause"], r["asked"]["reason"]) for r in results
                  if r.get("asked")), None)
    errors = [e for r in results for e in r.get("errors") or []]
    if stopped is not None:
        errors += [outcome.error(outcome.INTERRUPTED, f"{stopped} before the row began", state=s) for s in skipped]
        if not any(e["code"] == outcome.INTERRUPTED for e in errors):  # stopped as the last row ended
            errors.append(outcome.error(outcome.INTERRUPTED, f"{stopped} after its rows ended"))
    if crashed is not None:
        errors.append(outcome.crash_error(crashed, "gen-set"))
    payload = {
        "kind": "sprite-gen-gen-set-report",
        "run_dir": str(run_dir),
        "states": ordered,
        # `ok` has counted the finished rows since gen-set began; `all_ok` is the set's own yes or no.
        "ok": sum(1 for r in results if r.get("ok")),
        "all_ok": not errors and not skipped,
        "failed": [r["state"] for r in results if not r.get("ok")],
        "not_started": skipped,
        **({"asked": asked} if asked else {}),
        "errors": errors,
        # A set sprite-gen failed in cannot show that a row under way sent nothing.
        "provider_submitted": any(r.get("provider_submitted") for r in results) or crashed is not None,
        **({"interrupted": str(stopped)} if stopped is not None else {}),
        "items": results,
    }
    atomic_write_text(report_dir / "set.report.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(table)
    if crashed is not None:
        return payload
    if stopped is not None:
        raise outcome.Stop(f"gen-set: {stopped}; see {report_dir / 'set.report.json'}",
                           outcome.exit_code(outcome.INTERRUPTED, stopped), outcome=outcome.INTERRUPTED)
    if asked:
        raise ask_codex_unavailable(asked)
    return payload


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path, help="a prepared run (sprite-request.json, prompts/, references/layout-guides/)")
    parser.add_argument("--states", help="comma list; default = every non-mirrored state in the request")
    parser.add_argument("--provider", choices=gen_mod.PROVIDERS, help="honoured verbatim (openai bills per call); default "
                        "resolves like `gen`: env > codex, and a codex that cannot run asks or runs what was chosen for that")
    parser.add_argument("--model")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help=f"rows generated at once (default {DEFAULT_CONCURRENCY})")
    parser.add_argument("--force", action="store_true", help="regenerate rows that already exist")


def run(**kwargs: object) -> int:
    """0 when every row is there; `outcome.NOT_STARTED_EXIT` when the set failed and no row sent a provider
    request (refused before it began, or every failed row refused before its call); 1 for any other failure."""
    states = [s.strip() for s in str(kwargs.get("states") or "").split(",") if s.strip()] or None
    with outcome.recording("gen-set") as record:
        try:
            payload = run_set(
                run_dir=Path(str(kwargs["run_dir"])), states=states,
                provider=kwargs.get("provider"), model=kwargs.get("model"),  # type: ignore[arg-type]
                concurrency=int(kwargs.get("concurrency") or DEFAULT_CONCURRENCY), force=bool(kwargs.get("force")),  # type: ignore[call-overload]
            )
        except outcome.Interrupted as exc:  # stopped before the first row began: no set report to write
            raise outcome.Stop(f"gen-set: {exc}", outcome.exit_code(outcome.INTERRUPTED, exc)) from exc
        except SystemExit as exc:
            if isinstance(exc, (outcome.Stop, CodexUnavailableAsk)) or exc.code in (0, None) or record.requests:
                raise
            # The run directory, the request or the states were refused before any row sent anything.
            raise outcome.Stop(str(exc), outcome.NOT_STARTED_EXIT, outcome=outcome.PREFLIGHT) from exc
    if not payload["failed"] and not payload["not_started"]:
        return 0
    return 1 if payload["provider_submitted"] else outcome.NOT_STARTED_EXIT


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="sprite-gen gen-set", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    return outcome.entry(lambda: run(**vars(parser.parse_args(argv))))


if __name__ == "__main__":
    raise SystemExit(main())
