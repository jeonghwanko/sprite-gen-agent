# SPDX-License-Identifier: Apache-2.0
"""Which pixel edits and transform an instance is baked with: one lookup, `curation.frame_edits`.

A linked clone plays its source frame's edits (`edit_index`); an unlinked clone, or a legacy one that holds
edits of its own, keeps its own. Six bakes made this lookup each in their own words, and `compose-cycle` got
it wrong (it baked a linked clone unedited). Every bake now asks `frame_edits`, and none calls `edit_index`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sprite_gen.curate.curation import frame_edits

ROOT = Path(__file__).resolve().parents[2]
FLIP = {"flipX": 1}
SOURCE_OPS = {"1,1": [255, 0, 0, 255]}


def _curation(**idle):
    return {"states": {"idle": {"selected": [0, 7], "clones": {"7": 0}, **idle}}}


def test_a_linked_clone_takes_its_source_frames_edits():
    curation = _curation(pixels={"0": SOURCE_OPS})
    assert frame_edits(curation, "idle", 7, {0: FLIP}) == (SOURCE_OPS, FLIP)
    assert frame_edits(curation, "idle", 0, {0: FLIP}) == (SOURCE_OPS, FLIP)


def test_an_unlinked_clone_or_one_with_edits_of_its_own_keeps_its_own():
    unlinked = _curation(unlinked=[7], pixels={"0": SOURCE_OPS})
    assert frame_edits(unlinked, "idle", 7, {0: FLIP}) == (None, None)
    own = _curation(transforms={"7": {"dx": 3}})
    assert frame_edits(own, "idle", 7, {0: FLIP, 7: {"dx": 3}}) == (None, {"dx": 3})


def test_no_bake_looks_the_edits_up_by_itself():
    callers = []
    for path in sorted((ROOT / "sprite_gen").rglob("*.py")):
        if path.name == "curation.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "edit_index":
                callers.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert callers == []
