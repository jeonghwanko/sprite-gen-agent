# SPDX-License-Identifier: Apache-2.0
"""A local tool run with a bound on its time: ffmpeg, ffprobe, img2webp, node, or a sprite-gen child that only reads
pixels. None of them sends a provider request, so one stopped at its bound is a plain failure of this step.

Before 2026-10-29 these calls waited forever: a tool that hung (a clip ffmpeg cannot finish reading, a stuck pipe) held
the whole run, and an unattended caller (the creator kit's worker) could only stop it at its stage's own limit, with
nothing saying which step hung. Calls that start a provider (`gen`, `video`, codex), or may (the curation server's
reroll, `correction-loop`'s provider command), keep their own limits and are not run through here, and neither is a native
file picker that waits on a person (`serve_compose`).
"""

from __future__ import annotations

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
