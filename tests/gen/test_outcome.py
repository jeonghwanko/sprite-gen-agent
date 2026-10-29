# SPDX-License-Identifier: Apache-2.0
"""A generation run's outcome, for a program that runs sprite-gen unattended (docs/gen.md "Outcome").

Pinned here: every report carries `ok`, `errors` ({state, code, retryable, message}) and `provider_submitted`;
a run that failed before any provider request exits `outcome.NOT_STARTED_EXIT` and says so in its report; an
output folder is made, or refused, before the request; SIGTERM writes the report (naming a video request xAI took)
before the process ends; `version --json` and `doctor --json` describe the install without calling a provider.
No provider is called: fakes stand in, and mark where a real request would leave.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import cli, gen
from sprite_gen.gen import base as gen_base
from sprite_gen.gen import gen_set, outcome, video
from sprite_gen.gen.base import GenRequest

ROOT = Path(__file__).resolve().parents[2]


class _Provider:
    """A fake provider: marks its request as a real one does, then answers with `answer(request)`."""

    name = "fake"
    transparency = gen_base.TRANSPARENCY_CHROMA

    def __init__(self, answer=None) -> None:
        self.calls = 0
        self.answer = answer

    def generate(self, request: GenRequest, workdir: Path):
        self.calls += 1
        outcome.submitted(self.name)
        if self.answer is not None:
            self.answer(request)
        Image.new("RGBA", (8, 8), (40, 120, 200, 255)).save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=0.5, model=request.model)


def _use(monkeypatch, provider: _Provider) -> _Provider:
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: provider)
    return provider


def _report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- gen ----------------------------------------------------------------------------------------------------------


def test_a_finished_image_report_says_ok_with_no_errors_and_that_a_request_left(tmp_path, monkeypatch):
    _use(monkeypatch, _Provider())
    report = tmp_path / "deep" / "er" / "r.json"  # its folders are made before the request
    assert gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", report=report) == 0
    done = _report(report)
    assert done["ok"] is True and done["errors"] == [] and done["kind"] == "sprite-gen-image-report"
    assert done["provider_submitted"] is True and done["provider_requests"] == ["fake"]
    assert done["provider"] == "fake" and "exit_code" not in done


def test_a_refusal_before_any_request_exits_not_started_and_its_report_says_nothing_was_sent(tmp_path, monkeypatch):
    provider = _use(monkeypatch, _Provider())
    report = tmp_path / "r.json"
    with pytest.raises(SystemExit, match="gen: --trim-alpha needs --transparent") as stopped:
        gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", report=report, trim_alpha=True)
    assert stopped.value.code == outcome.NOT_STARTED_EXIT == 3
    assert provider.calls == 0  # checked before the call, not after a paid image
    refused = _report(report)
    assert refused["ok"] is False and refused["provider_submitted"] is False and refused["exit_code"] == 3
    assert [(e["code"], e["retryable"], e["state"]) for e in refused["errors"]] == [("preflight", False, None)]


def test_the_cli_prints_the_refusal_and_exits_not_started(tmp_path):
    report = tmp_path / "r.json"
    run = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "gen", "--provider", "codex", "--prompt", "  ",
                          "--out", str(tmp_path / "o.png"), "--report", str(report)],
                         capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert run.returncode == outcome.NOT_STARTED_EXIT, run.stderr
    assert "gen: empty prompt" in run.stderr and run.stdout == ""
    assert _report(report)["errors"][0]["code"] == "preflight"


def test_a_report_folder_that_cannot_be_made_is_refused_before_the_request(tmp_path, monkeypatch):
    provider = _use(monkeypatch, _Provider())
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot make the folder of --report") as stopped:
        gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", report=blocker / "r.json")
    assert stopped.value.code == outcome.NOT_STARTED_EXIT and provider.calls == 0
    assert not (tmp_path / "o.png").exists()


def test_an_out_folder_that_cannot_be_made_is_refused_before_the_request(tmp_path, monkeypatch):
    provider = _use(monkeypatch, _Provider())
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")
    report = tmp_path / "r.json"
    with pytest.raises(SystemExit, match="cannot make the folder of --out") as stopped:
        gen.run(provider="codex", prompt="a cup", out=blocker / "o.png", report=report)
    assert stopped.value.code == outcome.NOT_STARTED_EXIT and provider.calls == 0
    assert _report(report)["errors"][0]["code"] == "output-path"


@pytest.mark.parametrize(("said", "code", "retryable"), [
    ("grok-gen: image request failed (HTTP 429) code=rate_limited: {}", "provider-rate-limited", True),
    ("openai-gen: refused by the provider's content policy (HTTP 400) code=moderation_blocked: {}", "provider-refused", False),
    ("grok-gen: credential XAI_API_KEY rejected (HTTP 401): {}", "provider-auth", False),
    ("grok-gen: image request failed (HTTP 503): {}", "provider-unavailable", True),
    ("grok-gen: image request failed (HTTP 400) code=invalid_argument: {}", "provider-refused", False),
    ("codex-gen: codex exec exited 1\nboom", "provider-failed", True),
    # no answer that could be read: the server may have taken (and billed) it, so it is not sent again unseen
    ("openai-gen: request failed or timed out; no automatic retry (the server may have accepted it)",
     "outcome-unknown", False),
    ("xai: response was not valid JSON", "outcome-unknown", False),
    ("something no rule names", "outcome-unknown", False),
])
def test_a_failed_request_is_named_by_a_stable_code(tmp_path, monkeypatch, said, code, retryable):
    def refuse(_request):
        raise SystemExit(said)

    _use(monkeypatch, _Provider(refuse))
    report = tmp_path / "r.json"
    with pytest.raises(SystemExit) as failed:
        gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", report=report)
    assert failed.value.code == said  # the line and its exit 1 are as they were
    written = _report(report)
    assert written["provider_submitted"] is True and written["exit_code"] == 1
    assert [(e["code"], e["retryable"]) for e in written["errors"]] == [(code, retryable)]


def test_a_timeout_is_not_sent_again_and_not_retryable(tmp_path, monkeypatch):
    """codex was killed after its request left: it may have made and been charged for an image."""
    def stall(_request):
        raise gen_base.GenTimeoutError("codex-gen: no completion within 1s")

    provider = _use(monkeypatch, _Provider(stall))
    report = tmp_path / "r.json"
    with pytest.raises(SystemExit):
        gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", report=report)
    assert provider.calls == 1
    written = _report(report)
    assert written["provider_submitted"] is True
    assert [(e["code"], e["retryable"]) for e in written["errors"]] == [("provider-timeout", False)]


def test_a_timeout_is_called_again_only_when_asked(tmp_path, monkeypatch):
    stalls = []

    def stall_once(_request):
        if not stalls:
            stalls.append(1)
            raise gen_base.GenTimeoutError("codex-gen: no completion within 1s")

    provider = _use(monkeypatch, _Provider(stall_once))
    assert gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", timeout_retry=True) == 0
    assert provider.calls == 2
    stalls.clear()
    provider.calls = 0
    with pytest.raises(SystemExit):  # the old opt-out still parses, and is the default
        gen.run(provider="codex", prompt="a cup", out=tmp_path / "p.png", no_timeout_retry=True)
    assert provider.calls == 1


def test_sigterm_during_the_request_writes_the_report_and_exits_143(tmp_path, monkeypatch):
    def stopped_mid_call(_request):
        signal.raise_signal(signal.SIGTERM)
        time.sleep(1)  # the handler raises before this ends

    _use(monkeypatch, _Provider(stopped_mid_call))
    report = tmp_path / "r.json"
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit, match="stopped by SIGTERM") as stopped:
        gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", report=report)
    assert stopped.value.code == outcome.INTERRUPTED_EXIT == 143
    written = _report(report)
    assert written["ok"] is False and written["provider_submitted"] is True and written["exit_code"] == 143
    # Stopped after its request left: the same command would send it again, and the stopped one may be billed.
    assert [(e["code"], e["retryable"]) for e in written["errors"]] == [("interrupted", False)]
    assert signal.getsignal(signal.SIGTERM) is before  # the run's handler is gone with the run


@pytest.mark.skipif(sys.platform == "win32", reason="Windows ends a process on SIGTERM without running a handler")
def test_a_sigterm_from_outside_reaches_the_report(tmp_path):
    report = tmp_path / "r.json"
    driver = textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        from PIL import Image
        from sprite_gen import cli, gen
        from sprite_gen.gen import base, outcome

        class Slow:
            name, transparency = "fake", base.TRANSPARENCY_CHROMA
            def generate(self, request, workdir):
                outcome.submitted("fake")
                print("SENT", flush=True)
                time.sleep(60)

        gen._make_provider = lambda name, *, keep_session: Slow()
        sys.exit(cli.main(["gen", "--provider", "codex", "--prompt", "a cup", "--out", {str(tmp_path / 'o.png')!r},
                           "--report", {str(report)!r}]))
    """)
    child = subprocess.Popen([sys.executable, "-c", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, cwd=ROOT)
    assert child.stdout.readline().strip() == "SENT"
    child.send_signal(signal.SIGTERM)
    assert child.wait(timeout=30) == 143
    assert "stopped by SIGTERM" in child.stderr.read()
    written = _report(report)
    assert written["provider_submitted"] is True and written["errors"][0]["code"] == "interrupted"


# --- gen-set ------------------------------------------------------------------------------------------------------


def _prepared(tmp_path: Path) -> Path:
    from conftest import run_script

    out_dir = tmp_path / "run"
    states = {"idle": {"frames": 4, "fps": 6, "loop": True, "action": "standing"},
              "walk": {"frames": 6, "fps": 8, "loop": True, "action": "walking"}}
    done = run_script("prepare_sprite_run.py", "--out-dir", str(out_dir), "--character-id", "outcomebot",
                      "--request-json", json.dumps({"states": states}))
    assert done.returncode == 0, done.stdout + done.stderr
    Image.new("RGB", (64, 64), (255, 0, 255)).save(out_dir / "base-source.png")
    return out_dir


def _row_runner(*, refuse: set[str] = frozenset(), fail: set[str] = frozenset(), on_call=None):
    """A stand-in for a row's `gen`: refused before its call (exit 3, a report saying nothing left), failed after
    it (exit 1, a report naming the code), or a finished row."""

    def runner(prompt_file, out, refs, report, *, provider, model, log) -> int:
        log.write_text("fake gen\n", encoding="utf-8")
        if on_call is not None:
            on_call(out.stem)
        if out.stem in refuse:
            report.write_text(json.dumps({"ok": False, "provider_submitted": False,
                                          "errors": [outcome.error("preflight", "gen: reference image not found")]}))
            return outcome.NOT_STARTED_EXIT
        if out.stem in fail:
            report.write_text(json.dumps({"ok": False, "provider_submitted": True,
                                          "errors": [outcome.error("provider-rate-limited", "(HTTP 429)")]}))
            return 1
        Image.new("RGB", (256, 64), (255, 0, 255)).save(out)
        report.write_text(json.dumps({"provider": "codex", "ok": True, "errors": [], "provider_submitted": True}))
        return 0

    return runner


def test_a_set_whose_rows_were_all_refused_before_their_call_exits_not_started(tmp_path, monkeypatch):
    run_dir = _prepared(tmp_path)
    monkeypatch.setattr(gen_set, "run_gen_cli", _row_runner(refuse={"idle", "walk"}))
    assert gen_set.run(run_dir=run_dir, provider="codex", concurrency=2) == outcome.NOT_STARTED_EXIT
    payload = _report(run_dir / "reports" / "gen-set" / "set.report.json")
    assert payload["all_ok"] is False and payload["ok"] == 0 and payload["provider_submitted"] is False
    assert sorted((e["state"], e["code"]) for e in payload["errors"]) == [("idle", "preflight"), ("walk", "preflight")]


def test_a_set_with_a_row_that_failed_after_its_call_exits_1_and_names_the_row(tmp_path, monkeypatch):
    run_dir = _prepared(tmp_path)
    monkeypatch.setattr(gen_set, "run_gen_cli", _row_runner(fail={"walk"}))
    assert gen_set.run(run_dir=run_dir, provider="codex", concurrency=2) == 1
    payload = _report(run_dir / "reports" / "gen-set" / "set.report.json")
    assert payload["ok"] == 1 and payload["all_ok"] is False and payload["provider_submitted"] is True
    assert [(e["state"], e["code"], e["retryable"]) for e in payload["errors"]] == [("walk", "provider-rate-limited", True)]
    rows = {item["state"]: item for item in payload["items"]}
    assert rows["idle"]["errors"] == [] and rows["walk"]["provider_submitted"] is True
    # the failed row's own report is gone (no half state), what it said is kept in the set's
    assert not (run_dir / "reports" / "gen-set" / "walk.json").exists()


def test_a_row_whose_gen_ended_without_a_report_after_its_call_is_an_unknown_outcome(tmp_path, monkeypatch):
    """Killed, out of memory or crashed: it may have been charged, so it is never marked to run again unseen."""
    run_dir = _prepared(tmp_path)

    def killed(prompt_file, out, refs, report, *, provider, model, log) -> int:
        log.write_text("fake gen\n", encoding="utf-8")
        if out.stem == "walk":
            return -9
        return _row_runner()(prompt_file, out, refs, report, provider=provider, model=model, log=log)

    monkeypatch.setattr(gen_set, "run_gen_cli", killed)
    assert gen_set.run(run_dir=run_dir, provider="codex", concurrency=2) == 1
    payload = _report(run_dir / "reports" / "gen-set" / "set.report.json")
    assert payload["provider_submitted"] is True
    assert [(e["state"], e["code"], e["retryable"]) for e in payload["errors"]] == [("walk", "outcome-unknown", False)]


def test_a_row_whose_gen_said_it_finished_without_its_image_is_a_provider_failure(tmp_path, monkeypatch):
    run_dir = _prepared(tmp_path)

    def no_image(prompt_file, out, refs, report, *, provider, model, log) -> int:
        log.write_text("fake gen\n", encoding="utf-8")
        report.write_text(json.dumps({"provider": "codex", "ok": True, "errors": [], "provider_submitted": True}))
        return 0

    monkeypatch.setattr(gen_set, "run_gen_cli", no_image)
    assert gen_set.run(run_dir=run_dir, provider="codex", states="walk", concurrency=1) == 1
    payload = _report(run_dir / "reports" / "gen-set" / "set.report.json")
    assert [(e["state"], e["code"], e["retryable"]) for e in payload["errors"]] == [("walk", "provider-failed", True)]


def test_a_finished_set_says_all_ok(tmp_path, monkeypatch):
    run_dir = _prepared(tmp_path)
    monkeypatch.setattr(gen_set, "run_gen_cli", _row_runner())
    assert gen_set.run(run_dir=run_dir, provider="codex", concurrency=2) == 0
    payload = _report(run_dir / "reports" / "gen-set" / "set.report.json")
    assert payload["all_ok"] is True and payload["errors"] == [] and payload["ok"] == 2


def test_a_run_dir_that_is_not_prepared_is_refused_with_not_started(tmp_path):
    with pytest.raises(SystemExit, match="is not a prepared run") as stopped:
        gen_set.run(run_dir=tmp_path, provider="codex")
    assert stopped.value.code == outcome.NOT_STARTED_EXIT


def test_a_failed_row_report_is_never_reused_as_a_finished_row(tmp_path):
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"provider": "codex", "ok": False}), encoding="utf-8")
    assert gen_set.read_report(report) == (None, "report says the run failed")
    report.write_text(json.dumps({"provider": "codex"}), encoding="utf-8")  # a report written before `ok` existed
    assert gen_set.read_report(report)[0] == {"provider": "codex"}


