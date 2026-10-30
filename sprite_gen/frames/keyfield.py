# SPDX-License-Identifier: Apache-2.0
"""The chroma key's own measures, shared by the engines that read a keyed image.

Which channels a key saturates and which it leaves dark (`key_channel_split`), and the whole-image
fields of a colour's distance from the key, its tint toward it and its key hue (`key_distance_field`,
`key_tint_field`, `key_excess_field`, `cap_key_hue`), with the two depths/bars the chroma extraction and
the passes after it agree on. `frames.extract` keys stills and rows on them, `frames.decontam` gives an edge
its colour back, `frames.logo_matte` mattes lettering, and `video.frames` / `video.restoration` key clips;
none of them reaches into another for these.
"""

from __future__ import annotations

from sprite_gen._deps import np

KEY_CHANNEL_LIT = 192  # a channel the key saturates
KEY_CHANNEL_DARK = 64  # a channel the key leaves dark — the one bar `is_key_family` / `is_border_key_candidate` reuse
# How deep into the keyed region's rings an in-band key blend is still unmixed (`remove_chroma_background`);
# `decontam` reads the same depth so it treats the pixels the unmix left.
IN_BAND_UNMIX_KEY_DEPTH = 2
# Full correction also admits faint key tints. Auto mode must inspect the
# reference at this same threshold before choosing full correction.
SPILL_FULL_MIN_TINT = 8.0


def key_channel_split(chroma_key: tuple[int, int, int]) -> tuple[list[int], list[int]]:
    """Which channels the key saturates, and which it leaves dark.

    Depends only on the key, so the whole-image path resolves it once instead of
    per pixel. Degenerate keys — no saturated channel, or no dark one — have no
    tint axis at all and come back as two empty lists; that emptiness is the one
    switch both callers read, and it turns off the unmix and spill passes.
    """
    keyed_channels = [index for index, value in enumerate(chroma_key) if value >= KEY_CHANNEL_LIT]
    unkeyed_channels = [index for index, value in enumerate(chroma_key) if value < KEY_CHANNEL_DARK]
    if not keyed_channels or not unkeyed_channels:
        return [], []
    return keyed_channels, unkeyed_channels


# --- whole-image forms of the two primitives above ---------------------------
# remove_chroma_background asks `color_distance` and `key_tint_score` the same
# question of every pixel, which at 1.5 Mpx a row is where the extraction time
# went. These compute the identical float64 values one array at a time.
#
# "Identical" is the contract, not an aspiration: both quantities start as exact
# integer arithmetic on uint8 channels, and the one float operation in each
# (`math.sqrt`, `int / int`) is IEEE-754 correctly rounded, so the array form
# reproduces the scalar form bit for bit. tests/test_chroma_rcb_byte_identity.py
# holds the scalar side against a frozen pre-vectorization copy.


def key_distance_field(rgb: np.ndarray, chroma_key: tuple[int, int, int]) -> np.ndarray:
    """`color_distance` for an (H, W, 3) integer array of colors.

    The squared differences accumulate in int64 on purpose. 255**2 * 3 = 195_075
    overflows uint16 as well as uint8, and a wrapped square does not raise — it
    returns a plausible distance for the wrong pixel.
    """
    diff = rgb - np.asarray(chroma_key, dtype=np.int32)
    return np.sqrt((diff * diff).sum(axis=-1, dtype=np.int64).astype(np.float64))


def key_tint_field(rgb: np.ndarray, keyed_channels: list[int],
                    unkeyed_channels: list[int]) -> np.ndarray:
    """`key_tint_score` for an (H, W, 3) integer array of colors.

    Takes the channel split rather than the key, so the degenerate-key decision
    stays where `key_channel_split` made it — one answer, read the same way here
    as in the scalar primitive.
    """
    if not keyed_channels:
        return np.zeros(rgb.shape[:2], dtype=np.float64)
    keyed_sum = rgb[..., keyed_channels].sum(axis=-1, dtype=np.int64)
    unkeyed_sum = rgb[..., unkeyed_channels].sum(axis=-1, dtype=np.int64)
    return keyed_sum / len(keyed_channels) - unkeyed_sum / len(unkeyed_channels)


def key_excess_field(rgb: np.ndarray, keyed_channels: list[int],
                      unkeyed_channels: list[int]) -> np.ndarray:
    """Key hue requires every keyed channel to exceed every other channel.

    Green uses G-max(R,B); magenta uses min(R,B)-G. Unlike the averaged
    tint axis this does not call yellow/cyan green or red/blue magenta.
    """
    return rgb[..., keyed_channels].min(axis=-1) - rgb[..., unkeyed_channels].max(axis=-1)


def cap_key_hue(rgb: np.ndarray, keyed_channels: list[int], unkeyed_channels: list[int], tint: float) -> np.ndarray:
    """Take the key hue out of the colours that carry it and leave every other colour alone.

    A colour carries the key hue by the engine's own test (`key_excess_field`: G - max(R, B)
    for green, min(R, B) - G for magenta). Where that excess passes `tint`, the part beyond it
    comes off every keyed channel alike: for green that is G <= max(R, B) + tint, for magenta
    R and B come down together and whichever led keeps its lead. A red, a blue or a skin tone
    under a magenta key has no key hue and is returned as it is. Never raises a channel.
    """
    out = np.array(rgb, dtype=np.float64, copy=True)
    over = np.maximum(key_excess_field(out, keyed_channels, unkeyed_channels) - tint, 0.0)
    for channel in keyed_channels:
        out[..., channel] -= over
    return out
