# SPDX-License-Identifier: Apache-2.0
"""Pixel pitch and grid detection for the pixel-unfake path (edge histograms, run lengths, phase).

Moved out of `extract.py` unchanged; `extract` re-exports every name, so `extract.<name>` still works."""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

from PIL import Image

from sprite_gen._deps import np

# --- pixel unfake pipeline (fit.pixel_unfake) -----------------------------
# unfake.js/pixeldetector 계열 접근: ① runs 기반으로 생성물의 논리 픽셀 pitch 검출
# ② 에지 히스토그램으로 격자 위상(offset) 정렬 ③ 격자 단위 dominant-color 스냅
# 다운스케일(진짜 해상도 복원) ④ 런 전체 공유 팔레트 양자화 + 알파 이진화
# ⑤ 정수배 NEAREST 업스케일로 셀 배치. 비정수 리샘플이 전혀 없어 픽셀이 깨지지 않는다.


def _edge_histograms(image: Image.Image) -> tuple[list[int], list[int], int, int]:
    """Color-transition edge counts indexed by x (vertical edges) and y
    (horizontal edges). AA ramps register near the true block boundary, so the
    boundary position signal survives anti-aliasing. Every other row (column) is
    scanned, as the per-pixel loop this replaced did; index 0 never has a left
    (upper) neighbour and stays 0."""
    pixels = np.asarray(image.convert("RGBA")).astype(np.int16)
    height, width = pixels.shape[:2]
    rows = pixels[0::2]
    col = (np.abs(rows[:, 1:] - rows[:, :-1]).sum(-1) > 96).sum(0)
    cols = pixels[:, 0::2]
    row = (np.abs(cols[1:] - cols[:-1]).sum(-1) > 96).sum(1)
    return [0] + col.tolist(), [0] + row.tolist(), width, height


def detect_pixel_pitch(strip: Image.Image, max_pitch: int = 48) -> int:
    """True pixel-block pitch via edge-to-gridline alignment scoring.

    이전 구현(같은 색 런 길이 최빈값)은 AA 가장자리·블록 내부 질감이 만드는
    2px 런에 지배당해 큰 블록(~16px)을 놓치고 항상 2 를 내놨다 — 그 결과
    그리드 비정렬 축소로 이웃 픽셀이 섞여 뭉개졌다 (2026-07-05 사고).
    새 방식: 후보 피치 p 와 위상마다 "색 경계가 그리드선 ±w 안에 모이는
    비율"에서 우연 기대치 (그 창이 덮는 잉여류 수)/p 를 뺀 점수의 argmax.
    작은 p 가 공짜로 이기는 문제를 우연 보정이 막는다. 확신 없으면 1(스냅 안 함)로
    관측 가능하게 떨어진다.

    창 폭 w 는 모든 p 에 동일하다. 예전에는 `w = 1 if p >= 8 else 0` 이라
    p>=8 에서만 창이 열렸고, 그 결과 참 피치(>=8)의 우연 기대치가 3/p 로
    부풀어 창 없는 약수(p<8)에게 졌다 — k=8,10,12,14 에서 정확히 k/2 를
    반환하던 원인 (합성 정답 테스트 test_pitch_ground_truth 로 고정).
    창이 p 를 넘어 잉여류를 중복 합산하지 않도록 잉여류는 집합으로 센다."""
    col_edges, row_edges, _, _ = _edge_histograms(strip.convert("RGBA"))
    return _pitch_from_edges(col_edges, row_edges, max_pitch)


def _pitch_from_edges(col_edges: list[int], row_edges: list[int], max_pitch: int) -> int:
    """`detect_pixel_pitch` on a strip's edge histograms (`_edge_histograms`), for a caller that reads them anyway."""
    # 단순 argmax — 진짜 피치의 약수(p=7 vs 14)는 우연 기대치 |잉여류|/p 가
    # 커서 자동으로 밀린다. 최고점이 문턱(0.2) 미만이면 그리드 확신 없음 →
    # 1(스냅 안 함)로 관측 가능하게 폴백.
    best_pitch, best_score = 1, 0.2
    for p in range(2, max_pitch + 1):
        score = _axis_int_score(col_edges, p) + _axis_int_score(row_edges, p)
        if score > best_score:
            best_pitch, best_score = p, score
    return best_pitch


def _axis_int_score(edges: list[int], p: int, w: int = 1) -> float:
    """정수 피치 p 의 축별 점수 = (그리드선 ±w 에 모인 엣지 비율) − 우연 기대치.

    창 폭 w 는 모든 p 에 동일하고, 창이 덮는 잉여류는 집합으로 세어 중복 합산하지 않는다.
    """
    total = sum(edges) or 1
    best = 0.0
    for phase in range(p):
        residues = {(phase + offset) % p for offset in range(-w, w + 1)}
        hit = sum(sum(edges[r::p]) for r in residues)
        score = hit / total - len(residues) / p
        if score > best:
            best = score
    return best


def _axis_int_seed(edges: list[int], max_pitch: int = 48) -> int:
    """한 축만 보고 고른 정수 피치 씨앗. 확신 없으면 1."""
    best_pitch, best_score = 1, 0.1
    for p in range(2, max_pitch + 1):
        score = _axis_int_score(edges, p)
        if score > best_score:
            best_pitch, best_score = p, score
    return best_pitch


