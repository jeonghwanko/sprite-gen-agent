# SPDX-License-Identifier: Apache-2.0
"""`key_transparent(method="dominance")` and `gen --chroma-method` (docs/chroma-alpha.md "dominance").

Grok paints its "pure green #00FF00" field as a desaturated green, about (30, 188, 35) lit and (31, 82, 37) dark.
Every neutral sits 83 from the lit one on the CbCr plane, inside the YCbCr border flood's 88, and 26 from the dark
one, at the foot of the soft matte's ramp: the YCbCr matte erases a character's white and black on both. The
dominance matte keys only pixels where the key's channels dominate, and neutrals have no dominance. The default
stays the YCbCr matte, called exactly as before.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen import gen
from sprite_gen.gen import base as gen_base
from sprite_gen.gen import chroma as chroma_mod
from sprite_gen.gen.base import GenRequest

GROK_GREEN = (30, 188, 35)
GROK_DARK_GREEN = (31, 82, 37)
WHITE_PX, BLACK_PX = (20, 30), (40, 30)


def _figure(field: tuple[int, int, int], size: int = 64) -> Image.Image:
    """A 32 px square on ``field``: its left half pure white, its right half pure black, both touching the field."""
    image = Image.new("RGB", (size, size), field)
    for y in range(16, 48):
        for x in range(16, 48):
            image.putpixel((x, y), (255, 255, 255) if x < 32 else (0, 0, 0))
    return image


def _key(tmp_path: Path, image: Image.Image, **kwargs) -> tuple[dict, Image.Image]:
    src = tmp_path / "src.png"
    image.save(src)
    out = tmp_path / f"out-{kwargs.get('method', 'default')}.png"
    stats = chroma_mod.key_transparent(src, out, **kwargs)
    return stats, Image.open(out).convert("RGBA")


@pytest.mark.parametrize("field", [GROK_GREEN, GROK_DARK_GREEN], ids=["lit", "dark"])
def test_dominance_keeps_white_and_black_on_grok_green(tmp_path: Path, field) -> None:
    stats, keyed = _key(tmp_path, _figure(field), key="green", method="dominance")
    assert stats["method"] == "dominance"
    assert keyed.getpixel(WHITE_PX) == (255, 255, 255, 255)
    assert keyed.getpixel(BLACK_PX) == (0, 0, 0, 255)
    assert keyed.getpixel((16, 30)) == (255, 255, 255, 255)  # white at the edge: no dominance, full alpha
    assert keyed.getpixel((47, 30)) == (0, 0, 0, 255)
    assert keyed.getpixel((2, 2)) == (0, 0, 0, 0)
    assert stats["alpha_zero_pct"] == 75.0  # the field, and only the field
    assert stats["stale_transparent_rgb_pixels"] == 0


@pytest.mark.parametrize("field", [GROK_GREEN, GROK_DARK_GREEN], ids=["lit", "dark"])
def test_ycbcr_erases_the_same_white_and_black(tmp_path: Path, field) -> None:
    # Why the dominance matte exists: the default matte takes the whole figure with the field.
    stats, keyed = _key(tmp_path, _figure(field), key="green")
    assert stats["method"] == "ycbcr"
    assert keyed.getpixel(WHITE_PX)[3] == 0
    assert keyed.getpixel(BLACK_PX)[3] == 0


def test_an_enclosed_gap_is_keyed(tmp_path: Path) -> None:
    # A white ring around a hole of field, the gap between an arm and the body: no border reaches it.
    image = Image.new("RGB", (64, 64), GROK_GREEN)
    for y in range(12, 52):
        for x in range(12, 52):
            if not (24 <= x < 40 and 24 <= y < 40):
                image.putpixel((x, y), (255, 255, 255))
    _, keyed = _key(tmp_path, image, key="green", method="dominance")
    assert keyed.getpixel((32, 32)) == (0, 0, 0, 0)
    assert keyed.getpixel((16, 16)) == (255, 255, 255, 255)


def test_edge_pixels_lose_the_key_spill_and_interior_ones_keep_their_colour(tmp_path: Path) -> None:
    # A grey figure whose outer column carries a green spill (dom 20, rel 0.15: not background). The same colour
    # deep inside the figure is the subject's own and is not touched.
    tinted = (110, 130, 110)
    image = _figure(GROK_GREEN)
    for y in range(16, 48):
        image.putpixel((16, y), tinted)
    image.putpixel((24, 30), tinted)
    _, keyed = _key(tmp_path, image, key="green", method="dominance")
    r, g, b, a = keyed.getpixel((16, 30))
    assert (r, g, b) == (110, 114, 110)  # g capped at max(r, b) + 4
    assert 130 <= a <= 140  # 1 - (20 - 6) / 30 of opaque
    assert keyed.getpixel((24, 30)) == (*tinted, 255)


def test_the_magenta_family_keys_the_same_way(tmp_path: Path) -> None:
    field = (200, 40, 190)  # a desaturated magenta
    image = _figure(field)
    tinted = (150, 120, 150)  # dom 30, rel 0.2: an edge pixel, not background
    for y in range(16, 48):
        image.putpixel((16, y), tinted)
    stats, keyed = _key(tmp_path, image, key="magenta", method="dominance")
    assert stats["method"] == "dominance"
    assert keyed.getpixel((2, 2)) == (0, 0, 0, 0)
    assert keyed.getpixel(WHITE_PX) == (255, 255, 255, 255)
    assert keyed.getpixel(BLACK_PX) == (0, 0, 0, 255)
    r, g, b, a = keyed.getpixel((16, 30))
    assert (r, g, b) == (124, 120, 124)  # r and b capped at g + 4
    assert 45 <= a <= 55  # 1 - (30 - 6) / 30 of opaque


def test_a_pixel_the_source_had_transparent_stays_transparent(tmp_path: Path) -> None:
    image = _figure(GROK_GREEN).convert("RGBA")
    image.putpixel(WHITE_PX, (255, 255, 255, 0))
    image.putpixel(BLACK_PX, (0, 0, 0, 128))
    _, keyed = _key(tmp_path, image, key="green", method="dominance")
    assert keyed.getpixel(WHITE_PX) == (0, 0, 0, 0)
    assert keyed.getpixel(BLACK_PX) == (0, 0, 0, 128)


def test_the_dominance_matte_returns_an_image_the_caller_can_write(tmp_path: Path) -> None:
    # key_transparent scrubs RGB under alpha 0 by writing into the matte; an image over a numpy array is read-only.
    matte = chroma_mod._dominance_matte(_figure(GROK_GREEN).convert("RGBA"), chroma_mod.KEYS["green"]["target"])
    pixels = matte.load()
    pixels[0, 0] = (1, 2, 3, 4)
    assert matte.getpixel((0, 0)) == (1, 2, 3, 4)


def test_dominance_refuses_a_picture_with_nothing_to_key(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match=r"0\.0% transparent pixels"):
        _key(tmp_path, Image.new("RGB", (16, 16), (255, 255, 255)), key="green", method="dominance")


@pytest.mark.parametrize("decontam", ["auto", "palette"])
def test_dominance_with_decontam_is_refused(tmp_path: Path, decontam: str) -> None:
    with pytest.raises(SystemExit, match="--chroma-method dominance has no such pass"):
        _key(tmp_path, _figure(GROK_GREEN), key="green", method="dominance", decontam=decontam)
    assert not (tmp_path / "out-dominance.png").exists()


def test_an_unknown_method_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="unknown method 'cbcr'"):
        _key(tmp_path, _figure(GROK_GREEN), key="green", method="cbcr")


def test_the_default_calls_the_ycbcr_matte_exactly_as_before(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple] = []
    real = chroma_mod.remove_chroma_background_ycbcr

    def spy(*args, **kwargs):
        calls.append((args[1:], kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(chroma_mod, "remove_chroma_background_ycbcr", spy)
    image = _figure((255, 0, 255))
    stats, default = _key(tmp_path, image, key="magenta")
    assert calls == [(((255, 0, 255), []), {})]  # the pre-decontam signature: a caller's stand-in still fits
    assert stats["method"] == "ycbcr"
    _, explicit = _key(tmp_path, image, key="magenta", method="ycbcr")
    assert explicit.tobytes() == default.tobytes()

    calls.clear()
    _key(tmp_path, image, key="magenta", method="dominance")
    assert calls == []  # the dominance path never reaches the YCbCr matte


# --- gen wiring ---------------------------------------------------------------------------------------------------


class _GrokLikeProvider:
    """A chroma provider that draws the figure on Grok's desaturated green."""

    name = "fake-grok"
    transparency = gen_base.TRANSPARENCY_CHROMA

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: GenRequest, workdir: Path):
        self.calls += 1
        _figure(GROK_GREEN).save(request.raw)
        return gen_base.ProviderRun(provider=self.name, elapsed_seconds=1.0, model=request.model)


