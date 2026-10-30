# SPDX-License-Identifier: Apache-2.0
"""Select an observed local repeat after camera/subject drift analysis.

A changing cadence can contain a good cycle without one period fitting the
whole clip. Require a local lag minimum, repeat depth, motion context, and the
same two-step duration prior; an isolated matching endpoint is insufficient.
The local period taken is screened for two or three cycles and the finding
recorded (`fundamental`, sprite_gen/video/period.py), never cut shorter; the
legs are read off `signals` (`legs.strike_signals` of the analysed frames).
The cut taken is screened the other way too, for one step of a cycle twice as
long that the window could not hold (`step_screen`), never cut longer on it:
only a count of the steps cuts again — given (`video-loop --steps`), or read off
the clip's own crossings (`by_steps`, sprite_gen/video/crossings.py): where the
clip shows its steps, the cut is two of them, joined at the walk's own point,
the smoothest seam first.
"""
from __future__ import annotations
import math
from sprite_gen._deps import np
from sprite_gen.video import period as period_mod
from sprite_gen.video.crossings import HIDE_SHARE

REFUSED_SHOWN = 12  # refused windows a failed search reports, as many as a found cycle's candidates
# A two-step cut's seam is as smooth as the smoothest in-phase one's within this much more flow off (`_rank`): about
# what an ordinary frame of a walk lies off the line between its neighbours, in its window's usual steps.
SEAM_FLOW_BAND = 0.5
REPEAT_OVER_STEP_MAX = 2  # `detect` refuses a repeat further off than two playback steps ('repeat error over two steps')
SEAM_TRIES = 4  # two-step cuts, best first, whose seam is read as the seam gate reads it before one is taken (`by_steps`)
# The clip a cut on, frame by frame along the cut, at most this many of the cut's playback steps off: the same picture
# again (`same_picture`). An odd count of steps swaps the legs a cut on, and a step drawn nearer than the next frame
# is no swap: the count missed a crossing — asked only where a pass can hide (crossings.HIDE_SHARE), since from the
# side a step on can draw as near as that, its legs alike.
SAME_PICTURE_MAX = 1.0


