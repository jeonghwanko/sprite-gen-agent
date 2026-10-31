# SPDX-License-Identifier: Apache-2.0
"""The constants `video-loop` is tuned by: analysis sizes, seam and step bars, one-shot and gait thresholds, mode names, and the folder and file names a loop writes.

Moved out of `loop.py` unchanged; `loop` re-exports every name, so `loop.<name>` still works."""

from __future__ import annotations

from sprite_gen.video import legs as legs_mod
from sprite_gen.video import nonloop

ANALYSIS_SIZE = 96  # thumbnail edge for the distance matrix
STRIP_MAX_CELLS = 64  # upper bound on cells even when they are narrow
STRIP_MAX_WIDTH = 32000  # Chrome refuses images wider than ~32767 px; the cap is on PIXELS — a 650 px cell allows only 49 cells (2026-09-09 wolf idle: 64 cells = 41,664 px, unrenderable)
STRIP_MAX_HEIGHT = 520
SEAM_RATIO_MAX = 2.0  # loop seam / mean adjacent distance inside the cycle (a walk's or run's between its drawings, `seam_drawings`)
# A walk's or run's largest step between drawings as it plays, the wrap one of them, over their mean step
# (`drawing_steps`): past it the loop jumps inside, where the seam ratio, read at the wrap alone, does not look. Read
# between drawings, not frames: a loop held on twos or threes steps nothing and then two or three steps at every drawing.
# The step gate stands where the seam gate does: a --seam-max past it lifts it, every step's bound is then --seam-max,
# and a fixed cut the clip's crossings count an odd number of steps is kept with a warning line (`fixed_count`).
STEP_RATIO_MAX = 2.5
# The step gate's pixel jitter: a step over STEP_RATIO_MAX that its drawing moving at most this many pixels of the frames
# as filmed makes (`step_jitter`: the move that lays it on the drawing before, read on coverage, so no colour enters it,
# and with that move taken back the step within STEP_RATIO_MAX) is a pixel's jitter of a loop that barely moves, not a
# jump, over however small a mean step: one pixel and not two. Read as a mean RGBA difference instead, the same jump of a
# frame moved 2 px reads 0.057 drawn in colour and 0.021 drawn black.
STEP_JITTER_PX = 1.5
PERIODICITY_MIN = 0.15  # the period must dip at least 15% below the profile mean (flat profile = no repeat)
# Where a size hold or the gait fallback writes the frames it scaled back: a folder of the run's own in the out dir,
# `.gait-fallback-frames.<pid>-<random>` (`_Scratch`), removed however the run ends.
FALLBACK_FRAMES = "gait-fallback-frames"
GIF_FPS_DEFAULT = 24.0  # GIF/WebP playback density = the source rate: every cycle frame is kept, a fast action never looks slow (12 made a jump read sluggish, 2026-09-09)
GIF_FRAMES_MIN = 4
ONE_SHOT_MIN_CONTRAST = 3.0  # one-shot: the excursion peak must stand this far above the rest-pose noise (in MADs)
# Endpoint-relative departure in units of mean subject pixel mass. Contrast against
# the whole distance profile can miss a held action, so mass is a second explicit
# acceptance rule. Sustained excursion and departure/step gates reject stand jitter.
ONE_SHOT_MIN_MOVED = 0.4
ONE_SHOT_MIN_COHERENCE = 3.0  # departure must exceed three ordinary playback steps
ONE_SHOT_MIN_ACTIVE = 4  # isolated keying/jitter spikes are not a performed action
ONE_SHOT_PAD = 2  # one-shot: rest frames kept on each side of the excursion so the seam is rest -> rest
CYCLE_MODES = ("auto", "periodic", "one-shot", "fixed", "pinned", *nonloop.KINDS)
# --cycle pinned: the clip was asked to end on its first frame (first-last mode). A re-rendered
# first frame is never byte-identical to its source; on the analysis thumbnail (mean absolute
# RGBA difference, 0..1) it lands within this of it, well under one frame of visible motion.
PIN_NOISE_MAX = 0.005
ONE_SHOT_MIN_LEN = 4  # a one-shot's length is the clip's own fact; only a degenerate cut is refused
# Gait half-period guard. A walk cycle is two steps; when the two halves look alike in
# pixels (legs hidden by a costume, near/far leg not distinguishable) the half period dips
# as deep as — or deeper than — the full one, and the "smallest minimum within 15 %"
# rule picks one step. Pixels cannot settle that case, so a duration prior does: a gait
# period shorter than the state's floor is treated as one step, and the doubled period is
# taken when it repeats about as well. The cost is asymmetric — a wrongly doubled cycle
# is still a clean two-cycle loop, a halved one walks on one leg.
GAIT_DOUBLE_TOL = 0.25
GAIT_DOUBLE_SEARCH = 3  # frames either side of 2x the step where the full gait's own minimum may sit
GAIT_NEAR_EXACT_STEP_FRACTION = 0.10  # no ambiguity extension when repeat error is tiny compared with a playback step
# `--steps`: how many steps the cut `--anchor motion-auto` takes holds, as counted by whoever looked at
# it (docs/loop-repair.md section 3, "The step screen"). 1 cuts again two of its lengths long, 2 keeps it.
STEPS = (1, 2)
# A cut two steps long (one cycle) that the clip holds less than this share of a cycle past is cut and
# warned about (`cycle.coverage`): its repeat is barely in the clip, and the seam gate says whether it
# closes. The search itself sees a window repeat only with a quarter of its length past it in the clip,
# so a cut under this was taken on the count alone (local_cycle.counted).
COVERAGE_MIN = 0.25
ANCHOR_MODES = ("none", "feet", "body", "motion", "motion-auto")
BODY_ANCHOR_BAND = 0.6  # --anchor body reads the wrap offset from the top 60 % of the first frame's box: head and torso, not the legs
BODY_ANCHOR_SEARCH = 24  # px either side searched for the last frame's horizontal offset against the first
FOOT_BAND = legs_mod.FOOT_BAND  # fraction of the frame's own height, measured up from its lowest opaque row
# Jump-frame repair (docs/loop-repair.md section 2). auto: walk and run loops get a RIFE frame in
# place of each frame that follows a jump, any other loop RIFE frames round a wrap that jumps (the
# seam bridge), and a loop that needs one where no RIFE is installed is cut as filmed with a warning
# (a walk that cut before RIFE existed still cuts); on: the same, but no RIFE fails the loop — a
# caller that asked for the repair is never handed less; off: the loop is cut as filmed, and says so.
REPAIR_MODES = ("auto", "on", "off")
# The overlapped wrap (docs/loop-repair.md section 2, "The overlapped wrap"), under --repair auto or on. on: a two-step
# cut whose wrap the gate refuses has the frames beside its wrap made again from the clip's frames a cycle apart, each
# read with RIFE's sixteen single passes, 34 calls of the binary a frame and up to forty frames; off: it is not tried, the
# report says so, and the gate refuses the cut as filmed — for a host whose RIFE runs on a CPU, where one frame of a
# 960 px clip takes minutes and the loop's caller would stop it before it fails on its own.
OVERLAP_WRAP_MODES = ("on", "off")
OVERLAP_WRAP_OFF = "turned off (--overlap-wrap off)"
# A frame the jump repair leaves off the walk's flow (`jump_repair.unfixed`: no group brings it back, or the group that
# would was refused), or a part that pops at the wrap of the cut taken (`part_pop.left`, a take to film again: `retake`).
# warn: a warning line, the loop kept; refuse: the loop fails.
UNFIXED_MODES = ("warn", "refuse")
SIZE_HOLD_MODES = ("auto", "off")
FACINGS = ("right", "left")
# Where `video-cycle-align` keeps the cut as filmed (sprite_gen/video/align.py). A new cut makes
# the kept one stale, so writing the cycle removes it: an alignment after a re-cut (say, once RIFE
# is installed and the jumps are repaired) reads the new cut, not the old one.
CYCLE_SOURCE_DIR = "cycle.source"
# The strip as it was before `video-sway` (the Sway layer; old name `video-follow`) moved a region of it;
# a later sway reads from it, and a new cut or alignment removes it (docs/video-pipeline.md section 6).
# Its name, and the key the sway's record is written under, stay `follow` in this release: a consumer reads
# them by those names. The record is read under either key.
FOLLOW_SOURCE = "follow.source.png"
# A refused cut hands nothing over (`refuse`); the frames it wrote, if it got that far, are kept here to look at.
CYCLE_REFUSED_DIR = "cycle.refused"
SWAY_RECORD = "follow"
SWAY_RECORD_KEYS = ("sway", "follow")