def _axis_refine(edges: list[int], pitch: float, w: float = 1.0, bin_step: float = 0.25) -> tuple[float, float]:
    """소수 피치 p 에서 최적 위상과 그 점수. 잉여류를 히스토그램으로 접어 O(nnz + p/step).

    정수 격자만 볼 수 있던 예전에는 참 피치 17.24 를 17 로 반올림했고, 그 0.24 가
    스프라이트 폭을 가로지르며 누적돼(23칸이면 5.5px) 셀 경계가 블록 한가운데를
    지났다. 작은 디테일은 두 셀에 반씩 걸려 평균에 먹혔다.
    """
    total = sum(edges) or 1
    bins = max(4, int(round(pitch / bin_step)))
    hist = [0] * bins
    for x, count in enumerate(edges):
        if count:
            hist[int((x % pitch) / pitch * bins) % bins] += count
    span = min(bins, max(1, int(round((2 * w) / pitch * bins)) + 1))
    chance = min(1.0, span / bins)
    doubled = hist + hist
    window = sum(doubled[:span])
    best_score, best_bin = window / total - chance, 0
    for start in range(1, bins):
        window += doubled[start + span - 1] - doubled[start - 1]
        score = window / total - chance
        if score > best_score:
            best_score, best_bin = score, start
    # 창의 기하학적 중심이 아니라 창 안 엣지의 가중 무게중심을 쓴다. 중심을 쓰면
    # 엣지가 한 bin 에 몰린 완전 정렬 격자에서도 위상이 반창(=w) 만큼 밀렸다.
    weight = sum(doubled[best_bin : best_bin + span])
    if weight:
        centre = sum((best_bin + k) * doubled[best_bin + k] for k in range(span)) / weight
    else:
        centre = best_bin + (span - 1) / 2.0
    return best_score, (centre % bins) / bins * pitch


def detect_pixel_grid(
    strip: Image.Image, max_pitch: int = 48
) -> tuple[tuple[float, float], tuple[float, float]]:
    """참 픽셀 격자 = ((가로 피치, 세로 피치), (가로 위상, 세로 위상)). 전부 소수.

    AI 가 그린 도트는 블록 폭이 정수로 떨어지지 않는다 (주인공 base = 17.24px).
    측정은 소수로 하고, 스냅 결과(논리 픽셀 수)는 정수로 떨어진다.

    **피치는 축마다 다를 수 있다.** 생성물이 비균등 비율로 리스케일되면 가로 블록과 세로 블록의
    크기가 어긋난다 (주인공 chibi 베이스: 가로 30.38 / 세로 30.92). 한 피치를 두 축에
    강제하면 한 축이 통째로 미끄러진다 — 실측 가로 정렬률 11.7% (축별로 재면 75.7%).

    격자 확신이 없으면 ((1.0, 1.0), (0,0)) — 스냅하지 않는다.
    """
    # The edge histograms are read once: the integer seed (`detect_pixel_pitch`) and the refinement both read them.
    col_edges, row_edges, _, _ = _edge_histograms(strip.convert("RGBA"))
    combined = _pitch_from_edges(col_edges, row_edges, max_pitch)
    if combined <= 1:
        return (1.0, 1.0), (0.0, 0.0)

    half_span, step = 0.75, 0.02
    span = int(round(half_span / step))

    def refine(edges: list[int]) -> tuple[float, float]:
        # 씨앗 후보 = 축별 씨앗 + 두 축 합산 씨앗.
        # - 축별만 쓰면: 한 축의 정수 검출이 노이즈에 흔들려 약수(참 17.24 -> 씨앗 9)로 빠진다.
        # - 합산만 쓰면: 가로 24 / 세로 30 처럼 축마다 블록이 다른 그림에서 한 축의 참값이
        #   ±0.75 정밀화 창 밖에 놓인다.
        # 둘 다 후보로 두고 점수로 고르면 두 실패가 모두 막힌다.
        axis_seed = _axis_int_seed(edges, max_pitch)
        candidates = {float(s) for s in (axis_seed, combined) if s >= 2}
        if not candidates:
            return 1.0, 0.0
        # 정수 씨앗은 참 피치의 정수배를 집을 수 있다 (참 16.5 -> 씨앗 33: 33 간격선은
        # 엣지의 절반만 물지만, 정수만 보면 16 도 17 도 어긋나서 33 이 이긴다).
        # 그래서 씨앗의 약수들도 후보로 함께 정밀화하고 점수로 고른다.
        seeds = sorted(candidates | {s / d for s in candidates for d in (2, 3) if s / d >= 2.0})
        best = (-1.0, float(max(candidates)), 0.0)
        for centre in seeds:
            # centre 자체가 반드시 샘플에 들어가도록 대칭으로 훑는다 (예전엔 15.99/16.01 만 봐서
            # 정확히 정수인 격자에서도 소수로 빗나갔다).
            for i in range(-span, span + 1):
                pitch = centre + i * step
                if pitch < 2.0 or pitch > max_pitch:
                    continue
                score, phase = _axis_refine(edges, pitch)
                if score > best[0] + 1e-9:
                    best = (score, pitch, phase)
        return best[1], best[2]

    pitch_x, phase_x = refine(col_edges)
    pitch_y, phase_y = refine(row_edges)

    # 축별 피치는 서로 크게 다를 수 없다. 비균등 리스케일이어도 실측 차이는 2% 수준이다
    # (chibi 베이스: 30.38 / 30.92). 한 축이 다른 축의 1.5배를 넘게 벗어나면 그 축의
    # 검출이 무너진 것이다 — 엣지가 적은 축(균일한 세로 막대가 화면을 채우는 carry 포즈 등)에서
    # 참 피치의 약수가 이겨 3.00 같은 값이 나왔다 (down_carry_walk, 참값 9).
    # 엣지 총량이 많은 축을 신뢰해 양쪽에 쓴다. 조용히 고치지 않고 축 하나를 버렸음을 남긴다.
    lo, hi = sorted((pitch_x, pitch_y))
    if lo >= 2.0 and hi / lo > 1.5:
        if sum(col_edges) >= sum(row_edges):
            pitch_y = pitch_x
        else:
            pitch_x = pitch_y

    return (pitch_x, pitch_y), (phase_x, phase_y)


