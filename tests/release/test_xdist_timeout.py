# SPDX-License-Identifier: Apache-2.0
"""An xdist worker on Windows gives a test without its own timeout mark XDIST_WINDOWS_TIMEOUT in place of the configured
120 s (tests/conftest.py): there a timeout ends the worker's process, and tests of half a minute alone went over 120 s
beside other workers. Elsewhere, outside a worker, or with --timeout named, the configured timeout stands.

pytest-xdist is not a dev dependency, so a worker is stood in for by a plugin that gives the config the `workerinput`
xdist gives each worker's."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import XDIST_WINDOWS_TIMEOUT, xdist_windows_timeout

ROOT = Path(__file__).resolve().parents[2]

PROBE = '''
import pytest


def test_unmarked(request):
    marker = request.node.get_closest_marker("timeout")
    print("TIMEOUT", marker.args[0] if marker else None)


@pytest.mark.timeout(7)
def test_marked(request):
    print("TIMEOUT", request.node.get_closest_marker("timeout").args[0])
'''

WORKER = '''
def pytest_configure(config):
    config.workerinput = {"workerid": "gw0"}
'''


def _config(worker: bool, timeout: float | None = None) -> SimpleNamespace:
    config = SimpleNamespace(getoption=lambda name, default=None: timeout if name == "timeout" else default)
    if worker:
        config.workerinput = {"workerid": "gw0"}
    return config


@pytest.mark.parametrize(("windows", "worker", "expected"), [
    (True, True, XDIST_WINDOWS_TIMEOUT), (True, False, None), (False, True, None), (False, False, None),
], ids=["windows-worker", "windows-one-process", "posix-worker", "posix-one-process"])
def test_only_a_windows_worker_raises_the_default_timeout(monkeypatch, windows, worker, expected) -> None:
    monkeypatch.delenv("PYTEST_TIMEOUT", raising=False)
    assert xdist_windows_timeout(_config(worker), windows=windows) == expected


def test_a_named_timeout_stands_in_a_windows_worker(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_TIMEOUT", raising=False)
    assert xdist_windows_timeout(_config(True, timeout=30.0), windows=True) is None
    monkeypatch.setenv("PYTEST_TIMEOUT", "30")
    assert xdist_windows_timeout(_config(True), windows=True) is None


def test_a_worker_marks_only_the_tests_without_a_timeout_of_their_own(tmp_path: Path) -> None:
    # This repository's configuration, and tests/conftest.py as the one conftest (-p), over a file outside the suite.
    (tmp_path / "test_probe.py").write_text(PROBE)
    (tmp_path / "stand_in_worker.py").write_text(WORKER)
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_TIMEOUT"}
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), env.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"), "--rootdir", str(tmp_path),
         "--confcutdir", str(tmp_path), "--noconftest", "-p", "conftest", "-p", "stand_in_worker",
         "-p", "no:cacheprovider", "-s", "-q", str(tmp_path / "test_probe.py")],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.returncode == 0, f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    said = sorted(line.split()[-1] for line in proc.stdout.splitlines() if "TIMEOUT " in line)
    unmarked = str(XDIST_WINDOWS_TIMEOUT) if os.name == "nt" else "None"
    assert said == sorted([unmarked, "7"]), proc.stdout
