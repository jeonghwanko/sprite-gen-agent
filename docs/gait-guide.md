# Deterministic gait guides (phase + IK + world foot locking)

> Owns: `gait-guide`: authored gait phase, IK and foot locking as a deterministic proxy preview without AI — not generated character approval · Index: [docs/README.md](README.md)

`gait-guide` renders authored kinematic proxies without generation, credentials,
new models, or editing reference artwork. It is a motion-design utility. Its
outputs are not sprite-gen assets, approved character art or measured evidence
about an AI image. Do not use its ground truth to fill a generated-image review.

```powershell
sprite-gen gait-guide --profile examples/gait-mocha.json --out-dir runs/mocha-gait-01
```

In the AI Fighter kit use `npm run sprite -- gait-guide --profile
vendor/sprite-gen/examples/gait-mocha.json --out-dir output/mocha-gait-01`.
The output directory must not already exist. `--motion walk|run|both` selects the
motions. There is no provider option or hidden generation step.

## One continuous model

World axes are X forward, Y lateral and Z up. A is the negative-Y physical leg,
B positive-Y. IDs never change when screen positions cross. The B phase is
always half a cycle from A. Walking has duty > 0.5 and double support; running
has duty < 0.5 and flight. The example samples 16 walk and 12 run frames at the
centre of exposure intervals, without duplicating a closing frame.

During stance, the sole's world position is constant. The body advances by
`speed * time`. During swing, a quintic trajectory joins successive plants with
zero endpoint velocity/acceleration, and a smooth positive lift returns to zero.
The hip-to-ankle target is solved with two fixed-length bones and a forward knee
pole. Impossible targets fail; they are never clamped into plausible-looking
coordinates. This implementation uses fixed lateral lanes and flat soles.
Heel roll, toe push-off, body rotation and dynamic balance are not simulated.

The camera follows root translation. A planted foot therefore moves backwards
on screen while staying fixed against the world ground markers. Locking screen X
would cause sliding in the world. Body bob is authored, not fitted to foot pixels.

## Proportions and occlusion

The Mocha example approximates a large capybara head/muzzle, broad belly, boxing
gloves and short visible legs. Units and invisible joint depths are authored
assumptions documented in `provenance`, not a 3D reconstruction from the original.
All body radii and bone lengths stay fixed across frames. Colored legs are
diagnostic identifiers, not an appearance revision to the approved character.

The renderer intersects orthographic camera rays with ellipsoids and flat-soled
foot boxes. Each pixel selects the nearest surface by depth. Drawing one leg last
or sorting by screen X cannot control visibility. The middle X-ray panel shows
both physical skeletons, including occluded joints; the right part-ID panel shows
only actually visible surfaces. A body's occlusion of a foot is valid guide data,
not proof that a corresponding generated image follows the hidden pose.

## Files and evidence

## Explicit GPT input preparation

`gait-motion --guide-dir <approved-guide> --identity <original.png>
--body-reference <measured-original.json> --settings <projection.json>
--out-dir <new-input-directory>` makes NO image-provider call. It verifies the
actual poses, part-ID, amodal and depth files against the continuous model and
preserves the approved source. It produces calibrated version2 plans for the
existing `gen-motion` measured route, never an automatic art approval.

Settings explicitly fix `canvas`, `guide_scale`, `guide_translation`,
`body_scale`, `body_translation`, `elevation_degrees`, `sample_policy` and
`description`. A changed camera is an authored assumption, not recovered hidden
dimensions. Nonzero elevation projects world Z=0 to sloped, per-foot image
boundaries. Near/far screenY differences alone are not penetration. Geometry
reports are conditional on that declared camera/foot-lane model.

`sample_policy: preserve` rejects samples that fail the actual geometry contact
tolerance and native rasterized-foot contract (3.072px at1024, without relaxing it).
`clearance-safe` explicitly makes the smallest ordered, contact-preserving
continuous-time adjustment within the original sample neighborhood, paired a
half-cycle apart. Changes and exact hold durations are saved in
`resampling.json`; no foot is relabeled, moved to a floor or clamped. The derived
proxy part/depth/amodal and isolated foot masks are ray-rendered directly in the
FINAL canvas at the declared common origin/scale. They are not enlarged320x420
masks. All16/12 frames and the joint bundle contract must pass before ANY GPT
generation, including independent roots. Unsolved samples block the entire bundle;
an analytically valid >2px plan alone is insufficient. Input review is separate
from this numerical/raster design check, and neither approves generated artwork.

