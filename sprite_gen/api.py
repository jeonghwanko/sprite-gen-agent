# SPDX-License-Identifier: Apache-2.0
"""The names a program that embeds sprite-gen may import (docs/api.md).

Everything here keeps its name and its keyword arguments from release to release; a change to either is a
breaking change and is named in the changelog. Every other module is internal and may move: a caller that needs
something else asks for it to be added here, or runs the `sprite-gen` command. The names load on first use, so
`from sprite_gen.api import probe_access` does not import the motion or RIFE code.
"""
from __future__ import annotations

import importlib
from typing import Any

# name -> (module, attribute)
_EXPORTS = {
    # motion inspection (docs/asset-tools.md "Motion"): the report, and the stable codes of a stance's reasons
    "analyze_motion": ("sprite_gen.qa.motion", "analyze_motion"),
    "STANCE_REASONS": ("sprite_gen.qa.motion", "STANCE_REASONS"),
    # an asset (a strip, a run manifest, an atlas state) as frames, durations and an anchor
    "load_asset": ("sprite_gen.spec.assets", "load_asset"),
    # a run folder held by one writer, and files replaced all together or not at all
    "acquire_run_dir_lock": ("sprite_gen.spec.runio", "acquire_run_dir_lock"),
    "release_run_dir_lock": ("sprite_gen.spec.runio", "release_run_dir_lock"),
    "atomic_write_set": ("sprite_gen.spec.runio", "atomic_write_set"),
    # whether a provider's login can be used here, without calling it
    "probe_access": ("sprite_gen.workflow.access", "probe_access"),
}

__all__ = sorted([*_EXPORTS, "rife_status"])


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module 'sprite_gen.api' has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(importlib.import_module(module), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return __all__


def rife_status() -> dict[str, Any]:
    """RIFE as the engine finds it (SPRITE_GEN_RIFE, PATH, the user data folder), checked on one frame:
    `{"status": "ok", "path", "version"}`, `{"status": "missing", "reason"}` or `{"status": "broken", "reason"[, "path"]}`.
    It never downloads or installs anything (`sprite-gen rife install` does)."""
    from sprite_gen.video import rife, rife_install

    try:
        binary, model = rife.locate()
    except rife.RifeNotInstalled as error:
        return {"status": "missing", "reason": str(error)}
    except Exception as error:  # a broken install is reported, not raised: this is a status
        return {"status": "broken", "reason": str(error)}
    try:
        rife_install.check(binary, model)
    except SystemExit as error:
        return {"status": "broken", "path": str(binary), "reason": str(error.code)}
    return {"status": "ok", "path": str(binary), "version": rife.RELEASE}
