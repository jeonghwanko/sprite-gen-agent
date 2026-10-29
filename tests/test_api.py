# SPDX-License-Identifier: Apache-2.0
"""`sprite_gen.api` is a promise to programs that embed sprite-gen (the AI Fighter creator kit imports it): its names
and their keyword arguments do not change quietly. A change here that breaks this test is a breaking change."""
from __future__ import annotations

import inspect
import subprocess
import sys

import sprite_gen.api as api

# name -> the parameters a caller may pass, in order (`*` marks the keyword-only ones)
SIGNATURES = {
    "analyze_motion": "sequence, *, contacts, foot_box, facing",
    "load_asset": "source, *, state, fps, anchor",
    "acquire_run_dir_lock": "run_dir, owner",
    "release_run_dir_lock": "run_dir",
    "atomic_write_set": "payloads",
    "probe_access": "provider",
    "rife_status": "",
}


def _shape(fn) -> str:
    parts, starred = [], False
    for p in inspect.signature(fn).parameters.values():
        if p.kind is p.KEYWORD_ONLY and not starred:
            parts.append("*")
            starred = True
        parts.append(p.name)
    return ", ".join(parts)


def test_the_promised_names_are_exactly_these():
    assert api.__all__ == sorted([*SIGNATURES, "STANCE_REASONS"])


def test_each_promised_function_keeps_its_parameters():
    assert {name: _shape(getattr(api, name)) for name in SIGNATURES} == SIGNATURES


def test_stance_reason_codes_are_strings_with_texts():
    assert api.STANCE_REASONS and all(isinstance(k, str) and isinstance(v, str) and v for k, v in api.STANCE_REASONS.items())


def test_an_unknown_name_is_an_attribute_error():
    try:
        api.not_promised  # noqa: B018
    except AttributeError as exc:
        assert "not_promised" in str(exc)
    else:
        raise AssertionError("an unknown name must not resolve")


def test_importing_one_name_loads_only_its_module():
    code = ("import sys; from sprite_gen.api import probe_access; "
            "print(int(any(m in sys.modules for m in ('sprite_gen.qa.motion', 'sprite_gen.video.rife'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True).stdout
    assert out.strip() == "0"


def test_rife_status_reports_missing_without_raising(monkeypatch):
    from sprite_gen.video import rife

    def missing():
        raise rife.RifeNotInstalled("rife-ncnn-vulkan is not installed")
    monkeypatch.setattr(rife, "locate", missing)
    assert api.rife_status() == {"status": "missing", "reason": "rife-ncnn-vulkan is not installed"}
