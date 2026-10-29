# Pose-guided GPT locomotion (experimental)

> Owns: authored biped pose coordinates, one-pose generation, reference roles and review-bound export.

Optional [Decisions screening](decisions-review.md) reads this run before generation,
after an explicitly selected pose, or at sequence review. It writes separate advisory
reports and never fills the approval or geometry files below.

`gen-motion` provides an opt-in alternative when a GPT sprite row repeats one
support leg or changes anatomy. The usual `prepare` / `gen-set` row path remains
available. This path sends **one complete pose per image call**, with the original
identity image, a rendered target skeleton, and up to two already reviewed anchor
images. Numeric coordinates, physical leg IDs, foot contacts and overlap order
also appear in the prompt. This is image-reference conditioning through the
existing Codex provider, not ControlNet, a new model, temporal attention, or a
guarantee that GPT follows every coordinate.

## Author the plan

New work uses `kind: "sprite-gen-pose-plan"`, `version: 2`, with these fields.
Version 1 remains readable for historical runs; it has no measured body contract.

| Field | Meaning |
|---|---|
| `identity` | Immutable original appearance image, relative to the plan JSON; reference 2 in new runs. |
| `body_reference` | Actual original `sha256`, original pixel `canvas`, 4..12 named visible body `landmarks`, `reviewer`, and concrete `observation`. |
| `description` | Appearance constraints, view, arm action and whole-body balance. |
| `canvas` | Shared `[width,height]` in pixels. Choose a provider-supported size, e.g. `[1024,1024]`. |
| `ground_y` | One floor line in the common canvas, not each pose's alpha bottom. |
| `leg_labels` | Distinct descriptions for physical legs `A` and `B`, e.g. near-camera and far-camera at a fixed view. Never redefine these by screen-left/right. |
| `coordinate_source` | Must be `"authored"`. Desired coordinates are not measurements of generated pixels. |
| `motion`, `loop` | `"walk"` or `"run"`, and `true`. V1 supports bipeds only. |
| `frames` | Poses in playback order. Each has the fields below. |

Each frame additionally has `body_landmarks`: desired shared-canvas positions of
the same named points in `body_reference`. Choose clearly visible points spanning
the head AND trunk in both axes (e.g. ear root, muzzle tip, two waistband details).
Do not use hands, feet, whole-silhouette bounding boxes, or invisible hip centres
as body registration anchors. The reference measurements are observations; each
frame's positions are authored targets, locked before generation. The validator
rejects degenerate point sets, changes of body shape exceeding 8% pair-distance
residual, and greater than 4% median scale changes across the plan. It does not
infer anatomy or distinguish intentional perspective changes for you.

Each frame has `id`, `duration` in seconds, `phase`, `occlusion_front` (`A` or `B`),
`anchors` (up to two other frame IDs), `contacts`, and `joints`. A joint is `[x,y]`
in the shared canvas, with Y increasing downward. Required joints are `head`,
`neck`, `pelvis`, and `A_hip`, `A_knee`, `A_ankle`, `A_heel`, `A_toe`, plus the same
five joints for B. Head and neck are placement guides; appearance proportions,
silhouette and volume still come from the original image. Do not impose generic
human proportions on a short-legged animal character.

For example, a contact declaration is `{"A":["heel","toe"],"B":[]}`. Its declared
contact points must lie within 2 pixels of `ground_y`; B's heel and toe must both
clear the ground by more than 2 pixels. Both empty means flight. Walk plans cannot
contain flight, run plans must contain it, and both must include a single-foot
support phase for each physical leg. These checks detect contradictory **plans**;
they do not detect the generated character's feet.

Generate the main contact poses first. Intermediate poses should reference the
two accepted poses that bracket them, including one on each side of a support
change. Anchor dependencies can point forward in playback order but must form a
DAG. The last playback pose must reference the opening pose for the loop seam.
This permits key poses first, in-betweens second, rather than accumulating errors
by blindly copying the previous image. Every frame still includes the original.

Review the authored guides against the actual character before generation. Use
the requested final frame count (AI Fighter requires 16 walk / 12 run); a few
diagnostic key poses are not a finished animation. There is no automatic
coordinate inference, articulated rig fitting or pose interpolation in this version.

## Measured geometry before approval (v5)

Contract-4 continuous-gait prompts carried the exact canvas width/height and
aspect ratio, top-left pixel coordinate system, absolute joint targets and reserved
transparent outer margins. The Mocha run02 trace showed every one of those
sentences reaching the image tool while the output still missed size (1254px),
body placement and foot targets; contract 5 replaces them (below). Inspect the
actual image-tool trace, not only the prepared prompt. A completed portrait result for a square
plan is a structural canvas failure, separate from pose/anatomy failure; preserve
it and never crop or stretch it to pass normalization.

