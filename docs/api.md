# Python API for embedding programs

> Owns: `sprite_gen.api`, the names a program that embeds sprite-gen may import. Index: [docs/README.md](README.md)

Most callers run the `sprite-gen` command and read its JSON. A program that runs sprite-gen's Python in its own
process imports only from `sprite_gen.api`; every other module is internal and may be renamed or moved. The AI
Fighter creator kit is such a program.

A name in `sprite_gen.api` keeps its name and its parameters. Changing either is a breaking change: it is named
in the changelog and fails `tests/test_api.py`. A caller that needs something not listed here asks for it to be
added; it does not import the internal module.

| Name | What it is |
|---|---|
| `analyze_motion(sequence, *, contacts, foot_box, facing)` | The motion and stance report of `inspect-motion` ([asset-tools](asset-tools.md)) |
| `STANCE_REASONS` | Each stance reason's stable code and its text; decide on `reason_codes`, not on the texts |
| `load_asset(source, *, state, fps, anchor)` | A strip, run manifest or atlas state as frames, durations and an anchor |
| `acquire_run_dir_lock(run_dir, owner)` / `release_run_dir_lock(run_dir)` | One writer for a run folder at a time |
| `atomic_write_set(payloads)` | Several files replaced together or not at all |
| `probe_access(provider)` | Whether a provider's login can be used here, without calling the provider |
| `rife_status()` | RIFE as the engine finds it, checked on one frame: `ok`, `missing` or `broken`, never an install |

The names load on first use: `from sprite_gen.api import probe_access` imports the access check only, not the
motion or RIFE code.