def _gen_kwargs(out: Path, report: Path, **overrides):
    kwargs = dict(provider="fake-grok", prompt="a fighter", out=out, ref=[], model=None, aspect_ratio=None,
                  transparent=True, alpha_mode="auto", chroma_key="green", white_check=None, keep_session=False,
                  report=report, prompt_file=None, workdir=None)
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize(("flag", "method"), [({}, "ycbcr"), ({"chroma_method": "dominance"}, "dominance")])
def test_gen_threads_chroma_method_to_the_key_and_reports_it(tmp_path: Path, monkeypatch, flag, method) -> None:
    fake = _GrokLikeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    out, report = tmp_path / "fighter.png", tmp_path / "report.json"
    assert gen.run(**_gen_kwargs(out, report, **flag)) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["alpha"]["strategy"] == "chroma"
    assert payload["alpha"]["method"] == method
    assert payload["chroma"]["method"] == method
    white = Image.open(out).convert("RGBA").getpixel(WHITE_PX)
    assert white[3] == (255 if method == "dominance" else 0)


def test_gen_refuses_dominance_with_decontam_before_the_provider_is_called(tmp_path: Path, monkeypatch) -> None:
    fake = _GrokLikeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda name, *, keep_session: fake)
    with pytest.raises(SystemExit, match="--chroma-method dominance has no such pass"):
        gen.generate_image("fake-grok", "a fighter", tmp_path / "fighter.png", transparent=True, chroma_key="green",
                           chroma_method="dominance", decontam="auto")
    with pytest.raises(SystemExit, match="unknown --chroma-method 'cbcr'"):
        gen.generate_image("fake-grok", "a fighter", tmp_path / "fighter.png", transparent=True,
                           chroma_method="cbcr")
    assert fake.calls == 0


def test_the_cli_flag_defaults_to_ycbcr() -> None:
    parser = gen._build_parser()
    assert parser.parse_args(["--out", "x.png"]).chroma_method == "ycbcr"
    assert parser.parse_args(["--out", "x.png", "--chroma-method", "dominance"]).chroma_method == "dominance"
    with pytest.raises(SystemExit):
        parser.parse_args(["--out", "x.png", "--chroma-method", "rgb"])
    assert chroma_mod.CHROMA_METHODS == ("ycbcr", "dominance")