New input runs keep target1, original2, reviewed anchors3..4 (when present), then
two hash-bound extra images: visible-parts/depth and amodal/Xray. Visible masks
come only from nearest-depth surfaces. Amodal/Xray includes occluded design
geometry; it can NEVER fill missing observations of generated artwork. The
original alone supplies appearance; proxy colors/shapes must not be copied.
Input review and every v5 geometry/retry/registration gate remain required.

- `walk.webp` / `run.webp`: exact millisecond playback, plus `*-slow.webp`.
- GIF equivalents: durations quantized to GIF's 10 ms resolution; prefer WebP
  for timing measurements. The JSON pose durations are authoritative.
- `preview.html`: offline self-contained pause, scrub and speed controls.
- Contact sheets and frame PNGs: same scale/camera, normal and X-ray views.
- `poses.json`: world joints, phases, contacts, plant IDs, durations and root.
- `part-id-*.png`: 0 background, 1 A, 2 B, 3..8 body/accessories.
- `amodal-A/B-*.png`: projected leg masks before occlusion; design evidence only.
- `depth-*.npy`: camera-space depth in proxy units; larger is nearer, NaN background.
- `report.json`: dense two-cycle contact/bone/periodicity checks and actual raster
  overlap counts, depth winners and clipping. Depth zero-error without overlap
  would not exercise occlusion; inspect overlap counts and the actual preview.
- `file-hashes.json`: hashes of delivered artifacts before the report is written.

Numerical passes establish the stated kinematic constraints only. Review the
actual walk/run previews for readable support exchange, short-leg proportions,
contact transitions and occlusion before considering any image-generation use.

## Screen-space flight gate (VACE-03 regression)

A numerical `flight` label can hide less than one pixel of simultaneous lift.
`audit` now includes `screen_audit` at the exact canonical frame times. Both
half-cycles of running need a sampled simultaneous sole clearance >=4px at the
declared final camera scale. This is a design tolerance, not a biomechanical
guarantee. Near/far soles use their own projected ground points, not a shared Y.
Video pipelines must also call `screen_audit(profile, motion, exact_times)` using
the actual FPS, frame count, exposure offset and final pixels-per-unit. Do not
reuse a canonical 12-frame pass for a differently sampled 49-frame video.
Preserve low-clearance transition frames; never relabel or bottom-align them.
Fail before generation if either sampling schedule fails. Lower duty or revise
the trajectory jointly with reachable IK and cloth/foot occlusion, then inspect
the rendered feet. Lowering duty alone turns a jog into a sprint: the swing
arc is near zero at both ends, and flight only exists while one foot has just
lifted and the other is about to land. An optional run `flight_lift` (world
units, [0,1)) adds a C2 bump to BOTH swinging soles inside each flight window
only, matching the airborne body; stance soles stay at Z=0 and `audit` still
checks bone lengths, world lock, loop, exchange and transition smoothness.
The bundled run example uses duty0.4 with flight_lift0.15 (16-frame sampling
at 163.125px/unit, elevation16: >16px simultaneous clearance).

This projection gate is necessary but cannot see hidden/merged toes, raster
aliasing or wrong depth surfaces. Review complete visible paw contours, stance
and airborne samples, and both physical legs' occlusion/reappearance. A guide
pass never supplies observations for AI output. Keep failed versions.

For video experiments, preserve the exact control video, reference, prompt,
model/config hashes and sampled poses. Save the post-trim latent BEFORE decoding
through a dependency edge. On the tested Wan VAE, T16 introduced periodic seams
while T64 replayed all49 frames identically; this is specific measured evidence,
not a universal T64 setting for arbitrary clip lengths. Compare the SAME latent
to isolate decoding. Residual foot distortions also occurred where T16/T64 were
identical, so do not attribute all foot errors to decoding. Do not silently
lower temporal tile size after OOM or launch diffusion to repair a decode error.
