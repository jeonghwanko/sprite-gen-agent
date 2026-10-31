# SPDX-License-Identifier: Apache-2.0
"""A synthetic side-view fighter whose head keeps its size while the pose changes.

`fighter(scale=..., crouch=..., reach=..., tilt=...)` draws the same character at a figure scale, legs bent by
`crouch` (a shorter figure at the same scale), an arm pushed out by `reach` (a wider one) and the head turned by
`tilt` degrees about the neck. The head carries hair, an eye, a brow and a mouth, so a template match has
structure to lock on; nothing else on the body repeats it. Shared by the `scale-check` and `game-pack` tests.
"""
from __future__ import annotations


from PIL import Image, ImageDraw

SS = 4
CANVAS = (240, 300)
GROUND = 290          # the feet's line at scale 1, in canvas pixels
OUTLINE = (24, 18, 20, 255)
SKIN = (236, 200, 170, 255)
HAIR = (60, 40, 120, 255)
SHIRT = (200, 60, 50, 255)
PANTS = (40, 60, 110, 255)


def _head(size: float) -> Image.Image:
    """The head drawn upright on its own transparent square, `size` pixels (supersampled) across."""
    s = int(size)
    head = Image.new("RGBA", (s, s))
    d = ImageDraw.Draw(head)
    w = max(2, s // 24)
    d.ellipse((s * 0.12, s * 0.14, s * 0.88, s * 0.96), fill=SKIN, outline=OUTLINE, width=w)
    d.pieslice((s * 0.08, s * 0.02, s * 0.92, s * 0.78), 180, 360, fill=HAIR, outline=OUTLINE, width=w)
    d.polygon([(s * 0.1, s * 0.42), (s * 0.3, s * 0.3), (s * 0.18, s * 0.62)], fill=HAIR, outline=OUTLINE)
    d.ellipse((s * 0.6, s * 0.46, s * 0.74, s * 0.62), fill=(255, 255, 255, 255), outline=OUTLINE, width=w)
    d.ellipse((s * 0.65, s * 0.5, s * 0.72, s * 0.6), fill=(20, 20, 30, 255))
    d.line((s * 0.56, s * 0.42, s * 0.8, s * 0.38), fill=OUTLINE, width=w * 2)
    d.line((s * 0.62, s * 0.8, s * 0.8, s * 0.76), fill=OUTLINE, width=w)
    d.ellipse((s * 0.36, s * 0.5, s * 0.5, s * 0.68), fill=(220, 170, 150, 255), outline=OUTLINE, width=w)
    return head


def fighter(scale: float = 1.0, *, crouch: float = 0.0, reach: float = 0.0, tilt: float = 0.0,
            dx: float = 0.0, canvas: tuple[int, int] = CANVAS, ground: float = GROUND) -> Image.Image:
    """The fighter standing on `ground` at canvas x centre + `dx`, every length times `scale`."""
    W, H = canvas[0] * SS, canvas[1] * SS
    image = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(image)
    u = 1.0 * scale * SS
    cx, gy = (canvas[0] / 2 + dx) * SS, ground * SS
    leg = 90 * u * (1 - crouch)
    hip_y = gy - leg
    knee = 18 * u * crouch * 3
    for side in (-1, 1):
        d.line([(cx + side * 10 * u, hip_y), (cx + side * 10 * u + knee, hip_y + leg / 2), (cx + side * 12 * u, gy - 6 * u)],
               fill=PANTS, width=int(16 * u), joint="curve")
        d.rounded_rectangle((cx + side * 12 * u - 9 * u, gy - 10 * u, cx + side * 12 * u + 15 * u, gy), 3 * u, fill=OUTLINE)
    torso_top = hip_y - 80 * u
    d.rounded_rectangle((cx - 24 * u, torso_top, cx + 24 * u, hip_y + 6 * u), 10 * u, fill=SHIRT, outline=OUTLINE, width=int(3 * u))
    arm = 60 * u + reach * 60 * u
    d.line([(cx + 14 * u, torso_top + 14 * u), (cx + 14 * u + arm * 0.8, torso_top + 30 * u + arm * 0.3 * (1 - reach))],
           fill=SHIRT, width=int(13 * u))
    d.ellipse((cx + 14 * u + arm * 0.8 - 9 * u, torso_top + 21 * u + arm * 0.3 * (1 - reach),
               cx + 14 * u + arm * 0.8 + 9 * u, torso_top + 39 * u + arm * 0.3 * (1 - reach)), fill=SKIN, outline=OUTLINE, width=int(2 * u))
    head = _head(64 * u)
    if tilt:
        head = head.rotate(tilt, resample=Image.Resampling.BICUBIC, expand=True)
    image.alpha_composite(head, (int(cx - head.width / 2), int(torso_top - head.height + 8 * u + (head.height - 64 * u) / 2)))
    return image.resize(canvas, Image.Resampling.BOX)
