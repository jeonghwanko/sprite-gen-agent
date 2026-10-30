# SPDX-License-Identifier: Apache-2.0
"""The keying engines share `frames/keyfield.py` and `frames/morph.py` instead of reaching into each other.

Before the split, `decontam`, `logo_matte`, `video.frames`, `video.restoration` and the curation server
imported fourteen underscore names out of `extract`, `decontam` and `cutout`, and `decontam` imported
`extract` while `extract` had to import `decontam` lazily to dodge the cycle. A private name read from
another module is a shared one that has no home yet: it goes to `keyfield` / `morph`, or is made public
where it lives.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINES = ("sprite_gen.frames.extract", "sprite_gen.frames.decontam", "sprite_gen.frames.cutout")


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, [alias.name for alias in node.names]


def test_no_module_imports_a_private_name_from_a_keying_engine():
    reached = []
    for path in sorted((ROOT / "sprite_gen").rglob("*.py")):
        own = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        for module, names in _imports(path):
            if module in ENGINES and module != own:
                reached += [f"{path.relative_to(ROOT).as_posix()}: {module}.{n}"
                            for n in names if n.startswith("_") and not n.startswith("__")]
    assert reached == []


def test_decontam_and_the_shared_modules_do_not_import_extract():
    for name in ("decontam", "keyfield", "morph"):
        modules = {module for module, _ in _imports(ROOT / "sprite_gen" / "frames" / f"{name}.py")}
        assert "sprite_gen.frames.extract" not in modules, name