def _around(start, length):
    """The frames whose repeats are compared for a cut at `start`: a quarter of the cut either side."""
    radius = max(2, length//4)
    return radius, np.arange(max(0, start-radius), start+radius+1)


def _profile(distances, js, radius, lo, hi):
    n = len(distances)
    profile = {}
    for lag in range(max(2, lo//2), hi+2):
        observed = js[js+lag < n]
        if len(observed) >= radius+1:
            profile[lag] = float(distances[observed, observed+lag].mean())
    return profile


def _measure(distances, trajectory, start, length, profile):
    """The parts of a window's score, for a window the search refused (`refused`): the same sums as
    a candidate's, so a refused window and a candidate read alike."""
    step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
    if step <= 1e-10 or length not in profile:
        return None
    ratio = float(distances[start+length-1, start])/step
    x = np.arange(length)
    segment = trajectory[start:start+length]
    residual = float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean())
    error = profile[length]
    return {'start': start, 'length': length, 'score': error/step + .3*abs(math.log(max(.01, ratio))) + residual,
            'ratio': ratio, 'context_repeat_over_step': error/step, 'drift_line_residual_analysis_px': residual}


def _closure(distances, start, length):
    """How far a cut's wrap is from the clip's own way on there: its last frame against the clip's frame
    before its first, and the frame before its last against the one before that (the speed into the
    wrap), over the cut's mean playback step. None for a cut with fewer than two clip frames before it."""
    step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
    if start < 2 or step <= 1e-10:
        return None
    return float(distances[start+length-1, start-1] + distances[start+length-2, start-2])/2/step


def _read(measured):
    """A cut's pop as `cycle.seam_pop` records it: the jump, or why its top band could not be read."""
    return {'skipped': measured['skipped']} if 'skipped' in measured else {'pop': measured['pop']}


def _mark(row, measured):
    """A window row's pop: `seam_pop`, or None with `seam_pop_skipped` (why) when its top band could
    not be read — an unread window is neither taken as closing nor as popping."""
    row['seam_pop'] = measured.get('pop')
    if 'skipped' in measured:
        row['seam_pop_skipped'] = measured['skipped']


def _held(measured):
    """A window's held side as `cycle.seam_pop.held_edge` records it: its seam, or why it could not be read."""
    return {'skipped': measured['skipped']} if 'skipped' in measured else {'seam': measured['seam'], 'closes': measured['closes']}


def _held_rank(candidates, calm, chosen, wrap_pop, held_edge, length_cap=None, double=True):
    """Below the top band (`held_edge`, repair.held_edge on the side the popping first choice holds its
    part): among the calm candidates, those whose held side closes, the same score tolerance, then the
    smallest held seam. When no candidate closes it, windows twice a candidate's length: the candidate
    was one step of a walk whose arm, and the part it holds, swing once a cycle — two steps — and close
    only there (`steps` 2: the window is one cycle, `step_lengths` the candidates it is two of — each one
    step). Such a window is two lengths of a repeat the search confirmed, not itself seen repeating; it
    is taken when its held side closes and its top does not pop, the smallest held seam first. A window longer than `length_cap`
    (an explicit --max-len) is not one, and none is read without `double` (the steps were counted,
    `video-loop --steps`). Returns (picked or None, record)."""
    side = held_edge.side(chosen['start'], chosen['length'])
    if 'skipped' in side:
        return None, {'method': 'held-side-edge-v1', 'skipped': side['skipped']}
    side = side['side']
    record = {'method': 'held-side-edge-v1', 'side': side, 'reference': held_edge.reference}
    closing = []
    unread = 0
    for row in calm:
        measured = held_edge.seam(row['start'], row['length'], side)
        row['held_edge'] = measured.get('seam')
        if 'skipped' in measured:
            row['held_edge_skipped'] = measured['skipped']
            unread += 1
        elif measured['closes']:
            closing.append(row)
    record.update(measured=len(calm)-unread, unread=unread)
    if closing:
        cutoff = min(row['score'] for row in closing)*1.15+1e-8
        picked = min((row for row in closing if row['score'] <= cutoff),
                     key=lambda row: (row['held_edge'], row['seam_pop'], row['score'], row['start'], row['length']))
        record.update(doubled=False, chosen={'start': picked['start'], 'length': picked['length'], 'seam': picked['held_edge'], 'closes': True})
        return picked, record
    if not double:
        record['two_step'] = {'read': False, 'why': 'the steps were counted (--steps): no window twice as long is read'}
        return None, record
    lengths = sorted({2*row['length']+d for row in candidates for d in (-1, 0, 1)})
    if length_cap is not None:
        over = [length for length in lengths if length > length_cap]
        lengths = [length for length in lengths if length <= length_cap]
        if over:
            record['two_step_capped'] = {'max_len': length_cap, 'lengths_over': [over[0], over[-1]]}
        if not lengths:
            return None, record
    windows = []
    counts = {'measured': 0, 'unread': 0, 'top_pops': 0}
    for length in lengths:
        for start in range(held_edge.frames-length+1):
            measured = held_edge.seam(start, length, side)
            if 'skipped' in measured:
                counts['unread'] += 1
                continue
            counts['measured'] += 1
            if not measured['closes']:
                continue
            top = wrap_pop(start, length)
            if 'skipped' in top or top['pops']:
                counts['top_pops'] += 1  # a top that pops, or one that could not be read, is not taken
                continue
            windows.append({'start': start, 'length': length, 'period_local': length,
                            'steps': {'count': 2, 'by': 'held-edge',
                                      'step_lengths': sorted({row['length'] for row in candidates if abs(2*row['length']-length) <= 1})},
                            'held_edge': measured['seam'], 'seam_pop': top['pop'],
                            'repeat': 'two lengths of a confirmed repeat (two steps, one cycle), not itself seen repeating'})
    record['two_step'] = {'read': True, 'lengths': [lengths[0], lengths[-1]], **counts, 'closing': len(windows)}
    if windows:
        picked = min(windows, key=lambda row: (row['held_edge'], row['seam_pop'], row['start'], row['length']))
        record.update(doubled=True, chosen={'start': picked['start'], 'length': picked['length'], 'seam': picked['held_edge'], 'closes': True})
        return picked, record
    return None, record


def _shape_rank(candidates, chosen, wrap_pop, held_edge=None, length_cap=None, double=True):
    """Choose again when the chosen cut's top pops at the wrap (`wrap_pop`, repair.seam_pop on the
    analysed frames): among the candidates whose top was read and does not pop, the same score
    tolerance, then the smallest pop. Nothing is measured past the first choice when it does not pop,
    or when its top could not be read (kept, `skipped`), so a cut without a thin part swinging on its
    own beat is chosen exactly as before. A candidate whose top could not be read is not chosen.

    With `held_edge` (loop.HeldEdge), a popping first choice has its held part read below the top too
    (`_held_rank`): a cut whose held side closes is taken first, one or two lengths long. Where none
    closes, the choice is the top band's, and `held_edge.chosen` records that it does not close (the
    jolt warns, repair.held_edge_verdict). A window two lengths long is never longer than `length_cap`,
    nor read without `double`."""
    first = wrap_pop(chosen['start'], chosen['length'])
    record = {'method': 'top-band-wrap-v1', 'reference': first['reference'],
              'first_choice': {'start': chosen['start'], 'length': chosen['length'], **_read(first)},
              'applied': False}
    if 'skipped' in first or not first['pops']:
        return chosen, record
    calm = []
    unread = 0
    for row in candidates:
        measured = wrap_pop(row['start'], row['length'])
        _mark(row, measured)
        if 'skipped' in measured:
            unread += 1
        elif not measured['pops']:
            calm.append(row)
    record.update(measured=len(candidates)-unread, unread=unread)
    held = None
    if held_edge is not None:
        picked, held = _held_rank(candidates, calm, chosen, wrap_pop, held_edge, length_cap, double)
        record['held_edge'] = held
        if picked is not None:
            record.update(applied=True, chosen={'start': picked['start'], 'length': picked['length'], 'pop': picked['seam_pop'],
                                                **({'steps': 2} if 'steps' in picked else {})})
            return picked, record
    if not calm:
        record['why'] = 'every candidate whose top was read pops at the wrap; the first choice is kept'
        picked = chosen
    else:
        cutoff = min(row['score'] for row in calm)*1.15+1e-8
        picked = min((row for row in calm if row['score'] <= cutoff),
                     key=lambda row: (row['seam_pop'], row['score'], row['start'], row['length']))
        record.update(applied=True, chosen={'start': picked['start'], 'length': picked['length'], 'pop': picked['seam_pop']})
    if held is not None and 'side' in held:
        measured = held_edge.seam(picked['start'], picked['length'], held['side'])
        held['chosen'] = {'start': picked['start'], 'length': picked['length'], **_held(measured)}
    return picked, record


def _pop_said(read):
    """A cut's top band at the wrap as the seam pop gate reads it before the cut (`pop_gate`), for the record: the pop,
    and the gate's word — `refused` over its bound, `passed`, or `unreadable` (`pop_skipped`, why), which it does not
    refuse."""
    if 'skipped' in read:
        return {'pop': None, 'pop_skipped': read['skipped'], 'pop_gate': 'unreadable'}
    return {'pop': read['pop'], 'pop_gate': 'refused' if read['over'] else 'passed'}


def _pop_gate(chosen, rest, pop_gate):
    """The seam pop gate before the cut. `pop_gate(start, length)` reads a cut's top band at the wrap as the gate reads
    the loop — its frames corrected as the cut will be (loop.CutOnCells): `{'pop', 'over', 'max'}`, or `{'skipped',
    'max'}` where the top band cannot be read, which the gate does not refuse. The chosen cut is read first; where the
    gate would refuse it, the next of `rest` (in the caller's order), and the first of SEAM_TRIES the gate does not
    refuse is taken — as `by_steps` takes the first two-step cut its seam gate passes. Where it would refuse them all,
    the first choice stays, and the gate refuses it on the loop. Returns (the cut, the record: `max`, `tried`,
    `applied`, and `why` where none passed)."""
    tried = []
    picked = None
    bound = None
    for row in [chosen, *rest][:SEAM_TRIES]:
        read = pop_gate(row['start'], row['length'])
        bound = read['max']
        tried.append({'start': row['start'], 'length': row['length'], **_pop_said(read)})
        if tried[-1]['pop_gate'] != 'refused':
            picked = row
            break
    record = {'max': bound, 'tried': tried, 'applied': picked is not None and picked is not chosen}
    if picked is None:
        record['why'] = (f"the gate refuses each of the {len(tried)} cuts read: the first choice is kept, and the gate "
                         "refuses it on the loop")
        picked = chosen
    return picked, record


def counted(distances, trajectory, *, min_len, max_len, periodicity_min, signals=None, pop_gate=None):
    """A cut of a length someone counted (`video-loop --steps`) where the clip is too short to see a
    window that long repeat (`detect` found none): the counted length stands for the repeat. Every
    window [min_len, max_len] long with two clip frames before it is read, and the one whose wrap is
    nearest the clip's own way on there is taken (`_closure`, the earliest on a tie); whether it closes
    is the seam gate's word. With `pop_gate`, a window the seam pop gate would refuse gives way to the next nearest
    (`_pop_gate`, `seam_pop_gate`). Raises ValueError where no window has room."""
    n = len(distances)
    rows = []
    for length in range(min_len, max_len+1):
        for start in range(2, n-length+1):
            measured = _closure(distances, start, length)
            if measured is None:
                continue
            step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
            x = np.arange(length)
            segment = trajectory[start:start+length]
            rows.append({'start': start, 'length': length, 'period_local': length, 'closure': measured,
                         'ratio': float(distances[start+length-1, start])/step,
                         'drift_line_residual_analysis_px': float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean()),
                         'half_period_guard': {'applied': False}})
    if not rows:
        raise ValueError(f"no window {min_len}-{max_len} frames long has two clip frames before it in {n}")
    rows.sort(key=lambda row: (row['closure'], row['start'], row['length']))
    chosen, gate = _pop_gate(rows[0], rows[1:], pop_gate) if pop_gate is not None else (rows[0], None)
    return {**chosen, 'kind': 'periodic', 'method': 'counted-window-v1',
            **({'seam_pop_gate': gate} if gate is not None else {}),
            'repeat': 'not seen in the clip: the counted length stands for it', 'choice': 'closure',
            'period_global': None, 'periodicity_min': periodicity_min, 'review_recommended': True,
            'candidate_count': len(rows), 'candidates': rows[:REFUSED_SHOWN],
            'step_screen': period_mod.step_screen(distances, start=chosen['start'], length=chosen['length'],
                                                  periodicity_min=periodicity_min, signals=signals)}


def _screens(distances, chosen, *, lo, hi, gait_floor, periodicity_min, signals):
    """The cut's two screens, as `detect` records them: below it for two or three cycles (`fundamental`,
    period.screen on the local profile about it) and above it for one step of a cycle twice as long
    (`step_screen`, period.step_screen over the whole clip)."""
    radius, js = _around(chosen['start'], chosen['length'])
    profile = _profile(distances, js, radius, lo, hi)
    fundamental = period_mod.screen(
        chosen['period_local'], D=distances, prof=profile, minima=period_mod.local_minima(profile),
        mean=float(np.mean(list(profile.values()))), lowest=max(lo, gait_floor), periodicity_min=periodicity_min,
        signals=signals, gait=True, js=js, floor_why='the gait floor' if gait_floor >= lo else 'the window')
    steps = period_mod.step_screen(distances, start=chosen['start'], length=chosen['length'],
                                   periodicity_min=periodicity_min, signals=signals)
    return fundamental, steps


def judged(rows, judge):
    """Each window row's steps as the clip's crossings count them (`step_count`: verdict, code, k, phase)."""
    for row in rows:
        counted = judge(row['start'], row['length'])
        row['step_count'] = {k: counted[k] for k in ('verdict', 'code', 'k', 'phase') if k in counted}
    return rows


def detect(distances, trajectory, *, min_len, max_len, gait_floor,
           periodicity_min, double_tolerance, double_search, max_fraction=.5, signals=None, wrap_pop=None,
           held_edge=None, length_cap=None, double=True, closure=False, judge=None, pop_gate=None):
    """`wrap_pop(start, length)`, when given, is the top band's jump at that cut's wrap
    (repair.seam_pop): a chosen cut that pops is chosen again (`_shape_rank`), and a search that finds
    nothing raises with the windows it refused, popping ones last (`ValueError.diagnostics`).

    `pop_gate(start, length)`, when given, is the seam pop gate's reading of a cut before it is cut (`_pop_gate`): the
    cut chosen — after `_shape_rank` — that the gate would refuse gives way to the next of the candidates in the
    search's order (the equivalent ones earliest first, then the rest best first), the first of SEAM_TRIES the gate
    does not refuse (`seam_pop_gate` in the result).
    `held_edge` (loop.HeldEdge) reads the held side below the top of a cut chosen again; the cut it
    takes may be two lengths long (`steps` in the result: two steps, one cycle), but never longer than
    `length_cap`, nor with `double` false.

    `length_cap` is the caller's own ceiling on the cut — an explicit --max-len, or the window a count
    of steps sets (`video-loop --steps`) — which `max_len` alone does not mark (the state's default
    window is one too). It is the window's top as given: the share of the clip (`max_fraction`) bounds
    only a window the caller did not set. Each window still needs its repeat seen in the clip.

    Among the equivalent candidates the earliest is taken; with `closure` (a length the caller counted,
    `video-loop --steps`), the one whose wrap is nearest the clip's own way on there (`_closure`). The
    cut taken is screened for one step of a cycle twice as long (`step_screen`, period.step_screen,
    over the whole clip); nothing is cut on it.

    `judge(start, length)` (crossings.Reading.judge), when given, refuses a window whose steps the clip's
    crossings count as other than two ('one step', 'three steps': `refuted`), and a search that finds
    nothing names each refused window's count (`step_count`), those the crossings refute last."""
    n = len(distances)
    # A cycle has to be seen repeating: half the clip by default, more for the gait fallback, and as
    # long as the caller's own ceiling allows.
    lo = max(6, min_len)
    hi = min(max_len, length_cap) if length_cap is not None else min(max_len, int(n*max_fraction))
    if hi < lo:
        raise ValueError(f"automatic motion cycle window [{lo}, {hi}] has no repeated cycle")
    candidates = []
    refused = []
    # A window's repeat profile depends on its frames (start, radius) only, and radius = length//4 is the
    # same for about four lengths in a row: each is measured once and read, never changed, by every length.
    profiles = {}
    for length in range(max(lo, gait_floor), hi+1):
        radius = max(2, length//4)
        for start in range(n-length-radius-1):
            _, js = _around(start, length)
            profile = profiles.get((start, radius))
            if profile is None:
                profile = profiles[(start, radius)] = _profile(distances, js, radius, lo, hi)

            def refuse(why, start=start, length=length, profile=profile):
                row = _measure(distances, trajectory, start, length, profile)
                if row is not None:
                    refused.append({**row, 'refused': why})
            minima = [lag for lag in profile if lag-1 in profile and lag+1 in profile
                      and profile[lag] <= min(profile[lag-1], profile[lag+1]) and lag <= hi]
            if not minima:
                refuse('no local repeat minimum')
                continue
            deepest = min(profile[lag] for lag in minima)
            period = min(lag for lag in minima if profile[lag] <= deepest*1.15+1e-4)
            guard = {'applied': False}
            if period < gait_floor:
                doubles = [lag for lag in minima if abs(lag-2*period) <= double_search and lag >= gait_floor]
                if not doubles:
                    refuse('one step: no repeat near twice it')
                    continue
                doubled = min(doubles, key=profile.get)
                if profile[doubled] > profile[period]*(1+double_tolerance)+1e-4:
                    refuse('one step: twice it repeats worse')
                    continue
                guard = {'applied': True, 'from': period, 'to': doubled,
                         'gait_floor': gait_floor, 'reason': 'local-two-step-repeat'}
                period = doubled
            # Allow one frame of cut quantization around the measured local lag.
            if abs(length-period) > 1 or profile.get(length, math.inf) > profile[period]*1.15+1e-4:
                refuse(f'the local repeat is {period} frames')
                continue
            baseline = float(np.mean(list(profile.values())))
            error = profile[length]
            depth = 1-error/baseline if baseline > 0 else 0
            step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
            if depth < periodicity_min or step <= 1e-10 or error/step > 2:
                refuse('repeat too shallow' if depth < periodicity_min else 'repeat error over two steps')
                continue
            # A still/rest segment matching another still is not a repeated gait.
            first_motion = float(distances[js[:-1], js[1:]].mean())
            second_motion = float(distances[js[:-1]+length, js[1:]+length].mean())
            if min(first_motion, second_motion) < .25*step:
                refuse('a still segment')
                continue
            if judge is not None:
                counted = judge(start, length)
                if counted['verdict'] == 'refuted':
                    refuse(f"{counted['why']} in the clip's crossings")
                    continue
            ratio = float(distances[start+length-1, start])/step
            x = np.arange(length)
            segment = trajectory[start:start+length]
            residual = float(np.abs(segment-np.polyval(np.polyfit(x, segment, 1), x)).mean())
            score = error/step + .3*abs(math.log(max(.01, ratio))) + residual
            candidates.append({
                'start': start, 'length': length, 'period_local': period, 'score': score,
                'ratio': ratio, 'periodicity': depth, 'context_repeat_over_step': error/step,
                'context_pair_range': [int(js[0]), int(js[-1])+1],
                'context_motion_over_step': [first_motion/step, second_motion/step],
                'drift_line_residual_analysis_px': residual, 'half_period_guard': guard,
            })
    if not candidates:
        exc = ValueError("no periodic cycle found — no supported local two-step repeat after drift analysis")
        # The windows the search measured and refused, best first: a caller that must deliver anyway
        # (a forced cut) takes one the engine measured instead of the whole clip.
        refused.sort(key=lambda row: (row['score'], row['start'], row['length']))
        shown = refused[:REFUSED_SHOWN]
        if judge is not None:
            judged(shown, judge)
        if wrap_pop is not None:
            # Those whose top closes first, then those whose top could not be read, then those that pop.
            rank = {}
            for row in shown:
                measured = wrap_pop(row['start'], row['length'])
                _mark(row, measured)
                rank[row['start'], row['length']] = 1 if 'skipped' in measured else 2 if measured['pops'] else 0
            shown.sort(key=lambda row: (rank[row['start'], row['length']], row['score'], row['start'], row['length']))
        if judge is not None:
            shown.sort(key=lambda row: row['step_count']['verdict'] == 'refuted')
        exc.diagnostics = {'kind': 'periodic', 'method': 'local-repeat-drift-v1', 'status': 'refused',
                           'window': [lo, hi], 'refused_count': len(refused),
                           'order': 'score' + (', windows whose top could not be read after those whose top closes,'
                                               ' windows whose top pops at the wrap last' if wrap_pop is not None else '')
                                    + (', windows whose steps the crossings refute after the rest' if judge is not None else ''),
                           'candidates': shown}
        raise exc
    candidates.sort(key=lambda row: (row['score'], row['start'], row['length']))
    # Equivalent-quality candidates: prefer the earliest demonstrated repeat.
    cutoff = candidates[0]['score']*1.15+1e-8
    equivalent = [r for r in candidates if r['score'] <= cutoff]
    if closure:
        # A length counted, not found: its window was never the search's, and the earliest of the
        # equivalent cuts need not be the one that closes — the one whose wrap the clip itself plays is.
        for row in equivalent:
            row['closure'] = _closure(distances, row['start'], row['length'])
        chosen = min(equivalent, key=lambda row: (row['closure'] is None, row['closure'] or 0.0, row['start'], row['score'], row['length']))
    else:
        chosen = min(equivalent, key=lambda row: (row['start'], row['score'], row['length']))
    shape = None
    if wrap_pop is not None:
        chosen, shape = _shape_rank(candidates, chosen, wrap_pop, held_edge, length_cap, double)
    gate = None
    if pop_gate is not None:
        rest = sorted((row for row in candidates if (row['start'], row['length']) != (chosen['start'], chosen['length'])),
                      key=lambda row: ((0, row['start'], row['score'], row['length']) if row['score'] <= cutoff
                                       else (1, row['score'], row['start'], row['length'])))
        chosen, gate = _pop_gate(chosen, rest, pop_gate)
    if 'ratio' not in chosen:
        # A window two lengths long (`_held_rank`) was not a candidate: its wrap over its step, as a candidate's.
        s, L = chosen['start'], chosen['length']
        chosen['ratio'] = float(distances[s+L-1, s])/float(np.diag(distances[s:s+L, s:s+L], 1).mean())
    fundamental, steps = _screens(distances, chosen, lo=lo, hi=hi, gait_floor=gait_floor,
                                  periodicity_min=periodicity_min, signals=signals)
    return {
        **chosen, 'fundamental': fundamental, 'kind': 'periodic', 'method': 'local-repeat-drift-v1',
        'period_global': None, 'periodicity_min': periodicity_min,
        'review_recommended': True, 'candidate_count': len(candidates),
        'best_score': candidates[0]['score'], 'equivalent_score_tolerance': .15,
        'candidates': candidates[:12],
        **({'choice': 'closure'} if closure else {}),
        **({'seam_pop': shape} if shape is not None else {}),
        **({'seam_pop_gate': gate} if gate is not None else {}),
        'step_screen': steps,
    }


def _worse(row):
    return max(row['seam_flow']['off'])


def _calm(row):
    """The cut's top band was read at the wrap and does not pop (`wrap_pop`)."""
    return 'pops' in row['top'] and not row['top']['pops']


def same_picture(distances, start, length):
    """How far the clip one cut on lies from the cut, frame by frame along it, in the cut's playback steps: the mean of
    each frame's distance to the frame `length` on, over the cut's mean step. None where under half the cut has a
    frame one cut on in the clip, or the cut does not move."""
    n = len(distances)
    js = np.arange(start, start+length)
    js = js[js+length < n]
    step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
    if len(js) < length/2 or step <= 1e-10:
        return None
    return float(distances[js, js+length].mean())/step


def same_picture_count(reading, distances, judged):
    """A cut the crossings count an odd number of steps (`judged`, `reading.judge`), asked whether the count missed a
    crossing: where the feet are seen together over more than HIDE_SHARE of a step at a pass (`reading.overlap`) and the
    clip a cut on is the same picture (`same_picture`, at most SAME_PICTURE_MAX playback steps along the cut), no legs
    swapped. Returns (what was read, None where it is not asked; the cut's count, `uncertain` (`same-picture`) where the
    count missed a crossing, else `judged` itself)."""
    overlap = reading.overlap
    if not (judged['verdict'] == 'refuted' and judged['k'][0] % 2 and overlap is not None and overlap > HIDE_SHARE):
        return None, judged
    near = same_picture(distances, *judged['cut'])
    said = {'over_step': None if near is None else round(near, 3), 'max': SAME_PICTURE_MAX, 'overlap': round(overlap, 3),
            'hides': HIDE_SHARE}
    if near is None or near > SAME_PICTURE_MAX:
        return said, judged
    return said, {**judged, 'verdict': 'uncertain', 'code': 'same-picture',
                  'why': f"the crossings count {judged['why']}, but the feet are seen together {overlap:.2f} of a step at a "
                         f"pass (over {HIDE_SHARE:g}: a pass can hide there) and the clip a cut on is the same picture ({near:.2f} "
                         f"playback steps along the cut, at most {SAME_PICTURE_MAX:g}): no legs swapped, a crossing unseen"}


def _rank(rows, *, flow_limit, seam_max, in_phase):
    """Two-step cuts, best first, and how many of the first passed the gates: in phase (`in_phase`), its top band calm
    at the wrap (`_calm`: a thin part the flow hardly sees, a staff, does not jump), its wrap over its step (`ratio`) at
    most `seam_max` (what the seam gate holds a loop to) and both seam frames' flow off under `flow_limit`, the worse
    within SEAM_FLOW_BAND of the smoothest such cut's, then the wrap nearest the clip's own way on (`_closure`); after
    those the rest, in phase first, then calm, then under the flow limit, then under `seam_max`, then by the worse flow."""
    gated = [row for row in rows if in_phase(row) and _calm(row) and row['ratio'] <= seam_max and _worse(row) < flow_limit]
    best = min((_worse(row) for row in gated), default=None)
    band = sorted((row for row in gated if _worse(row) <= best + SEAM_FLOW_BAND),
                  key=lambda row: (row['closure'] is None, row['closure'] or 0.0, _worse(row), row['start'], row['length']))
    rest = sorted((row for row in rows if row not in band),
                  key=lambda row: (not in_phase(row), not _calm(row), _worse(row) >= flow_limit, row['ratio'] > seam_max, _worse(row),
                                   row['start'], row['length']))
    return band + rest, len(band)


def _gates(tried, seam_max):
    """A cut read before it is cut passes its seam gate (`seam` at most `seam_max`) and the seam pop gate does not
    refuse it (`pop_gate`, where it was read)."""
    return tried['seam'] <= seam_max and tried.get('pop_gate') != 'refused'


def by_steps(distances, trajectory, reading, *, found, refused, min_len, max_len, seam_flow, wrap_pop, seam_gate, flow_limit,
             seam_max, gait_floor, periodicity_min, signals, pop_gate=None):
    """The cut the clip's own steps choose (`reading`, crossings.Reading), after the pixel search: `found`, its cut,
    or `refused`, why it found none. Returns (the cut, or None where the search's word stands and it found none;
    `step_count`, the record).

    - The clip does not show its steps (`reading.readable` false: no feet seen, a lean that is not feet stepping,
      under two steps): the search's cut, as it chose it.
    - The search's cut covers two steps (`two`) and its wrap joins the walk at its own point (`reading.in_phase`):
      that cut, as it chose it.
    - The crossings count the search's cut an odd number of steps — the legs swapped a cut on — but the feet are seen
      together over more than HIDE_SHARE of a step at a pass (`reading.overlap`: the far foot behind the near one, a
      pass can hide there) and the clip a cut on is the same picture (`same_picture`, at most SAME_PICTURE_MAX
      playback steps along the cut): no swap, so the count missed a crossing and proves nothing. The search's cut, as
      it chose it, its count `uncertain` (`same-picture`). Where passes are brief — from the side, where a step on
      draws as near as the next frame with the legs alike — the count stands.
    - Else the cuts [min_len, max_len] frames long that cover two steps, any start, however long — the window that
      binds the search binds no count; none, and the search's word stands. Those in phase, if any, else all, are
      read at the wrap (`seam_flow(start, length)`: the flow off of the cut's last and first frames; `wrap_pop`, the
      top band's jump, repair.seam_pop) and ranked (`_rank`). Of the first SEAM_TRIES the first whose seam the seam
      gate passes (`seam_gate`: its wrap over its step as the gate will read the cut, at most `seam_max`) is taken,
      where none the first, and the gate says whether it closes (`method: observed-two-steps-v1`). The pixel
      search's own repeat rule is not asked: a part off the legs' beat (a tail) repeats on its own and its seam is
      the flow repair's; `repeat` records it.
    - A search's cut the count does not refute (`two` out of phase, `uncertain`) whose own seam the seam gate passes
      is replaced only by a two-step cut that closes: one of the cuts that passed the gates (`_rank`) whose seam the
      seam gate passes. Where none does, the search's cut, as it chose it (`declined`: the best two-step cut and why
      it does not close). A search's cut the gate refuses too gives way as before: the two-step cut's wrap may still
      be closed by the overlapped wrap (repair.overlap_wrap), and the gate says whether it is.

    With `pop_gate` (the seam pop gate's reading before the cut, `_pop_gate`), a cut the seam gate passes is read for
    its top band too, and passes only where the seam pop gate would not refuse it: of the two-step cuts the first
    that passes both is taken, and the search's cut stands only where both pass it (`pop`, `pop_gate` beside `seam`).

    A cut taken on two steps records `steps` (count 2, by crossings): `video-cycle-align` reads a return half way
    as its step, and the step screen's pixel suspicion of one step (`step_screen`) is settled, kept as
    `pixel_suspect`."""
    record = reading.record()
    record['search'] = ({'cut': [found['start'], found['length']]} if found is not None else {'refused': str(refused)})
    if not reading.readable:
        record['chosen_by'] = 'search'
        if found is not None:
            record['cut'] = reading.judge(found['start'], found['length'])
        return found, record
    searched = None
    if found is not None:
        searched = reading.judge(found['start'], found['length'])
        record['search'].update({k: searched[k] for k in ('verdict', 'code', 'k', 'phase') if k in searched})
        if reading.in_phase(searched):
            record.update(chosen_by='search', cut=searched)
            return _two_steps(found, record), record
        said, counted = same_picture_count(reading, distances, searched)
        if said is not None:
            record['search']['same_picture'] = said
            if counted is not searched:
                record.update(chosen_by='search', cut=counted)
                return found, record
    twos = reading.twos(min_len, max_len)
    record['two_cuts'] = len(twos)
    if not twos:
        record['chosen_by'] = 'search'
        if found is not None:
            record['cut'] = reading.judge(found['start'], found['length'])
        return found, record
    pool = [row for row in twos if reading.in_phase(row)]
    record['in_phase'] = len(pool)
    rows = []
    for counted in pool or twos:
        start, length = counted['cut']
        step = float(np.diag(distances[start:start+length, start:start+length], 1).mean())
        rows.append({'start': start, 'length': length, 'phase': counted['phase'], 'counted': counted,
                     'seam_flow': seam_flow(start, length), 'top': wrap_pop(start, length), 'closure': _closure(distances, start, length),
                     'ratio': float(distances[start+length-1, start])/step if step > 1e-10 else math.inf})
    ranked, band = _rank(rows, flow_limit=flow_limit, seam_max=seam_max, in_phase=lambda row: reading.in_phase(row['counted']))
    tried = []
    for row in ranked[:SEAM_TRIES]:
        tried.append({'start': row['start'], 'length': row['length'], 'seam': round(seam_gate(row['start'], row['length']), 3)})
        if pop_gate is not None and tried[-1]['seam'] <= seam_max:
            tried[-1].update(_pop_said(pop_gate(row['start'], row['length'])))
        if _gates(tried[-1], seam_max):
            break
    passed = [t for t in tried if _gates(t, seam_max)]
    picked = next(row for row in ranked if (row['start'], row['length']) == ((passed[0]['start'], passed[0]['length']) if passed
                                                                           else (tried[0]['start'], tried[0]['length'])))
    record['seam_gate'] = {'max': seam_max, 'tried': tried}
    start, length = picked['start'], picked['length']
    if searched is not None and searched['verdict'] != 'refuted' and not (passed and ranked.index(picked) < band):
        # A cut the count does not refute, and the seam gate passes, stands until a two-step cut closes in its place.
        own = round(seam_gate(found['start'], found['length']), 3)
        record['search']['seam'] = own
        if pop_gate is not None and own <= seam_max:
            record['search'].update(_pop_said(pop_gate(found['start'], found['length'])))
        if _gates(record['search'], seam_max):
            record.update(chosen_by='search', cut=searched, flow_limit=flow_limit, seam_max=seam_max, declined={
                'cut': [start, length], 'phase': picked['phase'], 'gated': ranked.index(picked) < band,
                'seam': next(t['seam'] for t in tried if (t['start'], t['length']) == (start, length))})
            return (_two_steps(found, record) if searched['verdict'] == 'two' else found), record
    record.update(chosen_by='two steps', cut=picked['counted'], gated=bool(band), flow_limit=flow_limit, seam_max=seam_max)
    # The pixel search's own word on this cut: its repeat, read as `detect` reads a window it refused.
    radius, js = _around(start, length)
    measured = _measure(distances, trajectory, start, length, _profile(distances, js, radius, max(6, min_len), length))
    if measured is not None:
        over = measured['context_repeat_over_step']
        record['repeat'] = {'over_step': round(over, 3), 'max': REPEAT_OVER_STEP_MAX,
                            **({'why': f'about its start the clip a cut length on is {over:.2f} playback steps off, over '
                                       f'{REPEAT_OVER_STEP_MAX}: the pixel search refuses it (repeat error over two steps) — a part '
                                       'off the legs\' beat repeats on its own'} if over > REPEAT_OVER_STEP_MAX else {})}
    cycle = {'start': start, 'length': length, 'period_local': length, 'ratio': picked['ratio'],
             'closure': picked['closure'], 'seam_flow': picked['seam_flow'], 'top': picked['top'], 'kind': 'periodic', 'method': 'observed-two-steps-v1',
             'repeat': 'two steps seen in the clip, joined at the walk\'s own point',
             'period_global': None, 'periodicity_min': periodicity_min, 'review_recommended': not band,
             'candidate_count': len(rows),
             'candidates': [{'start': row['start'], 'length': row['length'], 'phase': row['phase'], 'seam_flow': row['seam_flow']['off'],
                             'seam_pop': row['top'].get('pop'), 'ratio': round(row['ratio'], 3),
                             'closure': None if row['closure'] is None else round(row['closure'], 3)}
                            for row in ranked[:REFUSED_SHOWN]]}
    cycle['fundamental'], cycle['step_screen'] = _screens(distances, cycle, lo=max(6, min_len), hi=max(length, min_len),
                                                          gait_floor=gait_floor, periodicity_min=periodicity_min, signals=signals)
    return _two_steps(cycle, record), record


def _two_steps(cycle, record):
    """A cut the crossings count two steps: `steps` recorded, the step screen's pixel suspicion settled."""
    cycle.setdefault('steps', {'count': 2, 'by': 'crossings'})
    screen = cycle.get('step_screen')
    if screen and screen.get('suspect'):
        screen.update(suspect=False, pixel_suspect=True, settled_by='crossings',
                      why=f"two steps counted on the clip's crossings (cycle.step_count); the pixels alone: {screen['why']}")
    return cycle


def step_verdict(cycle):
    """The step count's warnings, in words: a cut taken on two steps whose seam the pixel search refuses (a part off
    the legs' beat) or that passed no gate, a search's cut kept where no two-step cut closes, and a clip that shows
    its steps but no cut of two."""
    counted = cycle.get('step_count')
    if not counted or not counted.get('readable'):
        return []
    s, L = cycle['start'], cycle['length']
    out = []
    if counted.get('chosen_by') == 'two steps':
        if not counted.get('gated'):
            out.append(f"the cut {s}/{L} covers two steps by the clip's crossings, but no two-step cut joins the walk within "
                       f"its phase, its top calm, its wrap within {counted['seam_max']:g} of its step and its seam under the flow "
                       f"limit {counted['flow_limit']:g} (phase {counted['cut']['phase']:+.1f} frames, wrap {cycle['ratio']:.2f}, "
                       f"seam flow {cycle['seam_flow']['off']}, top {cycle['top'].get('pop', cycle['top'].get('skipped'))}): "
                       "a part on a beat of its own closes on no cut of two steps — look at the wrap")
        if 'why' in counted.get('repeat', {}):
            worst = int(np.argmax(cycle['seam_flow']['off']))
            out.append(f"the cut {s}/{L} covers two steps by the clip's crossings, and {counted['repeat']['why']}; the seam's "
                       f"worst window is {cycle['seam_flow']['window'][worst]} of the body's box, turning "
                       f"{cycle['seam_flow']['turn'][worst]:+.2f} there — the flow repair reads it")
    elif 'declined' in counted:
        declined = counted['declined']
        out.append(f"the clip shows cuts of two steps, but none closes — in phase, its top calm, its wrap within "
                   f"{counted['seam_max']:g} of its step, its seam under the flow limit {counted['flow_limit']:g} and the seam "
                   f"gate passed (the best, {declined['cut'][0]}/{declined['cut'][1]}: phase {declined['phase']:+.1f} frames, "
                   f"seam {declined['seam']:g}): the search's cut {s}/{L} stands, {counted['cut']['verdict']} by the count "
                   f"({counted['cut']['why']})")
    elif 'two_cuts' in counted and counted['two_cuts'] == 0:
        out.append(f"the clip shows its steps (crossings {counted['crossings']}) but no cut of two: the cut {s}/{L} is "
                   f"{counted['cut']['verdict']} ({counted['cut']['why']}) — count its steps")
    return out
