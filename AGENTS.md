# Working on sprite-gen-agent

> For AI coding agents (Claude Code, Codex, …) that change this repository. Using sprite-gen to make
> sprites, images or clips is [SKILL.md](SKILL.md); this file is about changing the code and its docs.

sprite-gen-agent is a fork of [aldegad/sprite-gen](https://github.com/aldegad/sprite-gen) 3.4.0
(remote `upstream`), first written for the AI Fighter creator kit. What the fork adds is listed in
[NOTICE](NOTICE) and in the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md).

## Where to look

| You need | Read |
|---|---|
| The doc that owns a topic | [docs/README.md](docs/README.md) — the index; its "Owns" column says what each doc decides. Every leaf doc opens with the same `> Owns:` line |
| How the code is laid out | [docs/architecture.md](docs/architecture.md) §0; `sprite_gen/_modules.py` is the one table of domains and verbs |
| A verb's flags | `sprite-gen <verb> --help`; the flags live in that module's `add_arguments` |
| What an embedding program may import | [docs/api.md](docs/api.md) (`sprite_gen.api`, held by `tests/test_api.py`) |
| Renaming a key, a word or a flag | [docs/rename-gate.md](docs/rename-gate.md) — blocking, read before the first replace |
| Why only the venv runs anything | [docs/interpreter.md](docs/interpreter.md) |
| A run that is "quietly wrong" | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Releases and lanes | [docs/release.md](docs/release.md) |

Code area, tests and owning doc line up one to one:

| `sprite_gen/` | `tests/` | Main doc |
|---|---|---|
| `spec` | `tests/spec` | [run-contract](docs/run-contract.md), [architecture](docs/architecture.md) §3 |
| `gen` | `tests/gen` | [gen](docs/gen.md), [video](docs/video.md), [prompt-assembly](docs/prompt-assembly.md), [pose-motion](docs/pose-motion.md) |
| `video` | `tests/video` | [video-pipeline](docs/video-pipeline.md), [loop-repair](docs/loop-repair.md), [loop-review](docs/loop-review.md) |
| `frames` | `tests/frames` | [architecture](docs/architecture.md) §6, [chroma-alpha](docs/chroma-alpha.md), [sheet-slicing](docs/sheet-slicing.md) |
| `curate`, `serve` | `tests/curate`, `tests/serve` | [curation](docs/curation.md) |
| `compose` | `tests/compose` | [run-contract](docs/run-contract.md), [engine-export](docs/engine-export.md), [game-pack](docs/game-pack.md) |
| `effects` | `tests/effects` | [breathing](docs/breathing.md), [recolor](docs/recolor.md), [effects](docs/effects.md) |
| `qa` | `tests/qa` | [qa-motion](docs/qa-motion.md), [scale-check](docs/scale-check.md), [decisions-review](docs/decisions-review.md) |
| `background`, `scene` | `tests/background`, `tests/scene` | [asset-tools](docs/asset-tools.md), [scene](docs/scene.md) |
| `audio` | `tests/audio` | [sound](docs/sound.md) |
| `workflow` | `tests/workflow` | [user-workflow](docs/user-workflow.md) |

## Large files: read by section

Some files are too big to read whole, and a few lines are longer than a reader shows:

| File | Size | How |
|---|---|---|
| `CHANGELOG.md` | ~550 KB, lines up to ~10,000 chars | grep it; read only the `Unreleased` section or the version asked about |
| `docs/loop-repair.md` | ~280 KB, 2,700+ lines | grep `^#` for the outline, then read one section with an offset |
| `docs/video-pipeline.md`, `docs/run-contract.md` | ~60 KB each | same |
| `docs/chroma-alpha.md` | two lines over 8,000 chars | grep for the term rather than relying on a line read |

Not part of the repository: `.claude/worktrees/` holds other sessions' checkouts (never search or edit
there) and `output/` holds local run output.

## Environment

- Run everything with the repo venv, never a global `python3`: `.venv/Scripts/python.exe` on Windows,
  `.venv/bin/python` elsewhere. Bootstrap: `python -m venv .venv`, then `pip install -e ".[dev]"`.
- On Windows set `PYTHONUTF8=1`. Many tests read reports in the locale encoding and fail under cp949
  before their real assertion, which hides real failures.

## Tests

- Plain `pytest` is the fast bundle: `tests/conftest.py` deselects the tests marked `slow`. `pytest --full`
  runs every test; only the release lanes do (`scripts/linux_lane.sh`). Each test has a 120 s timeout.
- Run the areas you touched, not the whole suite (about an hour on a Windows checkout). CI runs the fast
  bundle on every pull request (`.github/workflows/test.yml`: Ubuntu, Python 3.14, no RIFE).

  ```bash
  PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q -rfE -p no:cacheprovider tests/<area>
  ```

  Add `tests/test_cli_kwargs.py` when `cli.py` or a module's `add_arguments` changes, `tests/packaging` when
  a module is added, moved or removed, and `tests/docs` when a doc is added, renamed or relinked.
  `tests/video` is long; run it in the background.
- On Windows some tests fail on main too (symlinks; RIFE-dependent tests skip). Compare against main, not
  against zero.
- Tests monkeypatch functions by module attribute (for example `loop.distance_matrix`). A refactor keeps
  calling them through that name; grep `monkeypatch.setattr(<module>, "…")` before rerouting a call.
- A speed-up that claims "bytes unchanged" proves it: the pre-change form is kept in a test and compared
  (`tests/test_p1_perf_equivalence.py`).

## Rules the code keeps

- **No silent fallback.** A missing tool, device or interpreter fails loudly and says why; nothing quietly
  runs another way ([interpreter](docs/interpreter.md), RIFE in [loop-repair](docs/loop-repair.md) §1).
- **A request that may have been charged is never sent again unasked.** Reports say `retryable` and
  `provider_submitted` ([gen](docs/gen.md#outcome--what-failed-whether-to-retry-whether-a-provider-was-charged)).
- **One taxonomy.** A verb is a module listed in `sprite_gen/_modules.py`; `--help`, the `scripts/` wrappers
  and the docs index are derived from it.
- **A stable API.** Names in `sprite_gen.api` keep their names and parameters; changing one is a breaking
  change.

## When you change something

- **New file:** first line `# SPDX-License-Identifier: Apache-2.0`.
- **Changed upstream file:** a `# Modified for sprite-gen-agent (<what>; see NOTICE).` line under the SPDX
  header, once per file.
- **User-visible change:** a bullet in CHANGELOG `Unreleased`, starting `**New:**`, `**Changed:**` or
  `**Behavior change:**`, linking the doc that owns it.
- **The doc that owns the behaviour** changes in the same pull request as the code. Find it from the index.
- **New doc:** opens with `> Owns: … · Index: [docs/README.md](README.md)` and gets exactly one line under a
  `## ` section of [docs/README.md](docs/README.md). `tests/docs` fails on a missing index line or a broken
  relative link.
- **Commits:** `type(area): what changed` (`fix`, `feat`, `perf`, `refactor`, `test`, `docs`, `chore`), a
  short subject, the measurements and reasons in the body.
- **Branches:** from `main`, a pull request to `main` on `origin` (jeonghwanko/sprite-gen-agent), merged
  with a merge commit. Nothing is pushed to `upstream` from here.
- Keep edits to upstream docs small and in place: the fork still merges upstream, and moved or split
  upstream files turn every merge into conflicts.

<!-- Added for sprite-gen-agent: the coding-agent guide (see NOTICE). -->