The Moka v4 trial exposed two separate faults: canvas resolution normalization
does not stabilize the body inside that canvas, and a free-text contact pass is
not a pixel measurement. Reference conditioning also cannot guarantee exact
coordinates. V5 addresses these with a calibrated plan, an independent geometry
gate, and **bounded whole-body registration from body points only**.

New version-2 plans create contract-3 runs. After generation, inspect the actual
PNG and fill `attempt-01/geometry-observations.json`: keep its hash binding, set
`status: "observed"`, give reviewer/observations, record the visible body landmarks
in actual output coordinates, and trace both physical feet in `feet.A/B` with a
fresh `outline` polygon plus observed `heel` and `toe` sole-boundary points.
Do not copy coordinates from the guide into this observation file. Hidden heel,
ambiguous physical leg, or uncertain body point means unverified, not a guessed
pass. Polygon masking measures alpha>=128 pixels; it is not semantic foot detection.
Contact points must have visible sole pixels nearby. Overlapping foot masks fail.

```powershell
sprite-gen gen-motion --plan walk-plan.json --run-dir runs/walk-v5 --measure contact-a
# Only if measurement reports registration_available:
sprite-gen gen-motion --plan walk-plan.json --run-dir runs/walk-v5 --register contact-a
```

Neither command calls a provider. Measurement fits a positive uniform scale and
XY translation from body landmarks alone, with no rotation, mirroring, stretching,
cropping, limb edits, or per-frame silhouette-height normalization. Target body
positions include authored bounce/lean; flight is not pulled onto the floor.
Feet never contribute to fitting the transform: each foot independently verifies
contact, penetration, target heel/toe position and flight clearance afterwards.

The fixed bounds are scale 0.85..1.15, translation at most 8% of the smaller canvas
dimension per axis, body residual at most 1.2% (minimum 2px), contact tolerance
0.3% (minimum 1px), and foot target residual 2% (minimum 2px). The fit must preserve
all source alpha pixels inside the canvas. These are explicit geometric tolerances,
not a claim of exact pixel control or permission to relax anatomy review.

Outcomes:

- `pass`: measured geometry is within bounds; complete fresh `geometry-review.json`.
- `registration_available`: a single bounded body transform also satisfies every
  independent foot constraint. Explicit `--register` saves `registered/output.png`,
  the transform, source/evidence hashes and derived observations, remeasures the
  rasterized alpha, and creates a fresh unverified `registered/review.json`.
- `blocked_pose_or_shape`: a body transform cannot reconcile the feet/shape, or
  would exceed bounds/clip. It writes a diagnosis, no corrected image. Revisit the
  calibrated pose approach; repeating the same request is blocked by `--retry`.

Raw output/report/review and provider source bytes are never overwritten by body
registration. All points of the character share one transform, so limb angles
and proportions cannot be repaired this way. Later anchors/export recompute the
geometry gate and validate source/evidence hashes; setting visual checks to pass
cannot bypass it. Edited observations revoke approval. Geometry passes do not
replace identity/anatomy review or the final alternation/sliding/loop review.

Legacy v1 runs retain their exact inputs and approvals. Do not invent missing
body calibration from a failed picture or move its floor to call it corrected.
The AI Fighter wrapper blocks new legacy generation but allows inspection,
reconciliation and historical export. A calibrated plan is materially new input;
use a new version directory, preserving all previous candidates.

## Continuous-gait runs: contract 5

New runs prepared from a `gait-motion` plan use contract 5. Existing contract-4
runs keep their prompts, gates and approvals unchanged.

- **Prompt.** The guide images still place the pose. The text names it in pose
  vocabulary derived from the continuous state: support (single, double, flight),
  each physical leg planted or lifted, roughly how far ahead/behind its own hip,
  lift as a share of hip height, and knee bend. Floating-point joint dumps, world
  state JSON, ground-line parameters and pixel margins are no longer sent; an image
  model follows pose words and reference images, it does not execute coordinates.
- **Geometry is diagnostic.** `geometry-observations.json` is still written and
  `--measure` still reports guide agreement (foot_pose, body scale/placement),
  but it no longer blocks visual review, anchors or a visual `--retry`. The
  authoritative per-pose record is `review.json`. Two attempts per pose remain.
  An existing `registered/` candidate keeps its v5 checks.
