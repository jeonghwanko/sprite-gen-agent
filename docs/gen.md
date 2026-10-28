# `sprite-gen gen` — provider-backed image generation (engine SSoT)

> Owns: `sprite-gen gen` / `gen-set`: providers, default resolution and what a default codex that cannot run turns into, transparency strategy per provider, the billed knobs (`--quality`, `--resolution`), when a refused provider request is sent again (every direct provider call), the outcome every generation report and exit code carries (`ok`, `errors`, `provider_submitted`, `version`, `doctor`), row usage · Index: [docs/README.md](README.md)

Generation is a first-class engine module (`sprite_gen/gen/`). One call = a prompt (+ optional reference images) → one **verified** PNG on
disk, with an optional transparent output whose strategy is decided per provider
(native alpha or deterministic chroma keying).

Providers are the backends the maintainer uses day to day. Others (Gemini,
OpenRouter, fal, BytePlus, …) are absent because nobody here uses them, not because
of a design objection; a contributed provider is welcome when its author will keep
maintaining it ([#37](https://github.com/aldegad/sprite-gen/issues/37)). A backend
whose terms forbid reaching a subscription login from third-party software is not
added, however it is invoked
([#36](https://github.com/aldegad/sprite-gen/pull/36), Antigravity).

| Provider | Backend | Auth | Billing | Output truth | Transparency strategy |
|---|---|---|---|---|---|
| `codex` | codex `image_gen` | ChatGPT OAuth | your ChatGPT subscription | inline base64 in the session rollout jsonl (found through codex's state DB, [below](#codex-sessions--the-rollout-and-the-state-db)), decoded deterministically | **`native`** — image_gen returns a real alpha channel when asked |
| `grok` | direct Imagine `/images/generations` or `/images/edits` | Grok login (preferred) or `XAI_API_KEY` | your SuperGrok Imagine quota; console credit only on the key route | inline bytes decoded and re-encoded as a verified PNG | `chroma` — Imagine returns JPEG only; generate on a key and matte it out |
| `openai` | direct Images `/v1/images/generations` or `/v1/images/edits` | `OPENAI_API_KEY` | **billed per call** — for servers and SaaS | inline base64 decoded and published as a verified PNG | **`native`** — `background: transparent` returns a real alpha channel |

The strategy is declared **once**, on the adapter (`Provider.transparency`), and is
the only place that says what a backend can do. See
[Transparent output](#transparent-output--strategy-per-provider).

## Subscription first — `openai` runs only when you chose it

`codex` and `grok` run on a subscription the user already pays for. `openai`
reaches the same family of GPT image models over the REST API with nothing but a
key — which is what a headless container (a Modal worker, CI, a SaaS backend) can
have, and where an interactive ChatGPT login cannot exist — and every call is
billed against that key. So it is registered, documented, and runs **only when the
user chose it**:

- it runs when `--provider openai` names it, or when it is the user's own answer
  for a default codex that cannot run ([below](#when-codex-cannot-run--ask-or-what-you-chose));
- it is never a default: `SPRITE_GEN_DEFAULT_PROVIDER=openai` is **refused**, not
  honoured, and nothing reaches it unasked;
- the guided flow (`sprite-gen workflow`) never offers it as the image provider; it
  offers it once, as one answer to "codex cannot run here — what now?", billing named;
- having `OPENAI_API_KEY` in the environment changes no route by itself;
- every call it does make prints one stderr line naming the charge *before* the
  request leaves.

If you are one person generating sprites, use `codex` or `grok`.

## Default provider selection

`--provider` is **optional**. When omitted, the backend is resolved by a fixed
precedence:

1. **`SPRITE_GEN_DEFAULT_PROVIDER`** env var (`codex` or `grok`) — the user override.
   An unknown value fails loud.
2. **`codex`** — the hard default (GPT `image_gen`).

A resolved default of `codex` that cannot run here turns into what the user chose
for that ([below](#when-codex-cannot-run--ask-or-what-you-chose)). The grok default
(`SPRITE_GEN_DEFAULT_PROVIDER=grok`) has no reverse fallback — a down grok fails
loud at generation time.

An **explicit `--provider`** is always honored verbatim — it is never overridden by
the choice below. An explicitly named provider that is down fails loud (the provider
adapter raises; a named codex whose usage is used up fails with codex's own words,
exit 1), preserving the operator's stated intent.

Every generation reports which backend actually ran: `provider` (the real
backend), `provider_resolved_from` (`explicit` / `SPRITE_GEN_DEFAULT_PROVIDER` /
`hard-default` / `fallback-from-codex`), and `provider_fallback` when a default
codex that could not run was switched to the provider chosen for that.

## When codex cannot run — ask, or what you chose

A call that names no provider (`gen`, `logo`, `gen-set`, `video-set`'s mid-step
redraw) runs on codex. When codex **cannot run here** it does not switch on its own. Codex cannot
run when

- **it is not signed in** (`cause: login`): `codex_provider.codex_login` — the CLI is
  not on PATH, `codex login status` fails or does not answer, or exits non-zero.
  Known before anything is generated.
- **its usage is used up** (`cause: usage-limit`): `codex exec` ends on one of the
  errors codex itself classes as its usage limit, read from what it says
  (`codex_provider.usage_limit`). Known only after `codex exec` ran, so the
  same choice applies there: the call runs again on the chosen provider.

What happens then is the user's choice, one of three:

| Choice | What a call that names no provider does |
|---|---|
| `ask` (the default) | Nothing is generated. One stderr line `sprite-gen ask codex-unavailable: {…}` and **exit 75**: the line's JSON holds `cause`, `reason` (the reading's own words), `question`, and `choices` — the flag that runs each answer (`--provider openai`, `--provider grok`; `video-set`: `--still-provider …`), `stop` null. The agent asks the user ([SKILL](../SKILL.md#when-codex-cannot-run--exit-75)). |
| `grok` | Runs on grok. One stderr line says codex could not run, why, and whose choice grok was; the report keeps `provider_fallback` (`from`, `to`, `cause`, `reason`, `default_source`, `chosen_by`). |
| `openai` | Runs on the OpenAI API, billed per call: the same switch line and `provider_fallback`, and the charge line every `openai` call prints. |

The choice is read in this order, only when codex cannot run:
`SPRITE_GEN_CODEX_UNAVAILABLE` (`ask` / `grok` / `openai`, an unknown value fails
loud), then the saved one (`sprite-gen defaults save --codex-unavailable …`, in the
[one settings file](user-workflow.md#one-settings-owner); `chosen_by: saved`), then
`ask`. The guided flow asks it once and saves it.

`gen-set` and `video-set` run `gen` per row or item with its output in a log. A
child that asked does not fail its row in silence: the set stops with the same
question and exit 75 once the rows under way have ended (a `gen-set` starts no
further stage; a `video-set` lets its other items finish, as they need no codex), its
report and `table.md` keep the question (`asked`), and the line names the set's own
flag. Rerun the set with the answer's flag; finished rows and items are reused.

## Direct Grok calls and authentication

`sprite-gen gen --provider grok` → `GrokProvider` → xAI Imagine API. The same
path works from any agent engine; no Grok Build subprocess is started.
The default is `grok-imagine-image-2.0`. `--model` selects an **image API model**,
not a Grok Build reasoning model.

Image and video calls share `sprite_gen/gen/xai.py` for credentials and JSON
transport. See
[authentication and expiry](video.md#setup--pick-one-credential)
for setup. No Grok executable is needed during generation with a valid credential.
No agent or credential fallback is started on failure; a refusal xAI documents as a request not
taken (429) is sent again, nothing else is ([a refused request sent again](#a-refused-request-sent-again)).

API contracts: [image generation](https://docs.x.ai/developers/model-capabilities/images/generation),
[image editing](https://docs.x.ai/developers/model-capabilities/images/editing),
[multi-image editing](https://docs.x.ai/developers/model-capabilities/images/multi-image-editing).

## Direct OpenAI calls and authentication

`sprite-gen gen --provider openai` → `OpenAIProvider` → the OpenAI Images REST API.
No `codex` CLI, no browser sign-in, no subprocess: the whole credential is
`OPENAI_API_KEY`, read from the environment at call time and never written
anywhere. Unset, the run stops naming the variable and where to get a key; set but
empty, it says so. It never falls back to the codex ChatGPT login or to another key;
a refusal OpenAI documents as a request not taken is sent again, nothing else is
([a refused request sent again](#a-refused-request-sent-again)) — and see
[subscription first](#subscription-first--openai-runs-only-when-you-chose-it) for why
nothing routes here on its own.

The default model is `gpt-image-2.5-flare`; `--model` selects another gpt-image
model. New images use `POST /v1/images/generations` (JSON); attaching `--ref` uses
`POST /v1/images/edits`, which takes **multipart/form-data** with the references as
repeated `image[]` parts in the order given (up to 16). A reference that is not a
readable PNG, JPEG or WebP fails before upload, and parts are named by position, not
by the local filename.

API contract: [OpenAI Images](https://developers.openai.com/api/images/).

## A refused request sent again

One rule for every direct provider call — the OpenAI and Grok image calls here, the
facing check, the clip request and polls of the video verbs (docs/video.md), and the
Anthropic transport of `pixel-draw` (docs/pixel-draw.md) — in `sprite_gen/gen/retry.py`:

- **A POST is sent again only when its provider documents the answer as a request it
  did not take.** That request made nothing, so asking again buys nothing twice.

## Outcome — what failed, whether to retry, whether a provider was charged

A program that runs `gen`, `gen-set`, `video`, `video-extend`, `video-edit`, `video-resume` or
`video-set` unattended reads the result from the report and the exit code. It does not need to
read stderr. Every report carries the same keys on success, on failure, on the codex question and on SIGTERM:

| Key | Meaning |
|---|---|
| `ok` | `true` when the run made what it was asked for. **`gen-set` and `video-set`**: `ok` is the count of finished rows or items, as it always was; `all_ok` is the set's yes or no. |
| `errors` | `[]` on success, else `{state, code, retryable, message}` per failure. `state` is the row or item state (`null` for a single image or clip); `video-set` adds `item`. |
| `provider_submitted` | `true` once a request left for a provider: a `codex exec` or `claude -p` process was started, or a non-GET request was sent to xAI, OpenAI or Anthropic. `false` means the run ended before any request left, so it cannot have been billed. A set is `true` when any row or item sent one. |
| `provider_requests` | The providers that requests left for, in order (single runs). |
| `exit_code` | On a failure report: the exit status the process ended with. |

The `code` values are stable. `retryable` means that running the same command again may succeed
without changing anything:

| `code` | `retryable` | When |
|---|---|---|
| `preflight` | false | Refused before any provider request: a flag, an input, a login or a key is wrong or missing. |
| `output-path` | false | The folder of `--out`, `--white-check` or `--report` could not be made. This is checked before any request. |
| `provider-asks` | false | A default codex cannot run, and the choice is to ask (exit 75, [above](#when-codex-cannot-run--ask-or-what-you-chose)). |
| `provider-auth` | false | The provider refused the credential (HTTP 401/403). |
| `provider-refused` | false | The provider's content policy refused the request (`CONTENT_POLICY`). Change the request. |
| `provider-usage-limit` | false | codex says its usage is used up. |
| `provider-rate-limited` | true | HTTP 429 was still returned after sprite-gen's own waits ([below](#a-refused-request-sent-again)). |
| `provider-timeout` | true | No answer in time (`SPRITE_GEN_GEN_TIMEOUT_SECONDS`). |
| `provider-unavailable` | true | HTTP 5xx, or no answer that could be read. |
| `provider-failed` | true | The provider ran and failed in another way: an exit code, or an answer with no image. |
| `postprocess-failed` | true | The provider answered, but what it made failed sprite-gen's checks (no alpha, a painted checkerboard, a loop that would not cut). |
| `generation-unfinished` | false | xAI accepted a video request that did not finish. Use `video-resume --request-id` to fetch it. Sending a new request would buy the clip twice. |
| `outcome-unknown` | false | Whether the provider made the result cannot be told. Inspect it before you send anything again. |
| `interrupted` | true | SIGTERM, SIGHUP or Ctrl-C stopped the run. |
| `internal-error` | false | sprite-gen itself failed. This is a bug. |

Exit codes:

| Exit | Meaning |
|---|---|
| 0 | Done. |
| 1 | Failed. Read `provider_submitted` in the report. |
| 2 | An argument was refused by the parser. No request was sent. |
| **3** | **Failed before any provider request.** Nothing was generated or billed. The report says `provider_submitted: false`. For a set, this exit means the set failed and no row or item sent a request. |
| 75 | The codex question. No request was sent for that default codex. |
| 143 / 129 / 130 | Stopped by SIGTERM, SIGHUP or Ctrl-C. The report was written first. |

**Stopping a run.** SIGTERM or SIGHUP stops the run, writes its report with `interrupted` and
`provider_submitted`, and then exits with 128 plus the signal number. A `video` stopped after xAI
took its request also writes that `request_id` and the `video-resume` command, because the clip
may still finish and be billed. A stopped `gen-set` or `video-set` sends SIGTERM to the rows or
items that are running, so each one writes its own report. Rows not yet begun never begin. The
set report then says which rows or items were stopped and which never began. Windows has no
catchable SIGTERM: `taskkill /F` and `TerminateProcess` end the process without a report.

**Paths are checked first.** The folders of `--out`, `--white-check` and `--report` are made before
any provider request. If one cannot be made, the run is refused with exit 3. A `--trim-alpha`
without `--transparent` is also refused before the request. Before this change, both were found
after the image was paid for. `inspect` makes its `--report` folder before reading the run, and
its report has `failed_states` (the states with an error). Use it instead of parsing the
`"<state>: …"` lines in `errors`.

**What an install can run.** `sprite-gen version --json` prints the version, the checkout's commit
and this contract: the exit codes and every code with its `retryable`. `sprite-gen doctor --json`
adds the runtime (NumPy, Pillow), each provider login as `workflow` reads it, and the tools
(`codex`, `grok`, `ffmpeg`, `ffprobe`, `img2webp`, RIFE). It never calls a provider. A ready
login does not prove that a subscription is paid or that quota is left. `doctor` exits 1 only
when the runtime cannot be imported.

## CLI

```bash
sprite-gen gen \
  [--provider codex|grok|openai] # optional; default = SPRITE_GEN_DEFAULT_PROVIDER env → codex; a codex that cannot run asks (exit 75) or runs what you chose for that. openai bills per call
  --prompt "…"            # or --prompt-file PROMPT.txt
  --out DEST.png \
  [--ref REF.png ...]     # repeatable; Grok accepts up to five references, openai up to sixteen
  [--transparent [--alpha-mode auto|native|chroma] [--chroma-key magenta|green]] \
  [--white-check CHECK.png] \
  [--aspect-ratio 1:1]    # grok and openai, e.g. 1:1 or 16:9; grok single-ref edits inherit the source ratio; openai maps it to a gpt-image size
  [--quality low|medium|high|xhigh|max|auto] # billed effort; openai takes the range, grok takes auto/low/medium
  [--resolution 1k|1.5k|2k]  # grok output-size tier, priced with --quality
  [--model ID] \
  [--layout-guide [figure|ui-kit]]  # attach a layout guide: the one-slot figure guide (bare flag) or the UI kit layout
  [--direction side|front|back|front_diagonal|back_diagonal [--facing right|left]] # add the engine's view sentence
  [--handed "the black smartwatch=left wrist" ...]  # with --direction: where an item on one side is in that view
  [--body-plan quadruped | --body-plan "the horse=quadruped" ...]  # with --direction: the view for that body
  [--report REPORT.json] \
  [--keep-session]        # codex: keep the rollout jsonl instead of deleting it
```

Backward-compatible wrapper: `$SPRITE_GEN_ROOT/.venv/bin/python $SPRITE_GEN_ROOT/scripts/generate_sprite_image.py …` (same args).

- **Non-transparent**: the raw PNG (background included) is copied to `--out`.
- **`--transparent`**: publishes a clean RGBA PNG using the provider's transparency
  strategy (below). Either way a result with no transparent area, or any transparent
  pixel that still carries non-zero RGB, **fails loudly before the output or success
  report is published** (No Silent Fallback).
- The pre-process raw is preserved next to the destination as `<out>.raw.png` for audit.
- `--report` writes a `sprite-gen-image-report` JSON: provider, prompt, out/raw paths,
  `raw_bytes`, `elapsed_seconds`, `session_id` (codex), an `alpha` block
  (`strategy` + the measured stats), the `chroma` stats when chroma keying ran, plus the provider `extra` block
  (`auth_source`, `transport`, `endpoint`, and the knobs the request actually
  carried). On codex, `model` is the model codex recorded the session running with, not a copy of `--model`
  ([below](#codex-sessions--the-rollout-and-the-state-db)). It also carries the outcome keys (`ok`, `errors`,
  `provider_submitted`; [Outcome](#outcome--what-failed-whether-to-retry-whether-a-provider-was-charged)). A
  run that fails writes its report too: `ok: false`, `provider: null` and `exit_code`. It is never a success
  report.

### codex sessions — the rollout and the state DB

codex writes no image file of its own that this provider trusts: the PNG is the inline base64 of the
`image_gen` record in the session's **rollout** (`rollout-<timestamp>-<session id>.jsonl`). The provider finds
that rollout in one of two ways:

- **codex's state DB**, when there is one: `state_5.sqlite` in `CODEX_SQLITE_HOME` when that is set, else in the
  Codex state root. Its `threads` table lists each session with its `rollout_path`, `model`, `model_provider`
  and `cli_version`. The row is read read-only, and the rollout is taken from wherever `rollout_path` points. A
  `sqlite_home` set in codex's `config.toml` (rather than the environment) is not read, so that DB is not
  consulted.
- **a scan of `<state root>/sessions`** for `rollout-*-<session id>.jsonl` — the only way for a codex with no
  state DB, and a check on one with it: a scan hit that is a different file from the DB's makes two rollouts
  for one session, which fails.

A DB that cannot be read does not stop the run: the scan still finds the rollout, and the report says why the
DB was skipped. A DB that lists the session at a path where no rollout exists fails loudly, naming that path.

The state DB is an index, not a second place the image is kept. The codex source read on 2026-10-29
(`codex-rs` at `rust-v0.156.1`, `rust-v0.157.1` and `rust-v0.165.0`) still writes every session's rollout and
keeps the `image_gen` records in it (`codex-rs/rollout/src/policy.rs`); no table of the state DB holds a turn's
items. (codex compresses a rollout to `.jsonl.zst` only once it is seven days old, long after this provider has
read it.) A codex that stopped writing the rollout would fail with the message above, not silently.

What the rollout holds has changed shape more often than where it lives. The image record by codex version:

| codex | Record the image rides on |
|---|---|
| 0.140 | `response_item` `image_generation_call` (`result`) |
| 0.144 | `event_msg` `image_generation_end` (`result`, `status`) |
| 0.149 – 0.165.0 | `event_msg` `item_completed`, item `type: Extension`, `kind: image_gen.generation` (`result`, `status`, `transparentBackground`) |
| 0.164.0 + | the same item may carry `fileId` (an attachment-store reference) instead of `result` |

From 0.164.0 the store decides between inline `result` and `fileId`. Through 0.165.0 `codex exec` runs on the
inline store (`passthrough_image_store()`), so the image stays inline. A `fileId` item fails with a message
that names the file id and says to pin a codex that writes images inline. It does not fail with "no result".

The `extra` block of a codex report records what the session ran on:

| Key | What it is |
|---|---|
| `requested_model` | `--model` as passed; `null` when codex's configured default was used |
| `model` (top level) | the model the session ran with: the rollout's last `turn_context.model`, else the state DB's `threads.model`, else `null` — never the request copied |
| `model_source` | `rollout`, `state-db` or `null` |
| `model_effort` | the reasoning effort the rollout's `turn_context` records |
| `model_provider`, `codex_cli_version` | from the rollout's `session_meta` (or the state DB row) |
| `state_db` | `{"status": "indexed" \| "absent" \| "unreadable", "reason"?}` |

`model` is the agent model that called `image_gen`. codex records no name for the image model behind the
tool, so no report field claims one.

- **`--direction`** adds the view sentence a sprite still is drawn with (`still_view_text`; a side or
  diagonal view also takes `--facing`, a front or back view refuses it) and records `extra.view`.
  **`--body-plan`** (with `--direction`; `quadruped`, `legless`, or `"<figure>=<plan>"` per figure of a
  scene) says that view without the feet, chest, hips, shoulders and shoes a person's still is drawn
  with, ending in what the body stands on; see
  [video-pipeline](video-pipeline.md#a-body-that-is-not-a-person----body-plan).
  **`--handed`** adds, per item, which of the character's own sides it is on and where that side is in
  the view; with it, `--facing-fix mirror` is refused and `regen` never mirrors. See
  [video-pipeline](video-pipeline.md#handedness--an-item-on-one-side).

## `--quality` and `--resolution` — the two billed knobs

They are one shared vocabulary; what a backend can honour is a per-provider
capability, declared in its adapter:

| Provider | `--quality` | `--resolution` |
|---|---|---|
| `codex` | refused — `image_gen` exposes no effort dial | refused |
| `grok` | `auto` `low` `medium` | `1k` `1.5k` `2k` |
| `openai` | `auto` `low` `medium` `high` `xhigh` `max` | refused — a gpt-image `size` comes from `--aspect-ratio` |

- **Omitted = the provider's own default.**
- **A level a provider cannot honour fails before the call.** It is never dropped
  from a body you are about to pay for, and never downgraded to whatever the
  backend felt like returning (No Silent Fallback). "Refused" above is a loud error
  naming the flag, not a silently ignored argument.
- **grok prices an image on the pair**, so `2k` + `medium` costs more than `1k` +
  `low`. The resolution names are **tiers, not pixel counts**: the service decides
  the pixels. Nothing here derives a size from the name.
- Every call that spends API credit rather than a subscription prints the knobs it
  is being billed on in the same stderr line as the charge.

## Transparent output — strategy per provider

Each adapter declares the one
strategy it can execute, and `--alpha-mode auto` (the default) follows it:

| Strategy | Who | What happens | Refused when |
|---|---|---|---|
| `native` | `codex` (**first choice**), `openai` (`background: transparent`) | On codex the transport prompt asks image_gen for a genuinely transparent background; on openai the request carries `background: transparent` with `output_format: png`, which the API answers with a real alpha channel. Either way the decoded PNG's alpha is **measured**: no alpha band or `alpha_zero_pct: 0.0` refuses to publish, RGB under alpha 0 is scrubbed, partial alpha (1–254) is left as produced and reported as `partial_alpha_pct`. | The model drew a checkerboard / flat background (RGB image) — nothing can recover alpha from that, so the run fails instead of silently keying. |
| `chroma` | `grok` (only option), `codex` with `--alpha-mode chroma` | Generate on a `#FF00FF` (or `#00FF00`) background — pick the key by subject colour (magenta subjects → green key) — and matte it out. Gradients and texture within that chroma family are supported. | `alpha_zero_pct: 0.0` after keying, or stale RGB under alpha 0; and, before keying, a background painted as a transparency checkerboard (below). |

- **`auto` steps down to `chroma` when `--ref` is attached**, on either native provider. The decision is made before
  the model runs, printed to stderr, and recorded as `alpha.strategy_source:
  "refs-attached"` (`provider-default` / `explicit` otherwise). `--alpha-mode native`
  still forces native alpha with refs — and fails loud on an RGB result.
- **The step down asks for its key.** A planned key needs a key background in the
  prompt, so the engine adds the `--chroma-key` key's line
  (`chroma.KEY_BACKGROUND_TEXT`, the sentence a clip's mid-step redraw also ends on)
  to a prompt that names no key, and leaves a prompt that does as it is — no second
  line. A prompt that names the other key is left on it: the matte reads the key off
  the borders. Write the key as "a magenta background" or "#FF00FF": "no green screen"
  reads as naming green, and "a background of pure magenta" is not read
  ([prompt-assembly](prompt-assembly.md#known-faults-kept-for-now)).
  `alpha.key_background` records `{"injected": true|false, "key": …}`,
  stderr says which, and the report's `prompt` is the prompt sent. Only `auto`'s step
  down adds the line: `--alpha-mode chroma` is the caller's own key and prompt, and a
  native run asks for alpha instead.
- **A ref run whose raw already has a transparent background is not keyed.** The key
  is planned before the model runs, but the raw is read before it is applied
  (`chroma.classify_raw_alpha`): a raw with an alpha band, at least 5 % of its pixels
  at alpha 0 and at least half of its one-pixel border at alpha 0 is `real-alpha`, and
  it is published on its own alpha through the `native` check
  (`alpha.strategy: "native"`, `alpha.strategy_source: "refs-attached-raw-alpha"`).
  A raw with no alpha band or no alpha-0 pixel — a drawn
  checkerboard, a key background, an opaque RGBA — is `no-alpha` and keyed
  (`strategy_source: "refs-attached"`), but a painted checkerboard is refused before
  keying (below). Alpha 0 that misses either bar is `ambiguous`:
  keyed, with a `warning` in the report and on stderr. The verdict and its
  numbers are in `alpha.raw_alpha` on every ref run that planned the key. The check
  belongs to `auto`'s step down only — `--alpha-mode chroma` keys whatever comes back.
  The judgment of the alpha band is one function,
  `chroma.alpha_verdict`: [`pixel-draw`](pixel-draw.md#gates)'s G3 asks it of every frame
  it drew (a sprite or icon frame must be `real-alpha`, a tile `no-alpha`).
- **A painted transparency checkerboard is refused before keying.** Asked for a key
  background, a model sometimes paints the grey and white checkerboard that editors show
  behind transparent pixels instead. The matte reads the key off the borders, so it reads
  the checkerboard and keys nearly the whole image away: a UI kit sheet keyed that way has
  nothing left to cut. A caller generates again.
- `--alpha-mode chroma` on codex is for prompts that already carry a key background: the native request is **not** added to the prompt
  and the raw is keyed like a grok run.
- `--alpha-mode native` on a `chroma`-only provider **fails loud before any model
  call** — a strategy the backend cannot execute is not a fallback candidate, and
  native → chroma never happens silently either (the prompt shapes are different).

## Sprite-row usage

In the atlas pipeline the rows of a prepared run are generated by
`sprite-gen gen-set --run-dir <run>`: every non-mirrored state, `--concurrency` 6 at a
time, each row with the identity ref the run declares (`base-source.*`, or the accepted
direction anchor for a direction-contract action row — `sprite_gen.curate.anchor` owns
that choice) plus its layout guide, into `raw/<state>.png`. Each row gets
`reports/gen-set/<state>.json`, the batch writes `reports/gen-set/table.md` and
`set.report.json`, existing rows are reused unless `--force`, a direction run generates
its anchors before its rows and stops when an anchor failed, and the exit code is
non-zero when any row failed: 3 when no row sent a provider request, 1 otherwise. `set.report.json` gains
`all_ok`, `errors` (each row's own, named by state) and `provider_submitted`, and each item gains its own
`errors` and `provider_submitted`. A failed row's report is removed so the next run regenerates it, but what
that report said is kept in the set report
([Outcome](#outcome--what-failed-whether-to-retry-whether-a-provider-was-charged)). `--provider` (`codex`, `grok` or `openai`) is honoured verbatim;
unspecified, it resolves exactly as `gen` does (above): a switch to the provider chosen for a
codex that cannot run is recorded per row with the row's `auth_source`, and rows that asked stop
the set with the question ([above](#when-codex-cannot-run--ask-or-what-you-chose)). Each row's
output goes to its log, but a row's billing line and a provider switch also print on the
terminal as they happen, so a metered row is never announced only in a file.

One row by hand is the same call `gen-set` makes: `--provider codex` (or `grok`) with
`prompts/<state>.txt`, writing `raw/<state>.png`.
The row prompts are `prepare`'s. For a body that is not one biped,
`prepare --body-plan quadruped` (or `legless`, or `"<figure>=<plan>"` per figure) writes every row
without a part the body lacks and says what it stands on
([sheet rows](video-pipeline.md#the-sheet-rows--prepare---body-plan)).
The row prompts still carry the request chroma key on the background and frame
extraction removes it downstream — rows are generated **without** `--transparent`, so
the native strategy does not apply to them.
The correction loop (`sprite-gen correction-loop --provider-command …`) can drive this
`gen` command as its regeneration step so inspect → score → hint → regenerate closes
against a real provider.

## Speed

Pick per need: grok for speed, codex
for tighter prompt adherence.

## `--layout-guide` — room above the head and under the feet

A single still drawn from words tends to fill its frame top to bottom. Animated in place
(a walk bobs, a jump rises), the head then touches the top of the frame. `--layout-guide`
draws the one-slot form of the row guide `prepare` writes for every row and attaches it
after any `--ref`:

- the frame, its inner safe box at the row guide's margin, and the centre line;
- an orange **crown line** and a teal **floor line**, each one margin *inside* the safe
  box. On the box's own edges a crown line
  leaves hair and hats to fill the room above it, and a floor line reads as a frame
  edge that the feet float above.

The prompt gains a paragraph saying what each line means: the anatomical top of the
skull on the orange line (hair, hats, raised limbs and props ignored, but kept inside
the box), the supporting soles on the teal line, the two lines as scale and height
rather than artwork, and nothing of the guide itself in the output. The guide is drawn
for `--aspect-ratio` (square without one) and the report records the cell it used in
`extra.layout_guide` (size, safe margins, `crown_y`, `floor_y`); the report's `refs` are
still the caller's own.

The guide is an attached image, so it changes the call: openai sends it to
`/images/edits` and bills it as image input, grok sends it as a reference, and
`--transparent` steps down to chroma keying as it does for any `--ref`. How closely a
model follows the guide differs from model to model, so measure the one you use
(`--model`) on your own subjects before relying on it.

## `--trim-alpha` — the bottom edge is the foot line

A generated transparent still carries an unpredictable band of empty alpha under the
feet and around the sides; two stills placed on the same floor line then stand at
different heights, and a still swapped for a strip cell (whose feet sit on the cell
bottom) jumps. `--trim-alpha` (only with `--transparent`) crops the published PNG to the
bbox of its opaque pixels (alpha ≥ 8) after the transparency step, so the image's bottom
edge *is* the ground-contact line. The subject is never cut — only fully transparent
margin goes — and the report records `extra.trim_alpha` with the `bbox`, `before` /
`after` sizes and the `margin_px` removed on each side. The `.raw.png` beside the output
is untouched.

