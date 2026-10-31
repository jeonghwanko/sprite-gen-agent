# Working on sprite-gen-agent

> For AI coding agents (Claude Code, Codex, …) that change this repository. Using sprite-gen to make
> sprites, images or clips is [SKILL.md](SKILL.md); this file is about changing the code and its docs.

sprite-gen-agent is a fork of [aldegad/sprite-gen](https://github.com/aldegad/sprite-gen) 3.4.0
(remote `upstream`), first written for the AI Fighter creator kit. What the fork adds is listed in
[NOTICE](NOTICE) and in the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md).

## Where to look

| You need | Read |
|---|---|
| The doc that owns a topic | [docs/README.md](docs/README.md) — the index; its "Owns" column says what each doc decides. Every leaf doc has a `> Owns:` line near its title (often in longer words than the index) |
| How the code is laid out | [docs/architecture.md](docs/architecture.md) §0 |
| Where a verb is defined | `COMMANDS` in `sprite_gen/cli.py` registers each verb by hand; its flags live in that module's `add_arguments` (`sprite-gen <verb> --help`) |
| Which domain a module is in | `sprite_gen/_modules.py`; the grouping in `sprite-gen --help` is derived from it |
| What an embedding program may import | [docs/api.md](docs/api.md) (`sprite_gen.api`, held by `tests/test_api.py`) |
| Renaming a key, a word or a flag | [docs/rename-gate.md](docs/rename-gate.md) — blocking, read before the first replace |
| Why only the venv runs anything | [docs/interpreter.md](docs/interpreter.md) |
| A run that is "quietly wrong" | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Releases and lanes | [docs/release.md](docs/release.md) |

Each package's tests are in the folder of the same name. The docs below are the main ones for the
package; the index has the rest.

| `sprite_gen/` | `tests/` | Main docs |
|---|---|---|
| `spec` | `tests/spec` | [run-contract](docs/run-contract.md), [architecture](docs/architecture.md) §3 |
| `gen` | `tests/gen` | [gen](docs/gen.md), [video](docs/video.md), [prompt-assembly](docs/prompt-assembly.md), [pose-motion](docs/pose-motion.md), [pixel-draw](docs/pixel-draw.md), [logo](docs/logo.md) |
| `video` | `tests/video` | [video-pipeline](docs/video-pipeline.md), [loop-repair](docs/loop-repair.md), [loop-review](docs/loop-review.md), [loop-comparison](docs/loop-comparison.md), [face-parts](docs/face-parts.md) |
| `frames` | `tests/frames` | [architecture](docs/architecture.md) §6, [chroma-alpha](docs/chroma-alpha.md), [sheet-slicing](docs/sheet-slicing.md), [ui-kit](docs/ui-kit.md) |
| `curate` | `tests/curate` | [curation](docs/curation.md), [directional-anchor-workflow](docs/directional-anchor-workflow.md) |
| `serve` | `tests/serve` | [curation](docs/curation.md) |
| `compose` | `tests/compose` | [run-contract](docs/run-contract.md), [engine-export](docs/engine-export.md), [game-pack](docs/game-pack.md) |
| `effects` | `tests/effects` | [breathing](docs/breathing.md), [recolor](docs/recolor.md), [frame-interpolation](docs/frame-interpolation.md), [asset-tools](docs/asset-tools.md) (shadow) |
| `qa` | `tests/qa` | [qa-motion](docs/qa-motion.md), [scale-check](docs/scale-check.md), [decisions-review](docs/decisions-review.md) |
| `background` | `tests/background` | [asset-tools](docs/asset-tools.md) |
| `scene` | `tests/scene` | [scene](docs/scene.md), [gait-guide](docs/gait-guide.md) |
| `audio` | `tests/audio` | [sound](docs/sound.md) |
| `workflow` | `tests/workflow` | [user-workflow](docs/user-workflow.md) |
| `util` | `tests/util` | — |
| `cli.py`, `api.py`, `local_tool.py`, `_modules.py` | `tests/test_*.py`, `tests/packaging` | [api](docs/api.md) |

`tests/docs` checks the docs, `tests/packaging` the package surface and taxonomy, `tests/release` the
release lanes.

## Large files: read by section

Some files are too big to read whole. No markdown line is over 2,000 characters; keep it that way.

| File | Size | How |
|---|---|---|
| `CHANGELOG.md` | ~170 KB (3.0.0 on, and `Unreleased`) | grep it; read only the `Unreleased` section or the version asked about |
| `CHANGELOG-before-3.0.md` | ~380 KB (1.x and 2.x) | grep only, for history |
| `docs/loop-repair.md` | ~290 KB, 2,800+ lines | read its "Reading this doc" table at the top, then only the part it points to |
| `docs/video-pipeline.md`, `docs/run-contract.md` | ~60 KB each | grep `^#` for the outline, then read one section with an offset |
| `sprite_gen/video/align.py`, `repair.py`, `batch.py`, `rife.py`, `sprite_gen/frames/extract.py`, `sprite_gen/video/loop.py` | 85–135 KB each | grep for the function, read around it |