def test_sigterm_stops_the_set_writes_its_report_and_exits_143(tmp_path, monkeypatch):
    run_dir = _prepared(tmp_path)

    def stop(state):
        if state == "idle":
            signal.raise_signal(signal.SIGTERM)
            time.sleep(0.5)

    monkeypatch.setattr(gen_set, "run_gen_cli", _row_runner(on_call=stop))
    with pytest.raises(SystemExit, match="gen-set: stopped by SIGTERM") as stopped:
        gen_set.run(run_dir=run_dir, provider="codex", concurrency=1, states="idle,walk")
    assert stopped.value.code == 143
    payload = _report(run_dir / "reports" / "gen-set" / "set.report.json")
    assert payload["interrupted"] == "stopped by SIGTERM" and payload["all_ok"] is False


# --- video --------------------------------------------------------------------------------------------------------


def test_sigterm_after_xai_took_the_request_names_it_for_video_resume(tmp_path, monkeypatch):
    def made():
        outcome.submitted("xai")  # the POST left
        outcome.note(request_id="req-123")  # and xAI took it
        signal.raise_signal(signal.SIGTERM)
        time.sleep(1)

    report = tmp_path / "clips" / "clip.report.json"
    args = video._build_resume_parser().parse_args(["--request-id", "x", "--out", str(tmp_path / "clips" / "c.mp4"),
                                                    "--report", str(report)])
    with pytest.raises(SystemExit) as stopped:
        video._publish_report(args, made, verb="video")
    assert stopped.value.code == 143
    written = _report(report)
    assert written["provider_submitted"] is True and written["request_id"] == "req-123"
    (error,) = written["errors"]
    assert error["code"] == "interrupted" and error["request_id"] == "req-123"
    assert error["retryable"] is False  # run again, it buys a second clip: resume it instead
    assert error["resume"].startswith("sprite-gen video-resume --request-id req-123")


