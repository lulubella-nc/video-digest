#!/usr/bin/env python3
"""One-shot environment setup for video-digest. Idempotent: safe to run every time.

Checks (and installs only what's missing):
  1. Python packages: yt-dlp, requests, faster-whisper, soundfile
  2. System ffmpeg
  3. Whisper model cache -> persisted under HF_HOME (default ~/.cache/huggingface)

Usage:
    python3 scripts/setup_env.py [--model small]

Run this once when the skill is first used (or whenever fetch/transcribe scripts
fail with ModuleNotFoundError). Everything that already exists is skipped.
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys

PACKAGES = {
    "yt_dlp": "yt-dlp",
    "requests": "requests",
    "faster_whisper": "faster-whisper",
    "soundfile": "soundfile",
}


def ensure_packages():
    missing = [pkg for mod, pkg in PACKAGES.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        print("python packages: all present, skip")
        return
    print(f"installing: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])
    print("python packages: installed")


def ensure_ffmpeg():
    if shutil.which("ffmpeg"):
        print("ffmpeg: present, skip")
    else:
        print("WARNING: ffmpeg not found in PATH. Install it via your OS package manager "
              "(apt/brew/brew). Audio download & ASR will fail without it.", file=sys.stderr)


def ensure_model(model_size):
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("model cache: huggingface_hub unavailable, will download on first use")
        return
    repo = f"Systran/faster-whisper-{model_size}"
    try:
        path = snapshot_download(repo)
        print(f"model cache: {repo} ready at {path}")
    except Exception as e:
        print(f"model cache: download failed ({e}); transcribe will retry with "
              f"HF_ENDPOINT mirror on first use", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Idempotent env setup for video-digest.")
    ap.add_argument("--model", default="small", help="whisper model to pre-download")
    args = ap.parse_args()
    ensure_packages()
    ensure_ffmpeg()
    ensure_model(args.model)
    print("setup done")


if __name__ == "__main__":
    main()