If `.claude/worktrees/` exists, it holds other sessions' checkouts: never search or edit there.

## Environment

- Run everything with the repo venv, never a global `python3`: `.venv/Scripts/python.exe` on Windows,
  `.venv/bin/python` elsewhere. Bootstrap: `python -m venv .venv`, then `pip install -e ".[dev]"`.
- On Windows set `PYTHONUTF8=1`. Many tests read reports in the locale encoding and fail under cp949
  before their real assertion, which hides real failures.

## Tests

- Plain `pytest` is the fast bundle: `tests/conftest.py` deselects the tests marked `slow`. `pytest --full`
  runs every test; only the release lanes do (`scripts/linux_lane.sh`, `scripts/t4_lane.py`). A test's
  default timeout is 120 s (`pyproject.toml`); long ones set their own.
- Run the areas you touched, not the whole suite (about an hour on a Windows checkout). CI runs the fast
  bundle on every pull request (`.github/workflows/test.yml`: Ubuntu, Python 3.14, no RIFE).

  ```bash
  PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q -rfE -p no:cacheprovider tests/<area>
  ```

  Add `tests/test_cli_kwargs.py` when `cli.py` or a module's `add_arguments` changes, `tests/packaging` when
  a module is added, moved or removed, and `tests/docs` when a doc is added, renamed or relinked.
  `tests/video` is long; run it in the background.
- On Windows, tests that make a symlink skip on an account that may not create one (`symlink_or_skip` in
  `tests/conftest.py`), and RIFE-dependent tests skip. A new test that makes a link uses that helper, and a
  stand-in executable needs a `.cmd` beside its shebang script (`tests/video/test_rife.py` `_stand_in`). A
  failure that main also has on Windows is not yours: compare against main, not against zero.
- Tests monkeypatch functions by module attribute (for example `loop.distance_matrix`). A refactor keeps
  calling them through that name; grep `monkeypatch.setattr(<module>, "…")` before rerouting a call.
  `frames/extract.py` and `video/loop.py` import back what moved out of them, so `extract.<name>` still
  answers, but a patch there reaches only the callers still in that file: a caller that moves takes its
  patched callees with it, or stays.
- A speed-up that claims "bytes unchanged" proves it: the pre-change form is kept in a test and compared
  (`tests/test_p1_perf_equivalence.py`).
- CI's `lint` job runs ruff and mypy as configured in `pyproject.toml` (`pip install -e ".[lint]"`). Run both
  before a pull request: `.venv/Scripts/python.exe -m ruff check .` and `.venv/Scripts/python.exe -m mypy`.
  mypy checks every module (the debt list it started with, 87 modules, was emptied on 2026-10-31).

## Rules the code keeps

- **No silent fallback.** A missing tool, device or interpreter fails loudly and says why; nothing quietly
  runs another way ([interpreter](docs/interpreter.md), RIFE in [loop-repair](docs/loop-repair.md) §1).
- **A request that may have been charged is never sent again unasked.** Reports say `retryable` and
  `provider_submitted` ([gen](docs/gen.md#outcome--what-failed-whether-to-retry-whether-a-provider-was-charged)).
- **A new module goes in `sprite_gen/_modules.py`**, or the CLI refuses to group it. A new verb also needs
  its line in `cli.py` `COMMANDS`.
- **A stable API.** Names in `sprite_gen.api` keep their names and parameters; changing one is a breaking
  change.

## When you change something

- **New file:** first line `# SPDX-License-Identifier: Apache-2.0`.
- **Changed upstream file:** NOTICE says such files carry `# Modified for sprite-gen-agent (<what>; see
  NOTICE).` under the SPDX header. Many earlier changes lack it; add it when you change a file that has none.
- **User-visible change:** a bullet in CHANGELOG `Unreleased`, starting with what kind of change it is —
  `**New:**`, `**Changed:**`, `**Behavior change:**`, `**Fixed:**`, `**Faster:**` or `**Removed:**`.
- **The doc that owns the behaviour** changes in the same pull request as the code. Find it from the index.
- **New doc:** opens with `> Owns: … · Index: [docs/README.md](README.md)` and gets exactly one line under a
  `## ` section of [docs/README.md](docs/README.md). `tests/docs` fails on a missing index line, a missing
  Owns line or a broken relative link (anchors are not checked).
- **Commits:** `type(scope): what changed` or `type: what changed` (`fix`, `feat`, `perf`, `refactor`,
  `test`, `docs`, `chore`, `ci`), a short subject, the measurements and reasons in the body.
- **Branches:** from `main`, a pull request to `main` on `origin` (jeonghwanko/sprite-gen-agent), merged
  with a merge commit. Nothing is pushed to `upstream` from here.
- Keep edits to upstream docs small and in place: the fork still merges upstream, and moved or split
  upstream files turn every merge into conflicts.
