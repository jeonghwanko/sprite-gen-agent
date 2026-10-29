# Optional Decisions screening

> Owns: `review-decisions`, immutable advisory reports, and offline `evaluate-decisions`.
> Experimental. No measured quality or end-to-end speed improvement yet.

This optional QA route screens existing images with OpenAI Decisions. It supports
input design, individual generated poses and complete sequences. It does not generate,
repair, select, approve or register artwork. Existing geometric gates, motion review,
normal/slow playback and user review boundaries remain required.

The default command only prepares a local request summary. A live call requires both
`--execute` and `--confirm-api-billing`, using the current user's `OPENAI_API_KEY` and
separate API credit. A subscription login alone is not an API credential. Never paste
keys into chat or commit them. The key is read only for an explicitly requested call;
its presence does not enable the feature. Do not put service credentials into a
creator workspace whose host contract forbids them; use an allowed, explicitly
configured reviewer environment instead. No provider/model fallback is implemented.

## Review existing gen-motion evidence

Use the checkout's own venv. On Windows replace `.venv/bin/sprite-gen` below with
`.venv/Scripts/sprite-gen.exe`. These examples prepare only; they do not call an API.

```sh
# After gen-motion --prepare-only, before image generation:
.venv/bin/sprite-gen review-decisions --pose-run output/walk-run --stage input --frame contact-a --out-dir output/reviews/input-a

# After generation, choose the exact raw or registered candidate explicitly:
.venv/bin/sprite-gen review-decisions --pose-run output/walk-run --stage pose --frame contact-a --candidate output/walk-run/poses/pose-contact-a/attempt-01/output.png --out-dir output/reviews/pose-a

# After gen-motion --export has prepared sequence-review.json:
.venv/bin/sprite-gen review-decisions --pose-run output/walk-run --stage sequence --out-dir output/reviews/walk-sequence
```

Inspect `request-summary.json` and the actual referenced images before a live call.
Repeat the same command with `--execute --confirm-api-billing` after choosing API
billing. The report directory must be separate from source evidence. Preparation
creates no replacement for `input-review.json`, `review.json` or `sequence-review.json`.

The adapter verifies the existing run fingerprint and prepared files. Pose review
requires a completed generation receipt, including exact candidate bytes; it never
chooses a latest candidate implicitly. Sequence review reads the selected pixel hashes,
frame order and durations from the existing sequence review binding, including registered
frames when selected. It refuses missing or ambiguous selected pixels. Input guidance
includes the prepared visible/depth and amodal X-ray images where present; these remain
design targets, not observations. Geometry annotations are not invented by this route.

## Other sprite routes and final exported frames

For rows, video-derived frames, final game-pack pixels or other assets, supply an
explicit spec. Obtain frames with the existing engine's adapters/extraction tools;
do not hand-reassemble or repair sheets as part of screening. Paths are relative to
the spec. Durations are seconds; preserve the actual playback order and timing.

```json
{
  "kind": "sprite-gen-decision-spec",
  "version": 1,
  "stage": "sequence",
  "intent": "Side-view walk loop. Keep the original outfit and physical leg identity. Contacts not yet measured.",
  "loop": true,
  "references": [{"file": "original.png", "role": "identity"}],
  "frames": [
    {"id": "frame-0", "file": "frames/0.png", "duration": 0.1},
    {"id": "frame-1", "file": "frames/1.png", "duration": 0.12}
  ]
}
```

The two-frame example illustrates structure only; it is not a full gait review.
Use every final frame for a full-cycle claim. `stage` is `input`, `pose` or `sequence`.
Reference roles are `identity`, `guide` and `anchor`; at least one identity is required.
The `frames` of an input review are rendered design guides. Other stages use actual
generated/exported artwork. Optional `sources: [{"file": "plan.json"}]` binds additional
local records without sending their text to the API. Put only necessary design intent
in `intent`; expected phase labels are never evidence of observed contact.

```sh
.venv/bin/sprite-gen review-decisions --spec output/review-spec.json --out-dir output/reviews/final-walk
```

Local limits: 1–64 frames (at least two for sequence), 1–8 references, 20 MiB per
source image, 16 megapixels per image, 48 MiB serialized request, 400 questions. These are local guards, not
promises about service capacity. Images are decoded to RGBA and sent as lossless PNG
at original dimensions, without crop, scale, alignment or compositing. Animated image
files are refused rather than silently inspecting just their first frame.

