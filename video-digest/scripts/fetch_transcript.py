#!/usr/bin/env python3
"""Fetch subtitles/transcript and chapter metadata from a YouTube or Bilibili video.

Usage:
    python3 scripts/fetch_transcript.py <video_url> [--out transcript.json] [--langs zh-Hans,zh,en]

Output: normalized JSON (written to --out, default <video_id>.json):
{
  "platform": "youtube" | "bilibili",
  "url": ..., "video_id": ..., "title": ..., "uploader": ...,
  "duration": <seconds>, "source": "official_subtitle" | "auto_subtitle",
  "chapters": [{"title": ..., "start": <sec>, "end": <sec>}],   # [] if none
  "segments": [{"start": <sec>, "end": <sec>, "text": ...}]
}

Exit code 3 means: no subtitles available -> run scripts/transcribe_audio.py (ASR fallback).

Environment:
    BILIBILI_SESSDATA  Optional. Bilibili login cookie (SESSDATA). Without it,
                       Bilibili usually hides AI-generated subtitles.
    HTTP_PROXY/HTTPS_PROXY  Honored for YouTube access where it is geo-blocked.

Dependencies: yt-dlp (YouTube), requests (Bilibili).
"""
import argparse
import functools
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse

# ---------------------------------------------------------------- common ----

LANG_PRIORITY = ["zh-Hans", "zh-Hant", "zh", "zh-CN", "en", "en-US", "en-GB"]


def pick_langs(cli_langs):
    return [l.strip() for l in cli_langs.split(",") if l.strip()] if cli_langs else LANG_PRIORITY


def ordered_keys(d, priority):
    """Keys of dict d ordered by language priority, then the rest."""
    keys = list(d.keys())
    out = []
    for want in priority:
        for k in keys:
            if k == want or k.startswith(want + "-"):
                if k not in out:
                    out.append(k)
    out += [k for k in keys if k not in out]
    return out


def detect_platform(url):
    if re.search(r"(youtube\.com|youtu\.be)", url):
        return "youtube"
    if re.search(r"(bilibili\.com|b23\.tv|^BV[0-9A-Za-z]{10}$|^av\d+$)", url):
        return "bilibili"
    return None


# ---------------------------------------------------------------- YouTube ---

def fetch_youtube(url, langs):
    import yt_dlp  # noqa: requires yt-dlp

    opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False)

    tracks, source = None, None
    for table, src in (("subtitles", "official_subtitle"),
                       ("automatic_captions", "auto_subtitle")):
        available = info.get(table) or {}
        for key in ordered_keys(available, langs):
            fmts = available[key]
            vtt = next((f for f in fmts if f.get("ext") == "vtt"), fmts[0] if fmts else None)
            if vtt:
                tracks, source = vtt["url"], src
                break
        if tracks:
            break

    if not tracks:
        return {"_no_subs": True, "title": info.get("title"), "duration": info.get("duration")}

    import urllib.request
    with urllib.request.urlopen(tracks, timeout=60) as r:
        vtt_text = r.read().decode("utf-8", "replace")

    segments = parse_vtt(vtt_text)
    chapters = [
        {"title": c.get("title", ""), "start": c.get("start_time", 0), "end": c.get("end_time", 0)}
        for c in (info.get("chapters") or [])
    ]
    return {
        "platform": "youtube",
        "url": url,
        "video_id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "source": source,
        "chapters": chapters,
        "segments": segments,
    }


def parse_vtt(vtt_text):
    """Minimal WebVTT parser -> [{start, end, text}]. Strips tags and duplicate rolling lines."""
    segments = []
    blocks = re.split(r"\n\s*\n", vtt_text)
    seen = set()
    for b in blocks:
        lines = [l.strip() for l in b.strip().splitlines() if l.strip()]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        m = re.match(r"([\d:.]+)\s*-->\s*([\d:.]+)", lines[ti])
        if not m:
            continue
        start, end = ts_to_sec(m.group(1)), ts_to_sec(m.group(2))
        text = " ".join(lines[ti + 1:])
        text = re.sub(r"<[^>]+>", "", text).strip()
        if not text:
            continue
        # auto-captions repeat the previous line while rolling; drop consecutive dupes
        if segments and (text == segments[-1]["text"] or text in segments[-1]["text"]):
            continue
        if text in seen and segments and start - segments[-1]["end"] < 1:
            continue
        seen.add(text)
        segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    return segments


def ts_to_sec(ts):
    parts = ts.split(":")
    parts = [float(p) for p in parts]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


# ---------------------------------------------------------------- Bilibili --

MIXIN_KEY_ENC_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
                     27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
                     37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
                     22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]

_BILIBILI_HEADERS = None
_WBI_KEYS = {"img_key": None, "sub_key": None, "ts": 0}


def _bili_headers(referer="https://www.bilibili.com"):
    global _BILIBILI_HEADERS
    if _BILIBILI_HEADERS is None:
        cookie = "buvid3=video-digest"
        sess = os.environ.get("BILIBILI_SESSDATA", "").strip()
        if sess:
            cookie += f"; SESSDATA={sess}"
        _BILIBILI_HEADERS = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": referer,
            "Cookie": cookie,
        }
    h = dict(_BILIBILI_HEADERS)
    h["Referer"] = referer
    return h


