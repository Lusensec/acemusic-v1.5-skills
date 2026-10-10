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
3. For long/complex or vocal tracks, prefer running the model **locally**
   (ACE-Step 1.5, `uv run acestep-api`, no gateway timeout) or re-issue.

Client-side socket timeout is set to 75s (slightly above the gateway cap) so a
504 is surfaced and retried instead of hanging.

## Key handling

Never commit keys. Resolve order in the CLI: `--key` → `ACEMUSIC_API_KEY` env
→ `./.acemusic_key` → `~/.acemusic_key`. `.gitignore` excludes both key files.
Free keys are obtained at https://acemusic.ai.

## Legal / copyright

ACE-Step's license encourages verifying originality of generated music,
disclosing AI involvement, and obtaining permission before imitating
protected styles. See the repo `LICENSE` & disclaimer in the ACE-Step 1.5
README.
