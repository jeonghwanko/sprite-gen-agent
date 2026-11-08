# SPDX-License-Identifier: Apache-2.0
"""A local tool run with a bound on its time: ffmpeg, ffprobe, img2webp, node, or a sprite-gen child that only reads
pixels. None of them sends a provider request, so one stopped at its bound is a plain failure of this step.

Before 2026-10-29 these calls waited forever: a tool that hung (a clip ffmpeg cannot finish reading, a stuck pipe) held
the whole run, and an unattended caller (the creator kit's worker) could only stop it at its stage's own limit, with
nothing saying which step hung. Calls that start a provider (`gen`, `video`, codex), or may (the curation server's
reroll, `correction-loop`'s provider command), keep their own limits and are not run through `run`, and neither is a native
file picker that waits on a person (`serve_compose`). The curation server and `correction-loop` bound theirs with
`run_tree`, which stops the provider such a command started along with it.
"""

from __future__ import annotations

import os
import sys
import signal
import subprocess
from pathlib import Path
from typing import Any

PROBE_SECONDS = 120  # ffprobe reading a clip's streams
TOOL_SECONDS = 900  # ffmpeg extracting or encoding a clip's frames, img2webp, node
CHILD_SECONDS = 1800  # a sprite-gen child that reads and writes frames (extract run again for one state)


def run(cmd: list[str], *, seconds: float, what: str, **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run(cmd, **kwargs)` bounded by `seconds`: a tool still running then is killed and the step fails
    with SystemExit naming `what` and the tool."""
    try:
        return subprocess.run(cmd, timeout=seconds, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"{what}: {Path(str(cmd[0])).name} gave no result in {seconds:g} s and was stopped") from exc


def run_tree(cmd: list[str], *, seconds: float | None, **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run(cmd, capture_output=True, **kwargs)` bounded by `seconds` (None: no bound), where a command
    still running then is stopped with every process it started, and TimeoutExpired raised with what it printed.

    `subprocess.run(timeout=)` kills only `cmd` itself: a provider CLI that a `reroll` child started would keep running,
    and keep writing into the run dir, after the caller has given up. This runs `cmd` in its own process group (POSIX)
    and kills the group, or the whole tree (`taskkill /T`, Windows)."""
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as proc:
        try:
            out, err = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            out, err = proc.communicate()
            raise subprocess.TimeoutExpired(cmd, seconds or 0, output=out, stderr=err) from None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
