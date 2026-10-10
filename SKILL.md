---
name: acemusic
description: >-
  通过免费的 ACEMusic 云端 API（ACE-Step v1.5 turbo，OpenAI 兼容接口）生成音乐/BGM：
  漫剧/短剧场景配乐、可循环短片段、纯音乐、带歌词人声曲；内置 504 网关超时自动重试
  与场景表批量生成。触发词：「漫剧 BGM」「生成音乐」「配乐」「主题曲」「音效音乐」、
  「ACE-Step」「ACEMusic」「scene music」「text to music」「给视频配背景音乐」。
  边界：只做音乐/音效生成；角色对白、旁白等语音合成（TTS）不在本 skill 范围，
  请改用 TTS 工具（EdgeTTS / CosyVoice / 讯飞等）。
license: MIT
metadata:
  title: "ACEMusic — 免费文生音乐（ACE-Step 1.5 turbo）"
  icon: "🎵"
---

# ACEMusic Skill — 免费文生音乐（ACE-Step v1.5）

Generate music via the free ACEMusic cloud API. Zero-dependency Python CLI
(stdlib only). Designed for short, loopable BGM segments — the reliable sweet
spot for the API's 60-second gateway timeout.

## When to use / not use

- **Use** for: scene BGM (漫剧/短剧/视频配乐), loop stings, theme-song drafts,
  instrumentals, lyric+vocal tracks (retry-heavy).
- **Do not use** for: character dialogue / narration TTS (use EdgeTTS,
  CosyVoice, iFlytek, …), or for >60s single-shot generation (the cloud
  gateway times out at ~60s — split into short clips instead).

## Prerequisites

- Python 3.8+ (standard library only, no pip installs).
- A free API key from <https://acemusic.ai> — store it as env
  `ACEMUSIC_API_KEY`, or a local `.acemusic_key` file (never commit it).

## Quick start

Run the CLI from the skill root (paths relative to this skill directory):

```bash
# 1) list models + pricing
python scripts/acemusic.py models

# 2) generate one short BGM clip (name = output base name)
python scripts/acemusic.py gen "15秒 动漫风BGM，国风电子，120BPM" --name opener

# 3) generate with explicit music parameters
python scripts/acemusic.py gen "战斗BGM" --bpm 150 --keyscale "A Minor" --duration 30 --instrumental

# 4) batch-generate a scene table (漫剧 one-episode BGM set)
python scripts/acemusic.py batch assets/scenes.example.json --out out/
```

- `gen` writes `<out>/<name>_<HHMMSS>.mp3` and prints size / estimated duration
  / caption. `--json` appends a machine-readable summary (paths, attempts, secs).
- `batch` reads `assets/scenes.example.json`-shaped JSON: a `tracks` list of
  `{name, prompt, [model], [temperature], [retries], [music params…]}` plus an
  optional top-level `out_dir`. It prints a per-track result table + JSON.
- Prompts: put duration in the text ("15秒 …"), plus style/instruments/BPM/key.
  Keep clips ≤15s for high success rate.

## Optional music parameters (all subcommands except `models`)

Every generation accepts these optional parameters (CLI flag ↔ batch JSON
field; batch fields also accept camelCase aliases such as `keyScale`):

| Flag | Batch JSON field | Meaning (ACE-Step official docs) |
|---|---|---|
| `--top-p` | `top_p` / `topP` | sampling top-p (documented `supported_sampling_parameters`) |
| `--duration N` | `duration` / `audio_duration` | target length in seconds (documented 10–600) |
| `--bpm N` | `bpm` | tempo, 30–300 |
| `--keyscale "C Major"` | `key_scale` / `keyScale` | key/scale. Note: `--key` is the **API key**; musical key is `--keyscale` |
| `--time-signature 4` | `time_signature` | code `2/3/4/6` → 2/4, 3/4, 4/4, 6/8 |
| `--lyrics "…"` | `lyrics` | lyrics text for vocal tracks |
| `--vocal-language zh` | `vocal_language` | vocal language code (default `en`; use `zh` for Chinese vocals) |
| `--thinking` | `thinking` | LM planning mode, quality boost |
| `--instrumental` | `instrumental` | force pure instrumental (no vocals) |
| `--seed N` | `seed` | fixed seed for reproducibility |
| `--temperature T` | `temperature` | sampling temperature (default 0.9) |

**Routing (auto):** each set parameter is sent as a structured API field
**and** merged into the prompt text as a hint (only when the prompt does not
already cover it). So the intent survives even if the gateway ignores unknown
fields; if the gateway rejects them (HTTP 400), the CLI automatically retries
with standard fields only. Field-by-field behavior matrix:
`references/api-notes.md` (§ Cloud field probe).

For 漫剧 scene tables, set `bpm`/`duration`/`key_scale`/`seed` per track to
keep an episode's BGM set stylistically consistent, and use `--instrumental`
plus `--lyrics` for theme-song drafts.

## 504 / gateway-timeout policy

The gateway hard-times-out synchronous requests at ~60s (504 = Cloudflare
page, often still "working"). The CLI already retries retryable codes
(429/5xx) with linear backoff (`--retries N`, `--retry-wait S`).

- On a final failure: shorten the prompt/duration and retry, or re-issue later
  (load-dependent; the same prompt often succeeds off-peak).
- Longer targets (`--duration` > 20s) spend more of the 60s window — expect
  more retries; prefer ≤15s clips for scene BGM.

## Verifying outputs

- Every saved file starts with the `ID3` header and decodes as MPEG; check
  `head -c 3` or the printed MB size (~128kbps: 15s ≈ 0.25MB).
- Batch JSON summary lists `ok: false` rows with `reason` for failed scenes.

## Legal

Before publishing: verify originality, disclose AI generation, and don't clone
protected artist styles without permission (ACE-Step license terms).

## Files

- `scripts/acemusic.py` — the CLI (models / gen / batch).
- `assets/scenes.example.json` — 漫剧 scene-table template.
- `references/api-notes.md` — probed API facts: endpoints, response shape,
  60s gateway cap, key handling, legal notes. Re-probe before assuming.