def test_an_unfinished_video_keeps_its_report_and_says_resume_not_retry(tmp_path):
    def unfinished():
        outcome.submitted("xai")
        raise video.GenerationUnfinished(
            "video: generation req-9 did not finish", verb="video", request_id="req-9", auth_source="grok-login",
            model="m", out=tmp_path / "c.mp4", polls=3, polls_refused=0, polls_unanswered=0, last_http=200,
            last_status="pending", last_error=None, elapsed_seconds=1.0)

    report = tmp_path / "r.json"
    args = video._build_resume_parser().parse_args(["--request-id", "x", "--out", str(tmp_path / "c.mp4"),
                                                    "--report", str(report)])
    with pytest.raises(video.GenerationUnfinished):
        video._publish_report(args, unfinished, verb="video")
    written = _report(report)
    assert written["kind"] == "sprite-gen-video-unfinished" and written["request_id"] == "req-9"
    assert [(e["code"], e["retryable"]) for e in written["errors"]] == [("generation-unfinished", False)]


def test_a_module_entry_prints_the_refusal_too(tmp_path):
    # video-set runs `python -m sprite_gen.gen.video` per clip and keeps its output in a log: the line must be there.
    report = tmp_path / "r.json"
    run = subprocess.run([sys.executable, "-m", "sprite_gen.gen.video", "--image", str(tmp_path / "missing.png"),
                          "--prompt", "walks", "--out", str(tmp_path / "c.mp4"), "--report", str(report)],
                         capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert run.returncode == outcome.NOT_STARTED_EXIT, run.stderr
    assert run.stderr.strip() and run.stdout == ""
    refused = _report(report)
    assert refused["provider_submitted"] is False and refused["errors"][0]["code"] == "preflight"


# --- version / doctor ---------------------------------------------------------------------------------------------


def test_version_json_names_the_outcome_contract(capsys):
    assert cli.main(["version", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["kind"] == "sprite-gen-version" and info["name"] == "sprite-gen" and info["version"]
    assert info["outcome"]["exit_codes"]["not_started"] == 3 and info["outcome"]["exit_codes"]["sigterm"] == 143
    assert info["outcome"]["codes"]["provider-timeout"] == {"retryable": False}
    assert set(info["outcome"]["codes"]) == set(outcome.CODES)


def test_doctor_json_reports_without_calling_a_provider(capsys, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(outcome, "submitted", lambda provider, **_: sent.append(provider))
    with outcome.recording("doctor") as record:
        rc = cli.main(["doctor", "--json"])
    info = json.loads(capsys.readouterr().out)
    assert rc == (0 if info["ok"] else 1) and info["kind"] == "sprite-gen-doctor"
    assert set(info["providers"]) == {"codex", "grok", "openai"} and "ffmpeg" in info["tools"]
    assert sent == [] and record.requests == []


def test_every_code_has_its_retryable_and_an_unknown_code_is_refused():
    assert outcome.error("preflight", "x") == {"state": None, "code": "preflight", "retryable": False, "message": "x"}
    with pytest.raises(ValueError):
        outcome.error("no-such-code", "x")


def test_a_child_that_left_no_report_counts_as_sent_unless_its_exit_says_otherwise(tmp_path):
    missing = tmp_path / "none.json"
    assert outcome.child_submitted(1, missing) is True
    assert outcome.child_submitted(-15, missing) is True
    # 75 without a report: codex may have run before it said its usage was used up
    assert outcome.child_submitted(gen_base.CODEX_UNAVAILABLE_EXIT, missing) is True
    for nothing_sent in (2, outcome.NOT_STARTED_EXIT):
        assert outcome.child_submitted(nothing_sent, missing) is False
    said = tmp_path / "said.json"
    said.write_text(json.dumps({"provider_submitted": False}), encoding="utf-8")
    assert outcome.child_submitted(1, said) is False


def test_a_transport_failure_after_the_request_left_is_not_retried(tmp_path, monkeypatch):
    from sprite_gen.gen import xai

    def cut(_request):
        raise xai.TransportError("timed out")

    _use(monkeypatch, _Provider(cut))
    report = tmp_path / "r.json"
    with pytest.raises(SystemExit):
        gen.run(provider="codex", prompt="a cup", out=tmp_path / "o.png", report=report)
    assert [(e["code"], e["retryable"]) for e in _report(report)["errors"]] == [("outcome-unknown", False)]


def test_sigterm_while_the_failure_report_is_written_still_leaves_it_and_exits_143(tmp_path, monkeypatch):
    def fail(_request):
        raise SystemExit("grok-gen: image request failed (HTTP 500): {}")

    _use(monkeypatch, _Provider(fail))
    written = gen.atomic_write_text

    def stopped_mid_write(path, text):
        signal.raise_signal(signal.SIGTERM)  # arrives exactly while the report is written
        return written(path, text)

    monkeypatch.setattr(gen, "atomic_write_text", stopped_mid_write)
    report = tmp_path / "r.json"
    rc = cli.main(["gen", "--provider", "codex", "--prompt", "x", "--out", str(tmp_path / "o.png"),
                   "--report", str(report)])
    assert rc == 143
    assert _report(report)["errors"][0]["code"] == "provider-unavailable"  # the failure, written whole


def test_sigterm_while_the_success_report_is_written_leaves_it_whole(tmp_path, monkeypatch):
    _use(monkeypatch, _Provider())
    written = gen.atomic_write_text

    def stopped_mid_write(path, text):
        signal.raise_signal(signal.SIGTERM)
        return written(path, text)

    monkeypatch.setattr(gen, "atomic_write_text", stopped_mid_write)
    report = tmp_path / "r.json"
    rc = cli.main(["gen", "--provider", "codex", "--prompt", "x", "--out", str(tmp_path / "o.png"),
                   "--report", str(report)])
    assert rc == 143 and _report(report)["ok"] is True


def test_a_stopped_set_starts_no_child_after_the_stop(tmp_path, monkeypatch):
    """A row still preparing when the stop lands never starts its paid child (`base.run_logged` refuses)."""
    run_dir = _prepared(tmp_path)
    ran = tmp_path / "child-ran"

    def runner(prompt_file, out, refs, report, *, provider, model, log) -> int:
        if out.stem == "idle":
            signal.raise_signal(signal.SIGTERM)  # the stop arrives while walk is still preparing
            return 1
        time.sleep(0.5)
        return gen_base.run_logged([sys.executable, "-c", f"open({str(ran)!r}, 'w').close()"], log)

    monkeypatch.setattr(gen_set, "run_gen_cli", runner)
    with pytest.raises(SystemExit) as stopped:
        gen_set.run(run_dir=run_dir, provider="codex", concurrency=2, states="idle,walk")
    assert stopped.value.code == 143
    assert not ran.exists()
    payload = _report(run_dir / "reports" / "gen-set" / "set.report.json")
    walk = next(item for item in payload["items"] if item["state"] == "walk")
    assert walk["provider_submitted"] is False and walk["errors"][0]["code"] == "interrupted"
    assert "was not begun" in walk["error"]


def test_a_child_is_never_started_once_the_run_is_stopped(tmp_path):
    with outcome.recording("t"):
        outcome.stop()
        with pytest.raises(outcome.NotStarted):
            gen_base.run_logged([sys.executable, "-c", "pass"], tmp_path / "log.txt")
    with outcome.recording("t"):  # a new run starts clear
        assert gen_base.run_logged([sys.executable, "-c", "pass"], tmp_path / "log.txt") == 0


def test_a_staggered_clip_stops_waiting_when_the_set_is_stopped():
    from sprite_gen.video import batch

    with outcome.recording("t"):
        batch._last_start[0] = time.monotonic()
        threading.Timer(0.1, outcome.stop).start()
        started = time.monotonic()
        with pytest.raises(outcome.NotStarted):
            batch._staggered_start(30.0)
        assert time.monotonic() - started < 5


def test_a_walk_start_never_reads_an_earlier_runs_report(tmp_path):
    from sprite_gen.video import batch

    base = tmp_path / "base.png"
    Image.new("RGB", (64, 64), (255, 0, 255)).save(base)
    item_dir = tmp_path / "item"
    item_dir.mkdir()
    stale = item_dir / "walk-start.report.json"
    stale.write_text(json.dumps({"ok": False, "provider_submitted": False}), encoding="utf-8")

    def killed(base, prompt, out, report, *, log, provider=None):  # sent its request, then killed: no report
        return -15

    with pytest.raises(SystemExit):
        batch.walk_start_still(base, "front", item_dir, key="auto", force=True, runner=killed)
    assert not stale.exists()
    assert outcome.child_submitted(-15, stale) is True


def test_a_resume_that_fails_after_its_polls_is_not_a_not_started_exit(tmp_path, monkeypatch):
    from sprite_gen.gen import xai

    monkeypatch.setattr(video, "resolve_credential", lambda: xai.Credential(token="t", source=xai.AUTH_SOURCE_GROK_LOGIN))
    monkeypatch.setattr(video, "http_json", lambda *a, **k: (200, {"status": "done", "video": {"respect_moderation": False, "url": ""}}))
    report = tmp_path / "r.json"
    with pytest.raises(SystemExit, match="content policy") as failed:
        cli.main(["video-resume", "--request-id", "req-1", "--out", str(tmp_path / "c.mp4"), "--report", str(report)])
    assert not isinstance(failed.value.code, int)  # a message, exit 1: not NOT_STARTED_EXIT
    written = _report(report)
    assert written["provider_submitted"] is False and written["exit_code"] == 1  # a poll bills nothing
    assert [(e["code"], e["retryable"]) for e in written["errors"]] == [("provider-refused", False)]


def test_retry_marks_a_post_but_not_a_get():
    from sprite_gen.gen import retry

    with outcome.recording("t") as record:
        retry.ask(lambda: (200, {}, None), provider="xai", who="x", label="l", method="GET")
        assert record.requests == []
        retry.ask(lambda: (200, {}, None), provider="xai", who="x", label="l")
        assert record.requests == ["xai"]
    outcome.submitted("after")  # no record open: marks nothing, fails nothing


def test_a_stop_is_retryable_only_while_nothing_was_sent():
    assert outcome.error(outcome.INTERRUPTED, "stopped")["retryable"] is True
    assert outcome.error(outcome.INTERRUPTED, "stopped", submitted=False)["retryable"] is True
    assert outcome.error(outcome.INTERRUPTED, "stopped", submitted=True)["retryable"] is False
    record = outcome.Record("gen", requests=["codex"])
    stop = outcome.error(outcome.INTERRUPTED, "stopped")
    assert outcome.report_fields(record, errors=[stop])["errors"][0]["retryable"] is False
    assert outcome.report_fields(outcome.Record("gen"), errors=[stop])["errors"][0]["retryable"] is True


def test_a_set_sprite_gen_fails_in_still_reports_its_finished_items():
    from concurrent.futures import Future
    finished, raised, pending = Future(), Future(), Future()
    finished.set_result({"state": "idle", "ok": True, "provider_submitted": True})
    raised.set_exception(RuntimeError("bug"))
    results: list = []
    outcome.gather_finished({finished: "idle", raised: "walk", pending: "run"}, set(), results)
    assert results == [{"state": "idle", "ok": True, "provider_submitted": True}] and pending.cancelled()
    error = outcome.crash_error(RuntimeError("bug"), "gen-set")
    assert (error["code"], error["retryable"]) == (outcome.INTERNAL, False) and "RuntimeError: bug" in error["message"]