def _bili_get(url, referer="https://www.bilibili.com"):
    import requests
    r = requests.get(url, headers=_bili_headers(referer), timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Bilibili API error {data.get('code')}: {data.get('message')} ({url})")
    return data["data"]


def _wbi_keys():
    if not _WBI_KEYS["img_key"] or time.time() - _WBI_KEYS["ts"] > 3600:
        # nav returns code -101 when logged out, but wbi_img is still present
        import requests
        r = requests.get("https://api.bilibili.com/x/web-interface/nav",
                         headers=_bili_headers(), timeout=30)
        nav = r.json()["data"]
        img = nav["wbi_img"]
        _WBI_KEYS["img_key"] = img["img_url"].rsplit("/", 1)[1].split(".")[0]
        _WBI_KEYS["sub_key"] = img["sub_url"].rsplit("/", 1)[1].split(".")[0]
        _WBI_KEYS["ts"] = time.time()
    return _WBI_KEYS["img_key"], _WBI_KEYS["sub_key"]


def _wbi_sign(params):
    img_key, sub_key = _wbi_keys()
    raw = img_key + sub_key
    mixin = functools.reduce(lambda s, i: s + raw[i], MIXIN_KEY_ENC_TAB, "")[:32]
    params = dict(params)
    params["wts"] = int(time.time())
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(params.items())}
    params["w_rid"] = hashlib.md5((urllib.parse.urlencode(params) + mixin).encode()).hexdigest()
    return params


def _parse_bvid(url):
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m:
        return m.group(1)
    m = re.search(r"av(\d+)", url)
    if m:
        return "av" + m.group(1)
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", url.strip()):
        return url.strip()
    raise ValueError(f"Cannot parse Bilibili video id from: {url}")


def fetch_bilibili(url, langs):
    vid = _parse_bvid(url)
    q = {"bvid": vid} if vid.startswith("BV") else {"aid": vid[2:]}
    view = _bili_get("https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(q))
    aid, cid, bvid = view["aid"], view["cid"], view["bvid"]
    page_url = f"https://www.bilibili.com/video/{bvid}"

    params = _wbi_sign({"aid": aid, "cid": cid, "bvid": bvid})
    player = _bili_get("https://api.bilibili.com/x/player/wbi/v2?" + urllib.parse.urlencode(params),
                       referer=page_url)

    # chapters: view_points type 2 are uploader-defined sections
    view_points = player.get("view_points") or []
    chapter_points = [p for p in view_points if p.get("type") == 2] or view_points
    chapters = []
    for i, p in enumerate(chapter_points):
        start = p.get("from", 0)
        end = chapter_points[i + 1].get("from", view["duration"]) if i + 1 < len(chapter_points) else view["duration"]
        chapters.append({"title": p.get("content", ""), "start": start, "end": end})

    subs = ((player.get("subtitle") or {}).get("subtitles")) or []
    if not subs:
        return {"_no_subs": True, "title": view["title"], "duration": view["duration"],
                "_hint": "Set BILIBILI_SESSDATA env var (login cookie) to unlock AI subtitles, "
                         "or fall back to transcribe_audio.py."}

    # order subtitle tracks by language priority
    subs_sorted = sorted(
        subs,
        key=lambda s: next((i for i, l in enumerate(langs) if s.get("lan", "").startswith(l)), 99),
    )
    track = subs_sorted[0]
    su = track["subtitle_url"]
    if su.startswith("//"):
        su = "https:" + su
    body = _bili_get(su, referer=page_url)
    segments = [
        {"start": round(x["from"], 2), "end": round(x["to"], 2), "text": x["content"].strip()}
        for x in body.get("body", []) if x.get("content", "").strip()
    ]
    return {
        "platform": "bilibili",
        "url": page_url,
        "video_id": bvid,
        "title": view["title"],
        "uploader": (view.get("owner") or {}).get("name"),
        "duration": view["duration"],
        "source": "official_subtitle" if not track.get("ai_status") else "auto_subtitle",
        "chapters": chapters,
        "segments": segments,
    }


# ---------------------------------------------------------------- main ------

def main():
    ap = argparse.ArgumentParser(description="Fetch subtitles + chapters from YouTube/Bilibili.")
    ap.add_argument("url")
    ap.add_argument("--out", default=None)
    ap.add_argument("--langs", default=None, help="comma-separated language priority, e.g. zh-Hans,zh,en")
    args = ap.parse_args()

    langs = pick_langs(args.langs)
    platform = detect_platform(args.url)
    if platform == "youtube":
        result = fetch_youtube(args.url, langs)
    elif platform == "bilibili":
        result = fetch_bilibili(args.url, langs)
    else:
        print(f"ERROR: unsupported URL (only YouTube / Bilibili): {args.url}", file=sys.stderr)
        sys.exit(2)

    if result.get("_no_subs"):
        print(f"NO_SUBTITLES: {result.get('title')} ({result.get('duration')}s)", file=sys.stderr)
        if result.get("_hint"):
            print("HINT: " + result["_hint"], file=sys.stderr)
        sys.exit(3)

    out = args.out or f"{result['video_id']}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"OK {result['platform']} | {result['title']} | {result['duration']}s | "
          f"{len(result['segments'])} segments | {len(result['chapters'])} chapters | "
          f"src={result['source']} -> {out}")


if __name__ == "__main__":
    main()
