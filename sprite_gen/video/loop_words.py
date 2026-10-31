# SPDX-License-Identifier: Apache-2.0
"""The sentences a loop report says about its repairs: ghost limbs, overlaps, pops and seam reasons.

Moved out of `loop.py` unchanged; `loop` re-exports every name, so `loop.<name>` still works."""

from __future__ import annotations

from typing import Any

from PIL import Image

from sprite_gen.video import part_pop as part_pop_mod
from sprite_gen.video import rife as rife_mod


def _ghost_lines(jump_repair: dict[str, Any]) -> list[str]:
    """What became of each filmed ghost the jump repair stage read, in words (`video-loop: warning:` lines)."""
    lines = []
    for r in jump_repair.get("rounds", []):
        if r.get("why") != "ghost":
            continue
        j = r["target"]
        line = (f"frame {j} of the cut carries a part-covered band over {100 * r['original']['ghost']:.2f} % of its body, at least "
                f"{rife_mod.GHOST_THICK} px thick — a filmed ghost; ")
        if r["taken"] == "rife":
            lines.append(line + f"given way to RIFE's frame between frames {r['neighbours'][0]} and {r['neighbours'][1]} "
                         f"(cycle/frame-{j:03d}.png) (docs/loop-repair.md section 2)")
            continue
        if r["taken"] is None:
            lines.append(line + f"and so are the frames beside it ({r['neighbours'][0]} and {r['neighbours'][1]}); kept, as there "
                         f"is no clean frame to give way to (cycle/frame-{j:03d}.png) — film this direction again (docs/loop-repair.md section 2)")
            continue
        if r["proposal"] is not None:
            why = f"RIFE's frame between its neighbours has a fault ({', '.join(r['faults'])})"
        elif r["no_proposal"] == "no interpolator":
            why = f"RIFE is not installed, so no frame between its neighbours was made (`{rife_mod.install_command()}`)"
        else:
            why = r["no_proposal"]
        lines.append(line + f"given way to frame {r['taken']['source']} beside it, shown twice in a row (cycle/frame-{j:03d}.png): "
                     f"{why} (docs/loop-repair.md section 2)")
    return lines


def _overlap_lines(overlap: dict[str, Any]) -> list[str]:
    """What became of the overlapped wrap (`repair.overlap_wrap`), in words (`video-loop: warning:` lines): the frames a
    person should look at where it was made, and why it was not where it was not."""
    said = (f"the cut's wrap does not close (seam {overlap['seam']:.2f} over {overlap['seam_max']:g}): a part on a beat of its "
            "own closes on no cut of two steps")
    if overlap["frames"]:
        taken = overlap["tries"][-1]
        return [f"frame {r['frame']} was made at the wrap between the clip's frame a cycle apart and its own, and may cross-fade "
                f"two drawings inside its outline (crossfade {r['proposal']['crossfade']:.4f}); kept, a reading to look at "
                "(docs/loop-repair.md section 2)" for r in taken["made"] if r["look"]]
    if "rife" in overlap:
        return [f"{said}, and RIFE is not installed, so the overlapped wrap was not made — run `{rife_mod.install_command()}` "
                "and cut it again (docs/loop-repair.md section 2)"]
    if "skipped" in overlap:
        return [f"{said}; the overlapped wrap was not made: {overlap['skipped']} (docs/loop-repair.md section 2)"]
    return [f"{said}, and the overlapped wrap was refused ({_overlap_refused(overlap)}): cut as filmed (docs/loop-repair.md section 2)"]


def _overlap_refused(overlap: dict[str, Any]) -> str:
    """Why each run of the overlapped wrap was refused, in words."""
    def why(t: dict[str, Any]) -> str:
        if "unmade" not in t:
            return ", ".join(t["rejected_for"])
        stopped = next(r for r in t["made"] if r["faults"])  # the first frame with a fault ended the run
        rest = ", the rest not made" if t["unmade"] else ""
        return f"{', '.join(t['rejected_for'])} ({', '.join(stopped['faults'])} at frame {stopped['frame']}{rest})"

    return "; ".join(f"{t['width']} frames {t['side']} the wrap: {why(t)}" for t in overlap["tries"])