- **Hidden points are declared.** A foot point may be `null` only when listed in
  that foot's `hidden` array; a fully hidden foot uses `outline: []` and
  `hidden: ["heel","toe"]`. Hidden points are reported in `unknown`; the audit
  status becomes `unverified_hidden`, never `pass`. Contradictions seen on visible
  pixels (penetration, lost clearance, visible contact residual) still block.
- **Sequence contact coverage gates export.** `--export` writes
  `contact-coverage.json` from the per-frame observations. Each physical stance
  interval (merged across the loop seam) needs at least two frames (or every frame
  of a shorter interval) whose observed planned contact points lie on the projected
  ground, and no contradiction; the planted point's offset from its moving target
  must stay within twice the tolerance (no slip). Each run flight needs one frame
  with both whole feet visibly clear. Feet are judged in each frame's
  body-registered space, so a whole-character offset is not a contact failure;
  the reported `body_fit_range` shows playback drift for the sequence review.
  A heel hidden in one frame no longer blocks a motion whose interval is otherwise
  evidenced; an interval with no visible evidence stays `unverified`.
  The tolerance (`coverage_contact_fraction`, 0.6% of the canvas) is a provisional
  design value, not a physical constant. In a blind repeatability check on two
  real outputs, same-definition pair differences stayed within 3.2px at 1024
  (0.3% would have flipped a contact verdict by annotator). A frame's feet are
  dropped as unreliable only when its body fit residual exceeds
  `coverage_body_fraction` (2.4%); the 1.2% registration limit flipped by
  annotator on real outputs and is not reused here.
  Export requires both a passed `sequence-review.json` and coverage `pass`; the
  asset records `contact_coverage_sha256`.
- **Pinned landmark definitions.** Cross-convention disagreement (up to 81px)
  dwarfed click noise (about 1-3px), so observe these exactly:
  - `nostril_lower_tip`: the pointed lower-right end of the NEAR (larger,
    screen-left) nostril's dark teardrop, as on the calibrated original. Never the
    far nostril, a muzzle crease or the muzzle outline.
  - foot `toe`: where the frontmost toe pad (furthest in the facing direction)
    meets the bottom sole edge, also for a foot seen front-on. Never the notch
    between pads or the lowest pad point.
  - foot `heel`: where the rear contour turns into the bottom sole edge. If the
    foot is seen front-on and only toe pads form its bottom edge, the heel is
    hidden: `null` and listed in `hidden`.

## Generate and review

For continuous gait inputs, first run `gait-motion` as documented in
`gait-guide.md`. Contract4 runs preserve the calibrated v5 gate while adding
hash-bound visible/depth and amodal/Xray references after any reviewed anchors.
They use explicitly projected per-foot world-ground boundaries rather than
forcing both feet to one imageY. The visible sole boundary is derived from the
declared proxy foot edge; original centerline world joints remain in provenance.
Pixel residuals are conditional design-model checks, not recovered world depth.
No hidden artwork point may be invented, and registration still fits body points
only, keeps raw pixels immutable and rechecks independent foot constraints.

Use the repository's Python environment. These commands do not change provider,
login, account, quality or billing settings. `--model` passes through the chosen
Codex model; repeat the same value when resuming that run.

```powershell
.\.venv\Scripts\python.exe -m sprite_gen.cli gen-motion --plan .\walk-plan.json --run-dir .\runs\walk-pose-v1 --prepare-only
.\.venv\Scripts\python.exe -m sprite_gen.cli gen-motion --plan .\walk-plan.json --run-dir .\runs\walk-pose-v1 --frame contact-a --model <chosen-model>
```

`--prepare-only` validates the plan and writes one guide/prompt per pose without
making any generation call. Generation sends, in order:

1. This frame's authored target pose PNG (A orange, B blue, green contact boxes).
2. Immutable original appearance, explicitly excluding its neutral stance/framing.
3. Reviewed anchors, in the plan's declared order, explicitly labeled as continuity
   references rather than target poses.

New runs require `input-review.json` before the first generation. Inspect the
original and actual rendered guides for proportions/placement, physical A/B
mapping, readable overlapping legs and valid contact targets. Record concrete
observations; a syntactically valid coordinate plan is not approval. This review
is performed within the agreed group, without another per-frame user question.
An original at a different aspect ratio is an appearance reference, not a shared
coordinate measurement. Check authored proportions against it before generation.

The guide labels separate head/neck/pelvis targets, physical hips, contact states
and clearance. The prompt prioritizes the target phase and describes each sole
relative to its own hip using normalized coordinates. Target-first ordering and
clearer instructions are experimental input improvements, not hard pose control
or evidence that locomotion will succeed. Existing runs preserve their original
prompt/image order; materially changed inputs require a new run.

