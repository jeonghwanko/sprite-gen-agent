# SPDX-License-Identifier: Apache-2.0
"""A local tool that hangs is stopped at its bound, and the step fails naming it (sprite_gen/local_tool.py)."""

import sys

import pytest

from sprite_gen import local_tool


def test_a_tool_past_its_bound_is_stopped_and_named():
    with pytest.raises(SystemExit, match=r"video-frames: python[\w.]* gave no result in 0.5 s and was stopped"):
        local_tool.run([sys.executable, "-c", "import time; time.sleep(30)"], seconds=0.5, what="video-frames")


def test_a_tool_that_ends_in_time_returns_its_result():
    done = local_tool.run([sys.executable, "-c", "print('ok')"], seconds=30, what="probe", capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.strip() == "ok"