def _unfixed_lines(jump_repair: dict[str, Any]) -> list[str]:
    """Each drawing the jump repair left off the walk's flow (`jump_repair.unfixed`), in words."""
    lines = []
    for u in jump_repair.get("unfixed") or []:
        frames = u["frames"]
        why = (f"the group that would bring it back was refused ({', '.join(u['refused'])}), so it plays as filmed"
               if "refused" in u else f"no group of up to {jump_repair['span']} drawings brings it under {jump_repair['limit']:g}")
        lines.append(f"frame{'s' if len(frames) > 1 else ''} {', '.join(map(str, frames))} of the loop lie{'' if len(frames) > 1 else 's'} "
                     f"{u['off']:.2f}x its window's usual step off the walk's flow after the jump repair: {why}")
    return lines


def _pop_settled(record: dict[str, Any], frames: list[Image.Image], cut: list[Image.Image]) -> None:
    """What became of a part that pops at the wrap of the cut taken (`part_pop`, docs/loop-repair.md section 2, "The part
    pop"), recorded: `outcome` — `none` where no part pops, `closed` where the jump repair changed frames of the cut and no
    part pops on the loop as it now plays, `named` where it plays as filmed — and the pops `left`, read again (`after`)
    where a frame of the cut was changed."""
    if not record["pops"]:
        record.update(outcome="none", left=[])
        return
    left = record["pops"]
    if any(a is not b for a, b in zip(frames, cut)):
        again = part_pop_mod.public(part_pop_mod.read(frames))
        record["after"] = {k: again[k] for k in ("drawings", "hold", "parts", "followed", "nearest", "pops")}
        left = again["pops"]
    record.update(outcome="named" if left else "closed", left=left)


def _pop_said(p: dict[str, Any], together: int = 1) -> str:
    """A part that pops (`part_pop.read`), in words; `together`, the parts that pop over the same run, the worst said."""
    return (f"the part at ({p['at'][0]:.0f}, {p['at'][1]:.0f}) px of frame {p['from']} moves {p['body_pct']:.2f} % of the body's "
            f"height into frame {p['to']}, {p['jerk_pct']:.2f} % off the curve its steps either side draw ({p['speed']:.1f}x its "
            f"usual step, {p['curve']:.1f}x the step that curve gives, {p['lead']:.2f}x its largest such jump elsewhere; it "
            f"{'comes back' if p['kind'] == 'flung' else 'stays'}{_beside(together)})")


def _beside(together: int) -> str:
    """The parts that pop with the one said, in words."""
    if together < 2:
        return ""
    return f"; {together - 1} part{'s' if together > 2 else ''} beside it pop{'' if together > 2 else 's'} with it"


def _pop_runs(pops: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int]]:
    """The pops (worst first) grouped by the run they pop over: the worst of each and how many pop there."""
    runs: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for p in pops:
        runs.setdefault((p["from"], p["to"]), []).append(p)
    return [(group[0], len(group)) for group in runs.values()]


RETAKE_REASON = "part-pop"


def part_retake(record: dict[str, Any], cut: tuple[int, int], *, given: bool) -> dict[str, Any] | None:
    """A take to film again where a part pops at the wrap of the loop as it plays (`part_pop.left`), as
    `video-cycle-align`'s `retake` is: `reason` (RETAKE_REASON), `reasons`, the numbers under `part_pop` — the pops left,
    the cut, and whether the search chose it (`searched`) — the `limits`, and `remedy`: `retake` where the engine's search
    chose the cut, `recut-or-retake` where it was given (`given`: --cycle fixed, or pinned). No other cut is taken for it.
    None where no part pops."""
    if not record.get("left"):
        return None
    return {"reason": RETAKE_REASON, "reasons": [RETAKE_REASON],
            "part_pop": {"pops": [{k: p[k] for k in ("seed", "from", "to", "at", "body_pct", "jerk_pct", "speed", "curve", "lead",
                                                     "kind", "margin")} for p in record["left"]],
                         "cut": list(cut), "searched": not given},
            "limits": {k: record[k] for k in ("floor", "speed", "curve", "lead")},
            "remedy": "recut-or-retake" if given else "retake"}