One successful call returns an `output.png` and a `review.json` initialized to
`unverified`. Inspect the actual pixels alongside the guide, original and anchors;
record reviewer and concrete observations for identity, physical leg identity,
hip attachment, pose match, contacts and body continuity. Never mark a pass from
the plan, the prompt, a decoder result or an attractive isolated silhouette.
For v2 plans, geometry measurement must precede the authoritative visual review
(`geometry-review.json` or `registered/review.json`); the initial raw `review.json`
is historical and cannot grant anchor/export approval. An agent can perform these checks within the user's agreed review group; this
does not require asking the user to approve every frame. Uncertain anatomy stays
unverified. Only a fully passed anchor can be sent to dependent frames.

A rejected legacy pose, or a raw geometry-passing v2 pose with an independent
visual rejection, can use `--retry`; only failed visual observations become concise
correction feedback (at most 600 characters each). Pass/unverified checks and
hashes stay out of the image prompt. `retry-feedback.json` binds that feedback to
the rejected pixels and review. Structural rejection never authorizes a fresh
generation: inspect/reconcile the existing output first.
The old candidate is preserved. There are at most two attempts for a pose in a
run. Stop repeated anatomy/contact failures; do not evade this bound by creating
new run directories for the same failed approach. A new run is for materially
revised plans or references, not unbounded retries.

An interrupted call, nonzero provider exit or missing output/report is **unknown**,
not a confirmed failure. Inspect provider records and existing files. `--reconcile`
can recognize a complete output and provider report without another generation
call. It cannot make an incomplete result successful. No automatic retry occurs.
The motion runner also disables the lower-level `gen` timeout retry. It retains
the Codex session for recovery; the report captures observed image-tool call
source in `extra.image_tool_trace`. Requested `refs` alone are not proof of the
final tool arguments. Missing trace stays unavailable; never infer successful
reference delivery from a generated picture. Do not execute recorded call source.

This route requests native transparency explicitly, avoiding the general `gen`
auto mode's magenta-key prompt when references are attached. Failure to produce
alpha remains a structural rejection, not permission to change providers.

If the provider returns a different resolution at the **same aspect ratio**, opt
into `--normalize-canvas`. This uniformly rescales the entire canvas to the plan's
resolution, with no crop, silhouette fit, translation or floor alignment. It keeps
the original PNG as `source-output.png` and records its hash, dimensions and exact
scale in `attempt.json.canvas_transform`. The provider report describes that original;
pose reviews bind the final `output.png`. Different aspect ratios remain rejected.
Old reviews with different pixel bindings are archived and reset to unverified;
normalization cannot inherit an old approval.
An existing resolution-only rejection can be recovered with `--reconcile
--normalize-canvas`, without calling the model again. Normalized coordinates,
including flight height, are retained; resampling does not establish pose compliance.

Generation here costs one call per pose, plus explicitly requested retries; a
16-frame cycle usually requires more calls than a one-row generation. Do not
silently opt the user into a different provider or an external paid pose service.

## Inspect the cycle and export

```powershell
.\.venv\Scripts\python.exe -m sprite_gen.cli gen-motion --plan .\walk-plan.json --run-dir .\runs\walk-pose-v1 --export
```

After every pose passes, this writes `sequence-preview.webp` and a hash-bound
`sequence-review.json`. Inspect the actual animation for physical leg alternation,
stance sliding, whole-body continuity and the last-to-first seam. Record actual
observations; missing or failed checks prevent a reviewed asset export. Repeat
`--export` after the review passes to write a standard `sprite-gen-asset` descriptor
in `asset.json`, consumable by `spec.assets` tools such as `inspect-motion` and scenes.
Use same-physical-foot ROIs for measured contact checks where the downstream
workflow requires them. A human/agent review record does not replace those measurements.

Export keeps the selected reviewed PNGs (raw or registered), frame order,
durations and a common floor anchor. Body registration is an explicit earlier
stage with a separate audit trail, never an implicit export-time adjustment.
It never trims, fits individual silhouettes, bottom-aligns, mirrors, splices a frozen
torso, or duplicates poses. Mismatched canvases and exact duplicate output pixels
are rejected. This is a standalone asset output, not an automatically populated
`prepare` run or an AI Fighter package. Downstream conversion must retain these
coordinates and perform its own required package/gait validation.

The run binds plan, original, rendered guides/prompts, input reference hashes,
generation reports, candidate pixels and reviews. Changed inputs require a new
run; edited pixels or a revoked anchor review block further dependent generation
and export. Existing exports remain historical snapshots, not live approval badges.
Offline tests exercise these contracts. Natural locomotion and character fidelity
still need a real, observed generation trial before claiming this method solves a
particular character's gait.