# --- run-length pitch estimator (perfectpixel-studio unfake 이식) ------------
# detect_pixel_grid(경계 히스토그램)의 세컨드 오피니언. 추정 전용 — 스냅 경로는
# 이 값을 절대 쓰지 않는다. 두 추정기가 벌어지면 경고로만 표면화한다.

RUNLEN_ALPHA_THRESHOLD = 10  # perfectpixel alphaThreshold — 소프트 매팅 프린지 제외
RUNLEN_RGB_TOLERANCE = 12  # perfectpixel nearRGB tol — AA 미세 노이즈는 같은 색
RUNLEN_MIN_RUNS = 32  # 축 전체 런이 이보다 적으면 추정 포기 (사진/노이즈)
RUNLEN_MODE_SHARE = 0.5  # 최빈값 ±1 창이 가중 질량의 절반 미만이면 확신 없음


def _runlen_axis_estimate(pixels, outer: int, inner: int, horizontal: bool, cap: int) -> float:
    """한 축의 동일색 런 길이 히스토그램 → 가중 최빈값의 소수 추정. 확신 없으면 1.0.

    짧은 런(AA 잔재·질감)이 개수로는 항상 많으므로 원본과 같이 run 길이로 가중한다
    (hist[s]·s). AI 도트의 블록 폭은 정수로 안 떨어지므로 최빈값 하나가 아니라
    ±1 창의 가중 무게중심을 쓴다 — 참 피치 30.56 이면 30/31 런이 44:56 으로 섞여
    나오고, 그 무게중심이 소수 피치를 복원한다.
    """
    hist = [0] * (cap + 2)
    for o in range(0, outer, 2):
        prev = pixels[0, o] if horizontal else pixels[o, 0]
        run = 1
        for i in range(1, inner):
            cur = pixels[i, o] if horizontal else pixels[o, i]
            if (cur[3] <= RUNLEN_ALPHA_THRESHOLD and prev[3] <= RUNLEN_ALPHA_THRESHOLD) or (
                cur[3] > RUNLEN_ALPHA_THRESHOLD
                and prev[3] > RUNLEN_ALPHA_THRESHOLD
                and abs(cur[0] - prev[0]) <= RUNLEN_RGB_TOLERANCE
                and abs(cur[1] - prev[1]) <= RUNLEN_RGB_TOLERANCE
                and abs(cur[2] - prev[2]) <= RUNLEN_RGB_TOLERANCE
            ):
                run += 1
            else:
                if 2 <= run <= cap:
                    hist[run] += 1
                run = 1
            prev = cur
        if 2 <= run <= cap:
            hist[run] += 1
    if sum(hist) < RUNLEN_MIN_RUNS:
        return 1.0
    weighted = [count * length for length, count in enumerate(hist)]
    mode = max(range(2, cap + 1), key=weighted.__getitem__)
    lo, hi = max(2, mode - 1), min(cap, mode + 1)
    window = sum(weighted[lo : hi + 1])
    if not weighted[mode]:
        return 1.0
    # 확신 게이트: 최빈값의 고조파 패밀리(k·mode ±1)가 가중 질량의 절반은 차지해야
    # 한다. 인접 논리 픽셀이 같은 색이면 런이 2·mode, 3·mode 로 늘어나므로 고조파는
    # 최빈값을 반박하는 게 아니라 지지하는 증거다 (실측: synthetic_fixture_a down_idle 은
    # 11 과 함께 22~23, 34~35 에 질량이 실린다). 패밀리 밖에 질량 절반이 흩어져
    # 있으면 블록 구조 확신이 없는 것 — 1.0 으로 관측 가능하게 포기한다.
    family = 0
    for k in range(1, cap // mode + 2):
        centre = k * mode
        family += sum(weighted[max(2, centre - k) : min(cap, centre + k) + 1])
    if family < sum(weighted) * RUNLEN_MODE_SHARE:
        return 1.0
    return sum(length * weighted[length] for length in range(lo, hi + 1)) / window


def estimate_pixel_grid_runlen(strip: Image.Image, max_pitch: int = 48) -> tuple[float, float]:
    """동일색 런 길이 최빈값으로 축별 피치를 추정한다 — 추정 전용 세컨드 오피니언.

    perfectpixel-studio internal/sprite/pixelize.go 의 unfake(DetectPixelScale)
    이식 (MIT — NOTICE 표기). 원본은 수평/수직 런을 한 히스토그램에 합치지만,
    우리 목적은 detect_pixel_grid 의 축 붕괴 방어라 축별로 분리해 센다 — 회귀:
    주인공 컴포넌트에서 y 피치가 x 값(29.52)으로 붕괴, 실측 30.56. 붕괴
    메커니즘은 정수 씨앗 로터리 실패(참 소수 피치가 어떤 씨앗의 ±0.75 정밀화 창에도
    안 들어감)라 경계 히스토그램 안에서는 자기 진단이 안 된다. 런 길이는 완전히
    다른 신호(경계 위치가 아니라 경계 사이 거리)라 세컨드 오피니언이 된다.

    확신 없는 축은 1.0 — 런 수 부족(RUNLEN_MIN_RUNS), 최빈값 창 질량 미달
    (RUNLEN_MODE_SHARE), 32px 미만 이미지 전부 관측 가능하게 포기한다.
    """
    image = strip.convert("RGBA")
    width, height = image.size
    if width < 32 or height < 32:
        return (1.0, 1.0)
    cap = min(max_pitch, min(width, height) // 8)
    if cap < 2:
        return (1.0, 1.0)
    pixels = image.load()
    return (
        _runlen_axis_estimate(pixels, height, width, True, cap),
        _runlen_axis_estimate(pixels, width, height, False, cap),
    )


def crosscheck_pitch_runlen(
    grid_pitch: tuple[float, float],
    runlen_pitch: tuple[float, float],
    axis_tolerance: float = 0.12,
    ratio_tolerance: float = 0.02,
) -> list[str]:
    """detect_pixel_grid vs 런길이 추정의 교차검증. 이견은 경고 문자열 목록으로.

    추정 전용 — 어느 값도 바꾸지 않는다. 자동 교체 금지: 어느 쪽이 맞는지는
    사람이/상위 게이트가 판단한다 (No Silent Fallback).

    runlen 의 오차 모델이 규칙 모양을 정한다: AA 가 런 양끝을 갉아먹으므로 runlen 은
    참 피치를 **한쪽(하향)으로만** 빗나간다 — 픽셀 단위 절대 바이어스라 작은 피치일수록
    상대 오차가 커진다 (실측: 피치 30 에서 −0.8px≈3%, 피치 9 에서 −2px≈20%).

    - 약수 오검출 (runlen 이 grid 보다 훨씬 큼): grid 가 참 피치의 약수로 떨어진 것
      (참 29.5 를 14.73 으로). runlen 은 과대추정하지 않으므로 슬랙 2px 만 두면 된다.
    - 배수/고조파 오검출 (grid 가 runlen 보다 훨씬 큼): grid 가 참 피치의 배수·고조파에
      낚인 것. runlen 하향 바이어스(≤3px) 를 슬랙으로 흡수한 뒤에도 크게 남으면 경고.
    - 축비(y/x) 불일치: 축 붕괴 (y 가 x 값으로). 회귀의 축차는 3.5%(29.52 vs 30.56)라
      축별 오차로는 임계 밑에 숨는다. AA 하향 바이어스의 공통 성분은 비율에서
      상쇄되지만(실측: 붕괴 신호 2.8%, 건강한 검출의 비 오차 0.7%), 축별 편차는
      남는다 — 편차는 절대량(서브픽셀, 축당 ~±0.35px)이라 비율 노이즈가 ~0.7/피치 로
      작은 피치에서 커진다 (synthetic_fixture_a 실측: 8~15px 대역에서 3~9% 드리프트가 흔함,
      진위 판별 불가). 그래서 0.7/피치 를 하한 슬랙으로 둔다: 히어로 스케일(30px)의
      실붕괴 신호(2.8%)는 임계(2.4%) 위, 소피치 대역의 판별 불가 드리프트는 밑.
    """
    notes: list[str] = []
    for name, grid, runlen in (
        ("x", grid_pitch[0], runlen_pitch[0]),
        ("y", grid_pitch[1], runlen_pitch[1]),
    ):
        if grid < 2.0 or runlen < 2.0:
            continue
        if runlen - grid > max(axis_tolerance * grid, 2.0):
            notes.append(
                f"pitch crosscheck: run-length mode estimates {name}={runlen:.2f} but grid "
                f"detection returned {name}={grid:.2f} — likely a divisor misdetection"
            )
        elif grid - runlen > axis_tolerance * grid + 3.0:
            notes.append(
                f"pitch crosscheck: run-length mode estimates {name}={runlen:.2f} but grid "
                f"detection returned {name}={grid:.2f} — likely a multiple/harmonic misdetection"
            )
    if min(*grid_pitch, *runlen_pitch) >= 2.0:
        grid_ratio = grid_pitch[1] / grid_pitch[0]
        runlen_ratio = runlen_pitch[1] / runlen_pitch[0]
        drift = abs(runlen_ratio / grid_ratio - 1.0)
        if drift > max(ratio_tolerance, 0.7 / min(grid_pitch)):
            notes.append(
                f"pitch crosscheck: axis ratio y/x disagrees — run-length {runlen_ratio:.3f} vs "
                f"grid {grid_ratio:.3f} ({drift:.1%}); one axis may have collapsed to the other"
            )
    return notes



# 임계값 t = ⌊평균⌋ 별 바이트 변환표. `_LE_KEEP[t]` 는 `v ≤ t` 를 그대로 두고 그 위를
# 0 으로 접어 `sum()` 이 곧 `S_lo` 가 되게 하고, `_LE_ONES[t]` 는 같은 조건을 1/0 으로
# 접어 `sum()` 이 `C_lo` 가 되게 한다 (`_grid_score_edges` 의 정수 정식화).
_LE_KEEP: list[bytes] = [bytes(range(t + 1)) + bytes(255 - t) for t in range(256)]
_LE_ONES: list[bytes] = [b"\x01" * (t + 1) + b"\x00" * (255 - t) for t in range(256)]


def _grid_rows(component: Image.Image) -> tuple[list[list[int]], list[bytes]]:
    """행별 불투명 픽셀 색인 — (행별 x 좌표 오름차순, 그 픽셀들의 RGB 를 이어붙인 bytes).

    채점은 셀 안의 불투명 픽셀만 본다. 격자 위상이 바뀌어도 이 색인은 그대로이므로
    컴포넌트당 한 번만 만들어 `_best_phase` 의 위상 스캔 전체가 공유한다. 압축해 두면
    셀 픽셀 수집이 파이썬 픽셀 루프가 아니라 **bytes 슬라이스**가 된다.
    """
    if component.mode != "RGBA":
        # 알파로 걸러 낸다 — 채널 수가 다르면 `row[3::4]` 가 조용히 엉뚱한 바이트를 읽는다.
        raise ValueError(f"_grid_rows expects an RGBA component, got {component.mode!r}")
    w, h = component.width, component.height
    data = component.tobytes()  # RGBA, 행 우선
    row_pos: list[list[int]] = []
    row_rgb: list[bytes] = []
    for y in range(h):
        base = y * w * 4
        row = data[base:base + w * 4]
        alpha = row[3::4]
        # 알파 128 = 이 모듈 전역의 불투명 판정 관례다 (extract.py 안 15곳이 같은 리터럴을
        # 쓴다). 이 함수만 이름 붙인 상수로 빼면 SSoT 가 반쯤만 옮겨져 더 나빠지므로,
        # 통일은 모듈 전체를 한 번에 바꾸는 별도 작업으로 둔다 (plan Notes).
        pos = [x for x in range(w) if alpha[x] >= 128]
        row_pos.append(pos)
        row_rgb.append(b"".join(row[4 * x:4 * x + 3] for x in pos))
    return row_pos, row_rgb


def _grid_row_splits(row_pos: list[list[int]], xs: tuple[int, ...] | list[int],
                     w: int) -> list[list[int]]:
    """행마다 x 절단선이 압축 색인의 어느 인덱스에 떨어지는지 (셀 픽셀 슬라이스 경계).

    x 분할은 6종 정도만 나오는데 격자는 36종이라, 분할별로 한 번 구해 두면 셀마다
    `bisect` 를 다시 돌리지 않는다.
    """
    cuts = [min(e, w) for e in xs]
    out: list[list[int]] = []
    for pos in row_pos:
        lo = 0
        idx = []
        for cut in cuts:
            lo = bisect_left(pos, cut, lo)
            idx.append(lo)
        out.append(idx)
    return out


def _grid_uniformity(component: Image.Image, pitch: tuple[float, float],
                     phase: tuple[float, float]) -> float:
    """격자 가설의 셀 내부 색 분산 (낮을수록 진짜 격자). 불투명 픽셀만, 셀 크기 가중.

    위상 스캔은 이 함수를 거치지 않는다 — `_best_phase` 는 행 색인을 한 번 만들어
    `_grid_score_edges` 를 직접 부른다. 여기는 단발 채점(`arbitrate_pitch` 의 진단)
    전용이라 색인을 주입받을 자리가 없다.
    """
    w, h = component.width, component.height
    rows = _grid_rows(component)
    xs = grid_edges(w, pitch[0], phase[0])
    ys = grid_edges(h, pitch[1], phase[1])
    return _grid_score_edges(rows, w, h, xs, ys)


def _grid_score_edges(rows: tuple[list[list[int]], list[bytes]], w: int, h: int,
                      xs: tuple[int, ...] | list[int], ys: tuple[int, ...] | list[int],
                      splits: list[list[int]] | None = None) -> float:
    """`_grid_uniformity` 의 채점 코어 — 격자 경계가 이미 정해진 뒤의 순수 계산.

    핫스팟이다 (실측 2026-07-25: `synthetic_fixture_b down_idle` 추출 35.5s 중 `_best_phase` 가
    23.9s = 67%, 컴포넌트당 유니크 격자 36 × 셀 1344 ≈ 48k 셀 · 픽셀 방문 6.6M 회).

    **정수 정확 산술** — 셀 편차를 float 로 누적하지 않는다. 셀의 한 채널 값 `v` 와
    평균 `m = s/n` 에 대해 `Σ(v − m) = 0` 이므로 평균 아래/위의 절대편차 합이 같고,

        Σ|v − m| = 2·(m·C_lo − S_lo)        (C_lo, S_lo = v ≤ ⌊m⌋ 의 개수·합)

    양변에 `n` 을 곱하면 `n·Σ|v − m| = 2·(s·C_lo − n·S_lo)` 로 **전부 정수**다. 셀의
    기여분 `dev·n` 은 `T/n` 이고(`dev = T/n²`), `T` 는 세 채널의 정확한 정수합이다.
    부동소수는 셀당 나눗셈 한 번과 누적에만 남는다.

    이 정식화 이전에는 원본 float 축약식(`sum(sum(abs(...)))`)을 글자 그대로 재현해
    바이트 동일을 계약으로 삼았다. 계약을 **정확한 유리수 값**으로 교체했다 (plan
    `sprite-gen/best-phase-hotspot`): 새 값은 같은 양의 정확판이라 옛 float 누적과
    마지막 ULP 에서 갈리지만(실측 최대 상대차 1.8e-16), 파이프라인이 실제로 쓰는
    출력인 `_best_phase` 의 argmin 은 전수 동일하다(synthetic_fixture_b 8 컴포넌트 × 64 위상).

    **argmin 불변은 정리가 아니라 측정이다** — argmin 은 점수의 불연속 함수라 "점수가
    ULP 만큼 바뀌면 argmin 이 안 바뀐다"를 보장하는 정리는 없다. 뒤집히려면 서로 다른
    두 위상의 점수가 유효숫자 15자리까지 붙어야 하는데, 관측된 distinct 점수 최소 간격은
    ~1e-5 다 (validator 독립 측정 2026-07-25: 저대비 팔레트를 포함한 무작위·적대 컴포넌트
    160개에서 최소 간격 1.15e-5 vs 신·구 최대 상대차 8.39e-15 = 여유 1.4e9 배, 별도
    300 컴포넌트 argmin 불일치 0건). 점수가 수천 개 정수 유래 셀 기여분의 합이라 그렇게
    붙기 어렵다. 자기 자산에서 다시 재려면 이 두 수(점수 간격 / 상대차)를 재라.

    **"뒤집히면 새 값이 맞다" 라고는 못 쓴다** — 정확한 건 **셀 단위까지**다. `total_dev` 는
    여전히 셀 기여분을 naive float 로 누적하므로 전체 점수의 정확도 우위는 평균적 경향이지
    보장이 아니다 (maintainer 실측 2026-07-25, 격자 840개를 `Fraction` 참값과 대조: 신 값이 더
    가까움 12.6% / **구 값이 더 가까움 7.0%** / 동률 80.4%, 차이 크기는 상대 4.0e-16 이하라
    행동 영향 0). 이 문단은 한때 "뒤집혀도 새 값이 정답 쪽" 이라고 단정했는데, 그게 바로 위
    문단이 경계하라고 적은 "측정 안 한 성질을 불변식으로 박기" 였다.
    정확 동점은 양쪽 다 안전하다: 순회 순서(`for i / for j`)와 strict `<` 동점 처리가
    구 코드와 동일해 동점은 양쪽 다 첫 위상으로 간다.

    `C_lo`/`S_lo` 는 `bytes.translate` + `sum` 으로 C 레벨에서 뽑는다 — 임계값별 변환표는
    256개뿐이라 모듈 상수로 굽는다.

    `_best_phase` 가 이 코어를 (xs, ys) 로 메모이즈한다: 8단계 위상 스캔은 `grid_edges`
    의 정수 스냅 때문에 서로 다른 위상이 **같은 정수 경계**로 자주 붕괴한다 (offset 이
    피치의 1/4 미만이면 lead=0 등). 같은 경계 → 같은 계산 → 같은 값이므로 캐시는
    정확하며, 64회 격자 순회가 고유 경계 수(실측 36)만큼으로 줄어든다.
    """
    row_pos, row_rgb = rows
    if splits is None:
        splits = _grid_row_splits(row_pos, xs, w)
    total_dev = 0.0
    total_n = 0
    ncol = len(xs) - 1
    for yi in range(len(ys) - 1):
        band = range(ys[yi], min(ys[yi + 1], h))
        for xi in range(ncol):
            parts: list[Any] = []
            add = parts.append
            for y in band:
                idx = splits[y]
                lo, hi = idx[xi], idx[xi + 1]
                if hi > lo:
                    add(row_rgb[y][3 * lo:3 * hi])
            cell = b"".join(parts)
            n = len(cell) // 3
            if n < 2:
                continue
            t = 0
            for k in range(3):
                vals = cell[k::3]
                s = sum(vals)
                floor_mean = s // n
                t += (s * sum(vals.translate(_LE_ONES[floor_mean]))
                      - n * sum(vals.translate(_LE_KEEP[floor_mean])))
            # `2 *` 는 위 대칭성(평균 아래 합 = 위 합)의 계수다.
            total_dev += 2 * t / n
            total_n += n
    return (total_dev / total_n) if total_n else 1e9


def _best_phase(component: Image.Image, pitch: tuple[float, float]) -> tuple[float, float]:
    """주어진 피치에서 셀 균일도가 최선인 위상 (축별 8단계 탐색 — 결정론)."""
    best = (0.0, 0.0)
    best_score = None
    steps = 8
    # 불투명 픽셀 색인을 한 번만 만들어 모든 위상이 공유한다 (재로딩 제거).
    rows = _grid_rows(component)
    w, h = component.width, component.height
    # (xs, ys) 정수 경계로 채점을 메모이즈 — 위상 스캔은 같은 경계로 자주 붕괴한다.
    cache: dict[tuple, float] = {}
    split_cache: dict[tuple, list[list[int]]] = {}
    for i in range(steps):
        for j in range(steps):
            phase = (pitch[0] * i / steps, pitch[1] * j / steps)
            xs = tuple(grid_edges(w, pitch[0], phase[0]))
            ys = tuple(grid_edges(h, pitch[1], phase[1]))
            key = (xs, ys)
            score = cache.get(key)
            if score is None:
                splits = split_cache.get(xs)
                if splits is None:
                    splits = split_cache[xs] = _grid_row_splits(rows[0], xs, w)
                score = _grid_score_edges(rows, w, h, xs, ys, splits)
                cache[key] = score
            if best_score is None or score < best_score:
                best_score = score
                best = phase
    return best


def arbitrate_pitch(images: list, detect_pitch: tuple[float, float],
                    runlen_pitch: tuple[float, float],
                    tag: str, warnings_out: list) -> tuple[tuple[float, float], str]:
    """detect 합의와 runlen 세컨드 오피니언이 크게 다를 때 실측 채점으로 승자 채택.

    하모닉 오검출 (회귀 2026-07-18 side_idle: detect 가 참 피치 9.5 의 하모닉
    6.5 를 잡아 45×83 과분할 → 물리캡 강제 리사이즈 = 이중 리샘플 찌그러짐) 방어.
    자동 폴백이 아니라 두 가설을 셀 균일도로 결정론 채점해 채택하고, 결과를
    경고로 관측 가능하게 남긴다.

    위상은 받지 않는다 — 채점은 가설마다 `_best_phase` 로 실측 위상을 직접 잡고,
    스냅 경로도 위상을 따로 재계산한다 (plan `frame-pitch-consensus-eats-a-row`).
    예전에는 프레임별 검출 위상을 받아 그대로 되돌려주기만 했다 (읽지 않는 인자).

    반환: (채택 피치, 채택 근거 라벨)."""
    dx, dy = detect_pitch
    rx, ry = runlen_pitch
    if rx < 2.0 or ry < 2.0:
        return detect_pitch, "detect"
    if abs(dx - rx) <= max(1.0, dx * 0.15) and abs(dy - ry) <= max(1.0, dy * 0.15):
        return detect_pitch, "detect"
    # 채택은 하지 않는다 (회귀 2026-07-18: 균일도 지표는 세밀 격자에 자명하게
    # 유리한 편향이 있어 up_idle 을 잘못 뒤집었다). 관측 로그만 남긴다 — 큰 불일치의
    # 실제 의미는 대개 '생성 해상도가 셀 계약보다 세밀' (해법 = 굵기 강제 리롤).
    # 두 가설을 같은 조건으로 채점한다 — 양쪽 다 위상을 실측(`_best_phase`)으로 잡는다.
    # 예전에는 detect 만 프레임 검출 위상으로 채점했는데, 그 위상은
    # 참 위상에서 pitch/2 까지 밀릴 수 있어(plan `frame-pitch-consensus-eats-a-row`:
    # 피치 13.00 에서 2.02 vs 실측 8.12) detect 쪽 균일도만 부당하게 낮게 나왔다.
    # 채택 로직은 그대로 detect 고정이고 이 점수는 경고 문구에만 쓰이지만, 사람이 읽고
    # 판단하는 숫자라 편향을 남겨두지 않는다.
    sample = images[0]
    detect_score = _grid_uniformity(sample, detect_pitch, _best_phase(sample, detect_pitch))
    runlen_phase0 = _best_phase(sample, runlen_pitch)
    runlen_score = _grid_uniformity(sample, runlen_pitch, runlen_phase0)
    warnings_out.append(
        f"{tag}: pitch disagreement — detect {dx:.2f}x{dy:.2f} (uniformity {detect_score:.1f}) "
        f"vs runlen {rx:.2f}x{ry:.2f} ({runlen_score:.1f}); detect kept. "
        f"large gap usually means the raw was drawn finer than the cell contract — reroll with a "
        f"block-count hint")
    return detect_pitch, "detect"


PITCH_FAMILY_RATIO = 1.1


def resolve_frame_pitch(own: tuple[float, float], consensus: tuple[float, float]) -> tuple[tuple[float, float], bool]:
    """프레임 자체 검출 피치의 채택 판정 — 합의 '피치 패밀리' 밖이면 오검출 가드.

    같은 스트립(한 번의 생성) 안에서 참 피치의 프레임 간 편차는 측정 노이즈
    수준(수 %)이다 — down_jump frame-0 회귀(합의 13.00 vs 자체 12.50, 4%)는
    own 이 진실이었고 합의 강제가 눈을 반쪽 냈다 (plan per-frame-pixel-grid).
    반면 하모닉/붕괴 오검출은 ×2/×3(또는 ÷2/÷3) 급으로 벗어난다 — up_run
    frame-2 회귀(합의 7.00x8.86 vs 자체 3.00x3.00, 2026-07-22)는 native
    87x162 를 만들었고, 행 일관 축소(conform_row_logical)가 그 캡 배율을 행
    전체에 적용해 나머지 프레임을 0.36배 콩알로 붕괴시켰다. 두 회귀 사이
    어디에도 참값이 없는 10%(비율 1.1)를 경계로 가른다 — 같은 스트립의 실제
    프레임 간 편차는 실측 0.4~2%, 눈반쪽 회귀도 4%였고, 10% 이탈(up_run
    frame-3 own y 8.00 vs 합의 8.86)조차 native 캡 초과로 행 전체 5% 축소를
    유발했다. 이내 = own 채택(측정 진실, 합의 강제 누적오차 없음), 밖 = 합의
    채택. 폴백은 호출부가
    프레임별 경고로 관측 가능하게 남긴다 (No Silent Fallback).

    반환: (채택 피치, 패밀리-밖 outlier 여부)."""
    own_x, own_y = own
    consensus_x, consensus_y = consensus
    if min(consensus_x, consensus_y) < 2.0:
        return own, False
    ratio = max(own_x / consensus_x, consensus_x / own_x,
                own_y / consensus_y, consensus_y / own_y)
    if ratio > PITCH_FAMILY_RATIO:
        return consensus, True
    return own, False


def grid_edges(length: int, pitch: float, offset: float) -> list[int]:
    """소수 피치를 정수 픽셀 경계로 확정한다.

    두 경우를 가른다. 판정 기준은 body 가 피치의 정수배에 얼마나 가까운가다.

    1) 정수배에 가까우면(잔차 <= 블록의 1/4) body 를 셀 개수로 **등분**한다. 피치 측정의 미세오차
       (16.00 을 15.96 으로 재는 것 같은)를 흡수해 격자가 딱 떨어진다.
    2) 정수배가 아니면 `lead + i*pitch` 를 직접 곱해 놓고, 남는 자투리는 마지막 셀이 흡수한다.
       스프라이트 bbox 는 AA 프린지 때문에 블록의 정수배가 아닐 수 있다 (회귀: 849px =
       27.46 블록). 이때 등분하면 셀 폭이 31.44px 로 늘어나 참 블록 30.92px 와 칸마다 0.52px
       어긋나고, 오른쪽 끝에서 반 블록이 밀려 스냅 결과의 얼굴이 부서졌다 (v1.56.2 회귀).

    어느 쪽이든 피치를 누적 덧셈하지 않으므로 부동소수 오차가 쌓이지 않는다.
    """
    if pitch <= 1.0:
        return [0, length]
    # 선행 부분셀은 스프라이트가 블록 중간에서 시작할 때만 의미가 있다. 컴포넌트는 bbox 로
    # 잘려 블록 경계에서 시작하므로, 서브픽셀 오프셋(위상 추정 노이즈)은 0 으로 스냅한다.
    raw_lead = offset % pitch
    lead = 0 if (raw_lead < pitch * 0.25 or raw_lead > pitch * 0.75) else int(round(raw_lead))
    body = length - lead
    if body <= 0:
        return [0, length]
    ratio = body / pitch
    cells = max(1, int(round(ratio)))
    integral = abs(ratio - cells) <= 0.25
    edges = [0] if lead == 0 else [0, lead]
    for i in range(1, cells):
        e = lead + int(round(body * i / cells if integral else i * pitch))
        if edges[-1] < e < length:
            edges.append(e)
    if edges[-1] != length:
        edges.append(length)
    return edges


def _grid_phase(image: Image.Image, pitch: int) -> tuple[int, int]:
    """The grid's phase on each axis: the residue mod `pitch` where most colour edges fall (the first, on a tie). The
    edge counts are `_edge_histograms`' folded mod `pitch` — the same scan of every other row and column."""
    col_edges, row_edges, _, _ = _edge_histograms(image.convert("RGBA"))
    col_hits = [sum(col_edges[r::pitch]) for r in range(pitch)]
    row_hits = [sum(row_edges[r::pitch]) for r in range(pitch)]
    offset_x = max(range(pitch), key=lambda i: col_hits[i])
    offset_y = max(range(pitch), key=lambda i: row_hits[i])
    return offset_x, offset_y


def _dominant_block_color(opaque: list, detail_bias: bool = False) -> tuple[int, int, int]:
    if len(opaque) == 1:
        return opaque[0][:3]

    def luma(p):
        return p[0] * 299 + p[1] * 587 + p[2] * 114

    lo = min(opaque, key=luma)
    hi = max(opaque, key=luma)
    centroids = [lo[:3], hi[:3]]
    assign = [0] * len(opaque)
    # 정수 산술만 (제곱거리·정수합//count) — 순서 무관이라 명시 누적으로 바꿔도
    # 원본과 값 동일하다. 제너레이터/`sum()` 호출 오버헤드만 제거.
    for _ in range(3):
        c00, c01, c02 = centroids[0]
        c10, c11, c12 = centroids[1]
        for i, p in enumerate(opaque):
            p0, p1, p2 = p[0], p[1], p[2]
            d0 = (p0 - c00) ** 2 + (p1 - c01) ** 2 + (p2 - c02) ** 2
            d1 = (p0 - c10) ** 2 + (p1 - c11) ** 2 + (p2 - c12) ** 2
            assign[i] = 0 if d0 <= d1 else 1
        for cluster in (0, 1):
            s0 = s1 = s2 = cnt = 0
            for i, p in enumerate(opaque):
                if assign[i] == cluster:
                    s0 += p[0]
                    s1 += p[1]
                    s2 += p[2]
                    cnt += 1
            if cnt:
                centroids[cluster] = (s0 // cnt, s1 // cnt, s2 // cnt)
    dominant = 0 if assign.count(0) >= assign.count(1) else 1
    if detail_bias:
        # 눈/아웃라인 같은 어두운 소수 디테일 보존: 두 클러스터의 명도차가 크고
        # 어두운 쪽 점유율이 1/3 이상이면 다수결 대신 어두운 클러스터를 택한다.
        darker = 0 if luma(centroids[0]) <= luma(centroids[1]) else 1
        share = assign.count(darker) / len(assign)
        if (
            darker != dominant
            and share >= 0.40
            and luma(centroids[darker]) < 70000
            and luma(centroids[1 - darker]) - luma(centroids[darker]) > 50000
        ):
            dominant = darker
    members = [p for i, p in enumerate(opaque) if assign[i] == dominant]
    return tuple(sum(p[c] for p in members) // len(members) for c in range(3))


def _pitch_pair(pitch: float | tuple[float, float]) -> tuple[float, float]:
    """스칼라 피치는 두 축에 같은 값, 쌍이면 (가로, 세로)."""
    if isinstance(pitch, (tuple, list)):
        return float(pitch[0]), float(pitch[1])
    return float(pitch), float(pitch)