## What the report means

Questions separate identity, anatomy, guide compatibility, pose match and contact
visibility. Sequence questions cover adjacent transitions, an optional last-to-first
transition, limb tracking and possible contact problems. A static frame collection
is not proof of smooth playback or physical gait; keep normal/slow preview review.

Each answer is `clear`, `defect` or `unverified`. These are screening outcomes, not
`pass`/`fail` approval entries. Occlusion, refusal and insufficient evidence stay
unverified. Both reported confidence and the selected option's probability must meet
`--threshold` (default 0.8). This is an **uncalibrated initial threshold**, not an 80%
quality guarantee. The report keeps the probabilities for later analysis.

`decision-review.json` records exact source and sent-image hashes, frame durations,
model, rubric version, request hash, threshold, per-question results, request ID when
available, observed token usage and request elapsed time. It never marks visual approval.
No estimated savings or fabricated coordinates are reported. The summary stores no
image base64 or credentials, and the HTTP client does not log provider error bodies.

A completed report for identical evidence/settings is reused without a second call.
Changed pixels, durations, source records, prompts or thresholds require a new report
directory. A started/interrupted, malformed or failed request is not retried automatically.
Inspect the saved status and any provider request records before explicitly making a new
review request in a new directory. HTTP/network failures, unexpected models, malformed
answers and changes during the request cannot become clear. No registration, generation
retry, API key fallback or automatic threshold adjustment follows a decision.

Exit 0 means preparation or a completed advisory report, **not** visual acceptance.
Exit 2 means the live request did not produce a verified response. Other local input or
configuration errors stop with an explanation.

## Compare with independent labels

Label the same evidence independently of Decisions results. Keep characters disjoint
between `calibration` and `test`; tune on calibration and freeze the rubric/threshold
before test. Never copy model outputs or planned contact labels into ground truth.
Use `unverified` for genuinely ambiguous evidence. Keep the old reviewer's classifications
as `baseline` and time the same review scope. Each evaluation uses one model, rubric and
threshold. Reports must still match their local evidence; archive inputs with reports.

```json
{
  "kind": "sprite-gen-decision-eval",
  "version": 1,
  "cases": [{
    "id": "walk-example",
    "character": "character-held-out-from-calibration",
    "split": "test",
    "labeler": "independent reviewer name",
    "report": "reviews/final-walk/decision-review.json",
    "request_sha256": "COPY_THE_ACTUAL_REPORT_REQUEST_HASH_AFTER_INDEPENDENT_LABELING",
    "expected": {"f000.identity": "clear", "sequence.limb_tracking": "unverified"},
    "baseline": {"f000.identity": "clear", "sequence.limb_tracking": "unverified"},
    "baseline_review_seconds": 12.0
  }]
}
```

These are illustrative labels and timing, not real evaluation evidence. Use actual
question names from the report and your own labels. Partial labeling is allowed;
metrics cover only those labeled questions, so include every required criterion in a
benchmark intended to represent the entire workflow.

```sh
.venv/bin/sprite-gen evaluate-decisions --dataset output/labeled-cases.json --output output/evaluation-v1.json
```

The offline evaluator reports confusion matrices, defect-as-clear misses, false alarms,
abstentions, detected defects, unsupported clears on ambiguous truth, baseline comparisons,
recorded review-call time and input tokens. Missing measurements stay null. It rejects
duplicate evidence and character leakage across splits. Labels remain user-supplied;
the tool cannot certify their independence. Frame checks within an animation are correlated.
Synthetic test fixtures validate software contracts only, not image-review performance.

End-to-end improvement stays **unmeasured**. To claim improvement, run a separate matched
production comparison measuring time/cost per independently accepted animation, regeneration
count and missed defects at the same quality requirement. Adding a screening call can also
increase latency or unnecessary revisions. Begin with input and key-pose screening; promote
any criterion into a blocking policy only after measured validation, outside this module.

API contract checked 2026-10-29: [Decisions guide](https://developers.openai.com/api/docs/guides/decisions)
and [create reference](https://developers.openai.com/api/reference/resources/decisions/methods/create).
