# ACEMusic (ACE-Step) API Notes

Probed 2026-10-14 against `https://api.acemusic.ai` (official ACE-Step cloud
from the open-source [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5)
project). These facts are what the CLI in `scripts/acemusic.py` encodes;
re-probe if the service changes.

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/v1/models` | Bearer / `ai_token` | model catalog + pricing |
| POST | `/v1/chat/completions` | Bearer / `ai_token` | generate (OpenAI-compatible shape) |

- `GET /v1/models?ai_token=<key>` also works (key via query param).
- Auth header: `Authorization: Bearer <key>` (or query `ai_token`).
- `/v1/usage`, `/v1/account`, `/v1/keys` all return 504 (not implemented).
- No published OpenAPI doc; `/openapi.json` and `/docs` are 404.

## Model card (from `/v1/models`)

```
id:              acemusic/acestep-v1.5-turbo
input_modalities: ["text", "audio"]
output_modalities:["audio", "text"]
pricing:          prompt=0 completion=0 request=0   (free)
sampling params:  temperature, top_p
context_length:   4096
```

So: **text-to-music and audio-reference are both supported; output is audio
+ a text caption.** All pricing fields are `0` (free).

## Generation request shape

OpenAI-compatible chat completion:

```json
{
  "model": "acemusic/acestep-v1.5-turbo",
  "messages": [{"role": "user", "content": "<prompt: style/duration/BPM/lyrics>"}],
  "temperature": 0.9
}
```

Prompt is natural language. Put the **duration in the prompt**
(e.g. "15秒 ...BGM"). `stream:true` does NOT avoid the gateway timeout.

## Response shape

```
choices[0].message.audio[0].audio_url.url = "data:audio/mpeg;base64,<mp3>"
choices[0].message.content                 = "<caption text, optional>"
usage = {"completion_tokens":100, "prompt_tokens":0, "total_tokens":100}
```

The audio is a **base64 data URL (MPEG/MP3)**. Decode `base64` after the
`;base64,` marker and write bytes → `.mp3`.

## The 60-second gateway limit (important)

The service sits behind Cloudflare. A **synchronous** request that takes
longer than ~60s returns **HTTP 504** with a Cloudflare error HTML page
(`504: Gateway time-out`, sometimes labelled "Working"). Observed:

- A ~15s BGM generation completed in 54.8s → **200 OK**.
- 20s / 30s / vocal prompts hit **504** at exactly ~61.1s.

**Mitigations built into the CLI:**
1. Keep clips short (≤15s) so generation finishes inside the 60s window.
2. Retry on retryable codes (`429/500/502/503/504`) with linear backoff
   (`--retries`, `--retry-wait`). The gateway timeout is retryable — the same
   prompt often succeeds on a later attempt when load is lower.
3. This skill is **cloud-only**. If a prompt reliably 504s, shorten the clip
   / retry later instead of adding features.

Client-side socket timeout is set to 75s (slightly above the gateway cap) so a
504 is surfaced and retried instead of hanging.

## Official model parameters (ACE-Step docs)

The model's full input surface is documented in the open-source project's
`docs/` (REST `API.md`, Python `INFERENCE.md`). The cloud gateway exposes an
OpenAI-compatible subset; the CLI mirrors these parameters as optional flags.
Source: https://github.com/ace-step/ACE-Step-1.5 (docs/en).

### Text / lyrics

| Param | Cloud flag | Notes |
|---|---|---|
| `prompt` (caption) | `gen "<prompt>"` | music description, natural language |
| `lyrics` | `--lyrics` | vocal lyrics; empty + `--instrumental` = no vocals |
| `vocal_language` | `--vocal-language` | `zh`/`en`/`ja`… default `en` |
| `instrumental` (implicit) | `--instrumental` | pure-music switch |
| `thinking` | `--thinking` | let the 5Hz LM plan/enhance (quality boost); auto-skipped for cover/repaint/extract tasks |

### Music attributes (hard controls in the local API)

| Param | Range | Cloud flag |
|---|---|---|
| `bpm` | 30–300 | `--bpm` |
| `key_scale` | e.g. `"C Major"`, `"Am"` | `--keyscale` |
| `time_signature` | `2`/`3`/`4`/`6` (→ 2/4,3/4,4/4,6/8) | `--time-signature` |
| `audio_duration` | 10–600 s | `--duration` |

User-provided values win; the LM only auto-fills missing fields.

### Sampling / generation control

| Param | Default | Cloud flag |
|---|---|---|
| `temperature` | 0.9 (CLI) | `--temperature` (documented `supported_sampling_parameters`) |
| `top_p` | — | `--top-p` (documented `supported_sampling_parameters`) |
| `seed` / `use_random_seed` | random | `--seed` (pin for reproducibility) |
| `batch_size` | 2 (max 8, local only) | — |

### Audio reference / editing (local API; cloud status unverified)

`reference_audio_path` (style transfer), `src_audio_path` (cover/repaint),
`task_type` (`text2music`/`cover`/`repaint`/`lego`/`extract`/`complete`),
`instruction`, `repainting_start/end`, `audio_cover_strength`. The model card
lists `audio` as an input modality, but the cloud request shape for a
reference-audio content part is undocumented — treat as **not exposed** by the
CLI.

## Cloud field probe (2026-10-14, free tier)

Question: does the cloud gateway accept ACE-Step-specific structured fields
(`duration`, `bpm`, `key_scale`, `time_signature`, `lyrics`, `vocal_language`,
`thinking`, `instrumental`, `seed`, `top_p`)?

Findings:

- **No 400 rejections observed.** Requests carrying the extra fields were
  never rejected at validation — they either 200 (off-peak) or 504 (load).
  Even a deliberately invalid `temperature: "banana"` 504'd instead of 400,
  i.e. the gateway does not strictly validate parameter values.
- Consequence: the CLI's **dual routing is safe** — structured fields + prompt
  text hints can both be sent without risking a hard rejection; the 400-strip
  fallback is just insurance.
- Whether the cloud model actually **honours** the structured fields
  (vs. ignoring them) could not be verified: during the probe window the
  service was under heavy load and even control calls (no extra fields)
  504'd, so output A/B comparison was impossible. The prompt-hint side of the
  dual routing guarantees the musical intent in all cases.
- `top_p` and `seed` are the only standard-OpenAI-style fields; treat the
  rest as "accepted, effect unverified".

Re-probe with A/B duration comparison when load is low: two identical prompts
with `duration: 15` vs `duration: 60` — if MP3 sizes differ ~4x, the field is
honoured.


## Key handling

Never commit keys. Resolve order in the CLI: `--key` → `ACEMUSIC_API_KEY` env
→ `./.acemusic_key` → `~/.acemusic_key`. `.gitignore` excludes both key files.
Free keys are obtained at https://acemusic.ai.

## Legal / copyright

ACE-Step's license encourages verifying originality of generated music,
disclosing AI involvement, and obtaining permission before imitating
protected styles. See the repo `LICENSE` & disclaimer in the ACE-Step 1.5
README.
