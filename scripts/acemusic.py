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

Usage:
  python acemusic.py models
  python acemusic.py gen "15秒 动漫风BGM，国风电子，120BPM" --name opener
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


def generate_once(payload, key):
    """One POST to the chat/completions endpoint. Returns (status, body, secs)."""
    started = time.time()
    status, body, _ct = http_request("POST", API_BASE + "/v1/chat/completions", key, payload)
    return status, body, time.time() - started


def generate_with_retry(payload, key, retries, retry_wait, label="track"):
    """POST with retries on gateway 5xx (the 60s Cloudflare timeout is 504)."""
    last_status, last_body = None, None
    for attempt in range(1, retries + 1):
        status, body, secs = generate_once(payload, key)
        last_status, last_body = status, body
        if status == 200:
            return body, attempt, secs
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


def cmd_gen(args):
    key = resolve_key(args)
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "temperature": args.temperature,
    }
    os.makedirs(args.out, exist_ok=True)
    body, attempt, secs = generate_with_retry(
        payload, key, retries=args.retries, retry_wait=args.retry_wait,
        label="gen")
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
        sub = argparse.Namespace(
            key=args.key, model=tr.get("model") or args.model,
            temperature=tr.get("temperature", args.temperature),
            out=out_dir, name=name, prompt=prompt,
            retries=tr.get("retries", args.retries),
            retry_wait=args.retry_wait, json=False)
        try:
            key = resolve_key(sub)
            payload = {"model": sub.model,
                      "messages": [{"role": "user", "content": sub.prompt}],
                      "temperature": sub.temperature}
            os.makedirs(out_dir, exist_ok=True)
            body, attempt, secs = generate_with_retry(
                payload, key, retries=sub.retries, retry_wait=sub.retry_wait,
                label=name)
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

    p_batch = sub.add_parser("batch", help="generate from a scene table (JSON)")
    p_batch.add_argument("scenes", help="path to scene table json")
    p_batch.add_argument("--out", help="output directory (overrides scene file out_dir)")
    p_batch.add_argument("--retries", type=int, default=3)
    p_batch.add_argument("--retry-wait", type=int, default=10)
    p_batch.add_argument("--temperature", type=float, default=0.9)
    p_batch.add_argument("--json", action="store_true")

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
