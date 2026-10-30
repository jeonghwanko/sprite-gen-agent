"""RIFE runs on one machine's GPU take turns (`rife.gpu_turn`): a run waits while another holds the turn, and each
gives back the memory it left cached before the next one starts, a failed run included."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sprite_gen.spec import runio
from sprite_gen.video import rife

pytestmark = pytest.mark.skipif(runio._LOCK_IMPL is None, reason="no cross-process lock here")


def test_a_run_waits_for_the_turn_and_the_last_cache_is_released_first(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rife, "GPU_LOCK", tmp_path / "gpu.lock")
    events: list[str] = []
    holding = threading.Event()

    def first() -> None:
        with rife.gpu_turn(lambda: events.append("first released")):
            holding.set()
            time.sleep(0.4)
            events.append("first ran")

    worker = threading.Thread(target=first)
    worker.start()
    holding.wait(5)
    began = time.perf_counter()
    with rife.gpu_turn(lambda: events.append("second released")):
        waited = time.perf_counter() - began
        events.append("second ran")
    worker.join()
    assert waited > 0.2
    assert events == ["first ran", "first released", "second ran", "second released"]


def test_a_run_that_fails_still_releases_and_hands_the_turn_on(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rife, "GPU_LOCK", tmp_path / "gpu.lock")
    released: list[int] = []
    with pytest.raises(RuntimeError, match="out of memory"):
        with rife.gpu_turn(lambda: released.append(1)):
            raise RuntimeError("CUDA out of memory")
    assert released == [1]
    with rife.gpu_turn(lambda: released.append(2)):  # the turn is free again
        pass
    assert released == [1, 2]
