# SPDX-License-Identifier: Apache-2.0
"""On Windows pytest refuses to start outside Python's UTF-8 mode (tests/conftest.py): many tests read reports in the
locale encoding and fail there before their real assertion. Elsewhere, and in UTF-8 mode, the run starts."""

from __future__ import annotations

import pytest

from conftest import utf8_mode_missing


@pytest.mark.parametrize(
    ("windows", "utf8_mode", "refused"),
    [(True, False, True), (True, True, False), (False, False, False), (False, True, False)],
)
def test_only_windows_without_utf8_mode_is_refused(windows: bool, utf8_mode: bool, refused: bool) -> None:
    refusal = utf8_mode_missing(windows=windows, utf8_mode=utf8_mode)
    assert (refusal is not None) is refused
    if refusal:
        assert "PYTHONUTF8=1" in refusal and "$env:PYTHONUTF8" in refusal