def _pop_lines(record: dict[str, Any], *, given: bool) -> list[str]:
    """Each pop that plays as filmed, a take to film again (`retake`), in words (`video-loop: warning:` lines)."""
    why = ("the cut was given (--cycle fixed or pinned): cut it where no part pops, or film it again" if given else
           "the search's cut is kept as cut, and no other cut is taken for a part: film it again")
    return [f"film this direction again ({RETAKE_REASON}) — {_pop_said(p, together)}: {why}" for p, together in
            _pop_runs(record.get("left") or [])]


def _step_reason(jump_repair: dict[str, Any], frames: set[int]) -> dict[str, Any]:
    """Why a step between drawings jumps, where the jump repair says it: a drawing of it (`frames`) left off the walk's
    flow (`jump_repair.unfixed`) — the group that would bring it back refused (`repair-refused`, its faults), or none
    found (`unfixed`) — a refused group's drawing first: the drawings beside it read off the flow because it is; with the
    failure line's `words`. Elsewhere the reason is not known (`reason` None)."""
    for u in sorted(jump_repair.get("unfixed") or [], key=lambda u: "refused" not in u):
        if not frames & set(u["frames"]):
            continue
        at = f"frame{'s' if len(u['frames']) > 1 else ''} {', '.join(map(str, u['frames']))} lie{'' if len(u['frames']) > 1 else 's'} off the walk's flow"
        if "refused" in u:
            return {"reason": "repair-refused", "frames": u["frames"], "refused": u["refused"],
                    "words": f": {at}, and the jump repair's frame there was refused ({', '.join(u['refused'])}), so it plays as filmed"}
        return {"reason": "unfixed", "frames": u["frames"],
                "words": f": {at}, and no group of up to {jump_repair['span']} drawings brings it back"}
    return {"reason": None}


def seam_reason(report: dict[str, Any], cycle: dict[str, Any]) -> dict[str, Any]:
    """Why the seam gate refuses a cut, where the warning lines say it: a cut of two steps by the clip's crossings whose
    wrap a part on a beat of its own leaves open (`own-beat`) — the overlapped wrap was asked (`wrap_overlap`,
    `_overlap_lines`), or no two-step cut closes (`local_cycle.step_verdict`). `overlap_wrap` says what became of the
    overlapped wrap (made, skipped, rife not installed, refused; None where it was not asked) and `words` the failure
    line's. Elsewhere the reason is not known (`reason` None)."""
    overlap = report.get("wrap_overlap")
    counted = cycle.get("step_count") or {}
    if overlap is None and not (counted.get("chosen_by") == "two steps" and not counted.get("gated")):
        return {"reason": None}
    words = "the cut covers two steps by the clip's crossings, and a part on a beat of its own (a tail, a held prop) closes on no cut of two steps"
    if overlap is None:
        outcome = None
    elif overlap["frames"]:
        outcome = "made"
        words += f": the overlapped wrap was made (frames {', '.join(map(str, overlap['frames']))}) and the wrap still does not close"
    elif "rife" in overlap:
        outcome = "rife not installed"
        words += ": RIFE is not installed, so the overlapped wrap was not made"
    elif "skipped" in overlap:
        outcome = "skipped"
        words += f": the overlapped wrap was not made ({overlap['skipped']})"
    else:
        outcome = "refused"
        words += f": the overlapped wrap was refused ({_overlap_refused(overlap)})"
    return {"reason": "own-beat", "overlap_wrap": outcome, "words": words}
