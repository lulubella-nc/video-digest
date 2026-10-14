#!/usr/bin/env python3
"""ASR fallback: download a video's audio and transcribe it with faster-whisper.

Use when fetch_transcript.py exits with code 3 (no subtitles available).

Usage:
    python3 scripts/transcribe_audio.py <video_url> [--out transcript.json] [--model small] [--lang zh]

Output: same normalized JSON schema as fetch_transcript.py, with "source": "asr".
Chapters are not recoverable from audio -> "chapters" is always [].

Model size trade-off (CPU, int8):
    tiny  ~ fastest, rough       base ~ ok for clear speech
    small ~ good default         medium/large-v3 ~ best, needs RAM/GPU

Dependencies: yt-dlp, faster-whisper, ffmpeg.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def download_bilibili_audio(url, workdir):
    """Fetch Bilibili audio via the playurl API (yt-dlp often gets HTTP 412 without cookies).

    Returns (wav_path, info_dict). Respects BILIBILI_SESSDATA when set.
    """
    import re
    import requests

    m = re.search(r"(BV[0-9A-Za-z]{10})", url) or re.search(r"av(\d+)", url)
    if not m:
        raise RuntimeError(f"cannot parse bilibili id from {url}")
    vid = m.group(1) if m.group(0).startswith("BV") else None
    cookie = "buvid3=video-digest"
    sess = os.environ.get("BILIBILI_SESSDATA", "").strip()
    if sess:
        cookie += f"; SESSDATA={sess}"
    headers = {"User-Agent": BROWSER_UA, "Referer": "https://www.bilibili.com/", "Cookie": cookie}

    q = {"bvid": vid} if vid else {"aid": m.group(1)}
    view = requests.get("https://api.bilibili.com/x/web-interface/view",
                        params=q, headers=headers, timeout=30).json()
    if view.get("code") != 0:
        raise RuntimeError(f"view API error: {view}")
    v = view["data"]
    page_url = f"https://www.bilibili.com/video/{v['bvid']}"
    headers["Referer"] = page_url

    play = requests.get("https://api.bilibili.com/x/player/playurl",
                        params={"bvid": v["bvid"], "cid": v["cid"], "fnval": 16, "fourk": 1},
                        headers=headers, timeout=30).json()
    dash = ((play.get("data") or {}).get("dash") or {})
    audios = dash.get("audio") or []
    if not audios:
        raise RuntimeError("no audio stream in playurl response (video may require login)")
    audio_url = sorted(audios, key=lambda a: a.get("bandwidth", 0))[-1]["baseUrl"]

    raw = os.path.join(workdir, "audio.m4s")
    with requests.get(audio_url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(raw, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    wav = os.path.join(workdir, "audio.wav")
    subprocess.run(["ffmpeg", "-y", "-i", raw, "-ac", "1", "-ar", "16000", wav],
                   check=True, capture_output=True)
    info = {"id": v["bvid"], "title": v["title"], "duration": v["duration"],
            "uploader": (v.get("owner") or {}).get("name"), "webpage_url": page_url,
            "extractor": "BiliBili"}
    return wav, info


def download_audio(url, workdir):
    """Download audio track. Bilibili goes through the playurl API; everything else via yt-dlp.
    Returns (wav_path, info)."""
    if "bilibili.com" in url or "b23.tv" in url:
        try:
            return download_bilibili_audio(url, workdir)
        except Exception as e:
            print(f"bilibili API audio failed ({e}), falling back to yt-dlp", file=sys.stderr)

    import yt_dlp

    outtmpl = os.path.join(workdir, "audio.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "http_headers": {"User-Agent": BROWSER_UA},
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "0",
        }],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
    }
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    wav = os.path.join(workdir, "audio.wav")
    if not os.path.exists(wav):
        candidates = [os.path.join(workdir, f) for f in os.listdir(workdir) if f.startswith("audio.")]
        if not candidates:
            raise RuntimeError("audio download failed")
        wav = candidates[0]
    return wav, info


def transcribe(wav_path, model_size, lang):
    # default to the HF mirror when huggingface.co is unreachable; no-op if already set
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from faster_whisper import WhisperModel

    # decode the wav ourselves (16 kHz mono) and pass a numpy array:
    # avoids faster-whisper/av version incompatibilities on some environments
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(wav_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        raise RuntimeError(f"expected 16 kHz wav, got {sr} Hz")

    model = WhisperModel(model_size, device="auto", compute_type="int8",
                         cpu_threads=os.cpu_count() or 4, num_workers=1)
    segments_iter, info = model.transcribe(
        np.asarray(audio, dtype="float32"),
        language=lang or None,
        vad_filter=True,
        beam_size=5,
    )
    segments = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in segments_iter if s.text.strip()
    ]
    return segments, info.language


def main():
    ap = argparse.ArgumentParser(description="Download audio and transcribe with faster-whisper.")
    ap.add_argument("url")
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default="small", help="whisper model size: tiny/base/small/medium/large-v3")
    ap.add_argument("--lang", default=None, help="force language code, e.g. zh / en (default: auto-detect)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as workdir:
        print("Downloading audio...", file=sys.stderr)
        wav, info = download_audio(args.url, workdir)
        print(f"Transcribing with faster-whisper [{args.model}]...", file=sys.stderr)
        segments, lang = transcribe(wav, args.model, args.lang)

    if not segments:
        print("ERROR: transcription produced no segments", file=sys.stderr)
        sys.exit(4)

    is_bili = "bilibili" in (info.get("webpage_url") or "") or (info.get("extractor") or "").startswith("BiliBili")
    result = {
        "platform": "bilibili" if is_bili else "youtube",
        "url": info.get("webpage_url") or args.url,
        "video_id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "source": "asr",
        "language": lang,
        "chapters": [],
        "segments": segments,
    }
    out = args.out or f"{result['video_id']}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"OK asr | {result['title']} | {result['duration']}s | {len(segments)} segments | "
          f"lang={lang} -> {out}")


if __name__ == "__main__":
    main()
