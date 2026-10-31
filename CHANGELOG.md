# Changelog

All notable public changes to `sprite-gen` are recorded here. Versions track the `version:` field in `SKILL.md` and `pyproject.toml`.

## Unreleased - sprite-gen-agent (fork of 3.4.0, AI Fighter creator kit)

These are the fork's additions on top of 3.4.0, first written for the AI Fighter creator kit ([NOTICE](NOTICE)). The version stays 3.4.0 until a release is cut.

- **New: `gen-motion`, pose-plan GPT motion.** It makes one pose at a time from an authored pose plan with joint guides, reviewed anchors, a fixed canvas and a cycle review. It also measures each pose's body and foot geometry and adds `gait-guide` and the gait-motion bridge with per-stance contact coverage. See [pose-motion](docs/pose-motion.md) and [gait-guide](docs/gait-guide.md).
  - An attempt whose `gen` sent nothing is `not-started`. `gen-motion` exits 3, and the next `--frame` runs again in the same attempt folder; this does not count as one of the two attempts.
  - `workflow` offers it as the `pose-plan` motion method (experimental, codex).
- **New: `scale-check`.** It measures each frame's figure size against a reference frame by its head and leaves an eye check where the match is not sure. `game-pack` applies the corrections about the anchor (`scale_check`, `frame_scale`). See [scale-check](docs/scale-check.md) and [game-pack](docs/game-pack.md).
- **New: machine-readable generation outcomes.** Reports from `gen`, `video*`, `gen-set` and `video-set` carry the following:
  - `ok`;
  - `errors` with stable codes and `retryable`;
  - `provider_submitted`, which is written on success, on failure, on the codex question and on SIGTERM.
  - Exit 3 means nothing was sent. A stopped set starts no further child. `version --json` and `doctor --json` are new. See [gen](docs/gen.md#outcome--what-failed-whether-to-retry-whether-a-provider-was-charged).
- **Changed: the codex provider finds a session's rollout through codex's state DB.** It falls back to scanning when there is no DB, and it reports the model codex actually ran. Verified on codex 0.156.1 to 0.165.0.
- **New: `inspect-motion` stance reasons carry stable codes** (`reason_codes`, `STANCE_REASONS`); the texts are unchanged.
- **New: `sprite_gen.api`.** It lists the names a program embedding sprite-gen may import, kept stable. See [api](docs/api.md).
- **New: opt-in Decisions screening and reproducible audio pack hashes.** Shipped audio player line endings are canonical.
- **Behavior change: a request that may have been charged is never sent again unasked.**
  - `gen` no longer calls codex a second time after a timeout. The timed-out `codex exec` was stopped after its request left, so it may already have made and been charged for an image. `--timeout-retry` asks for the second call; `--no-timeout-retry` still parses and is now the default.
  - `provider-timeout` is `retryable: false`.
  - A `gen-set` row or `video-set` item whose child ended after its request may have left, without a coded report (killed, out of memory, a crash), is `outcome-unknown` instead of the retryable `provider-failed`. A child that said it finished and left no image or clip stays `provider-failed`.
- **Changed: `video-cycle-align` makes a loop whose beat still breaks of source frames only, where that jolts less.**
  - Under `--between auto`, after RIFE's frames given way are made again and spread, a loop still at a jolt of `BEAT_JOLT` or more is resampled from source frames only. Its start and each frame's source frame are chosen so the largest jolt is least (`cadence.source_plan`); it is taken only where it jolts less. Loops that keep the beat keep their bytes.
  - The report's `beat.source_plan` names the phase and the jolt it replaced, and the dropped made frames' `smear` rows are `method: "source-plan"`.
  - New `--beat-limit` (0.25 to 0.4) sets the jolt at which a beat is broken (`broken-beat`). See [loop-repair](docs/loop-repair.md) section 4.
- **Behavior change: the remaining paid paths say what they sent (2026-10-29).**
  - A stop (`interrupted`) after a provider request left is `retryable: false`, in `gen`'s report and in the rows and items of `gen-set` and `video-set`: the same command would send the request again.
  - A `gen-set` or `video-set` that sprite-gen itself fails in (a bug) still writes its set report: the rows and items that finished, an `internal-error`, and `provider_submitted` true.
  - Local tools on the hosted path (ffprobe, ffmpeg frame extraction, img2webp, `extract`'s heal child, scene render) run with a time bound (`sprite_gen.local_tool`); one that hangs fails the step instead of holding the run.
  - `gen-motion` runs its `gen` through `run_logged` under a recording: a stopped `gen-motion` sends `gen` SIGTERM, so `gen` writes its report.
  - `review-decisions` takes a dated snapshot of the model asked for, and exits 1 (not 2) for an unverified review.
- **Faster: `transform_cell` (the gait fallback's scaled-back frames in `video-loop`) maps only up to the figure's reach, bytes unchanged.** 1.85x on a real 73-frame run (29.9 s to 16.2 s). `face_bake` and the curation export run their tools bounded too.
- **Changed, bytes unchanged: `frames/extract.py` and `video/loop.py` are split into modules beside them (2026-10-31).** `extract.py` 3,900 → 1,980 lines: `chroma_key`, `chroma_ycc`, `pixel_grid`, `frames_manifest`. `loop.py` 3,030 → 1,940 lines: `loop_params`, `loop_cycle`, `loop_pose`, `loop_cells`, `loop_output`, `loop_words`. Both import every moved name back, so `extract.<name>` and `loop.<name>` still work. `extract`'s engine revision now also hashes the three pixel modules; it changes once with this release, so each run's frames are re-derived once (same bytes).
- **Fixed: every text file is read and written as UTF-8**, whatever the platform's locale. On a cp949 Windows, a report holding an em dash had stopped `video-set`.
- **Fixed: a `video-frames` clip refused partway stops its keying workers at once** (2026-10-31). The worker pool that keys frames ahead was shut down only when the abandoned generator was collected, on whatever thread collected it, where the shutdown could hang the run; it is now closed when the keying loop ends.
- **Fixed: a request whose cell width, height or safe margin is no number (`null`, a word) is refused naming it**, by `prepare`'s floor, `extract` and `compose-atlas`, like a width of 0. It had stopped them with a TypeError or ValueError from inside `int()`.
- **Fixed: on Windows, a `ui-kit` publish stopped with Ctrl-C leaves nothing behind**, as docs/ui-kit.md says. Stopped between opening a kit file and writing it, a run had left its hidden staging folder: the open file could not be removed. The file is now created and wrapped in one call, and the clean-up closes any file an interrupted step still holds before it removes.
- **Fixed: on Windows, the missing-NumPy error names the venv interpreter that exists** (`.venv\Scripts\python.exe`, not `.venv\bin\python`), so its install command can be pasted as is. Tests that make a symlink skip, rather than fail, on a Windows account that may not create one; the RIFE stand-ins run on Windows, and the `video-resume` hints are compared as the platform quotes their path.
- **Changed, bytes unchanged: the keying engines share two modules instead of reaching into each other (2026-10-30).** `frames/keyfield.py` holds the key's channel split, the whole-image key fields (distance, tint, key hue, `cap_key_hue`) and the two bars the passes agree on; `frames/morph.py` holds mask growth, ring depth and box sums. `extract`, `decontam`, `logo_matte`, `video.frames` and `video.restoration` import them from there, and the names other modules read (`extract.grid_edges`, `border_key_candidate_field`, `painted_key_leans`, `cutout.detect_key_kind`, `corner_average`, `EXTRACT_KEY_THRESHOLD`) are public. `decontam` no longer imports `extract`, so the import cycle between them is gone. The engine revision now hashes `keyfield` and `morph` with `extract`, `decontam` and `layout`: a change in any re-derives the frames, and the new fingerprint re-derives each run's frames once.
- **Fixed: `sprite-gen <verb>` takes every flag its module takes (2026-10-30).**
  - `cli.py` kept its own copy of ten verbs' flags, and the copies had drifted. `sprite-gen extract --chroma-mode` / `--repalette`, `compose-gif --scale` / `--state` and `correction-loop --min-attempts` were refused, while `python -m` and the docs took them.
  - Each of those modules (`extract`, `compose-atlas`, `preview`, `compose-cycle`, `compose-gif`, `unpack-atlas`, `export-pngs`, `slice-sheet`, `inspect`, `score`, `correction-loop`) now declares its flags once, in `add_arguments`, and the CLI uses that.
- **Fixed: `compose-cycle` bakes a linked clone with its source frame's transform and pixel edits** (`edit_index`), as the atlas, GIF and PNG export already did. It had baked the clone unedited.
- **Fixed: the curation and compose webviews refuse a POST a browser sent from another page** (`Origin` not their own address: 403). A page on another site could POST to localhost with no CORS preflight (body-less, or `text/plain`) and reach `/api/reroll`, `/api/compose`, `/api/mount` and the other write routes. Requests without `Origin` (curl, scripts) are unchanged.
  - A browser's POST must also address the server as `localhost`, a `*.localhost` name or an IP literal (a LAN address included), so a page whose own name is re-pointed at 127.0.0.1 (DNS rebinding) is refused too. A webview opened through another host name (a reverse proxy, a MagicDNS name) can still read but not write; open it by address.
- **Fixed: a curation POST route that refuses by `SystemExit` answers 500 with the reason**, as GET routes do. It had dropped the connection with no response.
- **Faster, bytes unchanged: per-pixel loops and repeated work in the hot paths (2026-10-30).** Each rewrite is held to its frozen pre-change form in `tests/test_p1_perf_equivalence.py`.
  - The curation webview finds a frame's pixel size from where its colours change (a gcd), not by scanning every block size: 200 s to 2 s over 159 frames, so a cold `/api/run` no longer takes seconds. Each frame's size and content box are cached by file stamp.
  - `extract`: `connected_components` and `component_group_image`, `_edge_histograms` (pixel-grid detection) and `chroma_adjacent_count` (`inspect`) run on arrays; decontamination's `_box_sum` reads its corners as views instead of four full-size copies (2.2x). `cutout`'s transparency check runs on an array.
  - `inspect`'s alpha centroid (219 ms to 4 ms a frame) and the preview checker run on arrays.
  - `video-loop` and motion-auto measure each symmetric frame-distance matrix's upper triangle and mirror it (2x); `local_cycle` measures each window's repeat profile once for the lengths that share it; the masked NCC transforms each channel and the mask once for the correlations that share them.
- **Fixed: RIFE runs on one GPU take turns instead of running out of its memory (2026-10-31).** Two loops of one job aligned at once each loaded rife-v4.6 on office-stable's 8 GB RTX 4060; one kept 5.95 GiB cached between its runs and the other failed every frame with CUDA out of memory, which failed its `video-cycle-align` (44 times in one job, 2026-10-28). A run on CUDA now holds the machine's GPU turn (`rife.gpu_turn`, a lock file in the temp directory taken through the publish lock's `flock`/`LockFileEx`) and gives back what it left cached (`torch.cuda.empty_cache`) before handing it on. On office-stable, three processes of six 960x960 runs each: 9 of 18 runs failed before, none now (12.6 s for all 18); the frames are the same bytes.
- **Faster, bytes unchanged: `video-loop` reads a cut's path once and writes its previews at once (2026-10-31).** `repair.flow_states` keeps the last four paths it read, by their frames' pixels and box (a cut's seam, drawings and repair read the same frames up to four times: 8 reads of 3 cuts). The body anchor's wrap search reads the shifted region in place instead of rolling the whole frame for each shift (`body_wrap_offset`, 4x). img2webp runs while the GIF is made, into a folder of its own, and the WebP is moved into place only once the GIF is written, so a GIF that fails still leaves no WebP; the GIF's frames are prepared on up to eight threads (`gif_utils.PREPARE_THREADS`, 3.6 s to 1.1 s for 24 frames of 960 px), and the frames handed to img2webp are uncompressed PNGs. The WebP's check for colour under transparent pixels reads each pixel as one word. A real 73-frame walk cut (`--anchor motion-auto`): 97.6 s to 76.1 s; every file written, the report included, is the same bytes but for its `implementation_sha256`.
- **Faster, bytes unchanged: `video-frames`' ordered pass (2026-10-31).** The keyed frames are saved by a thread of their own, in clip order, while the next frame is keyed, and the frame in hand is the image saved rather than its file read back (`cutout(write=)`); leaving the pass, by a refusal or a failure too, waits for every save, so the frames on disk are those the pass reached. A speck's distance from the body is read from one table of the body's pixels summed from the corner instead of the body grown by its reach (`drop_specks`), and the edge bands are counted in numpy (`edge_contact`, about 150 ms to under 1 ms a frame). The same 73-frame clip: 65.8 s to 54.6 s, every keyed frame and the report byte-identical.
- **Faster, bytes unchanged: `video-frames` keys ahead in worker processes (2026-10-31).** What a frame's keying reads from that frame alone (the route, the painted key, the chroma matte as `extract.chroma_matte` and decontamination's band, local background, noise and material behind as `decontam.prepare`) is computed by `cutout.prepare_cutout` in worker processes, up to two frames a worker ahead; the clip's own pass, which carries one frame's palette and calls to the next, still runs frame by frame and takes it as `cutout(prepared=)`. `SPRITE_GEN_KEY_WORKERS` sets the workers (default `min(4, cpu_count)`; 1 keys in one process as before). A worker sends the examined pixels' colours and local backgrounds (and their 3x3 means) rather than whole planes, and leaves the frame's own colours for the main process to read again. `cutout`'s check for colour left in transparent pixels reads each pixel as one word (16 ms to under 1 ms a 960x960 frame). A real 73-frame walk clip: 103 to 135 s down to 43 s, every keyed frame and the report byte-identical at 1, 4 and 8 workers (8 is no faster: the ordered pass is what is left). `tests/video/test_key_workers.py` holds workers to one process.
- **Faster, pixels unchanged: `game-pack` encodes its atlas pages at once, the WebP copies at method 4 (2026-10-31).** A page's PNG and WebP and the next page's encode on up to four threads, at most two pages held at once; the PNGs and the manifest are byte-identical and every WebP decodes to the same pixels. WebP method 4 instead of 6 is about the same size (atlases -4 to +1.4 %, single images -8 to +6 % on 400 alpha PNGs from four projects) in 40 to 75 % of the time; method 0 was ten times faster still on hosted fighters' packs but up to eight times larger on other art. PNG stays at level 9 (level 6 cost 1 to 5 % on atlases and up to half on flat art), and beside the page's WebP it finished first on every page measured. The WebP is encoded from its own copy of the page: Pillow keeps a save's options on the image object until the save ends, so two saves of one object at once can read each other's and write a lossy WebP. Two hosted fighters' 2-page packs: 12.7-14.1 s to 4.1-4.4 s, files -1.2 to -0.2 %, peak memory 0.64-0.68 to 0.82-1.05 GB; five 4096 px pages: 42 s to 12 s, peak 0.76 to 1.35 GB. A pack made before this cannot be packed again into the same `--out-dir` (its WebP bytes differ); use a new folder.
- **Faster, bytes unchanged: decontamination's window sums run in integers (2026-10-30).** `morph.box_sum` and `box_sum_at` sum boolean and 8/16-bit unsigned input in an int32 table (int64 past its range), the same exact sums as float64 at half the memory traffic, and `box_sum_at` takes channels. `decontam.local_background` sums the keyed count and colour in one table and the fallback mask only at the pixels the preferred one left empty; the material behind an edge is one table, not four. `decontaminate` 562 ms to 461 ms a 960x960 frame; `video-frames` on a real 73-frame walk clip uses 7 to 10 % less CPU, every keyed frame byte-identical. Held to the frozen float forms in `tests/test_p1_perf_equivalence.py`. The engine revision hashes both files, so each run's frames are derived again once.
- **Changed, bytes unchanged: `video-loop`'s 800-line `_cut_loop` is split into its phases (2026-10-30).** Options settled, the cut chosen (`_select_cut`, one function a mode), its frames anchored, repaired and gated, the strip written, the wrap read and gated, the previews written; a refusal at any phase goes through one writer (`_Run.refused`). Faster on the way: a motion-auto cut is not searched again for the regions found when the search read it (one `auto_motion.discover` fewer, about 10%), the legs' signals are read once a search, the frame masses are read only by the modes that use them (a walk's search never does), and pixel-grid detection reads its edge histograms once (`_grid_phase` folds them). Twenty `video-loop` runs across every cycle mode, anchor and repair write the same files, reports and warnings as before.
- **Removed: `scripts/diagnose_video_loop.py` and five unreferenced `docs/assets/` images (2026-10-30).** Nothing in the repository, its tests or the creator kit named them. The images (`curator-drag-update.gif`, `demo-character.gif`, `demo-furniture.gif`, `curator-iso.png`, `cutout-demo.png`, 6.9 MB) were linked from no document. `SECURITY.md` now names this fork's GitHub test workflow, the `>=3.11` interpreter floor and the moved NumPy gate test.
- **Removed: four functions nothing called (2026-10-30)**: `extract.fit_pixel_unfake` (the integer placement is `row_placement`), `curation.frame_filename` (frame paths come from `spec/layout.row_frame_rel`), `prepare.sampled_reference_pixels` and `unpack_atlas.import_pngs` (call `import_png_groups` with one group). No code, test, document, the creator kit or findthem named them.

## v3.4.0 - Loop part pop retakes, cycle alignment walk beat repair and broken beats, ghost limb faults, and same foot, filmed jump, and seam jump retakes

**Upgrading from 3.3.0: A walk or run loop in `video-loop` is cut to the same bytes, whether a part pops in it or not, and `video-cycle-align` changes only alignment outputs whose beat broke or where a made frame has a limb seen through under `--between auto`.** For `video-loop`, every walk and run report gains `part_pop` and `retake`; `--unfixed refuse` also refuses a loop recorded `retake` reason `part-pop`, whatever `--repair`; no flags are added, no frames are made and no other cut is taken. `video-cycle-align` now reads ghost bands in windows of the body to catch limbs seen through, keeps frames given way on the beat under `--between auto` by making them again at clean fractions or widening jolts across neighbouring frames, changing only alignment outputs whose beat broke (a jolt of 0.25 beat or more) or where a made frame has a limb seen through (ghost window over 1.4 %); loops keeping the beat, loops with no frame given way, and `video-loop` (and `video-set`) keep their bytes for strip PNG, GIF, WebP, and cycle frames, while gaining `beat`, `ghost_window`, (on back and back_diagonal views) `same_foot`, and `retake` reasons (`broken-beat`, `same-foot`) in alignment `strip.json` and reports; every aligned loop's `strip.json` and report gain `screen`, and loops whose cut jumps gain the reasons `filmed-jump` and `seam-jump`, frames unchanged; and `clip_feet` in `video-loop`'s `strip.json` and reports for every walk or run cut; all outputs from `video-loop-compare` and `video-loop-repair` are unchanged, bytes included, and `--same-foot` defaults to `warn` so nothing is refused by default.

- **New: The part reading and the `retake` record.** Squares 8 % of the body height are followed drawing to drawing and a part whose jerk leaves the curve its steps either side draw pops at the wrap. The cut stays and the report records a take to film again (`retake`, reason `part-pop`): `remedy` is `retake` where the engine's search chose the cut, and `recut-or-retake` where the cut was given (`--cycle fixed`, or `pinned`); `searched` says which. See [loop-repair](docs/loop-repair.md) section 2, "The part pop".
- **New: a made frame's ghost band is now also read in windows of the body (`rife.ghost_window`, constant
  `rife.GHOST_BAND` = 0.10) to catch limbs seen through.** On frames the cycle alignment makes,
  `rife.ghost_window` counts the same part-covered pixels in regions at least `rife.GHOST_THICK` (5 px)
  thick that `rife.ghost` counts, but in windows a tenth of the body high sliding a quarter window at a
  time down the body, each as a share of the solid pixels in that window, reading the largest window's
  share and where it lies as fractions of the body from the top (`ghost_window_at`). Over
  `interpolation_quality.GHOST_WINDOW_WARN` = 0.014 (1.4 % of a window's solid pixels), the made frame has
  the fault `ghost` (though whole-body `ghost` stays under 0.3 %) and gives way, or is made again at another
  fraction, warning that it carries a limb seen through (for example: "carries a part-covered band over
  2.40 % of the solid pixels in a band 10 % of its body high (60 to 70 % down it), at least 5 px thick,
  though 0.18 % of its whole body, a limb seen through, a ghost"). This catches RIFE blending coverages
  over a gap beside a limb; it is not read on `video-loop`'s jump repair frames or filmed frames.
- **New: `video-cycle-align` reads the loop's beat and keeps frames given way on it under
  `--between auto`, checks for the same foot stepping twice, and `video-loop` records whether each cut
  steps the same foot twice (`clip_feet`).** On every aligned loop, `video-cycle-align` reads the leg
  path over the lowest `LEG_PATH_BAND` = 0.10 of the figure box (`BLOCK` 8 x 8 px, `SEARCH` 12 px,
  `MATCH_BAD` 40 of 255, `READ_HEIGHT` 410 px) and measures the beat jolt across each gap (`BEAT_JOLT`
  = 0.25 beat). Under `--between auto` (the default), where a loop's beat breaks from frames given way
  to the nearer source frame, the frames given way are taken in runs of neighbouring frames and made
  again at clean fractions (`align.RETIME_T` = 0.15 to 0.85, step 0.05) within `BEAT_JOLT` of their
  times, or their jolt is spread over neighbouring frames (`align.WIDEN` = 2, then 3 frames each side)
  moved off their own times and made by RIFE. A frame made only to keep the beat is taken only where
  its window reading is under `align.RETIME_WINDOW_MAX` = 0.006, well under the 1.4 % fault limit,
  besides having no fault. Runs that cannot be kept stay on the nearer source frame, are named in
  `beat.named`, and carry the `broken-beat` reason under `--between auto` in the `retake` record when
  beat gaps remain at `BEAT_JOLT` or more. On views where feet stand side by side (`STEP_VIEWS` = back,
  back_diagonal), `video-cycle-align` reads whether the same foot steps twice in a row (`FOOT_ROWS` = 0.03,
  `LIFT_MIN` = 0.010, `LIFT_CELLS` = 2, `DOWN_MAX` = 0.006), recording reason `same-foot`, and warns
  under `--same-foot warn` (the default) or stops the set before anything is rewritten under
  `--same-foot refuse`. Each reason has one warning line replacing earlier separate beat and same-foot
  lines, naming what is likely to fix it ("retake", "recut", or "recut-or-retake"), and the report's
  `retake` list carries these loops like the others (under `--between nearest` a broken beat is recorded
  in `beat` but is no reason). `video-loop` reads every cut of the cut's length on keyed frames of
  walks and runs, recording for every cut whether that cut, looped, steps the same foot twice (`clip_feet`)
  in `strip.json` and its report.
- **New: `video-cycle-align` reads a jump the take films between two of its frames, or at the cut's seam,
  on the screen as well as the beat.** For each step of the cut's own frames (`cycle.source/`, step i
  from frame i to i + 1, the last step from the last frame to the first, which is the cut's seam),
  `cadence.screen_steps(frames)` reads the mean absolute luminance difference over white (0 to 255) of
  the box every frame's figure fits in (`whole`) and of its lowest `cadence.SCREEN_ROWS` = 0.30 (`legs`;
  a strip cell's lowest 125 of its 406 px), read at `READ_HEIGHT` 410 px. The beat reads only the leg
  path where the feet go (the lowest 10 % of the box); two drawings filmed a pose apart (legs wide apart
  in one, crossed in the next, the tail up) move the feet little and change the picture much, so the beat
  passes them, but the screen sees them. It is read on the cut's own frames, not on the aligned frames
  (an aligned frame spans two or three of the cut's frames, which spreads a jump over the drawing changes
  beside it and hides it), on every aligned loop whatever its view and whatever `--between`. Under
  `cadence.screen_record`, a step is a jump when, over the box or on its leg rows, it is at least
  `cadence.SCREEN_JUMP` = 2.0 times the cycle's typical step and at least `cadence.SCREEN_ALONE` = 1.3
  times every other step of the cycle. The typical step is the upper quartile of the cycle's steps: on
  ones every step is a drawing change, on twos or threes most steps change nothing and the upper quartile
  is a drawing change. A cycle whose drawing changes are all alike names none; so does a cycle with two
  steps alike however large (neither is 1.3 times the other). A jump inside the cut gives the `retake`
  reason `filmed-jump` (details `filmed_jump`: `steps`, `at`, `whole`, `legs`, `remedy`
  "recut-or-retake"), because the take jumps between two poses there and the alignment does not change
  the poses a take shows; where the clip films another cycle without the jump, a cut of the same clip
  from there removes it, and where it does not, a new take does. A jump at the seam gives `seam-jump`
  (details `seam_jump`, same keys, `remedy` "recut-or-retake"), because another cut of the clip may close
  on a pose it holds, or a new take may not need the cut to close there (limits `screen_jump`,
  `screen_alone`, `screen_rows`). Warning lines name what is likely to fix it (for example: "<loop>: cut
  it again or film this direction again (filmed-jump): the take jumps between two of its frames, from
  frame 30 to 31 of cycle.source/ the picture changes 22 of 255 over the figure's box and 36 on its legs,
  2.10 and 2.50 times the cut's typical step and 1.50 and 1.80 times any other step (the loop's frames 13
  to 14); the alignment does not change the poses a take shows. Where the clip films another cycle
  without the jump, a cut there fixes it; where it does not, a new take does", or for a seam jump:
  "<loop>: cut it again or film this direction again (seam-jump): the cut's seam jumps, from frame ... ;
  another cut of the clip may close on a pose it holds, a new take may not need the cut to close there").
- **Report fields:** `cycle_align.beat` in `strip.json` and report row `beat` record `jolt`, `gap`,
  `over`, `jolts`, `limit` (0.25), `band` (0.1), and `path` (`read`, `on-time`, or `unread` with
  `why`). Each `smear` row (and a frame made again, under `retimed`) records `ghost_window` and
  `ghost_window_at` ([from, to] fractions of the body); both are `null` where the ghost screen does not
  read the loop. A retimed frame's row in `smear` has `method: "retimed"` (beside `rife` kept and
  `nearest` given way), keeps its first frame's measures and `faults`, and gains `retimed`: `t`,
  `from_t`, `off`, measures, `faults: []`, `look`. Every frame given way in a loop whose beat broke gains
  `retime`: `between`, `tried` (each fraction recording `ghost_window`, and `window_over` where it had no
  fault but read over the bar), `off_nearest`. Retimed frames count in `made_at` and `made_by_rife`, not
  `nearest_at` or `replaced`; the report gains a set-wide `retimed` count, and `beat.retimed` lists them
  per loop. Widened runs record in `beat.widened` (`run`, `width`, and `moved` with `at`, `off`, new
  source or pair and fraction); moved frames carry `smear` `method: "moved"` with `moved` and count in
  `made_at`. Runs kept on the nearer source frame record in `beat.named`. `cycle_align.same_foot` in
  `strip.json` and the report row records `read`, `order`, `steps` (`half`, `frames`, `peak`, `lift`),
  `twice`, and `clip`. `clip_feet` in `strip.json` and the `video-loop` report records `length`, `cuts`,
  `same_foot`, `alternate`, `unread`, `seam_in_step`, and `same_foot_at`. Each loop row and `strip.json`
  `cycle_align` gain `screen`: `whole` and `legs` (every step), `typical` (per region), `jumps` (each: `step`
  [i, i + 1] as frames of cycle.source/, `seam` true for the last step, `whole` and `legs` each [change, times
  the typical step, times the next largest step], `at` the gaps [k, k + 1] of the aligned (turned) loop that show
  it), and `limits` (`jump`, `alone`, `rows`). Under `--same-foot refuse`, a
  refused report is written with `applied: false`, `refused: "same-foot"`, and `same_foot`; the report's
  `same_foot` key names the mode. A loop's `retake` record (keeping its shape: `reason` the first,
  `reasons` all, details under each reason's snake_case key, `limits`) gains reasons `filmed-jump`
  (details `filmed_jump`: `steps`, `at`, `whole`, `legs`, `remedy`), `seam-jump` (details `seam_jump`: `steps`,
  `at`, `whole`, `legs`, `remedy`; limits `screen_jump`, `screen_alone`, `screen_rows`), `broken-beat` under
  `--between auto` (details `broken_beat`: `jolt`, `gap`, `over`, `named`, `remedy`; limit `beat_jolt`)
  and `same-foot` (details `same_foot`: `order`, `twice`, `clip` counting `length`, `cuts`, `same_foot`,
  `alternate`, `unread`, `seam_in_step`, or null, `remedy`; limits `lift_min`, `lift_cells`, `down_max`),
  in order: held-drawings, filmed-ghost, filmed-jump, seam-jump, broken-beat, same-foot; `remedy` is "retake",
  "recut", or "recut-or-retake", and the report's `retake` list carries these loops like the others.
- **Python callers:** new module `sprite_gen/video/cadence.py` with `leg_path`, `Beat`, `beat_record`,
  `turned`, `same_foot`, `clip_feet`, `view_reads_feet`, `screen_steps`, `screen_record` and constants
  `LEG_PATH_BAND` (0.10), `READ_HEIGHT` (410), `BLOCK` (8), `SEARCH` (12), `MATCH_BAD` (40.0),
  `BEAT_JOLT` (0.25), `STEP_VIEWS`, `FOOT_ROWS` (0.03), `LIFT_MIN` (0.010), `LIFT_CELLS` (2),
  `DOWN_MAX` (0.006), `SCREEN_ROWS` (0.30), `SCREEN_JUMP` (2.0), and `SCREEN_ALONE` (1.3); in
  `sprite_gen/video/rife.py` `ghost_window` and `GHOST_BAND` (0.10); in
  `sprite_gen/video/interpolation_quality.py` `GHOST_WINDOW_WARN` (0.014); in `sprite_gen/video/align.py`
  `measure_made(frame, a, b, t, reads=...)` (returning every measure a made frame is judged by),
  `retake(..., beat=None, feet=None, screen=None)`, `RETAKE_JUMP_REASON` ("filmed-jump"),
  `RETAKE_SEAM_REASON` ("seam-jump"), `RETAKE_BEAT_REASON`, `RETAKE_FEET_REASON`, `REMEDIES`,
  `RETIME_WINDOW_MAX` (0.006), `RETIME_T`, `WIDEN` (2, 3), `SAME_FOOT` ("warn", "refuse"),
  `align_set(..., same_foot="warn")`, and `resample` facts gaining `beat`.
- **Output changes:** under default `--between auto`, alignment outputs where frames given way broke the
  beat change when retimed or widened to keep the beat under `BEAT_JOLT` (0.25 beat), or where a made
  frame has a limb seen through (ghost window over 1.4 %) and gives way or is made again; loops whose
  frames given way keep the beat, loops with no frame given way or faulty made frame, and all outputs
  under `--between nearest` and `rife` keep their exact bytes across strip PNG, GIF, WebP, and cycle
  frames (gaining the `beat` record, `ghost_window`, `retake` reasons, and where `--view` is back or
  back_diagonal `same_foot` in `strip.json` and reports; under `--between rife` the alignment keeps the
  made frame and names it a ghost); every aligned loop's `strip.json` and report gain `screen`, and loops
  whose cut jumps gain the reasons, frames unchanged. For `video-loop` (and the loops `video-set` cuts),
  `strip.json` and report files gain `clip_feet` on every walk or run cut, while strip PNG, GIF, WebP,
  and cycle frames keep their exact bytes. All outputs from `video-loop-compare` and `video-loop-repair`
  are unchanged, bytes included.
- **Tests:** synthetic test suite in `tests/video/test_cycle_align_beat.py` uses figures with an
  outlined body over two outlined feet 16 px wide (two blocks) on the ground, sliding together by 8 px
  (a block) at each drawing forward half the cycle and back, with the interpolator drawing them at the
  fraction and smearing the body where told the pair breaks, and feet standing side by side lifting in
  turn. Tests cover: a cut of 54 frames on threes aligned to 24 where every fourth frame falls half way
  between drawings with RIFE smearing at fractions 0.45 to 0.55, the nearer source frame leaving a jolt
  of 2/3 of a beat (which 3.3.0 failed) and the fix making each again at 0.4 (0.13 of a beat early,
  largest jolt 0.13, nothing named); a fraction made again with no fault but a block the background shows
  through (its window between 0.6 % and 1.4 %) is passed over for the next clean fraction (0.6 instead of
  0.4), its try recording `window_over`; a cut of 20 stretched to 30 with two neighbouring frames given
  way, where making each again nearest its own fraction jolts the gap between them 0.325 beat but the
  run's choice (0.55 and 0.2) keeps every gap under 0.25 beat (largest 0.2), and where 0.55 is not clean
  both stay on the nearer source frame (named, largest jolt 1.0); a frame whose pair breaks at every
  fraction within the limit, half a beat late on the nearer frame, where two frames each side are moved
  a third and a sixth of a beat late and made there (every gap jolting a sixth of a beat); a cut of 40
  squeezed to 24 with one frame given way a fifth of a beat off on the nearer frame delivered as before
  without remaking; every frame at its own time giving jolt 0 without reading legs; `--between nearest`
  recording the jolt it leaves; same foot detection on back loops reading "RRL" (twice) and alternating
  "RL" round the ring, dipping under `LIFT_MIN` without reaching `DOWN_MAX` reading one step and reaching
  `DOWN_MAX` reading two steps, front, front diagonal, side, and no view not read, one half stepping not
  read, the clip that steps the same foot twice every cycle reading 29 of its 49 cuts so and 20 with their
  seam inside a step, the alternating clip reading 33 cuts alternating and 16 with their seam inside a
  step, a cut shorter than the cycle whose seam joins two right lifts reads, as a loop, right then left
  (alternating), and a clip holding just that cut counts it apart (`seam_in_step` 1, neither alternating
  nor the same foot), and a cut 1.5 cycles long joining a step to the same foot's next; a loop whose
  beat still breaks and a loop that steps the same foot twice carrying reasons `broken-beat` and
  `same-foot` with remedies (`--between nearest` and a beat with no gap over being no reason);
  integration through `video-loop` and `video-cycle-align` recording `clip_feet`, `--same-foot refuse`
  stopping the set with strip untouched and report refused, and naming the same-foot loop "film this
  direction again (same-foot)", with `remedy` "retake" and the clip's counts. New synthetic test suite
  in `tests/video/test_inbetween_seen_through.py` uses a wide body over two legs, the near leg's outer
  8 columns cut back over 60 rows in the first source frame (a gap open to the space between the legs)
  and whole in the second; the frame made between them has that strip at alpha 0.35 in the leg's colour:
  its whole-body ghost is under 0.3 % and its hole 0, so with no window reading (3.3.0) it has no fault;
  its window reads over 1.4 %, in the legs, and it is judged `ghost`; under `--between rife` the
  alignment keeps it and names it a ghost; a band at alpha just outside 0.1 to 0.9 is not read.
  In tests/video/test_inbetween_doubled.py the frame "drawn between, smeared along the step" (the mean
  of drawings from t 0.35 to 0.65) is no longer among the clean frames: its feet are part-covered over
  4.5 % of their window, so it is now judged a ghost (smeared half as far it is not); and in
  tests/video/test_held_drawings.py and tests/video/test_ghost_retake.py, loops whose frames the stand-in
  interpolator cross-fades and gives way now also carry `broken-beat` among their `retake` reasons (a
  loop drawn every frame with frames given way, a held loop squeezed past on twos, the filmed-ghost
  loops), held-drawings and filmed-ghost staying first where they apply.
  New tests in `tests/video/test_screen_jump.py` exercise screen jumps synthetically with an outlined
  body over an outlined bar 80 px wide in the leg rows, sliding by the drawing: a cut on threes whose bar
  slides 4 px a drawing except one drawing change of 20 px names that one step a jump (not the seam);
  cuts whose drawing changes are all 4 px name none on ones, twos, and threes; a cut with two 20 px
  changes names none; a cut that slides out all the way and comes back in one step at the seam names a
  seam jump; retake records and warning lines are covered for both reasons; and through `video-loop` and
  `video-cycle-align` at the cut's own length, the jump is named with the aligned loop's frames that show
  it, recorded in `strip.json`, while a cut whose changes are alike names nothing. Also
  `tests/video/test_loop_prop_seam_held.py` now expects `screen` among `cycle_align`'s keys.
- **Tests**: `tests/video/test_loop_part_pop.py` (the four bounds flip the pop; flung and stays; a pop over two steps reads as their mean; an ease through a fast bell does not pop; a fist jumping 2.5, 3.5 and 5 % passes the flow and surge readings and is named; a walk whose tail, foot and eye move across the wrap, and a run, have no pop; an ordinary walk is cut to the bytes it was; a cut given whose fist pops is named, `retake` `recut-or-retake`, `--unfixed refuse` refuses, and under `warn` it is the bytes it was; a clip whose fist drifts through every cut keeps the search's cut to the bytes it was, `retake` `retake`; a clip no part pops in is cut to the bytes it was).

## v3.3.0 - PyTorch RIFE backend for CUDA and CPU, executor for a GPU on another machine, and Linux CPU and cloud T4 release lanes

**Upgrading from 3.2.0: Defaults stay unchanged, cache keys update once, reports show the backend, install checks model hashes, and schemas are unchanged.** With `SPRITE_GEN_RIFE_BACKEND` unset (the default), RIFE is the `rife-ncnn-vulkan` binary as before. Its frames are the same bytes as 3.2.0. Measured: the binary's frames and sixteen-pass readings through the changed engine equal the old engine's, byte for byte. A `--between-cache` directory kept by 3.2.0 is made again once. The key now names the backend (`backend: "ncnn"`). The engine's `rife.py` changed, and its sha256 is part of the key anyway. Reports take `interpolator.kind` from what made the frames: `rife-ncnn-vulkan` as before for the binary, or `rife-torch` for the new backend. The overlapped wrap's and the seam bridge's records count `binary_calls` for the binary as before, or `runs` for the torch backend. `SPRITE_GEN_RIFE_EXECUTOR` (new) is unset by default, and RIFE runs in this process. `sprite-gen rife install` now checks the model's two files (`rife-v4.6/flownet.param`, `flownet.bin`) against pinned SHA-256 after unpacking, for either backend. A model that differs installs nothing. The line a missing RIFE is reported with (`install` in records, warnings) is `sprite-gen rife install` for the binary. Where the torch backend is chosen, that line is: `pip install "sprite-gen[torch]" && sprite-gen rife install --backend torch`. No command-line flags of `video-loop` / `video-cycle-align` / `video-set` change. No restoration schemas change: `source-restoration-v5`, repair report `schema_version` 4, comparison `schema_version` 6.

- **New: RIFE's torch backend runs rife-v4.6 in PyTorch for compute GPUs without Vulkan.**
  `SPRITE_GEN_RIFE_BACKEND=torch` runs the same rife-v4.6 in PyTorch (`sprite_gen/video/rife_torch.py`). It
  serves a GPU the binary cannot run on: rife-ncnn-vulkan needs Vulkan, and a GPU offered for compute alone
  (CUDA with no Vulkan driver) has none. The network is rife-v4.6's IFNet written again from Practical-RIFE's
  `IFNet_HDv3` (MIT, notice in NOTICE). Its weights are read out of the binary's own model files (SHA-256
  pinned, the binary's float16 weights), with no other download. One run does what one call of the binary does
  with `-x -z`. The pair is turned eight ways and run both ways in time, with flows averaged at each of four
  levels. The loop follows rife-ncnn-vulkan's `RIFE::process_v4` (MIT, notice in NOTICE).
  `SPRITE_GEN_RIFE_DEVICE` names `cuda` (default) or `cpu`. `SPRITE_GEN_RIFE_TTA` means the same for both
  backends. The default augmentation stays `-x -z`. To install: `pip install "sprite-gen[torch]"` (new extra:
  `torch>=2.13.0,<3`). Then run: `sprite-gen rife install --backend torch` (new flag; defaults to
  `SPRITE_GEN_RIFE_BACKEND`). This unpacks the model, and makes a check frame through PyTorch on
  `SPRITE_GEN_RIFE_DEVICE`. It reads the model only, so a Linux ARM machine the binary does not run on is not
  refused. A CPU-only host installs PyTorch's CPU build first: `pip install torch==2.13.0 --index-url
  https://download.pytorch.org/whl/cpu` (192 MB, against the CUDA build's 527 MB plus its CUDA libraries).
  Nothing falls back. With torch chosen the binary is never looked for or run. `cuda` where PyTorch finds no
  CUDA is refused naming why (a CPU-only PyTorch, or a CUDA that finds no GPU) and does not run on the CPU.
  `mps` (Apple's GPU) is refused (no lane measures it; on a Mac the binary runs on the GPU already). A backend
  or device named wrong is refused. A device that fails as the network is put on it or as a frame runs (out of
  its memory, its driver or CUDA failing) is a RIFE that fails, in PyTorch's own words: the loop fails with
  its record, and the install's check frame fails. PyTorch not installed, or no model where the backend looks
  (`SPRITE_GEN_RIFE_MODEL`, else the install root), is RIFE not installed, as the binary missing is. Under
  `--repair auto` the loop is cut as filmed with the warning, and its record names the torch install line. On
  CUDA each frame runs with cuDNN deterministic, its benchmark off and convolutions in IEEE float32 (no TF32).
  The same frame twice is the same bytes on one GPU, driver and PyTorch. What made a frame is the RIFE's
  identity: backend, model bytes loaded, network code, PyTorch, on CUDA the GPU, driver, CUDA, cuDNN and those
  settings; on the CPU its instruction set. Because of this identity, `--between-cache` never hands one
  backend's or GPU's frame to another. Measured on synthetic walkers: Seven walker pairs (clean, outline lost,
  ghost, torn, a cross-fade to look at) get the same verdicts from the binary on an Apple GPU, the binary on
  Mesa llvmpipe and the torch backend on a CPU. The torch frame lies 0.005 to 0.052 of 255 on average from the
  binary's Apple GPU frame. The binary's own llvmpipe frame lies 0.007 to 0.25. A 960 x 960 frame of a
  synthetic walker with `-x -z`: On an Apple M4 Max CPU (12 threads), 3.3 to 3.6 s; the binary on that Mac's
  GPU takes 1.27 to 1.37 s. In a Modal container of two CPUs (the app's job size), 22.6 to 28.5 s on two
  threads; the binary on Mesa llvmpipe in the same container takes 127.7 to 131.5 s. On an NVIDIA T4 rented
  from Modal (CUDA), 1.36 to 1.41 s. The torch backend's frames are not the binary's bytes, which is one
  reason the binary stays the default. [loop-repair](docs/loop-repair.md) section 1
- **New: a host with no GPU sends the torch backend's frames to a GPU elsewhere
  (`SPRITE_GEN_RIFE_EXECUTOR`).** The host names a send of its own, `SPRITE_GEN_RIFE_EXECUTOR=module:attr`: a
  function from a request's bytes to its answer's. It reaches `sprite_gen.video.rife_torch.serve` on the GPU's
  machine, the same sprite-gen with PyTorch and the model installed there. The engine knows nothing of what
  carries the bytes, and the host needs no PyTorch. A frame is one call (its two runs made there), and a read
  is one call (its two runs and sixteen passes, the passes reduced there to how far they disagree). A RIFE
  asks the executor what it is once, as it is made (the probe, which takes the cold start), so a `video-loop`
  makes at most 53 calls. The RIFE holds that answer for its life, and every request carries it. An executor
  that is not it any more (another GPU, driver or image) makes nothing, and the RIFE fails naming what differs
  (`gpu: Tesla T4 -> NVIDIA L4`). An answer from another identity is not handed on. So one RIFE never hands on
  frames of two GPUs, and a frame taken from `--between-cache` is the frame it would make; the next RIFE asks
  again and keys its frames anew. Refused: an executor named with the binary's backend or with
  `SPRITE_GEN_RIFE_DEVICE` (the device is the executor's), a send that cannot be imported, and an executor
  whose `rife.py` or protocol is another (run the same sprite-gen on both sides). A send that raises, an
  answer that does not read (not the format, damaged or cut short on the way, or not the frame's size) and the
  executor's own failure are each a RIFE that fails, in its words; nothing is asked again or run elsewhere.
  The host names one GPU type for the executor (no fallback list) and does not deploy it anew while a run goes
  on. Requests and answers are a compressed NumPy archive, read with `allow_pickle=False`, each member whole
  so its CRC-32 is checked before its array is read: an answer damaged on the way hands on no other pixels.
  Reports name `executor` and the executor's `device` and `gpu`; the overlapped wrap counts `runs` and
  `executor_calls`. Measured on an NVIDIA T4 rented from Modal, from a host of two CPUs without PyTorch: a 960
  x 960 `-x -z` frame of a synthetic walker takes 1.49 to 1.52 s, 0.1 to 0.2 s more than in the T4's own
  process, a read as long, and the frames and readings are the same bytes as there.
  [loop-repair](docs/loop-repair.md) section 1
- **Lane: `scripts/linux_lane.sh` tests PyTorch 2.13.0 CPU after the suite.** After the suite, PyTorch 2.13.0 CPU is installed into the same environment. It runs `sprite-gen rife install --backend torch`'s check frame. It runs every test marked `torch_rife` with `SPRITE_GEN_RIFE_DEVICE=cpu`, with none skipped. `--rife-only` runs the same. `--rife-unmeasured` lets `torch_rife` tests skip as it lets `real_rife` ones. CUDA is not measured by this lane.
- **Lane: `scripts/t4_lane.py` tests the torch backend on an NVIDIA T4, rented from Modal.** `modal run scripts/t4_lane.py` builds the checked-out commit's tree into an image as the Linux lane installs a clean clone: Python 3.14, PyTorch 2.13.0 from PyPI (its Linux wheel is the CUDA 13 build), the tree with `.[dev]`, and the model by `sprite-gen rife install --backend torch`, where the engine looks for no binary. On one T4 it makes the install's check frame on CUDA and runs every test marked `torch_rife` and not `real_rife` on CUDA, with none skipped. `--whole-suite` first runs the whole suite in that environment, PyTorch in it, as the Linux lane's RIFE-less run does. A commit passes the lanes where the Linux lane passed at it and one T4 lane run at it exited 0. [release](docs/release.md)
- **Tests**: `tests/video/test_rife_torch.py` (the eight ways undo themselves and are the single passes' eight
  turns; flows of a textured patch run alone each of the sixteen ways and turned back point alike, where a
  wrong sign, a transpose's x/y, or a backward swap puts one elsewhere; `-z` and `-x` give the same frame the
  other way in time and flipped; the install check frame's disc lands half way; the model loads strict and
  other bytes are refused by SHA-256; the same frame twice is the same bytes and the identity names what makes
  it; seven recorded synthetic walker pairs in `tests/fixtures/rife-walkers` are judged as the binary judged
  them with no binary, the frame within twice the binary's own GPU-to-GPU gap, and alike to this machine's
  binary where both run; a device that fails as the network is put on it or as a frame runs (out of its
  memory, its driver, CUDA not starting, a CUDA call deferred to its start) is a RIFE that fails in PyTorch's
  words, a TypeError there is raised as it is, a loop whose device fails as the network is put on it fails
  with its record, and the install's check frame fails; without PyTorch: torch chosen and not installed is
  RIFE not installed and the binary is never run, a loop's record names it, a backend or device named wrong is
  refused before anything is looked for, cuda where there is none is refused, a model not the pinned one is
  refused; with a runner stood in: reports name rife-torch and count runs, the loop and the alignment name the
  torch backend, and a frame kept by one runner is not taken for another); `tests/video/test_rife_executor.py`
  (a frame is one call and a read one call, and the RIFE probes once as it is made, their bytes the same
  device's in this process with this host's augmentation; every request names the engine code and the identity
  held; the host runs without PyTorch; the identity is the executor's as the RIFE was made; a T4 frame kept is
  the RIFE's own after the executor turns L4, which it then refuses to make anew, and the next RIFE misses and
  keeps the L4 frame under its own key; an executor whose GPU, driver, network code, weights, PyTorch, cuDNN
  or determinism changed makes nothing, the field named; an answer from another identity is neither handed on
  nor kept; another container of the same identity runs on, and the next RIFE takes every frame with its probe
  alone; a T4 and L4 pool is refused at the first L4 request; a frame made while this host's augmentation
  changed is not kept; other engine code or another protocol is refused before anything loads; a request that
  does not read, is pickled or was damaged on the way is answered so, in the reading's words; a send, an
  answer or an executor that fails is a RIFE that fails, in its words; an answer damaged in a member's local
  header, a compressed block, an array's header or its own bytes, or its end record, or cut short, is a RIFE
  that fails in the words of what reading it raised, which is its cause, and undamaged is the frame made here;
  each byte of the probe's, a frame's and a read's answer damaged in turn fails the RIFE or reads as it was
  made, nothing else raised and no other pixels handed on; a reading that cannot read what `pack` writes
  raises as it is, a fault of the code; an answer that is not bytes does not read; an executor named with the
  binary, with a device, or not there is refused and the binary never runs; a loop names the executor, and a
  loop whose executor's answer was damaged on the way fails with its record (`failed`, in the decompressor's
  words, the gait fallback's frames removed); marked `torch_rife`: `rife_torch.serve` makes the frame and the
  read of the backend in this process, loaded once); `tests/video/test_rife_install.py` (a zip whose model is
  not the pinned one installs nothing for both backends; `--backend torch` installs on a machine the binary
  does not run on and checks its frame through torch); `tests/release/test_lane_rife_collection.py` (a test
  RIFE decides carries `real_rife` or `torch_rife`); `tests/release/test_t4_lane.py` (the T4 lane installs the
  PyTorch and the Python the Linux lane does); new pytest mark `torch_rife` (skipped where PyTorch, the model
  or the named device is missing).

## v3.2.0 - Cycle alignment hole and doubled faults, and loop repair for size held cuts

**Upgrading from 3.1.0: `video-cycle-align` now gives way to the nearer source frame on made frames with enclosed limb holes under `--between auto`, changing only alignment outputs with such holes while made frames without them and all outputs from `video-loop`, `video-loop-compare`, and `video-loop-repair` keep their bytes.**

- **New: `video-cycle-align` now reads a new `hole` fault on frames RIFE makes between source frames and gives
  way to the nearer source frame.** On every frame RIFE makes between two source frames, `video-cycle-align`
  reads a new fault, `hole` ("a hole through a limb"). Under `--between auto` (the default) a made frame with
  this fault gives way to the nearer source frame, like the other faults (`smear`, `outline`, `ghost`); under
  `--between rife` the frame is kept and named in the warnings. `video-set --align-cycles auto` runs the same
  alignment. RIFE runs a frame's colour and its coverage as two separate runs. Where a step opens a gap
  between the legs in one source frame and not the other, the coverage run can carry that gap into a leg the
  colour run keeps whole: the made frame shows a transparent band through the leg with the leg's fill on both
  sides. Nothing is added (no `smear`), there is no part coverage (no `ghost`), and the leg's own fill is the
  only line round the band. The fault `outline` needs both outline readings: the dark line (`outline_loss`)
  counts a fill darker than 70/255 (dark tights, a boot) as outline, and the rank reading (`outline_shift`)
  reads the band's edge as lost only where it is 50/255 lighter than the source frames' lines at the same
  rank, which light lines elsewhere on the art (fur, hair drawn without a dark line) can hide; so such a frame
  passes every fault and is delivered. The reading `rife.hole(made, a, b, t)` measures pixels under solid
  (alpha under 0.5) that the outside of the picture cannot reach without crossing its solid body (enclosed
  holes). A pixel of a made frame's hole is the source frames' own when either source frame is under solid
  within 4 px (`rife.HOLE_REACH`, a square neighbourhood) of where that pixel lay at that frame's time, read
  in place (the background, still, and a gap kept, or opened by a step where the frame that shows it open has
  it) and carried with the whole figure along the motion the two source frames show for the whole figure.
  Where a made frame sits at fraction `t` of the way from source frame a to source frame b, a pixel of it lay
  in a at `t` of the motion back and lies in b at `1 - t` of it on (rounded so the two are the whole motion
  apart). The motion is measured only from the two source frames, never from the made frame and never from
  RIFE's own flow: over the box round all three frames' solid bodies, each pixel is read in a where it lay and
  in b where it lies for a candidate motion; the motion whose mean absolute difference of premultiplied colour
  and coverage over the box is least is the motion there, ties going to the smaller motion. It is followed up
  to 20 % of the body's height each way (`rife.HOLE_TRAVEL`, 0.2), coarse to fine: the whole range on a level
  pooled in square blocks of a power of two, the smallest for which the range is at most 24 px each way
  (`rife.HOLE_COARSE`, 24), then within 2 px of twice the coarser motion on each finer level, down to the
  pixel. A hole those two readings leave is the source frames' own, whatever carried it (a part moving over a
  body that stays, plain or patterned, turning, growing, sheared or swinging), where it is one hole of each
  source frame. A hole is an 8-connected piece of a frame's enclosed pixels: the made frame's count where they
  keep a pixel opened by a 5 px square, a source frame's where they keep one opened by a 3 px square
  (`rife.HOLE_SOURCE_THICK`, 3). Each is read for its area, its centroid, its shape (the logarithms of its
  pixels' two principal second moments, each plus 1/12, over its area, which turning and growing keep) and the
  colour round it (the mean premultiplied colour of the frame's solid pixels 5 to 8 px out). The two source
  frames' holes: areas within 4 times of each other (`rife.HOLE_PAIR_AREA`, 4.0), shapes within 2 times on
  each moment (`rife.HOLE_PAIR_SHAPE`, 2.0), colours within 0.12 on each channel (`rife.HOLE_COLOUR`, 0.12),
  centroids at most sqrt(2) times the reach and 4 px apart. The made frame's hole: area within a quarter of
  their range (`rife.HOLE_AREA`, 0.25), shape within 1.3 times of theirs (`rife.HOLE_SHAPE`, 1.3), colour
  within 0.12 of each, and centroid within 4 px and 0.1 of their distance (`rife.HOLE_PATH`, 0.1) of the point
  at `t` on the line between theirs. Pairs are taken one to one: no hole of any frame is in two pairs taken,
  so each source frame's hole explains one of the holes paired. A hole of the made frame that the first two
  readings give the source frames takes no pair, being theirs already, and the pairing reads only the holes
  those readings leave: of the sets of pairs that can be taken together, the set taken pairs the most holes,
  and of those, has the least summed cost (the same frames always giving the same set), so alike holes moving
  together each keep their own two. Pairs sharing a hole form a group, read apart: an exact search first asks
  whether one set pairs every hole of the group, cutting branches where the holes left cannot each keep a hole
  of each source frame of their own; where it finds one, every hole of the group is paired, and where there is
  none, a second exact search finds the set pairing the most. Each search takes at most 5000 steps per group
  (`rife.HOLE_PAIR_STEPS`, 5000): where the first runs past them, the second runs; a group whose second search
  runs past them pairs none of its holes, and the frame gives way to the nearer source frame. The made frame
  is never used to measure a motion, and RIFE's flow is never read. The motion is read only on frames that
  keep a hole after the in-place reading, and the pairing only on frames that keep one after the whole
  figure's motion; a frame with no hole in place is read with no further cost. The regions at least 5 px thick
  (`rife.HOLE_THICK`, opened by a 5 px square, so a pinhole or an antialiased crevice a pixel or two wide is
  not counted) are taken after each step, as a fraction of the made frame's solid pixels, or 0 where the frame
  has no solid body. The fault `hole` is any reading over 0 (`interpolation_quality.HOLE_WARN`, 0.0). A band
  cut through a leg's edge, reaching the outside, is not an enclosed hole and is left to the outline readings
  as before.
- Report fields: each row of a loop's `smear` list in the `video-cycle-align` report (and in `cycle_align` in `strip.json`) gains `hole`. A frame given way for it carries `faults: ["hole"]` and `method: "nearest"`, and gets a line in `warnings`; the line says the frame opened a transparent hole inside its outline over the given share of its body, at least 5 px thick, where neither source frame beside it has one, a gap through a limb, and that the nearer source frame was taken there. The `--between` help says the same.
- Python callers: new `rife.hole(made, a, b, t)`, `rife.HOLE_TRAVEL` (0.2), `rife.HOLE_COARSE` (24), `rife.HOLE_REACH` (4), `rife.HOLE_THICK` (5), `rife.HOLE_SOURCE_THICK` (3), `rife.HOLE_PAIR_AREA` (4.0), `rife.HOLE_PAIR_SHAPE` (2.0), `rife.HOLE_AREA` (0.25), `rife.HOLE_SHAPE` (1.3), `rife.HOLE_PATH` (0.1), `rife.HOLE_COLOUR` (0.12), and `rife.HOLE_PAIR_STEPS` (5000); new `interpolation_quality.HOLE_WARN` (0.0); `interpolation_quality.faults(measure)` returns `"hole"` where the measure carries a `hole` reading over `HOLE_WARN`, and measures without a `hole` key are judged as before.
- Output changes: an alignment whose made frame has an enclosed hole 5 px thick or more that neither source
  frame beside it has within 4 px, in place, carried by the whole figure's motion, or paired with one hole of
  each source frame as above, now delivers the nearer source frame at that cell instead of the made frame, and
  counts it in `replaced` and `nearest_at` rather than `made_by_rife` and `made_at`. Made frames without such
  a hole, and frames whose holes the source frames have, in place, carried or paired, are delivered as in
  3.1.0 with the same bytes; the report gains `hole` on every made frame. Synthetic numbers (the figure of the
  tests below): the frame between with a band 8 px wide and 40 px tall through its far leg reads
  `outline_loss` 0, `outline_shift` 11.3 % (so no `outline` fault, which needs both over 5 %), `ghost` 0, and
  `hole` 2.69 %; in 3.1.0 it is kept, now the nearer source frame is taken. The frame drawn between, the same
  with the arm's gap moved 3 px, and the same with a pinhole 3 px wide through the leg read `hole` 0 and are
  kept. A frame whose hole both source frames have, carried by a part (moving over a body that stays, plain or
  patterned, turning, growing, sheared or swinging within reach), is delivered as in 3.1.0 with the same
  bytes. Figure 5: a body that stays, patterned, with a part on it and a hole through the part's middle, the
  part moved from one source frame to the next, each frame between drawn exactly at its place. (i) The
  checkpoint validator's frame: a body 200 by 180 px with stripes 3 px wide every 6 px (a lighter colour by 40
  levels), on a 260 by 220 px canvas, a part 24 px square with a hole 10 px square, moved 32 px across: the
  in-place reading leaves the hole whole, 100 pixels or 0.28 % of the body, and the whole figure's motion is
  0; it is one hole of each source frame, alike and on its way, so it is paired: `hole` 0, and both made cells
  are kept as made, byte for byte. (ii) A body 240 by 260 px on a 300 by 300 px canvas in five patterns
  (stripes 3, 2 and 6 px wide every 6, 4 and 12 px running up and down; stripes 3 px wide every 6 px running
  across; checks 4 px square) at contrasts 15, 40 and 120, parts 24, 40 and 60 px square with holes 10, 17 and
  25 px square: moved 32 and 48 px across and diagonally at t 0.5 (the moves listed in the test), these
  frames, and the same three parts moved 16, 32 and 48 px across and diagonally at t 0.25 and 0.75 over the 3
  px stripes at contrast 40, every one read `hole` 0. Resampled to eight frames (t 0.25, 0.5 and 0.75 each
  way), parts of 24, 40 and 60 px moved 32 px diagonally and the 24 px part moved 48 px over stripes at
  contrast 120 keep every made frame, byte for byte. Figure 6: a torso 120 by 260 px that stays, patterned in
  blocks 4 px square each lighter by its own whole number up to 200 levels, with an arm beside its right edge:
  the upper arm and the forearm lie along that edge and are joined by the arm's outer side, so the gap between
  arm and torso, 16 by 12 px, has the torso's edge on its left and the arm on its other three sides; the arm
  reaches 48 px above and 52 px below the gap and moves 40 px down from one source frame to the next. The gap
  lies where the arm covers it in the other source frame, so the in-place reading leaves it, and the whole
  figure's motion is 0; it is one gap of each source frame, alike and on the arm's way, so it is paired:
  `hole` 0 at t 0.25, 0.5 and 0.75, and resampled to eight every made frame is kept, byte for byte. A frame
  with a gap only one source frame opens, on a part moving over a body that stays, plain or patterned, which
  neither the in-place reading nor the whole figure's motion carries and which has no hole in the other source
  frame to pair with, gives way to the nearer source frame where 3.1.0 kept it. The boundary, pinned by a
  test: a part 30 px square moving 32 px across a body that stays, a hole 14 px square open through it in one
  source frame and closed in the other, 8 px square in the frame between (half open): over a plain body and
  over the 3 px stripes alike it reads `hole` 0.10 % (its 64 pixels) and both made cells are given way to the
  nearer source frame, with `faults: ["hole"]`. Figure 7: the plate of the tests (a body 240 by 260 px that
  stays, striped or plain, with a plate on it and a gap through the plate, 11 by 31 px, each frame between
  drawn exactly at its place). The third checkpoint validator's frame, the plate turning 60 degrees as it
  moves 48 px over the stripes, reads `hole` 0 and is delivered as in 3.1.0, both made cells byte for byte,
  resampled to four and to eight; so are the plate turned 90 degrees, grown 2 times and sheared by 0.6 as it
  moves 48 px, and swung 45 degrees, over the stripes, and 125 such frames read `hole` 0. A frame between with
  a hole unlike both source frames' (the gap drawn 16 by 40 px: `hole` 1.04 %; drawn 18 by 18 px: 0.52 %; the
  plate growing 2.2 times as it moves 48 px, the source frames' gaps more than 4 times apart in area: 0.55 %),
  off its way (the plate drawn 12 px across it: 0.55 %; drawn where it is at t 0.8 in a frame made at t 0.5:
  0.30 %), or out of reach (the plate swung 60 degrees, its centre moving 100 px, where 77.5 px is let through
  on this body: 0.55 %; the plate moving 80 px: 0.55 %) gives way to the nearer source frame with `hole` among
  its faults. Figure 8: the plate 120 by 40 px with a row of four holes 8 px square, 24 px apart, moving 32 px
  down: the frame between drawn exactly, and with the row a pixel taller, read `hole` 0 and are delivered as
  in 3.1.0; with one hole more (midway between two, between the last two, past the end, or one of the row
  moved to a midway) it reads 0.10 % and gives way to the nearer source frame. Figure 9: on the same striped
  body, a plate with a row of two, three or four holes 8 px square, 12 px apart, moving on a curved way,
  int(40 t + 20 t (1 - t)) px across and int(32 t) px down at time t, the plate moving with the holes or
  staying while they move through it (with two holes it is the fourth checkpoint validator's frame): the frame
  between drawn exactly reads `hole` 0 and is delivered as in 3.1.0, both made cells byte for byte, for two,
  three and four holes, the plate moving or staying; with one hole more before the row, or with the row's last
  hole shut in the second source frame, it reads 0.10 % and gives way to the nearer source frame. Figure 10:
  on the same striped body, a plate 40 by 90 px with a column of three holes 8 px square, 24 px apart, moving
  32 px down, the middle hole shut in the second source frame: the frame between drawn exactly reads `hole` 0
  and is delivered as in 3.1.0: its middle hole, one more than the second source frame has, lies against holes
  of both source frames and the in-place reading takes it for theirs; the last hole pairs with its own two.
  Figure 11: on the same striped body, a plate 105 px square with a hole 8 px square that the first source
  frame and the frame between have in place and the second source frame shuts, beside a hole moving 52 px
  across and 48 px down, drawn at its exact midpoint 10 px from the first (the fifth checkpoint validator's
  frame): the frame between reads `hole` 0 and is delivered as in 3.1.0, both made cells byte for byte, and so
  with the hole in place kept by the second source frame instead. Figure 12: a body 200 by 260 px outlined in
  black on a 400 by 400 px canvas, moving whole, with holes through it; what the reading does not tell: the
  body moving 32 px down with a row of four holes 8 px square, 24 px apart, and one hole more midway between
  the middle two reads `hole` 0 and is delivered as in 3.1.0 (so is figure 8's row standing still, drawn a
  pixel taller, with one hole more midway); moving 48 px across and 48 px down with a gap 5 by 30 px and one
  gap more 10 px beside it in the frame between, it reads `hole` 0 and gives way for `outline`, as in 3.1.0;
  with the gap more 11 px beside, and a gap 8 px wide with its twin 13 px beside it, they read `hole` 0.29 %
  and 0.45 % and give way as in 3.1.0, now with `hole` among their faults besides `outline`. The jump repair
  of `video-loop`, its eased retime and overlapped wrap, `video-loop-compare`, and `video-loop-repair` do not
  read `hole`; their measures and outputs are as in 3.1.0. A `--between-cache` directory written by 3.1.0 is
  not taken from by this version: the code of `rife.py` is part of what a kept frame is keyed by, so those
  frames are made again.
- **Tests**: new `tests/video/test_inbetween_hole.py`, synthetic only: figure 1 (a 160 by 200 px figure of
  rectangles outlined in black, legs filled darker than 70/255, the near leg drawn over the far one, an arm
  joined to the body above and below with an 8 px gap between them in every frame; one source frame with its
  legs side by side, the other with a gap between them) checks that the band through the far leg reads as a
  hole over exactly its own 320 pixels as a share of the solid body, and the drawn frame, the arm's gap, that
  gap moved 3 px, a 3 px pinhole and an empty frame read 0; that the readings before this one pass the band
  (`outline_loss` 0, `outline_shift` over 5 %, `dark_excess` below 0, `ghost` 0, `partial_excess` 0, no fault)
  and that with `hole` the fault is `["hole"]`; that resampling the two frames to four under `--between auto`
  takes the nearer source frame at both made cells (cells 1 and 3) with the fault named in the warning words,
  while `--between rife` keeps them named; and that the clean frame and the frame with the moved arm gap are
  delivered as the very frame made. Figure 2 (a body 120 by 180 px of one colour with a gap 8 by 40 px through
  it, on a 240 by 220 px canvas, moved whole from one source frame to the next by 0, 4, 8, 12, 24 and 32 px,
  across and diagonally) checks that at every move it is resampled to four with both made cells delivered as
  the very frame made, no fault, and reads `hole` 0, each source frame moved half the way giving the frame
  between byte for byte. Figure 3 (a body 90 px wide over two legs 24 px wide, each standing in a boot that
  reaches under the other leg, so a gap a step opens between the legs is closed by the boots below; in one
  source frame the legs touch, in the other they are parted), still and moving 30 px between source frames, is
  kept the same way and reads 0; and moving with an 8 by 40 px band through its body, it reads exactly its 320
  pixels as a share of the solid body, in either order of the source frames, with both made cells given way to
  the nearer source frame and `hole` among their faults. Figure 4 (a body 200 by 180 px of one colour that
  stays, with a part on it 40 px square and a hole 14 px square through the part, the part moving 32 px from
  one source frame to the next) is kept the same way and reads 0. Figure 5 (a body that stays, patterned, with
  a part on it and a hole through the part's middle, the part moved from one source frame to the next, each
  frame between drawn exactly at its place: the checkpoint validator's frame with a body 200 by 180 px with
  stripes 3 px wide every 6 px, a lighter colour by 40 levels, on a 260 by 220 px canvas, a part 24 px square
  with a hole 10 px square, moved 32 px across; and a body 240 by 260 px on a 300 by 300 px canvas in five
  patterns, stripes 3, 2 and 6 px wide every 6, 4 and 12 px running up and down, stripes 3 px wide every 6 px
  running across, and checks 4 px square, at contrasts 15, 40 and 120, parts 24, 40 and 60 px square with
  holes 10, 17 and 25 px square): figure 5's validator frame resampled to four, and its parts of 24, 40 and 60
  px moved 32 px diagonally and the 24 px part moved 48 px over stripes at contrast 120 resampled to eight,
  keep every made frame as made; the wide grid of figure 5 (every pattern and contrast at each listed move at
  t 0.5, and the three parts moved 16, 32 and 48 px at t 0.25 and 0.75) reads `hole` 0 and is marked slow, so
  it runs with `pytest --full`. Figure 6 (a torso 120 by 260 px that stays, patterned in blocks 4 px square
  each lighter by its own whole number up to 200 levels, with an arm beside its right edge: the upper arm and
  the forearm lie along that edge and are joined by the arm's outer side, so the gap between arm and torso, 16
  by 12 px, has the torso's edge on its left and the arm on its other three sides; the arm reaches 48 px above
  and 52 px below the gap and moves 40 px down from one source frame to the next) reads `hole` 0 at t 0.25,
  0.5 and 0.75 and keeps every made frame resampled to eight. The boundary test (a part 30 px square moving 32
  px across a body that stays, a hole 14 px square open through it in one source frame and closed in the
  other, 8 px square in the frame between) checks that the half-open gap on the moving part, over a plain body
  and over the stripes, reads its 64 pixels as a share of the body and gives both made cells way to the nearer
  source frame with `faults: ["hole"]`. Figure 7: a body 240 by 260 px on a 300 by 300 px canvas that stays,
  in stripes 3 px wide every 6 px (lighter by 40 levels) or plain, with a plate on it (|u| < 24 and |v| < 28
  px in its own axes) and a gap through the plate (|u| < 6, |v| < 16), each frame between drawn exactly at its
  place in one motion: the plate's centre moving across as the plate turns, grows or is sheared about it, or
  the plate swinging from a point 100 px above its centre. One test checks that the third checkpoint
  validator's frame (the plate turning 60 degrees as it moves 48 px over the stripes), resampled to four,
  keeps both made frames as made, and resampled to eight (t 0.25, 0.5 and 0.75 each way) every one; that the
  plate turned 90 degrees, grown 2 times and sheared by 0.6 as it moves 48 px, and swung 45 degrees, resampled
  to four over the stripes, keep both made frames; and that over the plain body each of these reads `hole` 0.
  A test marked slow (it runs with `pytest --full`) checks that 125 frames read `hole` 0: the plate turned 0
  to 90 degrees, grown 1.1 to 2 times or sheared by 0.15 to 0.6 as it moves 0, 24 or 48 px, or swung 10 to 45
  degrees, over both bodies at t 0.5, and turned as it moves over the stripes at t 0.25. A boundary test
  checks that, on the plate over either body, a frame between with its gap drawn 16 by 40 px or 18 by 18 px,
  with the plate drawn 12 px across its way or where it is at t 0.8 in a frame made at t 0.5, with the plate
  growing 2.2 times as it moves 48 px (the source frames' gaps more than 4 times apart in area), with the
  plate moving 80 px, and with the plate swung 60 degrees, gives both made cells way to the nearer source
  frame with `hole` among their faults. Figure 8: the same body with a plate 120 by 40 px on it and a row of
  four holes 8 px square, 24 px apart, through the plate. Its test checks that, the plate moving 32 px down
  over either body, the frame between drawn exactly and the same with the row a pixel taller keep both made
  frames as made; that with one hole more midway between two of the row's, between the last two, past the
  row's end, or with one of the row's moved 12 px to a midway, both made cells give way to the nearer source
  frame with `hole` among their faults; and that, the plate standing still, the row drawn a pixel taller keeps
  both made frames. Figure 9: the striped body (240 by 260 px that stays, stripes 3 px wide every 6 px) with a
  plate on it and a row of two, three or four holes 8 px square, 12 px apart, through the plate, moving on a
  curved way from one source frame to the next, int(40 t + 20 t (1 - t)) px across and int(32 t) px down at
  time t; the plate moves with the holes or stays while they move through it. With two holes it is the fourth
  checkpoint validator's frame. One test checks that, resampled to four, the frame between drawn exactly keeps
  both made frames as made, for two, three and four holes, the plate moving or staying. Figure 10: the same
  striped body with a plate 40 by 90 px on it and a column of three holes 8 px square, 24 px apart, through
  the plate, the plate moving 32 px down from one source frame to the next. A boundary test checks that, with
  two and four holes, the plate moving or staying, a frame between with one hole more before the row, and a
  frame between whose second source frame has the row's last hole shut, give both made cells way to the nearer
  source frame with `hole` among their faults; and that with `rife.HOLE_PAIR_STEPS` set to one step, both
  searches (the search for a set pairing every hole, and the search for the set pairing the most) run past it
  and the frame between of two holes gives both made cells way as well. Figure 11: the striped body with a
  plate 105 px square on it, through it a hole 8 px square that the first source frame and the frame between
  have in place and the second source frame shuts, beside a hole moving 52 px across and 48 px down, drawn at
  its exact midpoint 10 px from the first; the fifth checkpoint validator's frame. Its test checks that,
  resampled to four, both made frames are kept as made, and so with the hole in place kept by the second
  source frame instead. Figure 12: a body 200 by 260 px outlined in black on a 400 by 400 px canvas, moving
  whole, with holes through it. Its test pins what the reading does not tell: it checks that the body moving
  32 px down with a row of four holes 8 px square, 24 px apart, and one hole more midway between the middle
  two keeps both made frames as made, and so do figure 8's row standing still, drawn a pixel taller, with one
  hole more midway, and figure 10's frame with the middle hole shut in the second source frame; that, the body
  moving 48 px across and 48 px down with a gap 5 by 30 px, one gap more 10 px beside it in the frame between
  reads `hole` 0 from either source frame to the other; and that the gap more 11 px beside, and a gap 8 px
  wide with its twin 13 px beside it, give both made cells way with `hole` among their faults. The test on
  triples alone: twenty alike holes, each with its own two holes and up to five more pairs near its own at
  seeded random costs. It checks that all twenty are paired, and that none is where the search for a set
  pairing every hole runs past the steps (the search for the set pairing the most runs past them on this
  group).

**Upgrading from 3.1.0: `video-cycle-align` now gives way to the nearer source frame on made frames with double outlines under `--between auto`, changing only alignment outputs with such outlines while made frames without them and all outputs from `video-loop`, `video-loop-compare`, and `video-loop-repair` keep their bytes.**

- **New: `video-cycle-align` now reads a new `doubled` fault on frames RIFE makes between source frames and
  gives way to the nearer source frame.** On every frame RIFE makes between two source frames,
  `video-cycle-align` reads a new fault, `doubled` ("a double outline"). Under `--between auto` (the default)
  a made frame with this fault gives way to the nearer source frame; under `--between rife` the frame is kept
  and named. `video-set --align-cycles auto` runs the same alignment. Where RIFE's flow does not follow a line
  (the top of a boot one source frame tilts and the other plants), it can fade the line out in place instead
  of moving it, and the made frame shows that source frame's line, lighter, in its own place, beside the line
  the frame drew: a double outline. Its coverage is whole (no `ghost`), nothing darker is added (no `smear`),
  the line is inside the body and the edge keeps its line (no `outline`), and there is no hole; the cross-fade
  reading (`crossfade`) only names frames to look at. So such a frame passed every fault and was delivered.
  The reading `rife.doubled(made, a, b)`: a frame's line is its solid pixels darker by more than 50/255 than
  its own luma closed by a square 2.5 % of the made frame's body height wide (`rife.DOUBLED_WIDE`, 0.025; a
  stroke narrower than that is a line, a dark fill wider is not). A pixel of one source frame's line is left
  at part strength where the three frames are solid, the other source frame draws no line within 4 px
  (`rife.DOUBLED_SHARED`, 4) and is lighter there by more than 50/255, and the made frame is darker than the
  other by more than 50/255 (still seen) but by no more than 0.8 of the line's contrast (`rife.DOUBLED_PART`,
  0.8), there and at its darkest pixel within 2 px (no line at full strength beside it). Such pixels count in
  regions 3 px thick (`rife.DOUBLED_THICK`, 3); widened by 3 px, those that touch make one run; the reading is
  the longest run's length (sqrt(12) times the root of its pixels' larger second moment) over the made frame's
  body height. The fault `doubled` is a reading over 0.03 (`interpolation_quality.DOUBLED_WARN`). Kept as the
  source frames' own: a line both draw within 4 px of each other, at any strength; a line carried at more than
  0.8 of its contrast (the source frame's own drawing); the soft edge of a line the flow moved (the full line
  lies within 2 px); a line left less than 50/255 darker than the other source frame there (not seen); a dark
  patch wider than the closing; a run of 3 % of the body or less. A line only one source frame draws (a crease
  that comes or goes), left at part strength, is a double outline too.
- Report fields: each row of a loop's `smear` list in the `video-cycle-align` report (and in `cycle_align` in `strip.json`) gains `doubled`. A frame given way for it carries `"doubled"` in `faults` and `method: "nearest"`, and gets a line in `warnings` with these words: "left a line one source frame beside it draws at part strength in its own place, over a run 10.21 % of its body high, a double outline, so the nearer source frame was taken there (--between auto)". The `--between` help names it.
- Python callers: new `rife.doubled(made, a, b)`, `rife.DOUBLED_WIDE` (0.025), `rife.DOUBLED_SHARED` (4), `rife.DOUBLED_PART` (0.8), `rife.DOUBLED_THICK` (3); new `interpolation_quality.DOUBLED_WARN` (0.03); `interpolation_quality.faults(measure)` returns `"doubled"` where the measure carries a `doubled` reading over `DOUBLED_WARN`, and measures without a `doubled` key are judged as before.
- Output changes: an alignment whose made frame shows a double outline over 3 % of its body height now delivers the nearer source frame at that cell instead, counted in `replaced` and `nearest_at` rather than `made_by_rife` and `made_at`. Other made frames are delivered as before with the same bytes; the report gains `doubled` on every made frame. A frame RIFE cross-fades so that both drawings' lines stay at part strength inside the silhouette over a long enough run now gives way too, still named to look at for its cross-fade (`look`); such frames were kept before because the cross-fade reading is no fault (it also reads over its line on a clean drawing between far-apart drawings, which reads no double outline and is kept). Synthetic numbers (the figure of the tests): a coat over two legs in boots outlined 3 px in near-black, the near boot lifted 18 px in one source frame and planted in the other; the frame drawn between with the first source frame's near-boot lines laid over it at 0.45 of their strength reads `outline_loss` 0, `outline_shift` 0, `ghost` 0, `hole` 0, `crossfade` 0 and `doubled` 0.102; in 3.1.0 it is kept, now the nearer source frame is taken. The frame drawn between, blurred, or smeared along the step, a hand moving 60 px over the coat, boots drawn with a second line, a hem drawn with two lines and softened, and a light crease carried at its own strength read `doubled` 0 and are kept. The jump repair of `video-loop`, its eased retime and overlapped wrap, `video-loop-compare`, and `video-loop-repair` do not read `doubled`; their measures and outputs are as before. A `--between-cache` directory written before this version is not taken from: the code of `rife.py` is part of what a kept frame is keyed by, so those frames are made again.
- **Tests**: new `tests/video/test_inbetween_doubled.py`, synthetic only: the figure above; checks that the line left at 0.45 passes every earlier reading and reads `doubled` 0.102, as do both source frames' lines left at half and the colour blended inside the drawing's coverage; that resampling two frames to four under `--between auto` takes the nearer source frame at both made cells with the fault in the warning's words, while `--between rife` keeps them named; that a crease only one source frame draws, left at 0.3 to 0.6, gives way, left at 0.25 reads 0, and the frames that draw it or not keep their bytes; that nine clean, soft or source-drawn frames read 0 and are delivered as the very frame made; that the walker of `tests/video/test_crossfade.py` keeps its drawing between far-apart legs (16 to 3 degrees) in grey and black outlines, reading 0, while its cross-fade gives way, and its 10 to 5 degree cross-fade reads 0.0195 and is kept; and where each bound turns (seen at 0.30 of the line's contrast, not at 0.28; part strength up to 0.80, not 0.82; a boot moved 9 px, not 8; a stroke 3 px wide, not a patch 10 px square; a run over 7 columns, 0.0343, not 6, 0.0294), and that a hand smeared back over a source frame's line reads as that line left (0.063). `tests/video/test_crossfade.py`: the alignment test of the cross-fading stand-in now expects its frames across the legs' long steps to give way under `auto` as double outlines, still named to look at, and to be kept and named to look at under `--between rife`; its 10 to 5 degree cross-fade under `auto` is kept with `doubled` under the line.

**Upgrading from 3.1.0: `video-loop-repair` can now restore damaged cells on held fixed cuts by reading source frames through recorded frame holds.** Fixed cuts held with `--size-hold auto` now record per-frame holds in `size_hold.cut_frames`. A 3.1.0 report of a held fixed cut lacks this record and stays `source-processing-unsupported`; cut it again with this version to enable repair. A report of a cut that was not held restores as before.

- **New: Held fixed cuts can now be restored from source frames.** Source projection applies each cut frame's recorded move and `padding_ltrb` before scrub, small-component cleanup, optional integer body ramp, one shared integer crop, and standard resampling. Unmodified cells must reproduce exactly, and cut frames must match `source_projection` hashes; a changed move on a replaced cell is refused as `source-processing-receipt-mismatch`. Reports without `cut_frames` stay `unknown` and `source-processing-unsupported`. Malformed `cut_frames` exit nonzero without writing a candidate. Cuts with `--anchor motion`, `motion-auto`, cycle alignment, wrap blends, or follow warps remain unprojected.
- Report fields (`<name>.loop.report.json`): `size_hold` records `cut_frames` on held fixed cuts, giving an ordered list of exact float entries with `scale`, `foot_point_px` (`[x, y]`), and `lift_px` (or `null`).
- Repair records: `projection` in repair reports and candidate `restoration` receipts gain `size_hold` (`padding_ltrb`, `cut_frames`) on held cuts, binding proposal IDs to recorded moves.
- Python callers: new `gait_fallback.frame_hold(k, measured, lift=None)` returns a frame's move dict, and `gait_fallback.hold_frame(frame, move, pad)` applies it to one frame. `gait_fallback.undo_scale` uses both and preserves previous byte outputs.
- Output changes: on a synthetic walk away (15.0 % smaller over 73 frames, feet 20 px higher) cut on frames 24 to 47 with `--anchor body`, projection reproduces 24 of 24 cells. Restoring smeared cell 9 (25 pixels) yields verdict `improved` and recovers the cell byte for byte: 119 px tall with feet on row 129 (held loop cells run 119 to 120 px tall, feet on rows 129 to 130; cut with `--size-hold off`, cell 9 is 111 px tall on row 120, and the loop spans 107 to 113 px tall, feet on rows 116 to 122). In 3.1.0 this request ended `unknown`, `source-processing-unsupported` with no candidate. For six synthetic cuts (174 files), `video-loop` matches 3.1.0 except `producer` and `size_hold.cut_frames`. For five non-held cuts (110 files), `video-loop-repair` matches 3.1.0 except `measurement_engine` and candidate report digests.
- **Tests**: `tests/video/test_held_cut_restoration.py` checks that the synthetic walk away cut on frames 24 to 47 with `--anchor body` restores smeared cell 9 byte for byte with verdict `improved`, matching first-cell height and row while the filmed source frame is under 0.95 height and 8 px or more higher; checks exact cell reproduction; checks that reports lacking `cut_frames` stay `source-processing-unsupported`; checks that four malformed records exit nonzero with no candidate; checks that a 1 % scale alteration triggers `source-processing-receipt-mismatch`; and checks that `frame_hold` round-trips through JSON to match `undo_scale` with and without feet held.

## v3.1.0 - Walk and run loop gates for held animation and seam pops, size hold for fixed cuts, talking face layer and default chroma decontamination

**Upgrading from 3.0.0: Fixed walk or run cuts and clips whose feet climb or sink now hold size and feet before cutting, changing bytes when size changes by 1 % or more.** Application scan numbers change for held clips, and held fixed cuts are not restored by `video-loop-repair`. Loops cut with `--size-hold off`, clips changing size by under 1 %, held `motion-auto` cuts on one row, and non-walk cuts keep their bytes. The report records `size_hold` on fixed walk or run cuts, `size_hold.feet` when size is held, `size_hold.anchor_regions` under `--anchor motion`, and `size_hold.at_cut` on fixed cuts; `producer` names the new version.

- **New: Fixed cuts hold size before the cut is read.** Walk or run cuts using `--cycle fixed` with any anchor (`none`, `body`, `feet`, `motion`) now hold size over the whole clip before reading the cut. A fixed cut is held only where the cut itself shows the change, comparing each frame of the cut with the frame one cycle of the cut's length later, or, where the clip ends before that frame, with the frame one cycle earlier: the median of those ratios must show at least half (`AT_CUT_SHARE`, 0.5) of the clip's predicted change over that length, and feet move only where the cut shows them move the same way. Where the cut does not show the change, it is cut as filmed, and the report says why (`size_hold.why`, `size_hold.feet.why`). The report's `size_hold` gains `at_cut` on a fixed cut the clip reads a change on (`pairs`, `share`, `change`, `feet_px`, `expected`, `feet_expected_px`, `size`, `feet`). Seams, foot crossings (`cycle.step_count`), the gate, and application scans are read on held frames. Under `--anchor motion`, target regions are read where held (`size_hold.anchor_regions`). Passing `--size-hold off` keeps filmed frames pixel for pixel, recording `size_hold: {"applied": false, "why": "--size-hold off"}`. Automatic searches with `--anchor body`, `feet`, or `none`, `--cycle pinned`, and idle cuts are not held.
- **Changed: The size hold moves climbing or sinking feet to the first frame's row.** Where size changes by 1 % or more and feet climb or sink by 1 % of first-frame height or more (`FOOT_HOLD_MIN`), frames shift so feet stay on the first frame's row. Foot drift is read one cycle on (`gait_fallback.foot_drift`), preventing settling poses from reading as climbing feet. This stops wrap jumps from shifting into position where motion search (`motion_anchor.SEARCH_RADIUS`, 28 rows) or body anchors cannot correct them. Level clips keep their bytes. `size_hold` records `feet` (`min`: 0.01, `method`: `one-cycle-on`, `change_px`, `held`).
- **Note: What --size-hold off leaves in place.** With `--size-hold off`, `motion-auto` keeps its gait fallback: if the first search finds no cycle, size changes of 3 % or more (`SCALE_DRIFT_MIN`) are scaled back one cycle on with feet where filmed (`gait_fallback.scale_undone`). Cuts with `off` keep their previous bytes.
- **Changed: Held fixed cuts are not restored from source frames.** `video-loop-repair` reads cuts through source projection, which treats `size_hold.applied` as `source-processing-unsupported`. Recuts or scans of held fixed clips are not restored from source frames.
- **Output changes** Fixed walk or run cuts, scans, and `motion-auto` cuts of clips with climbing or sinking feet change bytes when size changes by 1 % or more. Across ten synthetic walkers cut four ways with `auto` and `off`, 62 of 80 keep their bytes. Changed cases are the fixed body cut and scan of eight walks changing size by 1 % or more (shrinking 7 % and 2 %, growing 20 % and 2.5 %, slow walk shrinking 7 %, two walks away, settling walk growing 2.5 %), plus `motion-auto` of the two walks away. On a walk away (15 % smaller, feet 20 px higher), a fixed body cut refused at seam ratio 6.83 passes held at 1.25 (size alone failed at 10.03); shrinking 2 % moves from 3.80 to 1.46; a walk whose feet climb 96 px refused under `motion-auto` at 13.03 passes held at 1.08. Scan ratios drop on the 7 % shrinking walk from 1.16 to 0.52, and on the walk away from 1.66 to 0.42. Shrinking 7 % cut by hand drops from 7.97 to 2.04 (still refused), and a slow walk shrinking 7 % from 18.31 to 4.48. The walk away under `motion-auto` moves from 0.79 to 1.41 because feet are held on row before cutting rather than closed by motion ramps. On test fixtures, a run filmed from a standing start that raises and waves its arm and crouches reads 6.9 % smaller over the clip while its cut shows 0; a walk of 26 frames cut 20 long reads 2.5 % larger over the clip while its cut shows 0; walkers that grow or shrink show 0.94 to 1.07 of predicted change at the cut; and every synthetic case gives the same bytes with this rule as without it.
- **Python callers** `gait_fallback.foot_drift(frames, measured)` returns `min`, `method`, `change_px`, `held`, and `lift` (frame shifts in px, None when not held). `gait_fallback.FOOT_HOLD_MIN` (0.01) is added. `gait_fallback.undo_padding` and `undo_scale` take `lift=None`. `gait_fallback.held_box(box, k, measured, pad, lift=None)` maps filmed frame `k` boxes to held coordinates. `gait_fallback.at_cut(frames, measured, feet, *, start, length)` is added and returns that record; `gait_fallback.AT_CUT_SHARE` (0.5) is added.
- **Tests** In `tests/video/test_gait_fallback.py`: fixed cuts of the walk away and 2 % shrinking walker pass held under 2.0 seam ratio, wrap changes matching internal steps (size-only hold fails on feet); `motion-auto` on the 96 px climbing walk passes held on row with motion anchor applied; `--size-hold off` gives filmed frames pixel for pixel and records why; stable walkers keep identical bytes under `auto` and `off` and record not held; idle fixed cuts omit `size_hold`; foot drift reads 21 px for the 20 px walk away, 0 for level walks, and 0 for settling poses (fitted lines read 2.7 px); held walks away need no top room and `held_box` aligns head boxes; `--anchor motion` regions match held positions; forward walks mark feet not held while walks away close at feet; a hand cut of the run with earlier motion and of the short walk is not held, `size_hold.at_cut` shows 0 over all pairs, the report says why, and strip and frames match `--size-hold off`; held fixed cuts show the change at the cut (`size_hold.at_cut.size`, and `feet` for the walk away); a synthetic walk away from the camera (15 % smaller by the end, feet 20 px higher), cut by hand on the last 24-frame cycle of a 72-frame clip with 0, 1 or 5 of the cut's own first frames repeated after it, and the same walk cut on its first cycle with 0, 1 or 5 of the cut's last frames placed before it, is held each time on 24 pairs, one for each frame of the cut (the repeated frames show no change against the cut), and the loop passes the seam gate. In `tests/video/test_loop_silent_pass.py`: the walk away jitter test passes `--size-hold off` so feet climb crop rows as before.

**Upgrading from 3.0.0: Bakes retain the same bytes for figures whose anatomy is unchanged. The `pixel` refusal message now names the sides and pixel counts. A figure that had `face-absent` may now have a face, with a lower rigid boundary only when that face ends below the neck. `video-sway-inspect` records use schema 3, and a cell whose every offset is 0 is now copied as it was, including colour under clear pixels with `--gain 0`; the other changes add options, and without them the strip, records and inspection stay as before.** Smooth bakes that leave the in-breath share out keep their bytes. A smooth `breathe.json` config and a breath sway's `drive` record now carry `inhale`, Python callers of `smooth_wave` and `smooth_shoulder_wave` must pass the share, and `breathe.INHALE` is gone. A smooth `breathe.json` config and a breath sway's `drive` record now also carry `start_cell` (0 when left out) and `loop_cells` (null when left out), and a record without them reads as the loop itself from its first cell.

The Breathe layer still supports `pixel` and `smooth`.

- **Changed: Pixel refusal.** `pixel` still refuses stretched content past the top, left, or right of a cell instead of cropping it.
  The message names each side and its opaque pixel count. A pixel past a corner counts on both sides.
  When an overflowing side is the drawing's own border, the message also gives the number of pixels covering that edge.
  It advises `--mode smooth` or sidecar `mode: "smooth"` for an illustration cut by its frame.
  It advises a wider cell margin or lower `depth` for a pixel sprite.
  It warns that padding, baking, and cropping back silently loses pushed off content.
  The curator preview uses the same refusal wording.
  The border reading follows the 2% rule in [video pipeline](docs/video-pipeline.md) section 3.
  See [breathing](docs/breathing.md) for behavior at frame edges.
- **New: Second eye pair search.** It runs only when the existing search finds no pair.
  Its minimum eye area is 0.2% of opaque pixels above the neck.
  Its eye width to height limit is 3.0, and its minimum eye center distance from the body axis is 0.02 of content width.
  Pair placement, separation, size similarity, and row overlap conditions remain the same.
  A face found by this search clears `face-absent`. It lowers the boundary only if the face ends below the neck.
- **Faster: Breathe bakes.** Array operations speed up pixel outline thinning, appendage weights, and row mapping.
  `smooth` solves displacement only for pixels that move.
  Anatomy and settings checks run once per row, and `sprite-gen breathe` shares one anatomy measurement between its report and bake.
  On synthetic 960 x 960 cells with Apple M1, bake time fell from 0.20 s to 0.10 s for `smooth`.
  For an outlined figure with arms in `pixel`, it fell from 0.98 s to 0.05 s.
  These measurements exclude PNG reading and writing.
  `bake_breathe_sequence(images, cfg, anat=None)` and `anatomy_report(images, cfg, anat=None)` accept measured anatomy.
  `protect(anat)` accepts one column or an array of columns.
- **New: Sway on a cycle's frames.** `video-sway --frames` moves full-size frames and writes them with `sway.json` to a new folder.
  A whole `cycle/` can supply its frame time; other damped-mass runs require `--fps`.
- **New: Sway on the breath.** `--breathe` drives the move from a checked smooth `breathe.json`, with `--lag` setting how late the part follows.
  The first cell, rests and close have zero offset.
- **New: A region moved whole.** A fifth `--region` number, `flat`, keeps the centre at full weight before the weight falls to the rim.
- **New: Across apart from down.** `--gain-across` sets the across gain separately from `--gain`, which sets the gain down.
- **Changed: Zero-offset cells.** A cell where no region moves is copied byte for byte, including colour under clear pixels.
- **Changed: Inspection seams.** `video-sway-inspect` drops a pair's seam points covered by a third moving region.
  Its record uses `schema_version` 3.
- **New: Breathe beat chosen to miss the hands.** In `smooth` mode, `--avoid-region` chooses a breath beat whose peaks keep the chest above the hands' own rise and records the choice in `breathe.json`.
- **New: The in-breath share.** The smooth sidecar key `inhale` is the share of one breath the in-breath takes; the in-breath peaks at that phase and the rest of the breath is the out-breath.
  Default 0.40, range 0.2 to 0.8 with both ends allowed; out-of-range values are refused, not clamped, and a pixel row refuses the key.
  With the key left out or at 0.40 every bake is byte-identical to before.
  With `breath_peaks`, the rest between peaks p and q is at p + (1 - inhale) x (q - p).
  `sprite-gen breathe --inhale S` sets it, and the normalized smooth config in `breathe.json` always carries it.
  `--avoid-region` scores every candidate on the row's own `inhale` curve, the curve the bake uses.
  Sway `--breathe` takes the chest curve, the late curve and the phase check at the record's share, so `breath_peaks` phases written at another share are accepted when they match.
  A record without the key reads as 0.40 and sways exactly as before, and a hand-written record for a breath made elsewhere can state its share.
  The sway record's `drive` now also carries `inhale`.
  The curator sidecar round trip keeps `inhale` as written and its range table mirrors 0.2 to 0.8.
- **New: A stretch of a loop.** A loop set has an idle loop and short pieces that leave it or join it part way through a breath, and such a piece can now be breathed and swayed on the loop's own breath instead of being padded into a sequence and cut out by hand.
  The smooth sidecar key `loop_cells` is the number of cells of the loop the row carries the breath of, an integer of 2 or more, and `start_cell` is the loop's cell the row starts at, an integer from 0 to loop_cells minus 1, default 0.
  Cell i of the row has the phase of the loop's cell (start_cell + i) mod loop_cells, the very same floating point value, the beat (`breaths`, or `breath_peaks` whose cells are cells of that loop) is the loop's, and the row's first cell is then not necessarily at rest.
  With `loop_cells` left out or null the row is its own loop, with both keys left out phases and bakes are byte identical to before, and the normalized smooth config in `breathe.json` always carries `start_cell: 0` and `loop_cells: null`.
  `sprite-gen breathe --loop-cells N --start-cell K` sets them; `--avoid-region` is refused with either, since the beat is chosen on a whole loop's frames and a stretch carries on its loop's beat.
  `sprite-gen breathe --reference FRAME.png`, in any mode, measures the row's anatomy (head line, chest, how far it rises and its place in the frame) on that frame instead of the row's first frame, normally the loop set's first cell, and `breathe.json` records `reference` with the file's `path` and `sha256`.
  Sway `--breathe` reads `start_cell` and `loop_cells` from the record, recomputes the phases with them and offsets a stretch's cell i exactly as the loop's cell (start_cell + i) mod loop_cells, from the stretch's first cell on, with nothing wound up before it.
  The fold rule and `--stretch-floor` read the loop's whole breath, so a stretch moves by the same gain as its loop and the seams between them do not jump; the record's `reach_px`, `fold` block and `least_cell` are the loop's, and `least_cell` is a cell of the loop.
  `sprite-gen video-sway --reference FRAME.png`, only with `--frames`, names the frame the regions were read on; each frame's body motion is read with it laid first, `body_px`, `body_worn_px` and `body_bob_px` come from or span it, and `sway.json` records `reference` with `path` and `sha256`.
  The sway record's `drive` now also carries `start_cell` and `loop_cells`, and a record without the keys reads as the loop itself from its first cell and sways exactly as before.
  Refused, never clamped or wrapped: either key on a pixel row, a negative or non integer `start_cell`, a `loop_cells` under 2 or non integer, `start_cell` at or above `loop_cells`, `start_cell` at or above the row's cell count with `loop_cells` left out (at the bake), `breath_peaks` whose last peak is within one cell of the end of `loop_cells`, a `--reference` of another size on either command, and `--reference` with `--loop-dir`.
  The curator sidecar round trip keeps `start_cell` and `loop_cells` as written and its range check mirrors the Python refusals.
- **Changed: Breath curve functions.** `breathe.smooth_wave(t, inhale)` and `breathe.smooth_shoulder_wave(t, lag, inhale)` now require the share.
  The module constant `breathe.INHALE` is removed; the default lives in `curation.BREATHE_SMOOTH_SHAPE["inhale"]`, and `breathe.smooth_inhale(cfg)` returns a row's share.
  `breath_beat.choose(images, lift, inhale, avoid, anchor, frame_ms)` and `breath_beat.nudged(n, k, inhale)` take it as well.
- **Tests:** `tests/effects/test_breathe.py` checks border counts, advice, smooth edge preservation, and the second face search.
  `tests/effects/test_breathe_js_mirror.py` checks curator refusal wording and byte identity with the pixel bake.
  `tests/effects/test_breathe_smooth.py` checks pixel and smooth byte goldens and shared anatomy and strain measurements, and that the in-breath takes the row's share: at share 0.42 the cells at phases 0.21 and 0.42 are byte-identical to the default row's cells at 0.20 and 0.40, `breath_peaks` rests at the share, out-of-range and pixel values are refused, and the CLI flag is honoured.
  `tests/effects/test_breathe_smooth_curator.py` checks that the sidecar round trip keeps `inhale`.
  `tests/effects/test_breathe_publish.py` checks that the stand-in bake accepts the new argument.
  `tests/video/test_sway_frames.py`, `tests/video/test_sway_breath.py`, `tests/video/test_sway_flat_across.py`, and `tests/video/test_sway_seams.py` check the Sway paths, drives, weights, gains and seams.
  `tests/video/test_sway_breath.py` also checks that the sway follows the share the record says for `breaths` and `breath_peaks`, that a record without the key sways as the default, and that the default breath sways the same bytes as before the change.
  `tests/effects/test_breathe_smooth.py` also checks that a stretch's cell i has the loop cell (start_cell + i) mod loop_cells phase for `breaths` and `breath_peaks` (inside the loop, past its close, the whole loop, and the loop turned), that a stretch has exactly the phases the pad hand step gave, that a row without the keys is its own loop, the stretch refusals, that the CLI bakes what the sidecar bake bakes, and that `--reference` measures the anatomy on that frame, bakes with it and records it.
  `tests/effects/test_breathe_smooth_curator.py` also checks that the round trip keeps `start_cell` and `loop_cells` and that rows the bake refuses are kept and reported.
  `tests/video/test_sway_breath.py` also checks, on a synthetic bust, that a 17 cell way out of a 48 cell loop of two breaths, starting at the loop's cell 13 with its body 0 to 3 px across, is offset as the loop's cells are, at the gain the loop is lowered to by the fold rule, a gain at which the 17 cells alone would not fold; that engine commands give the pad hand step's phases, offsets, fold, gain, reach, carry and cells byte for byte; that without `--reference` the carry is off by where the body lies in the first frame; that old records without the keys sway as before; and the `--reference` size and `--loop-dir` refusals.
  `tests/effects/test_breath_beat.py` checks beat choice and refusals, and that each beat candidate is read on the row's share.
  `tests/video/test_follow_stretch_floor.py` and `tests/video/test_follow_inspect.py` cover the changed inspection schema and frames options.

- **Tests**: A plain `pytest` runs a fast bundle: tests marked `slow` are deselected, the header points to
  `pytest --full`, and the summary counts deselections. `pytest --full` runs every test. The release lane,
  `scripts/linux_lane.sh`, passes `--full` to both `pytest` calls, including `-m real_rife`, so its gate still
  runs every test. `tests/release/test_fast_bundle.py` checks both calls and probes selection with and without
  `--full`. The `slow` rule in `pyproject.toml` is 1 s per case or 5 s across a function's cases on an 8-core
  M1 running the whole suite; marking a function marks every case. Five tests remain unmarked as the only fast
  callers of `video-stop`, `workflow`, `defaults`, `video-sway`, and `export-aseprite`. Three fixture-first
  tests remain unmarked because other fast tests build their fixtures. In `tests/frames/test_ui_kit.py`, the
  fast bundle keeps 16 of 512 scattered-drawing grid seeds, 8 of 256 full-size-frame band grid seeds, 9 of 256
  missing-element sets, 2 of 17 counterexample sheets, and one placement and start in each publish sweep.
  `tests/curate/test_anchor_selection.py` extracts each run once per module instead of repeating 1.5 to 2.6 s
  of setup per test; 25 of 27 affected tests are now fast. Removed
  `tests/video/test_loop_overlap.py::test_a_tail_on_its_own_beat_is_closed_at_the_wrap_with_the_clips_frames_a_cycle_apart[on]`;
  its wordless case makes the same call with the default "on". Removed
  `tests/video/test_walk_steps.py::test_cycle_align_takes_a_loop_cut_two_steps_as_one_cycle` and
  `tests/video/test_one_step_cut.py::test_cycle_align_takes_the_loop_cut_again_on_its_count`;
  `tests/video/test_loop_prop_seam_held.py::test_cycle_align_aligns_a_two_step_loop_as_the_one_cycle_it_is`
  covers alignment from recorded `steps: 2`. Merged
  `tests/video/test_loop_overlap.py::test_the_cli_passes_overlap_wrap_off_and_on_is_its_default` into
  `tests/video/test_loop_overlap.py::test_with_the_overlapped_wrap_off_nothing_is_read_for_the_wrap_and_the_gate_refuses_the_cut_as_filmed`,
  retaining both sets of assertions on one CLI cut with `--overlap-wrap off`. In a new virtual environment,
  the former suite ran 7,881 tests (7,880 passed, 1 skipped) in 21 minutes 54 seconds; `pytest --full` now
  runs 7,881, including two new release tests, and the fast bundle runs 6,555 in about 6 minutes. Package
  code, commands, and outputs are unchanged.

**Upgrading from 3.0.0: the `sprite-gen logo` text checks read a refused image once more before refusing it, and on codex they read on a fixed model and effort.** A mood or logo reading of `mismatch` or `no-text` is read again, up to `logo.MAX_READS` readings, which is 2. One reading that spells the asked text passes. An image is refused only when every reading differs. A logo now costs four to six calls: two images and two text checks, each read once or twice. Beyond a check's second reading nothing is sent again on its own. On codex the text checks no longer follow the login's `~/.codex/config.toml`. They read on `gpt-6-sol` at reasoning effort `high`, and the report names the model and effort of every reading. **`sprite-gen logo` refuses a chroma key that is one of the title's own colours, and a cut that is see-through inside its letters.** A letter colour in the key's hue is, by its pixels, a mix with the key, so the matte cuts it see-through. `--chroma-key auto` now skips a key whose hue covers 5 % or more of the title's box in the mood image, or 1 % when the brief names a colour in that hue. When both keys are the title's colours, the run is refused at the new step `key` before the lettering image is bought. After the matte, see-through pixels walled in by the lettering, farther from it than the logo's own glow reaches, refuse the logo at `matte` on 1 % of the visible lettering or more. A run that went through before with a key in the title's hue, or with a see-through cut inside its letters, is now refused at `key` or at `matte` instead of passing with `status: ok`. Runs whose title carries neither key's hue pick the same key as before.

- **A misreading is read once more before it refuses an image.** Before, each check read the image once, and one misreading refused it. A model reading stylised lettering can read the same picture two ways, so a correctly drawn title could be refused on one reading. Both checks use the one rule, `logo.read_text`. A call that fails is not asked again. With no reading before it, the check is `unverified` as before. With a misreading before it, the misreading stands and the image is refused. A refusal message names every reading, for example `the mood image reads ['IO'] and then ['I0'], not ['ION']`. docs/logo.md records a measurement on a synthetic pixel title drawn one stroke short (the Hangul syllable 쿠 drawn as 구, the middle bar of ㅋ left out). The codex reader at gpt-6-sol/high read it as 구 in 10 readings of 10 and never as 쿠, so neither one reading nor a check of two readings (0 of 5) passed the wrong picture. A second reading can only pass a wrong picture if the reader writes a stroke that is not drawn.
- `logo.report.json`: `text_check.mood` and `text_check.logo` keep the deciding reading's `verdict` and `read` and gain `max_reads` and `readings`, each reading in order with its `reading` number, `verdict` and `read` lines, or `reason` when it could not be read. `calls` lists a check's call once per reading, each with `reading` (1 or 2) and `effort` (`null` where the provider names none).
- Codex reader: the text checks run `codex exec --model gpt-6-sol -c model_reasoning_effort="high"`, named on each call, so a check's verdict does not change with the machine it runs on. Before, each machine read on its own config and the report recorded `model: null`. The values are `facing_vision.CODEX_READ_MODEL` and `facing_vision.CODEX_READ_EFFORT`. `--model` still names the images' model only. The facing check still runs on the login's config, unchanged. The xAI and OpenAI text checks read on their own fixed models, as before.
- Python callers: `logo.read_text` returns `(check, calls)`, a list of call records, instead of `(check, call)`. `facing_vision.codex_inspect` takes optional `model` and `effort` keyword arguments. Without them its command line is as before.
- **With `--chroma-key auto`, a key in the title's hue is not chosen.** `logo_matte.choose_key` rules out a key whose hue is 5 % or more of the title's box (`logo_matte.CLASH_HUED_PCT`), or 1 % (`logo_matte.NAMED_CLASH_HUED_PCT`) when `--style` or `--scene` names a colour in its hue (`logo.KEY_COLOUR_WORDS`: green, mint, lime, emerald, jade, chartreuse for green; magenta, fuchsia, pink, purple, violet, plum, orchid for magenta). A word alone never rules a key out; the pixels decide. The keys left are scored as before. When both keys are the title's colours there is no third: the run is refused at `key` (`refused_at: key`) before the lettering image is bought. The message gives each key's share and the brief's words for it and says to restyle the title without one of the two hues or to name the key to lose with `--chroma-key` (`--mood <mood.png>` skips the mood image). An explicit `--chroma-key` is kept; when it is one of the title's colours, a warning says so. Before, such a cut passed with `status: ok`, no warning, `key_hue_left` 0 and a matching text check.
- **A cut that is see-through inside its letters is refused at `matte`.** `logo_matte.see_through_inside` measures see-through pixels (coverage under 240 of 255) walled in by opaque lettering and farther from it than the logo's own glow reaches beside it. Walled in means no way out to the picture's edge except across opaque pixels: the way out starts from the transparent pixels on the edge of the trimmed logo and runs through transparent pixels and see-through ones; a see-through gap of 2 px or narrower does not count as a way, a transparent gap of any width does. A hole joined to the outside, even through one pixel, is outside; a transparent hole walled in by the letters (an O's counter, or a letter colour cut all the way to nothing) is no way out, and a see-through patch round such a hole is still counted inside. Transparent pixels are never counted, only see-through ones. The same way out decides the glow's reach (`glow_reach_px`): the 99th percentile of how far the see-through pixels that have a way out to the picture's edge lie from the opaque lettering, and at least 2 px. On 1 % of the visible lettering or more (`logo_matte.INSIDE_MAX_PCT`) the logo is refused before its text is read, the cut is kept as `logo.rejected.png` and the preview is written. The message says to generate the lettering again on the other key or to restyle the title without that colour. Only the logo matte's own cut (`method: logo-unmix`) is refused; a lettering image returned with its own alpha is measured, not refused. A glow fades out beside the letters and pools in a counter no farther than it reaches there, so glows are not counted.
- `logo.report.json`: `steps.key.scores.<key>` gains `named`, `clash_at_pct` and `clashes`, and `steps.key.key` is `null` when refused at `key`. `logo` gains `see_through_inside` with `px`, `pct_of_visible`, `glow_reach_px`, `max_pct` and `wide`. `refused_at` can now be `key`.
- Python callers: `logo_matte.choose_key(mood, box=None, named=None)` takes `named`, the brief's colour words per key from `logo.named_colours(style, scene)`, and returns `key: None` when both keys are the title's colours. `logo_matte.see_through_inside(image)` is new.
- **Tests**: `tests/gen/test_logo.py` checks that one matching reading is the whole check, that a misreading
  read again as asked passes at either check, that two misreadings refuse the mood image and refuse the logo
  and keep it aside with a refusal naming both, that a check reads no more than `MAX_READS` (set to 3, a third
  reading is taken), that a failed call is not asked again, that a misreading stands when the second call
  failed, and that the report names the reader model and effort of every reading. The existing refusal tests
  now script two misreadings. `tests/gen/test_vision_read.py` checks that codex reads on the named model and
  effort, passed before the image list, that docs/logo.md names both values, and that the facing check's
  command carries neither. `tests/frames/test_logo_matte.py` checks that a title in both keys' hues (synthetic
  patchwork letters: pink and plum in magenta's hue, mint in green's) gets neither key, that a colour in a
  key's hue rules the key out, that a colour the brief names rules it out at the smaller share while the same
  share unnamed and the word with no such pixels do not, that on green the mint patches are cut see-through
  and found inside (9.5 % of the visible lettering) while the other key keeps them and finds nothing, that six
  glow fixtures (a white glow, an outlined cyan glow, a cyan glow pooled in an O's counter on green and on
  magenta, an orange glow on green, pixel lettering) find nothing inside, that a mint patch walled in by a
  navy letter on green is still found inside, with the glow's reach at 2 px, when one or four squares of the
  key in it are cut to nothing, that a hole glued to the letter's edge is inside while sealed and outside once
  one pixel joins it to the key round the letter, and that big hollow O and A letters find nothing inside,
  plain or with a glow pooled round a counter whose middle stays clear (three glows). `tests/gen/test_logo.py`
  checks that a title in both keys' hues is refused at `key` before the lettering is bought, that a named key
  in the title's hue is kept and the warning says so, that a see-through cut is refused at `matte` and kept
  aside with no text check, with or without a 9x9 square of the key in the middle of its patch, and that a
  glowing title is not see-through inside. `tests/logo_lettering.py` gains the `quilt` fixture: square fabric
  patches in outlined pixel letters, one with a counter, on a key. It also gains `walled_patch` (a navy letter
  with a mint patch on pure green, with squares or a pixel of the key painted in) and `hollow_letters` (the
  big O and A shapes), which `lettering` draws through its new `shapes` argument.

**Upgrading from 3.0.0:** Existing runs and commands do not change. This release adds two verbs and a JavaScript player. The wheel now also carries `sprite_gen/video/*.mjs`.

- `sprite-gen video-face` builds a face pack from a reference, a keyed talking clip, and keyed body motions.
- `sprite-gen face-bake` runs the pack's player through node to bake a voice onto body frames, with optional MP4 and beside video output.
- Face request kind `sprite-gen-face-request`, version 1, records face geometry, clip placement, a rest loop, and optional own-mouth spans or timelines.
- Face pack kind `sprite-gen-face-pack`, version 1, carries sprites, body cells, anchors, keeps, rules, a report, a copied player, and SHA-256 digests.
- The lower-face layer covers the mouth, jaw line, cheeks, and chin. The body's own dark grey and black that reach in from the layer's boundary, such as a headset boom or a choker, the body's own lines in the layer's feather, and a hand that reaches over the face from outside the layer stay above it.
- Each body frame receives a place, size, and tilt anchor. The pack records anchor residuals and corrects outlying readings against neighbouring frames. A body frame whose own mouth is closed has its place read again on a ring of skin round the mouth, because a head that turns moves its hair, headset, and cheeks further than its mouth, and a frame whose mouth is open takes the move of the read frames round it. The pack records each frame's move and the frames read on the ring.
- Talking frames are assigned to smile or neutral sets by closed-mouth lift. Entry chooses a matching set and rest.
- The player `sprite-gen-face-v1.mjs` contains the voice flap, face pick, scene plan, and live canvas playback rules.
- A clip's own mouth and sound finish before a requested line starts. The voice and layer then wait together for the first frame whose switch to the chosen rest is within the 1.4 jump line. Layer exit also uses that jump line.
- The live player keeps one queue of every `play` and `say` request, in the order the calls are made, and starts each request on the frame after the one before it has ended, so two voices never overlap. A line placed right after a clip with its own mouth is planned together with that clip, the plan `planScene` makes and `face-bake --play` bakes, and its voice and layer start at the plan's `line_start` after the own-mouth span. The queue has no length limit and drops nothing: a request whose sound does not load or decode, or whose line `planScene` refuses, rejects its promise when its turn comes, and the next request goes on.
- Bakes check pack digests, changed pixels outside the weight, and composite jumps. Refused bakes retain their frames for inspection.
- See [docs/face-parts.md](docs/face-parts.md) for the request, pack, player, commands, and limits.
- **Tests**: `tests/video/test_face_layer.py` uses `tests/talking_face.py` to check smile and neutral sets; clip entry set and rest; own-mouth timelines, including a close during voiced sound and refusal of a missing or never-closing timeline; a head 60 px from its place and tilted 3 degrees; a head that turns, tilting up to 5 degrees about its mouth while its hair, headset, and cheeks move up to 12 px sideways and its mouth stays where it is, with two of its eleven frames open, whose anchor place is within 1 px of the mouth on closed frames and within 2 px on the two open frames and whose tilt is the band's, below minus 2.5 degrees where the turn is fullest; hand, headset boom, and choker keeps; a tongue in the body's own open mouth, joined to a hand by a finger at its corner, covered by the layer; unchanged pixels outside the weight; synthetic cells keeping the key's colour under no coverage; digests and new-folder pack writes; the player flap against its original numpy rule and the pick against the draft Viterbi over every frame; a line waiting for a clip's own mouth and for a frame its layer can come on at; the live player run by node on a fake audio clock, with two lines requested during a greeting's own mouth, three lines requested on the loop, one line requested during the own mouth and one after it, a line whose voice loads late, a line requested right after a clip whose sound loads late, a line whose voice loads only after the greeting's own mouth has ended or only after the greeting has ended, which waits for the greeting to end while the greeting plays to its end with the layer off, a line that plays on the loop when requested right after a clip whose sound fails to load, and a request that fails; loop and post-greeting bakes with greeting frames unchanged byte for byte; and refusal of a changed pack.

**Upgrading from 3.0.0: This release changes defaults for video frames.**

Outputs that change bytes are frames from `video-frames` and `video-set` on a green or magenta key.
These changes occur within 6 px inside the keyed silhouette only.
Video frames keyed with explicit `--decontam palette` also change bytes, because they use the same video fit.

Outputs that do not change bytes include runs on a white key, subjects with no interior, and runs with `--alpha-mode brightness`.
Stills (`cutout`, `gen`) and row extraction (`extract`, `inspect`) do not change bytes.
Runs with `--decontam off` also give the 3.0.0 bytes back exactly.

To get the 3.0.0 frames back, pass:
`--decontam off`

- **Default change: `video-frames` and `video-set` now default to `--decontam auto` (before: `off`).**
  This release updates these defaults to clean chroma-bleed edges automatically.

  On a chroma key, H.264 4:2:0 chroma mixes key colour into strands one to three pixels wide.
  With decontamination off, a red hair strand on green keeps an olive rim.
  The `auto` pass restores the subject's own colour to those edges.

  The video default stayed `off` until now out of caution rather than any known defect.
  The pass was benchmarked on a green key only.
  The video fit is less decisive than the still fit.
  In the documentation's benchmark table, the video-fit row (10.8 % residual contamination, 9.38/8.73 halo, 91.4 % recall) was measured in September 2026 with flank recovery.
  It has not been remeasured since flank recovery was dropped, because those benchmark inputs and tools are not at hand.
  The diagnosis table in `docs/chroma-alpha.md` (its `decontam` section) has the current video fit's numbers.
  Still-fit rows in that table are unchanged.

  Earlier fixes resolved the Grok-magenta regression through the key-hue cap and corrected the frame-edge interaction.
  No blocking defect remains known.

  On synthetic diagnosis clips (CRF 18, `--spill small`, 6 frames, one machine), `auto` reduced key contamination on red strands on painted green from 24.2 % to 10.2 %, and on pure green from 27.4 % to 10.9 %.
  Halo composite error on dark and white backgrounds shifted from 11.6/12.1 to 9.6/10.1 on painted green, and from 12.0/11.9 to 10.0/9.9 on pure green.
  Strand recall stayed at 91.4 % on painted green and 92.4 % on pure green.
  Strand recall no longer rises with the pass, because that gain came from the flank that the video fit no longer recovers.
  Halo gains are smaller than before for the same reason.
  On these moving scenes, key contamination is about one point higher than with the hold and settle alone, because a change of call now takes a few frames to reach the fit's colour.

  Key contamination dropped from 3.4 % to 0.5 % on magenta, and from 5.4 % to 1.8 % on Grok darker magenta.
  Halo shifted from 15.0/19.6 to 12.7/16.4 on magenta, and from 20.7/31.5 to 19.3/30.0 on Grok darker magenta (compared to 17.1/24.7 when flank recovery was present).
  Strand recall shifted from 87.0 % to 86.9 % on magenta and from 73.3 % to 73.2 % on Grok darker magenta.
  Outline ink CIELAB error shifted from 19.3 to 20.2 on magenta, and from 21.7 to 22.6 on Grok darker magenta.
  Red hair counts as key-coloured material there, so the palette is not key-free and the warning line appears.

  Contamination dropped from 20.6 % to 8.8 % on a translucent glow scene (halo 12.6/9.4 to 11.7/8.5, strand recall 85.7 % to 85.4 %).
  It dropped from 23.8 % to 11.4 % on a small green charm scene (halo 11.8/12.9 to 10.0/11.1, strand recall 90.7 %).

  Changed pixels make up about 2.4 % of each frame (4.7 % in translucent scenes).
  All changed pixels lie within 6 px inside the silhouette.
  Nothing outside the silhouette changes any more.
  Interior pixels remain byte-identical.

  Stills (`cutout`, `gen`) and row extraction (`extract`, `inspect`) still default to `off`.
  The row extractor frames are a derived cache that `heal` re-derives, so changing their default would silently rewrite existing runs.
  Stills remain as they were.

- **Temporal fit: The video fit holds edge decisions across frames, blends a change of call over frames, settles still pixels, and drops flank recovery.**
  With default `auto`, the edge of a subject standing still sparkled from frame to frame where `--decontam off` remained calm.
  Codec noise pushed edge pixels across the pass's threshold tests from frame to frame.
  These tests decide blends, tints, material behind, explained pixels, palette colours, and flank coverage.
  Each crossing changed the written colour by far more than the noise itself did.
  The raw matte with `off` follows codec noise smoothly, staying calm despite keeping an olive rim.

  `video-frames` and `video-set` now maintain a clip history in clip order.
  This history passes to each frame through `decontaminate` as `history` (`decontam_history` in `cutout` and `remove_chroma_background`).

  Each test now holds its previous outcome at that pixel until current evidence passes the bar the other way by 16 (an RGB distance, `VIDEO_HOLD`).
  A palette colour picked in the previous frame stays unless another colour's line passes closer by 16.
  Moving pixels change their evidence far beyond 16 and are decided afresh.
  Only near-threshold calls stick.
  With the hold alone, a call that did change could still move a still edge through the whole gap between the matte and the fit in one frame.

  The blend mixes two renderings made from this frame's evidence at every pixel the pass examines.
  One is the matte with its key hue capped, and the other is the fit's palette colour at the pixel's light, with the fit's coverage where the call refits coverage and the matte's coverage elsewhere.
  For a palette-colour change, the fit's rendering mixes the two colours' renderings.
  A new palette colour waits until the change in hand is done, while a return to the old colour turns that change around.
  The mix moves toward this frame's call by at most 0.2 a frame (`VIDEO_BLEND`).
  It moves less if needed to limit the output change to 4 (`VIDEO_STEP`) plus 0.25 (`VIDEO_STEP_MOTION`) per unit the source moved, measured in premultiplied colour or coverage across its 3x3 box.
  A pixel new to the band starts at its call if it was transparent in the previous frame, and otherwise starts from the matte.
  Both renderings follow a moving subject at once; the change of call is spread over frames.

  A still pixel whose mix moved, has not reached its call, or came into or left the band moves at most 4 a frame from its previous output, measured in premultiplied colour and coverage.
  Here, still means its source moved by at most 8 in any channel anywhere in its 3x3 box (`VIDEO_STILL`).
  A pixel the hard cut keyed in this frame stays transparent.

  Still pixels settle toward the current fit when their mix is at its call in both frames.
  When a pixel was written in both frames in the same palette colour, and its source is still, it settles.
  It moves a tenth of the way (`VIDEO_SETTLE` 0.1) toward this frame's fit and stays within 12 per channel (`VIDEO_SETTLE_REACH`) of it.
  When it must catch up to get within 12, it now moves at most 4 a frame in premultiplied colour and coverage instead of moving all at once.
  This dampens codec noise while following real slow changes within 12 levels.

  The video fit also drops flank recovery.
  It no longer gives coverage back to keyed pixels (hard cut background within 3 px of the subject).
  Decoded chroma blur created faint blends on keyed pixels, and codec noise carried them back and forth over the noise floor.
  This filled dark gaps between strands with a faint smear that came and went across frames.
  The video fit now examines the band only (subject pixels within 6 px of the keyed background).
  The still fit keeps its flank recovery unchanged.

  With history enabled, frame bytes depend strictly on previous frames in clip order and remain deterministic.
  A single video-fit call without history gives the same band bytes as before, leaving keyed flank pixels as the matte keyed them.

  On a synthetic still scene under decoder noise (red strands 1 to 3 px wide on painted green, 8 frames, sigma 2):
  Mean edge change between frames on a dark background was 2.34 with `off` (99th percentile 8.3).
  With the hold and settle alone, the mean was 2.27 (99th percentile 8.3).
  With the blend and still steps, the mean is 2.25 (99th percentile 8.0).
  Key contamination on that scene was 6.5 % with `off`, 2.3 % with the hold and settle alone, and 2.4 % now.

  On a second synthetic scene, decoder noise changed between two frames and the source then stayed still for 19 frames.
  With the hold and settle alone, changed calls appeared in one frame and their pixels stopped moving when the source stopped.
  Now those edge pixels keep moving toward their new calls, by no more than 4.31 premultiplied a frame including uint8 rounding, and arrive by frame 14.

  Per-frame stats and clip totals include `held_px`, `blending_px`, and `settled_px`.
  `held_px` counts pixels where a held outcome or colour differed from an independent fit.
  `blending_px` counts pixels showing a mix of two calls rather than this frame's call.
  `settled_px` counts pixels the settle or a still step moved from the previous frame's output.
  In video, `recovered_px` is now 0.

  Keying cost was remeasured on an Intel i5-7500 with a 1080x1920 synthetic clip at CRF 18 and `--spill small`, six frames, one process per run, in the same session.
  Keying took 1.14 s a frame with `off`, 3.25 s with the hold and settle alone, and 3.36 s now.
  Peak memory was about 400 MB with `off`, 945 MB with the hold and settle alone, and 1155 MB now, adding about 365 MB over `off` per megapixel.
  Because `video-set` keys three items at once by default, it can use about 2.3 GB more than `off` at that size.

- **Guards: Two new guards protect key-coloured details and material behind edges in the video fit.**
  The still fit output is unchanged byte for byte, but explicit `--decontam palette` runs in video also use these guards.

  The key-coloured parts guard protects small details that share the key colour.
  A subject can own a key-coloured part too small for the palette to keep, such as a green charm on red hair under a green key.
  When such a part covers under 0.5 % of the interior, the palette learns key-free colours.
  Without the guard, the pass repainted the rim in the nearest key-free colour, turning the green charm's rim brown.

  Now, when the palette is key-free, band pixels within 6 px of such a part keep the matte's bytes.
  There is no flank in video any more, so this guard covers band pixels only.
  This applies to pixels near parts that are opaque, deeper than 6 px, and have key hue above 8.
  The key-hue cap leaves those pixels alone.

  On synthetic tests, the share of the charm's key-coloured edge pixels that lost half their key hue or coverage was 0.77 with `off` and 0.82 now.
  Its colour error was 41.7 with `off` and 45.6 now (64.9 without the guard in the diagnosis).

  The material-behind guard preserves edges where subject material continues to the silhouette boundary.
  A band pixel keeps the matte's bytes when its colour, observed directly or at its own luma, falls within the opaque margin of the mean colour of subject pixels right behind it.
  This check reads subject pixels deeper than 2 px within a 3 px box radius.
  Such pixels are treated as material continued to the edge rather than blends or tints.
  The key-hue cap still applies to them.
  Unlike the still fit, this guard does not require the pixel to be free of the key's hue, because decoded chroma blur gives every edge some key colour.

- **Diagnostics: A one-line stderr warning and new report fields track skipped frames and guard activity.**
  `video-frames` prints a single line on stderr after its JSON summary when edge decontamination warrants attention.
  The line starts with `video-frames: warning: decontam auto ...` and ends by naming `--decontam off` as the way to keep the matte as it is.

  The warning prints when `auto` ran on no frames, when `auto` skipped some frames, or when the subject owns key-coloured material so the palette is not key-free.
  On magenta keys, the red hair's blue channel sits more than 8 above its green, making the hair key-coloured material (9.6 % of the interior on magenta and 6.6 % on Grok's darker magenta) and triggering this warning.
  The warning also prints when the key-coloured part guard kept edge pixels, reporting how many and noting that thin details farther out may be repainted.
  A few key-hued pixels deep inside from codec noise guard no edge and raise no warning.
  Brightness alpha raises no warning.
  `video-set` prefixes this warning line with the item name and records it under `warnings` in the set report.

  The JSON report adds `decontam.reason` for the first skip reason and `decontam.skipped_frames` when frames are skipped.
  Per-frame stats and clip totals in the video fit add `key_part_kept_px`, `behind_kept_px`, `held_px`, `blending_px`, and `settled_px`.
  Where `auto` cannot run, frames remain exactly as the matte keyed them.
  The report records "the white matte has no key colour to remove" for a white key.
  It records "no interior to learn the subject's colours from" when no pixel lies deeper than 6 px inside the silhouette.
  Under `--alpha-mode brightness`, `auto` defaults to off, recording `{"mode": "auto", "applied_frames": 0, "reason": "brightness alpha has no key colour to remove"}`.
  Explicit `--decontam palette` with brightness alpha remains refused.

  Known limits apply to this release.

  The hold works on pixels that stay put.
  A change of call on a still edge arrives at most 4 a frame.
  The largest change, between the matte's full coverage and a faint fit, can take up to about 64 frames, or about 2.7 s at 24 fps.
  Until it arrives, the edge shows a mix of the matte's colour and the fit's.
  A strand that moves is decided afresh in every frame, while its change of call is spread over a few frames and both renderings follow it.
  A moving edge still changes from frame to frame as its colour does, so a red rim moving over a dark background changes more than the olive rim `off` leaves.
  Pixels whose call changes show a mix of the two calls for a few frames.

  The settle follows a slow change within 12 levels per channel and catches up past that at most 4 a frame.

  The video fit gives back no coverage the hard cut keyed.
  Outer pixels of a soft edge that the cut erased stay erased.
  Only on stills, outside a dark outline the flank can give a few faint pixels in the subject's colour.

  A thin feature whose colour is absent from the interior can be read as a palette colour plus key.
  For example, a thin yellow line beside red hair with a pink highlight turned pink (colour error 26.2 with `off`, 47.5 now).
  `--decontam off` keeps that line.

  A thin key-coloured detail farther than 6 px from any key-coloured interior is repainted in a palette colour.

  The thinnest strands retain some olive because decoders share chroma across aligned 2x2 blocks, leaving a one-pixel strand with half the key's chroma.
  A block-aware fit is planned separately.

  Pixel-art rows binarizing alpha, the green-only benchmark, and increased processing time and memory remain known limits.

- **Tests**: `tests/frames/test_chroma_decontam.py` checks that both video verbs default to `auto` and that still verbs and the row extractor do not.
  It checks that `off` gives the engine's original bytes.
  It checks that `video-set` hands its value to `video-frames` and lists the warning.
  It checks that a white key, a subject with no interior, and brightness alpha give the same bytes as `off` with the reason.

  A new section tests the video fit over time on the red-strand scene standing still while decoder noise changes every frame (luma per pixel, chroma per aligned 2x2 block, eight frames).
  The default's edge changes from one frame to the next no more than `off`'s, in mean and at the 99th percentile.
  The edge is still cleaned, with key contamination at most half of `off`'s.
  Clip totals count held and settled pixels, and the first frame has none.
  A frame given twice comes out with the same bytes both times.
  Another test decodes one frame of the red-strand scene, then holds a second noise draw (sigma 3) still for nineteen frames.
  After the source stops changing, the default keeps moving toward the calls the noise changed.
  No pixel moves more than `VIDEO_STEP` in one frame in premultiplied colour or coverage, plus one for uint8 rounding.
  It arrives by the last frame.
  Clip totals count `blending_px`, and the first frame has none.

  The video fit leaves every pixel the hard cut keyed transparent and recovers nothing.
  The still fit still recovers its flank.

  Changed tests reflect dropped flank recovery in video.
  A head 6 px under the top frame edge passes the edge gate under the default and keeps the blur rows below the edge band as the matte keyed them.
  For magenta skin tone in a 4:2:0 frame, the video fit's halo is read on the band where it acts (at most 0.9 of `off`'s), and the keyed flank keeps the matte's bytes.
  The still fit keeps its halved halo.

  The rim of a small green charm keeps the matte's pixels and loses at most 0.1 more than `off` while strands away from it are cleaned.
  On magenta and Grok magenta, halo and alpha error do not rise.

  A few key-hued pixels deep inside raise no warning.
  `video-frames` prints the warning line on stderr.

  `tests/video/test_gait_guard_anchor_wide.py` has its stand-in for `run_frames` take `decontam` and checks that the set's `auto` reaches it.

**Upgrading from 3.0.0: Walk and run loops now pass a step gate and fixed walk or run cuts are counted, so some cuts that passed before are refused.** Against 2.45.3, on which this change was built, every cut 2.45.3 passed that neither new reading refuses gave the same loop files byte for byte (strip, sidecar, frames, GIF and WebP bytes, the same stderr save new warning lines), measured on a batch of walk clips cut with caller arguments (the automatic walk cut, cuts by hand, forced cuts). The report gains `step_gate` on walk or run loops and `cycle.step_count` on fixed walk or run cuts, `producer` names the new version, and the CLI summary gains `step_gate`, whose `jitter` names the frames entered by steps over 2.5 that are a pixel's jitter, along with `jump_repair.unfixed`. Refusals outside the new readings keep their lines word for word, except the seam line's end where warning lines name a part on a beat of its own. Callers matching failure lines by first words need rules for `video-loop: loop step ratio` (the clip jumps inside: film again), `video-loop: loop step count refutes the fixed cut` (cutting again by hand failed), and `video-loop: loop frames left off the flow` only if passing `--unfixed refuse`; failure reasons can also be read from `seam_refusal`. Any `--seam-max` past 2.5 lifts the step gate and the count refusal, and forced delivery passes `--seam-max 1000` together with `--seam-pop-max off`, because `--seam-max` does not lift the seam pop gate described below. Reading feet on a fixed walk or run cut costs less than the distance matrix read by every cut.

- **Behavior change: Walk and run loops that play round now pass a step gate that inspects spacing between
  drawings.** After the seam gate, cells are read between drawings rather than frame by frame, so held
  animation on twos or threes does not appear uneven when drawings span multiple frames. The largest step from
  one drawing to the next, the wrap included, over the mean of the others, must be at most `STEP_RATIO_MAX`
  (2.5), else the loop fails with `video-loop: loop step ratio <ratio> exceeds 2.5 (<kind> length <L> frames
  from <start>, into frame <frame>, read between its <n> drawings); the loop jumps inside — regenerate the
  clip`, with jump repair reason `: frame <frame> lies off the walk's flow, and the jump repair's frame there
  was refused (<faults>), so it plays as filmed` before ` — regenerate the clip` where present. A step over
  2.5 is exempt from the bound when it is a pixel's jitter (`loop.step_jitter`): the move that aligns its
  drawing with the previous drawing with the least coverage difference, measured on alpha so colour has no
  effect, is at most `STEP_JITTER_PX` (1.5) pixels of the frames as filmed, and the step with that move taken
  back is within 2.5 of the mean. In a walk going away from the camera, foot rises of a whole crop row measure
  2.5 to 3.6 times the mean as drawn, black or grey, but move one pixel and pass without a warning line. One
  frame of a 400 px tall walk cut moved 6 px and back moves 2 pixels of the frames as filmed, and the gate
  refuses the loop in every colour, including black. The report records `step_gate` on every walk or run loop
  (`drawings`, `hold`, `mean_step`, `largest_ratio`, `largest_from`, `largest_into`, `wrap_ratio`, `max`,
  `jitter`, `lifted`, `kept`; `skipped` for fewer than three drawings). The bound is the larger of 2.5 and
  `--seam-max`, so any `--seam-max` past 2.5 lifts it; a scan or forced delivery with `--seam-max 1000` keeps
  a loop whose largest step is over 2.5 with warning `video-loop: warning: the loop's largest step between
  drawings is <ratio>x their mean (into frame <frame>), over 2.5: it jumps inside — kept, as --seam-max <seam-max>
  lifts the step gate` and `step_gate.kept`.
- **Behavior change: Fixed cuts fail when foot crossings count an odd number of steps.** Manual walk or run cuts using `--cycle fixed` now read the clip's foot crossings and evaluate the cut, matching `--anchor motion-auto`. The report records `cycle.step_count` with `chosen_by: caller`. A cut counted an odd number of steps fails with `video-loop: loop step count refutes the fixed cut <start>/<L> — the clip's crossings count one step, each seen where a walk loops on two, and an odd count swaps the legs at the wrap; cut it again two steps long (about <2 steps> frames)`. An even count is not refused by the count check, and the exemption `--anchor motion-auto` makes for a pass hidden among feet held together (the clip a cut on the same picture) applies identically through shared `local_cycle.same_picture_count`. The strip is unchanged, and there is no `steps` field. Any `--seam-max` past 2.5 lifts this refusal; a scan or forced delivery with `--seam-max 1000` keeps an odd step cut with warning `video-loop: warning: the clip's crossings count the fixed cut <start>/<L> one step, each seen: a walk loops on two steps, and an odd count swaps the legs at the wrap — kept, as --seam-max <seam-max> lifts the step gate` and records `cycle.step_count.lifted`.
- **New: Refused jump repair drawings are listed in `jump_repair.unfixed` and can fail the command.** When jump repair refuses a frame group, its frames remain as filmed; where the playing loop still reads a drawing of it 3.0 or more of its window's usual steps off the flow, that drawing is recorded in `jump_repair.unfixed` alongside `refused` (the faults). Every `unfixed` drawing prints warning line `video-loop: warning: frame <frame> of the loop lies <off>x its window's usual step off the walk's flow after the jump repair: the group that would bring it back was refused (<faults>), so it plays as filmed — a jump played as filmed; --unfixed refuse refuses such a loop (docs/loop-repair.md section 2)`. A new flag `--unfixed warn|refuse` (default `warn`) controls handling: `refuse` fails such a loop with an error line starting `video-loop: loop frames left off the flow — `. This failure is not lifted by `--seam-max`.
- **Fix: The seam gate failure line explains the cause where warning lines know it.** The first words of the failure line stay the same up to `the cycle does not close — `; where warning lines name a part on a beat of its own (an `--anchor motion-auto` cut two steps long by the clip's crossings whose wrap the gate refuses), the rest says so and names what became of the overlapped wrap, for example `video-loop: loop seam ratio <ratio> exceeds 2.0 (periodic length <L> frames from <start>); the cycle does not close — the cut covers two steps by the clip's crossings, and a part on a beat of its own (a tail, a held prop) closes on no cut of two steps: the overlapped wrap was not made (turned off (--overlap-wrap off)) — regenerate with that part moving on the walk's beat`. Elsewhere the line is word for word as before. Every refusal by the wrap, steps, or count records `seam_refusal` in the report (`reading`: `wrap`, `steps` or `count`). For `wrap`, it records `ratio`, `max`, `reason` (`own-beat` or null), and `overlap_wrap`. For `steps`, it records `ratio`, `max`, `into`, `reason` (`repair-refused`, `unfixed` or null), plus `frames` and `refused` where a reason was found. For `count`, it records `cut`, `verdict`, `k`, `why`, and `two_steps`.
- **Tests** — `tests/video/test_loop_silent_pass.py`: a side walk in place with a middle frame moved 10 px fails the step gate while its wrap passes, recording `step_gate` and `seam_refusal`, kept by `--seam-max 1000` with a warning line; an eight drawing walk on threes passes when frame by frame steps exceed 2.5; melted ponytail outlines and smeared stand in RIFE frames stay `unfixed` with faults, cite the refused frame in step gate lines, and fail under `--unfixed refuse`; CLI tests confirm `--unfixed` defaults to `warn` and rejects other words. Hand cuts fail on one step, pass at two, keep one step cuts under `--seam-max 1000`, and exempt hidden far feet on twos. A walker whose tail swings on its own beat fails seam checks with the reason in its line and `seam_refusal`; a sideways wrap shift keeps its line word for word with `seam_refusal.reason` null. The walk going away from the camera passes with foot rises of a whole crop row as drawn, black and in a grey near the backdrop: its steps over 2.5 times the mean move one pixel and are recorded in `step_gate.jitter`. A standing side walk with a frame moved 2 px and back fails on that step as drawn, black and in that grey, with every frame's alpha the same. In `tests/video/test_body_anchor_gate.py`, the sliding side walk with its first frame moved 3 px is refused on the step into frame 1: those 3 px take up the sideways slide at the wrap and put the jump inside the loop. The seam still reads the source frames and is not read again.

- **Fix: Windows run dir lock handling.** Every command that writes a run dir (for example `extract`, `compose-atlas`, `export-pngs`, `unpack-atlas`) takes a single-writer lock: the entry `.sprite-gen.lock` inside the run dir. Before this fix, on Windows only, when that name held a link pointing at a file that does not exist, the writer followed the link and made the lock file at the link's target, outside the run dir, and that file was left behind when the writer released the lock; also on Windows only, a folder at that name, a link to a folder, or a link whose target is written with forward slashes ended the command with a bare `OSError` (`EACCES` or `EINVAL`) instead of the named refusal. Now on Windows a link or a folder at that name is refused by name, the same as on macOS and Linux (`run dir lock ... is not a lock file (a link or a folder)`), and nothing is followed, made or removed, while macOS and Linux behave exactly as before. The test is `tests/spec/test_run_dir_lock.py`.

**Upgrading from 3.0.0: commands that write beside what they read now check and refuse any input that overlaps a file or folder the run replaces or deletes, even with `--force`.** This judgment is defined in `docs/run-contract.md` and enforced by `runio.refuse_overlap` before any command creates output. A run is stopped whenever an input matches an output, lies inside it, or passes through anything inside it, and the refusal names both paths.

- **Behavior change: Overlap refusal for inputs and outputs.** Twelve commands now call `runio.refuse_overlap` before making anything, with or without `--force`: `unpack-atlas`, `video-loop`, `video-stop`, `video-frames`, `correction-loop`, `logo`, `video-extend`, `video-edit`, `video-loop-compare`, `video-loop-repair`, `video-source-manifest` and `shadow`. Inputs include paths given to read and files found inside them (such as frames in `--frames-dir`), followed via `runio.follow_path`. Outputs include each file written, folder replaced or emptied, and report, but not folders only added to (which is why `--out-dir` for `video-loop` and `video-stop` is not compared). Comparisons check real path and file identity (`runio.same_folder`, `runio.inside_folder`), treating links and different spellings on volumes that ignore case as identical, while input link loops are refused by name. A link at a written file is refused if it leads to an input, even where the run would replace the link rather than write through it, whereas paths passing through an output and exiting with `..` (such as `out/cycle.source/../keyed`) are permitted. In `logo`, the folder's own `mood.png` and `lettering.png` remain permitted for `--mood` and `--lettering` while any other output file is refused, and `correction-loop` permits an `--out-dir` inside the run dir or one holding an earlier loop's attempts, best candidate and report.
- **Fix: Directory recursion and casing collisions on volumes that ignore case.** Before, `correction-loop` compared only `--out-dir` itself with the run dir, so with a link `attempt-1` in its `--out-dir` leading to the run dir, the loop wrote its attempt into the run it read. Separately, before, the best candidate's copy of the run dir copied itself into itself until the path was too long. (The default `--out-dir`, `<run-dir>/correction-loop`, lies inside the run dir; the copy now leaves it out.) `correction-loop` now takes an earlier loop's candidate away before the provider makes the next one there. `video-sway-inspect` now refuses an `--out-dir` that is the loop directory spelt in capitals on a volume that ignores case, instead of writing boards into it. In addition, `video-loop-compare`, `video-loop-repair`, `video-source-manifest` and `shadow` now call `runio.refuse_overlap` so reports or outputs spelt in capitals on volumes that ignore case are refused instead of overwriting inputs, while `background-tile`, `inspect-motion`, `scene-inspect`, `scene-render`, `game-pack`, `ui-kit` and `video-sway-inspect` verify their single output with `runio.same_folder` and `runio.inside_folder`.
- **Behavior change: Unique working folders via stage names.** Any folder a run writes into and then deletes is now created with `runio.new_stage` and named `.<name>.<pid>-<random><suffix>`. These include the frames `video-loop` scales back for a size hold or gait fallback (`.gait-fallback-frames.<pid>-<random>`, removed however the run ends), the PNG frames built into animated WebP files beside the WebP for `video-loop`, `video-stop`, `video-cycle-align`, `video-sway` and `video-loop-repair` (`.<name>.webp.<pid>-<random>.frames`), the working folder `video-frames` keys into (`.keyed.<pid>-<random>.work`), and the working folder of `logo` (`.logo-work.<pid>-<random>`). Folders at old fixed names (`.gait-fallback-frames`, `.webp-frames`, `.keyed.work`, `.logo-work`), whether holding an input or belonging to anyone else, are never written into or removed.
- **Behavior change: Input protection, staging, and locks in `unpack-atlas`.** `unpack-atlas` never touches its inputs, refusing before making anything and with or without `--force` any input that is the run dir, lies inside it, goes through anything inside it, or holds it. Guarded inputs include `--pngs-dir`, `--atlas`, `--manifest`, the atlas a manifest names, and what the import reads in `--pngs-dir` (every frame, `_base` image, `_refs` PNG, `meta.json`, and every folder it reads one from, any of which may be a link). Refusals name the first entry the input goes through inside the run dir, while paths that only pass through the run dir itself (such as `run/../incoming`) are not refused. The command builds runs in a dedicated staging folder beside the run dir (`.<name>.<pid>-<random>.sg-staging`) and moves prior content aside into a backup folder inside it (`.sg-backup.<pid>-<random>`), never reusing or removing existing staging or backup folders and putting every prior file back on a failed publish. The run dir's single writer lock is the entry `.sprite-gen.lock` itself (`runio.acquire_run_dir_lock`, every writer) and belongs to the process that took it, and a link or a folder at that name is refused by name and never followed, read or removed, so reclaiming a stale lock deletes only a stale lock file.

**Upgrading from 3.0.0: A held walk or run whose wrap fell between two drawings, refused before, may now pass.** A walk drawn a frame each gives the same loop files byte for byte. Reports record `seam_read` (`read`, `drawings`, `hold`) on every walk or run whose seam ratio gates the cut, and `seam_after_anchor` gains the same three fields when cells are read again, with `strip.json` and the failure line of the seam gate unchanged.

- **Behavior change: Walk and run loops gated by their seam ratio now measure the seam between drawings.** For any walk or run whose cut is gated by the seam ratio (neither pinned nor played once), the seam is read between drawings using each drawing's first frame (`loop.seam_drawings`, built on `repair.flow_drawings`), matching the reading used by the step gate. This applies in three places: the seam gate itself, both on source frames (`seam_measurement: source-frames`) and rendered cells (`rendered-cells`); the body anchor ramp rule, reading the wrap and every step between the cells' drawings; and candidate evaluation under `--anchor motion-auto` (`loop.seam_on_cells` through `loop.wrap_on_cells`) when selecting two-step cuts or testing overlapped wrap attempts. Loops showing fewer than three drawings continue to be read on every frame (`seam_read.read: cells`).
- **Report: Reports record whether the seam gate read frames or drawings.** On every walk or run whose seam ratio gates the cut, `seam_read` records `read` (`cells` or `drawings`), `drawings`, and `hold`. When `--anchor body` ramps cells and triggers a re-read, `seam_after_anchor` records those same three fields for the cells that play, and its `largest_step_into` names the first frame of a drawing. Strip sidecar `strip.json` does not change.
- **Known limits: Measuring between drawings allows cuts from a third of the stride short to a sixth long on twos, and from a third short to a quarter long on threes, to pass.** On synthetic 24-frame stride walks standing in place, cuts of 16 to 28 frames on twos (ratio 1.02 to 1.67; 30 refused at 2.06) and 16 to 30 frames on threes (0.95 to 1.48) pass the seam gate, whereas drawn on ones cuts of 16 to 22, 28, and 30 are refused (2.38 to 4.07). A shifted final drawing passes up to 2 px on twos (1.66; 3 px refused) and up to 4 px on threes (1.73; 6 px refused). Under `--anchor body` sliding 0.2 px a frame, cuts of 16 to 28 frames pass on twos and 16 to 30 on threes, tolerating shifts of 2 px on twos and 6 px on threes. The step gate already passed these cuts (largest step 1.26 to 1.86), while the earlier frame-by-frame seam gate refused them all. For full synthetic limits and ramp discretization behavior, see `docs/loop-review.md` section "The body anchor and the seam gate".
- **Tests:** `tests/video/test_held_walk_seam.py`: a held walk cut between its drawings passes the seam gate on twos and threes; a walk drawn a frame each is read on every frame and its ratio is the frames' own; a held walk whose last drawing is moved 10 px is refused between its drawings; a held sliding walk passes on the drawings the ramp plays, and a jump the ramp moves into a held walk is refused as the source frames refuse it; a held walk motion-auto cuts passes on its cells between their drawings (a side walk on twos, a side walker on threes); motion-auto's seam gate reads a held cut between its drawings, and a last drawing with a band of its body gone is over the limit. `tests/video/test_walk_steps.py`: the walker on threes with a tail on its own beat records its tried two-step cut within the seam gate between drawings, declined as out of phase; the search's cut stands as before.

**Upgrading from 3.0.0: walk and run loops whose seam pop exceeds the bound are now refused by default.** The check is governed by `--seam-pop-max BOUND|off` (default 10). The flag `--seam-max` does not lift this gate. Callers that force delivery with `--seam-max 1000` or scan fixed cuts with lifted gates must add `--seam-pop-max off` beside `--seam-max 1000` to deliver what they delivered before. Callers matching failure lines by their first words need a rule for `video-loop: loop seam pop`. Loops that pass the gate, loops with an unreadable top band, and pinned clips produce the exact same files as before, save an `--anchor motion-auto` cut taken in place of a first choice the gate would have refused before the cut.

- **Behavior change: seam pop is now a gate.** A walk or run loop whose `pop` is over the bound is refused, while a pop exactly at the bound passes. The gate applies to all walk or run loops gated by seam ratio (`--cycle fixed`, periodic searches, `--anchor motion-auto`, and `--repair off`), but does not gate pinned clips (`--cycle pinned`). The check runs after the seam gate and step gate; cuts refused by earlier gates fail with their existing lines. On refusal, the failure line is `video-loop: loop seam pop <pop> exceeds <bound> (<kind> length <L> frames from <start>): the top of the silhouette jumps into the loop's first frame up or down <percent> % of the body height, <ratio>x its median step — the loop does not close at its top; cut it where it does, or regenerate the clip`, detailing the pop, bound, cut, and directional jump metrics, without printing a warning line or adding to `jolt.warnings`. Loops whose top band cannot be read pass with verdict `unreadable` and retain their warning line. The flag `--seam-max` does not lift this gate, because seam pop measures top band placement rather than seam ratio distance.
- **New: `--seam-pop-max` flag.** `video-loop --seam-pop-max BOUND|off` sets the seam pop bound. It defaults to 10 (`repair.SEAM_POP_REFERENCE`) and accepts any number over 0, or `off` to turn the gate off.
- **Behavior change: `--anchor motion-auto` evaluates candidates for seam pop before cutting.** Before a cut is taken, candidate cuts are evaluated using `loop.corrected_cut` and `repair.seam_pop_of`. If the search cut would be refused, the engine evaluates up to four candidates (`local_cycle.SEAM_TRIES`) in search order (equivalent cuts earliest first, then remaining best first) and selects the first passing cut; if all four fail, the first choice stays and fails on the loop. For step counts, foot crossing cuts passing the seam gate are checked for pop, taking the first of four cuts that passes both gates. Every motion-auto run now corrects the search cut once before taking the cut.
- **Report: `seam_pop_gate` record and step count fields.** Walk and run reports and CLI summaries add `seam_pop_gate` containing `verdict` (`passed`, `refused`, `unreadable`, `off`, or `not-applicable`), `max`, `pop`, and `why`. Failed reports include this record as well. Motion-auto searches record `cycle.seam_pop_gate` with `max`, `tried`, `applied`, and `why`. In addition, `step_count.seam_gate.tried` rows and `step_count.search` gain `pop`, `pop_gate`, and `pop_skipped`.
- **Tests: synthetic and unit coverage.** Synthetic tests in `tests/video/test_seam_pop_gate.py` verify a standing rod walk where an 11 px tip drop reads a pop of 11.0 and is refused, while a 9 px drop reads 9.0 and passes. Tests confirm that bound 12 or `--seam-pop-max off` keeps the 11 px loop with identical files, `--seam-max 1000` alone does not keep it, unreadable tops pass as `unreadable`, pinned clips report `not-applicable`, and `--anchor motion-auto` switches from a cut with pop 6.0 (5.0 on twos) to a passing cut with pop 0.0 under bound 3. Unit tests cover `repair.seam_pop_gate`, `local_cycle._pop_gate`, `detect`, and `by_steps`. In `tests/video/test_loop_prop_seam.py`, fixed cuts with dropping staff tips are preserved under `--seam-pop-max off`.

## v3.0.0 - Remove rig layer composition

**Upgrading from 2.46.0: `sprite-gen compose-layers` and its other launch forms are removed, and rig layer declarations are no longer read.** `compose-atlas` no longer writes a manifest `rig` block or `animation.rows.<state>.track` for a run that declared a rig. Runs that never declared `rig`, `layers` or a state `track` get the same manifest as before. Existing run files and `layers/` folders stay on disk.

### Breaking

The rig layer feature has no replacement in 3.0.0:

| Removed | Use |
|---|---|
| `sprite-gen compose-layers`, `python -m sprite_gen.compose.compose_layers`, `scripts/compose_layers.py` | No replacement. The CLI verb now ends as an unknown command with argparse exit code 2. |
| `sprite_gen/compose/layers.py` (declaration checks for rig profiles, row tracks and composite stacks), `sprite_gen/compose/compose_layers.py` (baking declared stacks into `<run>/layers/`) | No replacement. |
| `sprite-request.json` keys `rig`, `layers` and `states.<state>.track` | Remove them with `sprite-gen migrate-request <run> --apply`. |
| `sprite_gen.spec.runio.normalize_retired_fit_keys` | `normalize_retired_keys`, which returns a list of keys moved or left out, or an empty list. |
| `docs/layer-tracks.md` | No replacement. Its links are gone from the other docs, READMEs and `SKILL.md`; the skill description no longer lists "rig layers". |

- `prepare` drops the retired keys from `--request` and `--request-json`, naming them in its existing stderr note. Code no longer reads them. On the first read of an old run's request, the process prints one stderr line naming the keys, treats them as absent in memory and leaves the file byte for byte unchanged. Unrelated request rewrites, including reroll and interpolate takes and curation fps edits, preserve those keys as they were.
- `sprite-gen migrate-request <run>` lists the keys it would remove without writing. `--apply` removes them from the file; a second apply has nothing to move. The changed bytes move `run_revision`, and the command names the curation rows it will drop before writing. The command also retains its existing `fit.pixel_perfect` to `fit.pixel_unfake` migration. `runio.migrate_request_file` now returns the list of keys moved or left out instead of a boolean.
- Talking face parts are planned as a new feature in a later 3.x minor release.
- **Tests**: Added `tests/spec/test_request_retired_rig_keys.py` to check the one-time stderr note and unchanged old request bytes, migration dry runs and repeated applies, preservation on unrelated edits, and named drops in `prepare`. Removed `tests/compose/test_layer_compose.py`, `tests/compose/test_layer_contract.py` and `tests/packaging/test_layer_cli_entrypoint.py` with the feature.

## Before 3.0.0

The 1.x and 2.x entries are in [CHANGELOG-before-3.0.md](CHANGELOG-before-3.0.md).
