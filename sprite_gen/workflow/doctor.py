# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen version` and `sprite-gen doctor` — what this install is, and what it can run here, for a program.

Neither sends anything to a provider: `doctor` reads logins the way a generation would (`workflow.access`), finds
tools on PATH and imports the runtime, nothing more. A login it calls ready is not proof of a paid subscription or
of quota left (docs/gen.md "Outcome").
"""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sprite_gen.gen import outcome
from sprite_gen.gen.base import CODEX_UNAVAILABLE_EXIT, print_json

# Bumped when the outcome keys, codes or exit codes change in a way a caller must know about.
OUTCOME_CONTRACT = 1
PACKAGE = "sprite-gen"
FORK = "sprite-gen-agent"
_ROOT = Path(__file__).resolve().parents[2]


def package_version() -> str | None:
    """The checkout's own `pyproject.toml` version when there is one (an editable install's metadata can lag a
    pull), else the installed distribution's."""
    pyproject = _ROOT / "pyproject.toml"
    if pyproject.is_file():
        found = re.search(r'(?m)^version = "([^"]+)"', pyproject.read_text(encoding="utf-8"))
        if found:
            return found.group(1)
    try:
        from importlib.metadata import version

        return version(PACKAGE)
    except Exception:  # noqa: BLE001 — no metadata is an answer, not a failure
        return None


def git_commit() -> str | None:
    """The commit the checkout is at (a kit pins sprite-gen by commit), or None outside a git checkout."""
    if not (_ROOT / ".git").exists() or shutil.which("git") is None:
        return None
    try:
        done = subprocess.run(["git", "-C", str(_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def version_info() -> dict[str, Any]:
    return {
        "kind": "sprite-gen-version",
        "name": PACKAGE,
        "fork": FORK,
        "version": package_version(),
        "commit": git_commit(),
        "python": platform.python_version(),
        "outcome": {
            "contract": OUTCOME_CONTRACT,
            "exit_codes": {"ok": 0, "failed": 1, "usage": 2, "not_started": outcome.NOT_STARTED_EXIT,
                           "codex_unavailable_ask": CODEX_UNAVAILABLE_EXIT, "sigterm": outcome.INTERRUPTED_EXIT},
            "codes": {code: {"retryable": retryable} for code, retryable in outcome.CODES.items()},
        },
    }


def _tool(name: str, *, version_args: tuple[str, ...] = ("--version",)) -> dict[str, Any]:
    """Is `name` on PATH, where, and the first line its version flag prints (never more than 10 s)."""
    found = shutil.which(name)
    if found is None:
        return {"found": False}
    line = None
    try:
        done = subprocess.run([found, *version_args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10)
        line = next((text for text in (done.stdout + "\n" + done.stderr).splitlines() if text.strip()), None)
    except (OSError, subprocess.SubprocessError):
        pass
    return {"found": True, "path": found, "version": line.strip() if line else None}


def _runtime() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for module in ("numpy", "PIL"):
        try:
            imported = __import__(module)
            checks[module] = {"ok": True, "version": getattr(imported, "__version__", None)}
        except Exception as exc:  # noqa: BLE001 — a missing runtime is the finding
            checks[module] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return checks


def _providers() -> dict[str, Any]:
    from sprite_gen.workflow.access import probe_access

    found: dict[str, Any] = {}
    for provider in ("codex", "grok", "openai"):
        try:
            found[provider] = probe_access(provider)
        except Exception as exc:  # noqa: BLE001 — one unreadable login must not hide the others
            found[provider] = {"provider": provider, "login": "unknown", "reason": f"{type(exc).__name__}: {exc}"}
    return found


def _rife() -> dict[str, Any]:
    from sprite_gen.video import rife

    try:
        binary, model = rife.locate()
    except (rife.RifeNotInstalled, SystemExit, OSError, ValueError) as exc:
        return {"found": False, "reason": str(exc).split(";")[0] if str(exc) else type(exc).__name__,
                "install": rife.install_command()}
    return {"found": True, "binary": str(binary), "model": str(model)}


def doctor_info() -> dict[str, Any]:
    """`ok` is whether the runtime imports (nothing runs without it); providers and tools are reported as found,
    since which of them a run needs depends on the run."""
    runtime = _runtime()
    return {
        "kind": "sprite-gen-doctor",
        **{key: value for key, value in version_info().items() if key != "kind"},
        "ok": all(check["ok"] for check in runtime.values()),
        "runtime": runtime,
        "providers": _providers(),
        "tools": {
            "codex": _tool("codex"),
            "grok": _tool("grok"),
            "ffmpeg": _tool("ffmpeg", version_args=("-version",)),
            "ffprobe": _tool("ffprobe", version_args=("-version",)),
            "img2webp": _tool("img2webp", version_args=("-version",)),
            "rife": _rife(),
        },
    }


def _print_text(info: dict[str, Any]) -> None:
    print(f"{info['name']} {info['version'] or '(unknown version)'} ({info['fork']}"
          f"{', ' + info['commit'][:12] if info.get('commit') else ''}), Python {info['python']}")
    for name, check in (info.get("runtime") or {}).items():
        print(f"  {name:<10} {'ok ' + str(check.get('version') or '') if check['ok'] else 'MISSING: ' + check['error']}")
    for name, probe in (info.get("providers") or {}).items():
        print(f"  {name:<10} login {probe.get('login')}, billing {probe.get('billing', '-')}")
    for name, tool in (info.get("tools") or {}).items():
        print(f"  {name:<10} {'found ' + str(tool.get('path') or tool.get('binary') or '') if tool['found'] else 'not found'}")


def add_version_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="the version, commit and outcome contract as JSON")


def add_doctor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="every check as JSON (no provider is called)")


def run_version(**kwargs: object) -> int:
    info = version_info()
    if kwargs.get("json"):
        print_json(info)
    else:
        print(f"{info['name']} {info['version'] or '(unknown version)'}")
    return 0


def run_doctor(**kwargs: object) -> int:
    info = doctor_info()
    if kwargs.get("json"):
        print_json(info)
    else:
        _print_text(info)
    return 0 if info["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sprite-gen doctor", description=__doc__)
    add_doctor_arguments(parser)
    return run_doctor(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
