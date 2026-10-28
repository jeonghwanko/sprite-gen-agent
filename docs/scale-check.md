# Scale check — one figure size in every frame

> Owns: `sprite-gen scale-check` — measuring each frame's figure size against a reference frame by its head, the report, and the eye check for a frame it cannot measure. Applying the corrections: [game-pack.md "Scale check"](game-pack.md#scale-check) · Index: [docs/README.md](README.md)

An image model draws every pose of a row as a separate figure. A row fitted at one scale still has poses a few to twenty per cent bigger or smaller than its stance, and two rows fitted apart differ more: a walk at 0.83 of the stance, a run at 1.2. In a game that draws every clip at one body height the character then swells and shrinks as it moves. A bounding box cannot measure this — a crouch is shorter and a kick wider at the same scale — so the ruler is the head, the one part whose size does not change with the pose.

## Command

```bash
sprite-gen scale-check --reference idle.strip.json --asset run/manifest.json \
  --wide-state fall --out scale-check.json --board scale-check.png
```

| Option | Meaning |
|---|---|
| `--reference`, `--reference-state`, `--reference-frame` | The frame every other frame is measured against (zero-based; default frame 0). Usually the neutral stance or idle. |
| `--asset` | An asset to measure (repeatable): a strip, a run manifest or any source `game-pack` reads. Every state is measured unless `--state` names some; an asset with none of the named states is refused. |
| `--wide-state` | A state whose head turns past 45° (a fall, a flip, lying down): its search covers ±`--wide-angle` (default 90°). |
| `--min-score` | The head match a size is taken from (default 0.8). Below it the frame awaits an eye check. |
| `--deadband` | A size within this of 1 is left alone (default 0.02): no resample for a slip nobody sees. |
| `--range` | `LOW,HIGH` sizes searched (default `0.7,1.4`). A best size on the edge of the range is reported unsure (`edge`), not taken: the figure may lie beyond it. |
| `--head-box` | `X,Y,W,H` of the head in the reference frame, when the top of its ink is not its head (a raised weapon, a speck above it). |
| `--out`, `--board` | The report JSON; a review PNG with the reference head beside every matched head at its own size (a yellow line marks the reference head's height). |

The exit status is 0 when every frame is measured and 1 when a frame awaits an eye check; the report is written either way.

## How a frame is measured

The template is the reference frame's head: the top 15 % of its opaque rows (alpha ≥ 64), as wide as the ink in that band, or `--head-box`. A head box under 24 px either way is refused: one of its pixels is then more than a size step, and the sizes it reports are noise with a good score. Each frame is composited on grey and searched for it by normalized cross-correlation (OpenCV's `TM_CCOEFF_NORMED`, computed with NumPy FFTs) over scale × rotation: coarse on a copy reduced so the head is about 40 px tall (a smaller head is searched at its own size), then fine at full size (±0.05 in steps of 0.01, ±7.5° in steps of 2.5°) around the coarse hit. The size is the scale the template was drawn at in whole pixels.

A face changes with the expression: a shout or shut eyes match the stance badly. After the first pass, up to eight heads matched at 0.88 (or `--min-score`, when higher) or better become further templates, and every frame still below `--min-score` is searched with them too — never with its own head; the best match wins and the report names its template.

There is no feature matcher behind the head match to give a second opinion (the engine's runtime is NumPy and Pillow only, [interpreter.md](interpreter.md)). The other frames of the same state take its place: a match below `--min-score` but at least 0.65 is a size when at least three distinct drawings of its state, each matched on its own, agree on the size within 3.5 % (`how: "agree"`). Frames drawn apart do not lock onto a wrong place at one shared size by chance; a held frame repeated in a clip counts once. Anything else is not guessed: it is reported for a person to look at. On a real GPT row the right head often scores 0.70–0.77, because the head is redrawn each time, and wrong locks have been seen at the same scores; that is why a weak match alone is never enough.

## Report

```json
{
  "kind": "sprite-gen-scale-check", "version": 1, "method": "head-ncc-v1",
  "reference": { "identity": { "source": ".../idle.strip.json", "state": null, "...": "..." },
                 "frame": 0, "sha256": "…", "head_box": [91, 63, 149, 98], "head_box_from": "measured" },
  "settings": { "min_score": 0.8, "deadband": 0.02, "range": [0.7, 1.4], "angle": 45, "wide_angle": 90, "wide_states": [],
                "agree": { "score": 0.65, "within": 0.035, "frames": 3 } },
  "assets": [ { "identity": { "source": ".../manifest.json", "state": "walk", "...": "..." },
                "frames": [ { "frame": "walk/0", "sha256": "…", "score": 0.97, "size": 1.12, "angle": 0.0,
                              "template": "reference", "edge": false, "how": "head", "correction": 0.8929 } ],
                "state_correction": 0.91 } ],
  "unresolved": [], "ok": true
}
```

* `frame` names a frame `state/index`; a source without states is `-`.
* `sha256` is the frame's decoded RGBA pixels: `game-pack` applies a correction only to the pixels it was measured on. A record is found by its state and these pixels, not by the source path, so a request and its report still apply after the folder moves.
* `how`: `head` (measured), `agree` (a weaker match its state confirms), `reference` (the reference frame's own pixels), `empty` (no ink: nothing to scale) or `check` (unsure; `correction` is `null`). `edge` marks a size on the edge of `--range`, always `check`.
* `correction` is `1 / size`, or 1 inside the deadband. `state_correction` is the median of a state's measured corrections, `null` while one of its frames awaits a check.

## The eye check

Open the board. A frame marked `check` is one whose head the search could not settle — the head hidden, turned away, or a pose the template does not reach. Look at the frame beside the reference and decide its correction by eye, then give it to `game-pack` as `scale_check.fix["walk/3"]` ([game-pack.md "Scale check"](game-pack.md#scale-check)). A fix also overrides a measured frame when the board shows a wrong lock.

<!-- Added for sprite-gen-agent (see NOTICE). -->
