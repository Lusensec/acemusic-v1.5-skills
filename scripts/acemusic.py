#!/usr/bin/env python3
"""ACEMusic CLI — free text-to-music generation via the ACEMusic cloud API.

Wraps the ACE-Step v1.5 turbo model served at https://api.acemusic.ai
(OpenAI-compatible /v1/chat/completions endpoint). Built for short, loopable
BGM segments (e.g. 漫剧/comic-drama scene music).

Zero dependencies: Python 3.8+ standard library only.

API key resolution order:
  1. --key CLI flag
  2. ACEMUSIC_API_KEY environment variable
  3. ./.acemusic_key file (local to current directory)
  4. ~/.acemusic_key file

Never commit key files. .gitignore excludes them.

Optional music parameters (mirroring the official ACE-Step docs):
  --top-p / --duration / --bpm / --keyscale / --time-signature /
  --lyrics / --vocal-language / --thinking / --instrumental / --seed

Routing strategy: each set parameter is sent BOTH as a structured body field
AND merged into the prompt text (as a "，时长 30 秒 / BPM 150 / 调式 C Major …"
hint) when the prompt does not already mention it — so the parameters are
respected even if the gateway ignores unknown fields. If the gateway rejects
the extra fields with HTTP 400, the CLI automatically retries with standard
fields only (prompt hints already carry the intent).

Usage:
  python acemusic.py models
  python acemusic.py gen "15秒 动漫风BGM，国风电子" --name opener
  python acemusic.py gen "战斗BGM" --bpm 150 --keyscale "A Minor" --duration 30
  python acemusic.py gen "主题曲" --lyrics "月光洒在古巷上" --vocal-language zh
  python acemusic.py batch assets/scenes.example.json --out out/
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

API_BASE = "https://api.acemusic.ai"
DEFAULT_MODEL = "acemusic/acestep-v1.5-turbo"
# The gateway (Cloudflare) hard-times-out synchronous requests at ~60s.
# Keep the client timeout slightly above that so a 504 is surfaced cleanly.
CLIENT_TIMEOUT = 75
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
# Cloudflare (Error 1010) blocks non-browser User-Agent signatures.
# Send a full browser-like header set — the plain Python-urllib UA gets 403.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}
DATA_URL_RE = re.compile(r"^data:audio/([a-z0-9.]+);base64,(.+)$", re.DOTALL)
# time_signature codes from the official docs: 2/3/4/6 → 2/4, 3/4, 4/4, 6/8
TS_MAP = {"2": "2/4", "3": "3/4", "4": "4/4", "6": "6/8"}
# ACE-Step-specific top-level fields; stripped if the gateway answers 400.
EXTRA_FIELD_KEYS = ("duration", "bpm", "key_scale", "time_signature", "lyrics",
                    "vocal_language", "thinking", "instrumental")


def fail(msg, code=1):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(code)


def warn(msg):
    sys.stderr.write("WARN: %s\n" % msg)


def resolve_key(args):
    if getattr(args, "key", None):
        return args.key.strip()
    env = os.environ.get("ACEMUSIC_API_KEY", "").strip()
    if env:
        return env
    for path in (os.path.join(os.getcwd(), ".acemusic_key"),
                 os.path.expanduser(os.path.join("~", ".acemusic_key"))):
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                key = fh.read().strip()
            if key:
                return key
    fail("no API key found. Set ACEMUSIC_API_KEY, pass --key, or write the key "
         "to ./.acemusic_key (obtain a free key at https://acemusic.ai)")


def http_request(method, url, key, payload=None, timeout=CLIENT_TIMEOUT):
    """Return (status, parsed_json_or_text, content_type)."""
    headers = dict(BROWSER_HEADERS)
    headers["Authorization"] = "Bearer %s" % key
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read()
        ct = e.headers.get("Content-Type", "")
        status = e.code
    except urllib.error.URLError as e:
        fail("network error calling %s: %s" % (url, e.reason))
    text = raw.decode("utf-8", errors="replace")
    if "json" in ct:
        try:
            return status, json.loads(text), ct
        except json.JSONDecodeError:
            return status, text, ct
    return status, text, ct


def extract_audio(j, out_dir, name_hint):
    """Extract MP3 bytes from an OpenAI-style response; return (path, caption)."""
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    caption = msg.get("content")
    audios = msg.get("audio") or []
    if not isinstance(audios, list):
        audios = [audios]
    saved = []
    for i, item in enumerate(audios):
        url = None
        if isinstance(item, dict):
            url = (item.get("audio_url") or {}).get("url") or item.get("url")
        elif isinstance(item, str):
            url = item
        if not url:
            continue
        m = DATA_URL_RE.match(url.strip())
        if not m:
            warn("unexpected audio url shape (truncated): %s" % url[:120])
            continue
        ext = "mp3" if ("mpeg" in m.group(1) or "mp3" in m.group(1)) else m.group(1)
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (name_hint or "track")).strip("_")[:40]
        path = os.path.join(out_dir, "%s_%02d.%s" % (safe, i, ext))
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(m.group(2).replace("\n", "").replace("\r", "").replace(" ", "")))
        saved.append(path)
    return saved, caption


def build_payload(model, prompt, temperature, *, top_p=None, duration=None,
                  bpm=None, key_scale=None, time_signature=None, lyrics=None,
                  vocal_language=None, thinking=False, instrumental=False,
                  seed=None):
    """Build the request payload.

    Set parameters are sent twice:
      1. as structured top-level body fields (documented OpenAI fields always;
         ACE-Step-specific fields may be ignored or rejected by the gateway), and
      2. as plain-text hints merged into the prompt, but only when the prompt
         does not already cover the concept — so intent survives even if the
         gateway drops the structured fields.
    Returns (payload, extra_keys_used).
    """
    content = (prompt or "").strip()
    hints = []
    if duration:
        if not re.search(r"(?i)duration|时长|\d+\s*秒", content):
            hints.append("时长 %d 秒" % duration)
    if bpm:
        if not re.search("(?i)bpm", content):
            hints.append("BPM %d" % bpm)
    if key_scale:
        if not re.search("(?i)keys?cale|key_scale|调式|调性", content):
            hints.append("调式 %s" % key_scale)
    if time_signature:
        if not re.search(r"(?i)拍号|拍子|time.?sig|\d\s*/\s*\d+\s*拍", content):
            hints.append("拍号 %s" % TS_MAP.get(str(time_signature), str(time_signature)))
    if lyrics:
        if "歌词" not in content:
            hints.append("歌词：%s" % lyrics)
    if vocal_language:
        if not re.search("语言|language", content):
            hints.append("演唱语言 %s" % vocal_language)
    if instrumental:
        if not re.search("(?i)纯音乐|instrumental|无人声|纯 instrumental", content):
            hints.append("纯音乐，无人声")
    if thinking:
        hints.append("高质量精细编排")
    if hints:
        content = content + "，" + "，".join(hints)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
    }
    used = []
    if top_p is not None:
        payload["top_p"] = top_p
    if seed is not None:
        payload["seed"] = seed
    if duration is not None:
        payload["duration"] = duration
        used.append("duration")
    if bpm is not None:
        payload["bpm"] = bpm
        used.append("bpm")
    if key_scale is not None:
        payload["key_scale"] = key_scale
        used.append("key_scale")
    if time_signature is not None:
        payload["time_signature"] = time_signature
        used.append("time_signature")
    if lyrics is not None:
        payload["lyrics"] = lyrics
        used.append("lyrics")
    if vocal_language is not None:
        payload["vocal_language"] = vocal_language
        used.append("vocal_language")
    if thinking:
        payload["thinking"] = True
        used.append("thinking")
    if instrumental:
        payload["instrumental"] = True
        used.append("instrumental")
    return payload, used


def strip_extras(payload, used_keys):
    for k in used_keys:
        payload.pop(k, None)
    return payload


def generate_once(payload, key):
    """One POST to the chat/completions endpoint. Returns (status, body, secs)."""
    started = time.time()
    status, body, _ct = http_request("POST", API_BASE + "/v1/chat/completions", key, payload)
    return status, body, time.time() - started


def generate_with_retry(payload, key, retries, retry_wait, label="track", extra_keys=()):
    """POST with retries on gateway 5xx (the 60s Cloudflare timeout is 504).

    HTTP 400 (gateway rejects unknown fields) triggers one automatic fallback:
    strip the ACE-Step-specific fields and re-send — the prompt text already
    carries the parameters as hints, so nothing is lost.
    """
    last_status, last_body = None, None
    stripped = False
    for attempt in range(1, retries + 1):
        status, body, secs = generate_once(payload, key)
        last_status, last_body = status, body
        if status == 200:
            return body, attempt, secs
        if status == 400 and extra_keys and not stripped:
            warn("%s: HTTP 400 — gateway likely rejects unknown fields; "
                 "retrying with standard fields only (prompt hints keep the parameters)" % label)
            payload = strip_extras(payload, extra_keys)
            extra_keys = ()
            stripped = True
            continue
        if status in RETRYABLE_HTTP and attempt < retries:
            wait_s = retry_wait * attempt
            warn("%s: HTTP %d after %.1fs (gateway timeout/overload) — retry %d/%d in %ds"
                 % (label, status, secs, attempt, retries - 1, wait_s))
            time.sleep(wait_s)
            continue
        break
    if isinstance(last_body, dict) and last_body.get("error"):
        msg = json.dumps(last_body["error"], ensure_ascii=False)
    else:
        msg = str(last_body)[:300]
    fail("%s: last HTTP %s — %s" % (label, last_status, msg))


def cmd_models(args):
    key = resolve_key(args)
    status, body, _ = http_request("GET", API_BASE + "/v1/models", key)
    if status != 200:
        fail("models: HTTP %s — %s" % (status, str(body)[:300]))
    for m in body.get("data", []):
        pricing = m.get("pricing", {})
        free = all(v in ("0", 0) for v in pricing.values())
        print("-" * 60)
        print("id:      %s" % m.get("id"))
        print("name:    %s" % m.get("name"))
        print("in/out:  %s -> %s" % (m.get("input_modalities"), m.get("output_modalities")))
        print("price:   prompt=%s completion=%s request=%s %s"
              % (pricing.get("prompt"), pricing.get("completion"),
                 pricing.get("request"), "[FREE]" if free else ""))
        print("desc:    %s" % m.get("description", ""))
    if args.json:
        print(json.dumps(body, ensure_ascii=False))


def track_value(track, default, *aliases):
    """Per-track JSON field (snake_case + camelCase aliases), fallback to CLI arg."""
    for name in aliases:
        if name in track and track[name] is not None:
            return track[name]
    return default


def cmd_gen(args):
    key = resolve_key(args)
    if args.duration and not (10 <= args.duration <= 600):
        warn("duration %ds is outside the documented 10-600s range; gateway may ignore it"
             % args.duration)
    if args.bpm and not (30 <= args.bpm <= 300):
        warn("bpm %d is outside the documented 30-300 range" % args.bpm)
    payload, used = build_payload(
        args.model, args.prompt, args.temperature,
        top_p=args.top_p, duration=args.duration, bpm=args.bpm,
        key_scale=args.keyscale, time_signature=args.time_signature,
        lyrics=args.lyrics, vocal_language=args.vocal_language,
        thinking=args.thinking, instrumental=args.instrumental, seed=args.seed)
    os.makedirs(args.out, exist_ok=True)
    body, attempt, secs = generate_with_retry(
        payload, key, retries=args.retries, retry_wait=args.retry_wait,
        label="gen", extra_keys=used)
    saved, caption = extract_audio(body, args.out, args.name or "track")
    if not saved:
        fail("gen: HTTP 200 but no audio in response. content=%s"
             % str(caption)[:300])
    ts = datetime.now().strftime("%H%M%S")
    paths = []
    for p in saved:
        # re-name with timestamp suffix to avoid collisions in out dir
        base, ext = os.path.splitext(p)
        final = os.path.join(args.out, "%s_%s%s" % (base, ts, ext))
        os.rename(p, final)
        paths.append(final)
        size = os.path.getsize(final)
        print("saved: %s (%.2f MB, ~%.1fs at 128kbps, attempt %d, %.1fs wall)"
              % (final, size / 1048576.0, size * 8.0 / 128000.0, attempt, secs))
    if caption:
        print("caption: %s" % str(caption)[:300])
    if args.json:
        print(json.dumps({"paths": paths, "attempts": attempt, "secs": round(secs, 1)},
                         ensure_ascii=False))


def cmd_batch(args):
    with open(args.scenes, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    tracks = doc.get("tracks") if isinstance(doc, dict) else doc
    if not isinstance(tracks, list) or not tracks:
        fail("batch: %s must contain a non-empty 'tracks' list" % args.scenes)
    out_dir = args.out or (doc.get("out_dir") if isinstance(doc, dict) else None) or "acemusic_out"
    results = []
    for i, tr in enumerate(tracks, 1):
        name = tr.get("name") or ("track_%02d" % i)
        prompt = (tr.get("prompt") or "").strip()
        if not prompt:
            warn("scene %d has no prompt, skipped" % i)
            results.append({"name": name, "ok": False, "reason": "no prompt"})
            continue
        print("== [%d/%d] %s ==" % (i, len(tracks), name))
        # per-track JSON fields (snake_case + camelCase aliases) override CLI args
        kwargs = dict(
            top_p=track_value(tr, args.top_p, "top_p", "topP"),
            duration=track_value(tr, args.duration, "duration", "audio_duration", "audioDuration"),
            bpm=track_value(tr, args.bpm, "bpm"),
            key_scale=track_value(tr, args.keyscale, "key_scale", "keyscale", "keyScale"),
            time_signature=track_value(tr, args.time_signature,
                                       "time_signature", "timesignature", "timeSignature"),
            lyrics=track_value(tr, args.lyrics, "lyrics"),
            vocal_language=track_value(tr, args.vocal_language, "vocal_language", "vocalLanguage"),
            thinking=track_value(tr, args.thinking, "thinking"),
            instrumental=track_value(tr, args.instrumental, "instrumental"),
            seed=track_value(tr, args.seed, "seed"),
        )
        sub_ns = argparse.Namespace(
            key=args.key, model=tr.get("model") or args.model,
            temperature=tr.get("temperature", args.temperature),
            out=out_dir, name=name, prompt=prompt,
            retries=tr.get("retries", args.retries),
            retry_wait=args.retry_wait, json=False, **kwargs)
        try:
            key = resolve_key(sub_ns)
            payload, used = build_payload(
                sub_ns.model, prompt, sub_ns.temperature,
                top_p=sub_ns.top_p, duration=sub_ns.duration, bpm=sub_ns.bpm,
                key_scale=sub_ns.key_scale, time_signature=sub_ns.time_signature,
                lyrics=sub_ns.lyrics, vocal_language=sub_ns.vocal_language,
                thinking=bool(sub_ns.thinking), instrumental=bool(sub_ns.instrumental),
                seed=sub_ns.seed)
            os.makedirs(out_dir, exist_ok=True)
            body, attempt, secs = generate_with_retry(
                payload, key, retries=sub_ns.retries, retry_wait=sub_ns.retry_wait,
                label=name, extra_keys=used)
            saved, caption = extract_audio(body, out_dir, name)
            if not saved:
                results.append({"name": name, "ok": False, "reason": "no audio in 200 response"})
                continue
            results.append({"name": name, "ok": True, "path": saved[0],
                            "secs": round(secs, 1), "attempts": attempt,
                            "caption": str(caption or "")[:200]})
        except SystemExit as e:
            results.append({"name": name, "ok": False, "reason": str(e.code or e)})
    ok = sum(1 for r in results if r.get("ok"))
    print("\nbatch done: %d/%d ok" % (ok, len(results)))
    print(json.dumps(results, ensure_ascii=False, indent=1))
    if args.json:
        print(json.dumps(results, ensure_ascii=False))


MUSIC_OPT_HELP = "optional music parameters (cloud API, see references/api-notes.md)"


def add_music_opt(p, help_base=MUSIC_OPT_HELP):
    p.add_argument("--top-p", type=float, default=None,
                   help=help_base + ": top-p sampling (documented supported_sampling_parameter)")
    p.add_argument("--duration", type=int, default=None,
                   help=help_base + ": target duration in seconds (documented 10-600)")
    p.add_argument("--bpm", type=int, default=None,
                   help=help_base + ": tempo in BPM (documented 30-300)")
    p.add_argument("--keyscale", default=None,
                   help=help_base + ": key/scale, e.g. 'C Major', 'Am' (note: --key is the API key)")
    p.add_argument("--time-signature", default=None,
                   help=help_base + ": time signature code 2/3/4/6 → 2/4, 3/4, 4/4, 6/8")
    p.add_argument("--lyrics", default=None,
                   help=help_base + ": lyrics text for vocal tracks")
    p.add_argument("--vocal-language", default=None,
                   help=help_base + ": vocal language code, e.g. zh/en/ja (default en)")
    p.add_argument("--thinking", action="store_true",
                   help=help_base + ": enable LM planning mode (quality boost)")
    p.add_argument("--instrumental", action="store_true",
                   help=help_base + ": force pure instrumental (no vocals)")
    p.add_argument("--seed", type=int, default=None,
                   help=help_base + ": fixed seed for reproducibility")


def main():
    ap = argparse.ArgumentParser(description="ACEMusic free text-to-music CLI (ACE-Step v1.5 turbo)")
    ap.add_argument("--key", help="API key (default: $ACEMUSIC_API_KEY, ./.acemusic_key, ~/.acemusic_key)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="model id")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON at the end")
    sub = ap.add_subparsers(dest="cmd")

    p_models = sub.add_parser("models", help="list available models and pricing")
    p_models.add_argument("--json", action="store_true")

    p_gen = sub.add_parser("gen", help="generate one track")
    p_gen.add_argument("prompt", help="text description, e.g. '15秒 动漫风BGM，国风电子，120BPM'")
    p_gen.add_argument("--name", help="output file name base")
    p_gen.add_argument("--out", default="acemusic_out", help="output directory")
    p_gen.add_argument("--retries", type=int, default=3,
                       help="attempts including the first (5xx/504 are retried)")
    p_gen.add_argument("--retry-wait", type=int, default=10, help="base backoff seconds")
    p_gen.add_argument("--temperature", type=float, default=0.9)
    p_gen.add_argument("--json", action="store_true")
    add_music_opt(p_gen)

    p_batch = sub.add_parser("batch", help="generate from a scene table (JSON)")
    p_batch.add_argument("scenes", help="path to scene table json")
    p_batch.add_argument("--out", help="output directory (overrides scene file out_dir)")
    p_batch.add_argument("--retries", type=int, default=3)
    p_batch.add_argument("--retry-wait", type=int, default=10)
    p_batch.add_argument("--temperature", type=float, default=0.9)
    p_batch.add_argument("--json", action="store_true")
    add_music_opt(p_batch, help_base=MUSIC_OPT_HELP + " (batch: per-track default; "
                     "each track object may override: top_p/duration/bpm/key_scale/"
                     "time_signature/lyrics/vocal_language/thinking/instrumental/seed)")

    args = ap.parse_args()
    if args.cmd == "models":
        cmd_models(args)
    elif args.cmd == "gen":
        cmd_gen(args)
    elif args.cmd == "batch":
        cmd_batch(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
