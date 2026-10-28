# SPDX-License-Identifier: Apache-2.0
"""Codex sessions found through codex's state DB, and through the sessions-root scan without one.

Fixtures (tests/fixtures/codex): a rollout shaped as codex 0.153.4 writes one (no state DB is consulted), a rollout
shaped as codex 0.165.0 writes one (with that version's `inputImages`, `imagegenRequestId` and `generationId`), the
same rollout with the image as an attachment-store `fileId` (the 0.164+ `GeneratedImageRef::File` shape), and the
`threads` table of codex's `state_5.sqlite`. The three older record shapes (`image_generation_call`,
`image_generation_end`, the inline `image_gen.generation` item) are covered in test_gen.py.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from sprite_gen.gen import codex_provider
from sprite_gen.gen.base import GenRequest, verify_png

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex"
SCAN_SESSION = "018f0000-0000-7000-8000-0000000001a1"  # rollout-codex-0.153.4.jsonl
INDEXED_SESSION = "018f0000-0000-7000-8000-0000000001b2"  # rollout-codex-0.165.0.jsonl


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv(codex_provider.SQLITE_HOME_ENV, raising=False)
    monkeypatch.setattr(codex_provider, "codex_login",
                        lambda: codex_provider.CodexLogin(codex_provider.AUTH_SOURCE_LOGIN))
    return home


def _place_rollout(fixture: str, session_id: str, directory: Path) -> Path:
    rollout = directory / f"rollout-2026-10-28T09-30-18-{session_id}.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / fixture, rollout)
    return rollout


def _state_db(directory: Path, rows: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / codex_provider.STATE_DB_FILENAME
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript((FIXTURES / "state_5-threads.sql").read_text(encoding="utf-8"))
        for row in rows:
            values = {"created_at": 1793179818, "updated_at": 1793179893, "source": "exec", "model_provider": "openai",
                      "cwd": "/tmp/sprite-gen-gen-fixture", "title": "a mushroom", "approval_mode": "never",
                      "sandbox_policy": '{"type":"workspace-write"}', "history_mode": "paginated", **row}
            columns = ", ".join(values)
            conn.execute(f"INSERT INTO threads ({columns}) VALUES ({', '.join('?' for _ in values)})",
                         tuple(values.values()))
        conn.commit()
    return db


def _generate(tmp_path: Path, monkeypatch, session_id: str, codex_ran, *, model: str | None = None):
    class _Completed:
        returncode = 0
        stdout = json.dumps({"type": "thread.started", "thread_id": session_id}) + "\n"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        codex_ran()
        return _Completed()

    monkeypatch.setattr(codex_provider.subprocess, "run", _fake_run)
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    raw = workdir / "raw.png"
    result = codex_provider.CodexProvider(keep_session=True).generate(
        GenRequest(prompt="a mushroom", raw=raw, model=model), workdir)
    assert verify_png(raw) > 0
    return result


def test_rollout_without_state_db_is_found_by_scan(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    sessions = codex_home / "sessions" / "2026" / "10" / "23"

    result = _generate(tmp_path, monkeypatch, SCAN_SESSION,
                       lambda: _place_rollout("rollout-codex-0.153.4.jsonl", SCAN_SESSION, sessions))

    assert result.session_id == SCAN_SESSION
    assert result.model == "gpt-6-astra"
    assert result.extra["model_source"] == "rollout"
    assert result.extra["model_effort"] == "xhigh"
    assert result.extra["requested_model"] is None
    assert result.extra["codex_cli_version"] == "0.153.4"
    assert result.extra["model_provider"] == "openai"
    assert result.extra["state_db"] == {"status": "absent"}
    assert result.extra["transparent_background_reported"] is True


def test_state_db_locates_a_rollout_outside_the_sessions_root(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    # A codex that keeps its rollouts somewhere the scan of `sessions/` never looks is still read, because its state
    # DB names the file.
    elsewhere = codex_home / "rollouts" / "2026" / "10" / "28"
    rollout_path = elsewhere / f"rollout-2026-10-28T09-30-18-{INDEXED_SESSION}.jsonl"

    def _codex_ran() -> None:
        _place_rollout("rollout-codex-0.165.0.jsonl", INDEXED_SESSION, elsewhere)
        _state_db(codex_home, [{"id": INDEXED_SESSION, "rollout_path": str(rollout_path), "model": "gpt-6-astra",
                                "cli_version": "0.165.0"}])

    result = _generate(tmp_path, monkeypatch, INDEXED_SESSION, _codex_ran, model="gpt-6-astra")

    assert result.model == "gpt-6-astra"
    assert result.extra["requested_model"] == "gpt-6-astra"
    assert result.extra["model_source"] == "rollout"
    assert result.extra["model_effort"] == "high"
    assert result.extra["codex_cli_version"] == "0.165.0"
    assert result.extra["state_db"] == {"status": "indexed"}
    assert result.extra["image_tool_trace"]["status"] == "captured"


def test_state_db_and_scan_agree_on_the_same_rollout(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    sessions = codex_home / "sessions" / "2026" / "10" / "28"

    def _codex_ran() -> None:
        rollout = _place_rollout("rollout-codex-0.165.0.jsonl", INDEXED_SESSION, sessions)
        _state_db(codex_home, [{"id": INDEXED_SESSION, "rollout_path": str(rollout), "model": "gpt-6-astra"}])

    result = _generate(tmp_path, monkeypatch, INDEXED_SESSION, _codex_ran)

    assert result.extra["state_db"] == {"status": "indexed"}
    assert result.model == "gpt-6-astra"


def test_state_db_row_without_a_rollout_names_the_missing_file(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    missing = codex_home / "sessions" / "2026" / "10" / "28" / f"rollout-2026-10-28T09-30-18-{INDEXED_SESSION}.jsonl"

    def _codex_ran() -> None:
        _state_db(codex_home, [{"id": INDEXED_SESSION, "rollout_path": str(missing), "model": "gpt-6-astra"}])

    with pytest.raises(SystemExit, match="state DB lists session") as error:
        _generate(tmp_path, monkeypatch, INDEXED_SESSION, _codex_ran)
    assert str(missing) in str(error.value)


def test_state_db_and_scan_naming_two_files_fail(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    def _codex_ran() -> None:
        _place_rollout("rollout-codex-0.165.0.jsonl", INDEXED_SESSION, codex_home / "sessions" / "2026" / "10" / "28")
        other = _place_rollout("rollout-codex-0.165.0.jsonl", INDEXED_SESSION, codex_home / "elsewhere")
        _state_db(codex_home, [{"id": INDEXED_SESSION, "rollout_path": str(other)}])

    with pytest.raises(SystemExit, match="multiple rollout jsonl files"):
        _generate(tmp_path, monkeypatch, INDEXED_SESSION, _codex_ran)


def test_model_falls_back_to_the_state_db_row(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    sessions = codex_home / "sessions" / "2026" / "10" / "28"

    def _codex_ran() -> None:
        rollout = _place_rollout("rollout-codex-0.165.0.jsonl", INDEXED_SESSION, sessions)
        lines = [line for line in rollout.read_text(encoding="utf-8").splitlines() if '"turn_context"' not in line]
        rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _state_db(codex_home, [{"id": INDEXED_SESSION, "rollout_path": str(rollout), "model": "gpt-6-astra-db",
                                "cli_version": "0.165.0"}])

    result = _generate(tmp_path, monkeypatch, INDEXED_SESSION, _codex_ran, model="asked-for")

    assert result.model == "gpt-6-astra-db"
    assert result.extra["model_source"] == "state-db"
    assert result.extra["requested_model"] == "asked-for"


def test_no_recorded_model_is_none_not_the_request(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    sessions = codex_home / "sessions" / "2026" / "10" / "23"

    def _codex_ran() -> None:
        rollout = _place_rollout("rollout-codex-0.153.4.jsonl", SCAN_SESSION, sessions)
        lines = [line for line in rollout.read_text(encoding="utf-8").splitlines() if '"turn_context"' not in line]
        rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = _generate(tmp_path, monkeypatch, SCAN_SESSION, _codex_ran, model="asked-for")

    assert result.model is None
    assert result.extra["model_source"] is None
    assert result.extra["requested_model"] == "asked-for"


def test_unreadable_state_db_is_reported_and_the_scan_still_reads(tmp_path: Path, monkeypatch,
                                                                    codex_home: Path) -> None:
    sessions = codex_home / "sessions" / "2026" / "10" / "23"

    def _codex_ran() -> None:
        _place_rollout("rollout-codex-0.153.4.jsonl", SCAN_SESSION, sessions)
        (codex_home / codex_provider.STATE_DB_FILENAME).write_bytes(b"not a sqlite database" * 64)

    result = _generate(tmp_path, monkeypatch, SCAN_SESSION, _codex_ran)

    assert result.extra["state_db"]["status"] == "unreadable"
    assert "DatabaseError" in result.extra["state_db"]["reason"]
    assert result.model == "gpt-6-astra"


def test_codex_sqlite_home_is_where_the_state_db_is_read(tmp_path: Path, monkeypatch, codex_home: Path) -> None:
    sqlite_home = tmp_path / "codex-sqlite"
    monkeypatch.setenv(codex_provider.SQLITE_HOME_ENV, str(sqlite_home))
    elsewhere = codex_home / "rollouts"

    def _codex_ran() -> None:
        rollout = _place_rollout("rollout-codex-0.165.0.jsonl", INDEXED_SESSION, elsewhere)
        _state_db(sqlite_home, [{"id": INDEXED_SESSION, "rollout_path": str(rollout)}])
        _state_db(codex_home, [])  # the state root's own DB has no row; CODEX_SQLITE_HOME wins

    result = _generate(tmp_path, monkeypatch, INDEXED_SESSION, _codex_ran)

    assert result.extra["state_db"] == {"status": "indexed"}


def test_an_attachment_store_image_names_the_file_id_instead_of_no_result(tmp_path: Path, monkeypatch,
                                                                         codex_home: Path) -> None:
    # codex 0.164+ can write the image as an attachment-store `fileId` instead of an inline `result`; `codex exec`
    # did not through 0.165.0, and a codex that does must say so rather than "has no result".
    sessions = codex_home / "sessions" / "2026" / "10" / "28"

    with pytest.raises(SystemExit, match=r"attachment store \(fileId='file-fixture-0001'\)"):
        _generate(tmp_path, monkeypatch, INDEXED_SESSION,
                  lambda: _place_rollout("rollout-codex-file-id.jsonl", INDEXED_SESSION, sessions))


def test_state_db_without_a_threads_table_is_absent(tmp_path: Path, codex_home: Path) -> None:
    with closing(sqlite3.connect(codex_home / codex_provider.STATE_DB_FILENAME)) as conn:
        conn.execute("CREATE TABLE other (id TEXT)")
        conn.commit()

    assert codex_provider._indexed_thread(INDEXED_SESSION, codex_home).status == "absent"
