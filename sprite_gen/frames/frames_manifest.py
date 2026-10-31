# SPDX-License-Identifier: Apache-2.0
"""Loading and validating a run's frames manifest and failure evidence against its generation.

Moved out of `extract.py` unchanged; `extract` re-exports every name, so `extract.<name>` still works."""

from __future__ import annotations

import json
from pathlib import Path

from sprite_gen.spec.layout import frames_dir_rel
from sprite_gen.spec.runio import load_request


def _load_validated(path: Path, name: str, require) -> dict:
    """Read a canonical run JSON record, FAILING LOUD on any corruption — unreadable,
    unparseable, or a broken schema — instead of silently treating it as absent/empty. EVERY
    reader (extract's subset seeding + commit, inspect / the correction loop) goes through here:
    silently reading a `{}`, a missing-field, or a wrong-`ok` record as empty would drop a
    still-unresolved failure (extract-failure.json) or a real generation (frames-manifest.json)
    from view, so a corrupt record would read as "all clear" (No Silent Fallback / Consistency).
    Returns {} ONLY when the file is genuinely absent — an existing-but-broken record never reads
    as empty. `require` enforces the kind-specific contract on top of the shared shape."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"corrupt {name} {path}: {exc}\n"
            f"  refusing to treat a canonical run record as empty; inspect/repair or delete it deliberately."
        )
    if not isinstance(data, dict):
        raise SystemExit(f"corrupt {name} {path}: expected a JSON object, got {type(data).__name__}")
    for key in ("errors", "warnings", "rows"):
        if not isinstance(data.get(key), list):  # required list, not merely list-if-present
            raise SystemExit(f"corrupt {name} {path}: '{key}' must be a list")
    for key in ("errors", "warnings"):
        for item in data[key]:
            if not isinstance(item, str) or not item.strip():  # e.g. null / "" slipping through
                raise SystemExit(f"corrupt {name} {path}: every '{key}' entry must be a non-empty string")
    for row in data["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("state"), str) or not row.get("state"):
            raise SystemExit(f"corrupt {name} {path}: every 'rows' entry needs a non-empty string 'state'")
    require(data, path, name)
    return data


def _require_complete_manifest(data: dict, path: Path, name: str) -> None:
    if data.get("ok") is not True:
        raise SystemExit(f"corrupt {name} {path}: a published frames manifest must have 'ok': true")


def _require_failure_evidence(data: dict, path: Path, name: str) -> None:
    if data.get("ok") is not False:
        raise SystemExit(f"corrupt {name} {path}: failure evidence must have 'ok': false")
    if not data["errors"]:
        raise SystemExit(f"corrupt {name} {path}: failure evidence must have a non-empty 'errors' list")
    # every diagnostic is a per-state message (`<state>: ...`) — the correction loop keys on the
    # state prefix, so a prefix-less or empty entry is a silently-lost failure (e.g. a `null`
    # entry would be dropped by the per-state merge and delete the whole record on next success).
    for key in ("errors", "warnings"):
        for item in data[key]:
            if ":" not in item or not item.split(":", 1)[0].strip():
                raise SystemExit(f"corrupt {name} {path}: every '{key}' entry must be state-scoped ('<state>: ...'), got {item!r}")


def load_frames_manifest(path: Path) -> dict:
    """Read a published frames-manifest.json (a COMPLETE generation), failing loud on corruption
    or a broken schema (`ok`:true, required `rows`/`errors`/`warnings` lists, rows carry a
    `state`). Returns {} only when genuinely absent (No Silent Fallback — see _load_validated)."""
    return _load_validated(path, "frames manifest", _require_complete_manifest)


def load_failure_evidence(path: Path) -> dict:
    """Read extract-failure.json (the run's unresolved per-state failures), failing loud on
    corruption or a broken schema (`ok`:false, non-empty state-scoped `errors`, `warnings`/`rows`
    lists). Returns {} only when genuinely absent (No Silent Fallback — see _load_validated)."""
    return _load_validated(path, "failure evidence", _require_failure_evidence)


def _require_generation_consistency(run_dir: Path, manifest: dict, name: str,
                                    allow_pending_states: bool = False) -> None:
    """Beyond JSON schema, a published generation must AGREE with the physical frame tree and the
    request. Frames and the manifest are published together as one transaction, so a disagreement
    means corruption/staleness: fail loud rather than let a consumer build output from stale,
    partial, or orphan frames (Consistency / No Silent Fallback). Checks:
      - every request state has exactly one row (no missing state, no duplicate row);
      - no physical frame state dir is an orphan the manifest omits;
      - every row's canonical frames exist on disk.

    `allow_pending_states` (관찰자 전용 — 큐레이션 뷰): 아직 생성되지 않은 요청 상태
    (manifest 행도 없고 물리 프레임도 없음)는 '진행 중'으로 허용한다. 부분 추출이
    정식 흐름(단계별 생성·증분 추출)이 된 뒤 뷰가 미생성 상태 때문에 죽으면 안 된다.
    소비자(compose/export 등)는 기본값(False)으로 완결 세대를 계속 강제한다."""
    frames_root = run_dir / "frames"
    try:
        request = load_request(run_dir)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read sprite-request.json for {run_dir}: {exc}")
    request_states = set(request.get("states", {}))
    row_states = [row["state"] for row in manifest["rows"]]
    dupes = sorted({s for s in row_states if row_states.count(s) > 1})
    if dupes:
        raise SystemExit(f"corrupt {name} {run_dir}: duplicate manifest rows for state(s) {dupes}")
    row_state_set = set(row_states)
    # request states and manifest row states must match EXACTLY, both directions: a missing row is
    # an incomplete generation, an extra row is a stale/unknown state some consumers would render
    # while serve (request-driven) hides it — a per-consumer divergence.
    missing = sorted(request_states - row_state_set)
    if missing:
        pending_only = [
            m for m in missing
            if not (run_dir / frames_dir_rel(request, m)).is_dir()
        ]
        if not (allow_pending_states and pending_only == missing):
            raise SystemExit(f"corrupt {name} {run_dir}: manifest missing row(s) for request state(s) {missing} — incomplete generation")
    unknown = sorted(row_state_set - request_states)
    if unknown:
        raise SystemExit(f"corrupt {name} {run_dir}: manifest has row(s) for state(s) {unknown} not in the request (stale/unknown state)")
    # 물리 고아 탐지 (경로 기반): 캐노니컬 프레임을 직접 담은 모든 디렉토리가
    # 어떤 row 의 파일 디렉토리이거나 요청 상태의 예약 위치여야 한다.
    row_dirs = set()
    for row in manifest["rows"]:
        for rel in row.get("files") or []:
            if isinstance(rel, str):
                row_dirs.add(str(Path(rel).parent))
    physical_dirs = set()
    if frames_root.is_dir():
        for frame in frames_root.rglob("frame-*.png"):
            if frame.name.endswith(".plain.png") or frame.parent.name == "orig":
                continue
            physical_dirs.add(str(Path("frames") / frame.parent.relative_to(frames_root)))
    orphan = sorted(physical_dirs - row_dirs)
    if orphan:
        raise SystemExit(f"corrupt {name} {run_dir}: physical frames under {orphan} have no manifest row (orphan/stale generation)")
    request_states_spec = request.get("states", {})
    for row in manifest["rows"]:
        state = row["state"]
        files = row.get("files")
        if not isinstance(files, list) or not files:
            raise SystemExit(f"corrupt {name} {run_dir}: manifest row '{state}' has no frame files (empty generation)")
        prefix = frames_dir_rel(request, state) + "/"
        for rel in files:
            if not isinstance(rel, str) or not rel.startswith(prefix):
                raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' file {rel!r} is not under {prefix} (state boundary)")
            if not (run_dir / rel).is_file():
                raise SystemExit(f"corrupt {name} {run_dir}: manifest row '{state}' references missing frame {rel}")
        # the canonical physical frames for this state must EXACTLY equal the row's files — no
        # missing (the `files:[]` / deleted-frames case) and no extra (a stale frame on disk).
        state_dir = run_dir / frames_dir_rel(request, state)
        physical = sorted(
            f"{prefix}{f.name}" for f in state_dir.glob("frame-*.png") if not f.name.endswith(".plain.png")
        ) if state_dir.is_dir() else []
        if physical != sorted(files):
            raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' files {sorted(files)} != physical canonical frames {physical}")
        # a complete generation has exactly the request's frame count for the state —
        # 행 프레임 풀 = primary + 선언된 테이크들 (takes 1급 계약)
        state_spec = request_states_spec.get(state, {})
        expected = int(state_spec.get("frames", len(files)))
        expected += sum(int(take.get("frames", 0)) for take in (state_spec.get("takes") or []))
        if len(files) != expected:
            raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' has {len(files)} frame(s), request expects {expected}")
        if "frames" in row and row["frames"] != len(files):
            raise SystemExit(f"corrupt {name} {run_dir}: row '{state}' row.frames={row['frames']} != {len(files)} files")


def load_consistent_frames_manifest(run_dir: Path, name: str = "frames manifest",
                                     allow_pending_states: bool = False) -> dict:
    """For readers that TOLERATE an ungenerated run (serve scaffold, inspect from raw): return the
    validated + consistent manifest if present; {} only when the run genuinely has NO generation
    (no manifest AND no physical frame dirs); fail loud if the manifest is corrupt/inconsistent, OR
    absent while physical frames exist (an orphan/stale generation — not a fresh scaffold).
    `allow_pending_states=True` (뷰 전용) 는 아직 생성 전인 요청 상태의 행 부재를 허용한다."""
    frames_root = run_dir / "frames"
    manifest = load_frames_manifest(frames_root / "frames-manifest.json")
    if not manifest:
        physical = sorted(d.name for d in frames_root.iterdir() if d.is_dir()) if frames_root.is_dir() else []
        if physical:
            raise SystemExit(
                f"orphan frames in {run_dir}: physical frame dir(s) {physical} exist but "
                f"frames/frames-manifest.json is absent — not a fresh scaffold. Re-extract or remove them."
            )
        return {}
    _require_generation_consistency(run_dir, manifest, name, allow_pending_states=allow_pending_states)
    return manifest


def require_frames_manifest(run_dir: Path) -> dict:
    """Gate for a finished-generation consumer (compose / export / preview / gif / cycle): the
    published frames manifest must be present, schema-valid, AND consistent with the physical frame
    tree + request (No Silent Fallback / Consistency). A consumer of a completed generation must
    call this before reading physical frames, so it never silently produces output from a missing,
    broken, stale, partial, or orphan generation."""
    manifest = load_consistent_frames_manifest(run_dir)
    if not manifest:
        raise SystemExit(
            f"frames/frames-manifest.json not found in {run_dir}; run a successful extract before "
            f"consuming this generation."
        )
    return manifest
