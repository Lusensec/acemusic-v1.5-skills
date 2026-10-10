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
  CosyVoice, iFlytek, …), or for >60s single-shot generation (run ACE-Step
  locally instead).

## Prerequisites

- Python 3.8+ (standard library only, no pip installs).
- A free API key from <https://acemusic.ai> — store it as env
  `ACEMUSIC_API_KEY`, or a local `.acemusic_key` file (never commit it).

## Quick start

Run the CLI from `scripts/` (paths relative to this skill directory):

```bash
# 1) list models + pricing
python scripts/acemusic.py models

# 2) generate one short BGM clip (name = output base name)
python scripts/acemusic.py gen "15秒 动漫风BGM，国风电子，古筝+合成器，120BPM" --name opener

# 3) batch-generate a scene table (漫剧 one-episode BGM set)
python scripts/acemusic.py batch assets/scenes.example.json --out out/
```

- `gen` writes `<out>/<name>_<HHMMSS>.mp3` and prints size / estimated duration
  / caption. `--json` appends a machine-readable summary (paths, attempts, secs).
- `batch` reads `assets/scenes.example.json`-shaped JSON: a `tracks` list of
  `{name, prompt, [model], [temperature], [retries]}` plus an optional top-level
  `out_dir`. It prints a per-track result table + JSON.
- Prompts: put duration in the text ("15秒 …"), plus style/instruments/BPM/key.
  Keep clips ≤15s for high success rate.

## 504 / gateway-timeout policy

The gateway hard-times-out synchronous requests at ~60s (504 = Cloudflare
page, often still "working"). The CLI already retries retryable codes
(429/5xx) with linear backoff (`--retries N`, `--retry-wait S`).

- On a final failure: shorten the prompt/duration and retry, or run locally.
- Re-issuing the same prompt at a quieter moment usually succeeds.

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
