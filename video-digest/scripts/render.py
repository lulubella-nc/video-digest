#!/usr/bin/env python3
"""Render digest.json into the two deliverables of video-digest:

    1. a self-contained infographic-style HTML page (magazine warm palette)
    2. an inline-styled HTML fragment ready to paste into WeChat Official Account editor

Usage:
    python3 scripts/render.py digest.json --html digest.html --wechat wechat.html

digest.json schema (produced by the agent after analyzing the transcript):
{
  "video":    {"platform": "youtube"|"bilibili", "url": "...", "video_id": "...",
               "title": "...", "uploader": "...", "duration": <seconds>},
  "tldr":     "一句话总结",
  "overview": ["核心信息1", "核心信息2", ...],          # 3-5 items
  "keywords": ["关键词", ...],                           # 4-8 items
  "chapters": [
    {"title": "章节标题", "start": <sec>, "end": <sec>,
     "summary": "本章 2-3 句概要",
     "points": [{"ts": <sec>, "headline": "观点标题", "detail": "观点展开描述"}]}
  ]
}
Templates are read from ../assets relative to this script.
"""
import argparse
import datetime
import html as html_mod
import json
import os
import sys

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def esc(s):
    return html_mod.escape(str(s), quote=True)


def fmt_ts(sec):
    sec = int(sec)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def ts_link(video, sec):
    url = video.get("url", "")
    vid = video.get("video_id", "")
    if video.get("platform") == "youtube":
        base = url if "watch" in url else f"https://www.youtube.com/watch?v={vid}"
        return f"{base}&t={int(sec)}s"
    if video.get("platform") == "bilibili":
        base = url or f"https://www.bilibili.com/video/{vid}"
        return f"{base}{'&' if '?' in base else '?'}t={int(sec)}"
    return url


# ------------------------------------------------- digest page fragments ----

def _reading_minutes(text):
    """Estimate reading time: ~400 CJK chars/min, ~200 Latin words/min."""
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    latin_words = len("".join(ch if ord(ch) < 0x2E80 else " " for ch in text).split())
    return cjk / 400 + latin_words / 200


def render_stats(video, chapters):
    dur = int(video.get("duration") or 0)
    text = "".join(p.get("detail", "") + p.get("headline", "")
                   for c in chapters for p in c.get("points", []))
    text += "".join(digest.get("overview") or []) + (digest.get("tldr") or "")
    read_min = max(1, round(_reading_minutes(text)))
    saved = max(0, round((1 - read_min * 60 / dur) * 100)) if dur else 0
    cells = [
        (fmt_ts(dur), "视频时长"),
        (str(len(chapters)), "章节数"),
        (f"≈{read_min} 分钟", "阅读用时"),
        (f"{saved}%", "时间节省"),
    ]
    return "\n    ".join(
        f'<div class="stat"><div class="num">{esc(n)}</div><div class="cap">{esc(c)}</div></div>'
        for n, c in cells)


def render_chapters_html(video, chapters):
    out = []
    for i, ch in enumerate(chapters, 1):
        points = "\n".join(
            f'''          <li>
            <a class="ts" href="{esc(ts_link(video, p.get("ts", ch.get("start", 0))))}" target="_blank" rel="noopener">▶ {fmt_ts(p.get("ts", 0))}</a>
            <div class="pt-body">
              <div class="pt-head">{esc(p.get("headline", ""))}</div>
              <div class="pt-detail">{esc(p.get("detail", ""))}</div>
            </div>
          </li>'''
            for p in ch.get("points", []))
        out.append(f'''      <div class="chapter">
        <div class="ch-card">
          <div class="ch-head">
            <span class="ch-no">{i:02d}</span>
            <span class="ch-title">{esc(ch.get("title", ""))}</span>
            <a class="ch-time" href="{esc(ts_link(video, ch.get("start", 0)))}" target="_blank" rel="noopener">{fmt_ts(ch.get("start", 0))} – {fmt_ts(ch.get("end", 0))}</a>
          </div>
          <div class="ch-summary">{esc(ch.get("summary", ""))}</div>
          <ul class="points">
{points}
          </ul>
        </div>
      </div>''')
    return "\n".join(out)


# ------------------------------------------------- wechat fragments ---------

def wx_overview(overview):
    lis = []
    for i, o in enumerate(overview, 1):
        lis.append(
            f'<section style="display:flex;margin:10px 0;padding:12px 14px;background:#fffdf7;'
            f'border:1px solid #ddd2bd;border-radius:10px;">'
            f'<span style="flex:0 0 auto;font-family:Georgia,serif;font-size:20px;color:#e8c9b8;'
            f'font-weight:bold;margin-right:10px;line-height:1.3;">{i:02d}</span>'
            f'<span style="flex:1;font-size:14.5px;line-height:1.8;">{esc(o)}</span></section>')
    return "\n    ".join(lis)


def wx_chapters(chapters):
    out = []
    for i, ch in enumerate(chapters, 1):
        pts = "".join(
            f'<section style="margin:8px 0;padding-left:12px;border-left:2px solid #e8c9b8;">'
            f'<section style="font-size:12px;color:#a8823c;">▶ {fmt_ts(p.get("ts", 0))}</section>'
            f'<section style="font-size:14.5px;font-weight:bold;line-height:1.7;">{esc(p.get("headline", ""))}</section>'
            f'<section style="font-size:13.5px;color:#5c5344;line-height:1.8;">{esc(p.get("detail", ""))}</section>'
            f'</section>'
            for p in ch.get("points", []))
        out.append(
            f'<section style="margin:16px 0;padding:16px 18px;background:#fffdf7;border:1px solid #ddd2bd;border-radius:12px;">'
            f'<section style="display:flex;align-items:baseline;margin-bottom:4px;">'
            f'<span style="font-family:Georgia,serif;font-size:26px;color:#bf4a1f;font-weight:bold;margin-right:8px;">{i:02d}</span>'
            f'<span style="font-family:Georgia,\'Songti SC\',serif;font-size:16.5px;font-weight:bold;flex:1;">{esc(ch.get("title", ""))}</span>'
            f'</section>'
            f'<section style="font-size:11.5px;color:#a8823c;margin-bottom:8px;">{fmt_ts(ch.get("start", 0))} – {fmt_ts(ch.get("end", 0))}</section>'
            f'<section style="font-size:13.5px;color:#5c5344;padding-bottom:10px;margin-bottom:6px;border-bottom:1px dashed #ddd2bd;">{esc(ch.get("summary", ""))}</section>'
            f'{pts}</section>')
    return "\n    ".join(out)


# ------------------------------------------------- main ----------------------

def fill(template, mapping):
    for k, v in mapping.items():
        template = template.replace("{{" + k + "}}", str(v))
    leftover = [t for t in template.split("{{")[1:] if "}}" in t]
    if leftover:
        print(f"WARNING: unfilled placeholders: {['{{' + t.split('}}')[0] + '}}' for t in leftover]}",
              file=sys.stderr)
    return template


def main():
    global digest
    ap = argparse.ArgumentParser(description="Render digest.json to HTML page + WeChat fragment.")
    ap.add_argument("digest", help="digest.json path")
    ap.add_argument("--html", default="digest.html")
    ap.add_argument("--wechat", default="wechat.html")
    args = ap.parse_args()

    with open(args.digest, encoding="utf-8") as f:
        digest = json.load(f)

    video = digest.get("video", {})
    chapters = digest.get("chapters", [])
    platform_label = {"youtube": "YouTube", "bilibili": "Bilibili · B站"}.get(
        video.get("platform"), video.get("platform", ""))
    duration_text = fmt_ts(video.get("duration") or 0)
    subtitle = digest.get("tldr", "")[:60]  # hero subtitle fallback
    gen_date = datetime.date.today().isoformat()

    with open(os.path.join(ASSETS, "digest-template.html"), encoding="utf-8") as f:
        page = f.read()
    page = fill(page, {
        "TITLE": esc(video.get("title", "未命名视频")),
        "SUBTITLE": esc(digest.get("subtitle", "")),
        "PLATFORM": esc(platform_label),
        "UPLOADER": esc(video.get("uploader", "未知")),
        "DURATION_TEXT": duration_text,
        "URL": esc(video.get("url", "#")),
        "TLDR": esc(digest.get("tldr", "")),
        "STATS_HTML": render_stats(video, chapters),
        "OVERVIEW_HTML": "\n      ".join(f"<li>{esc(o)}</li>" for o in digest.get("overview", [])),
        "KEYWORDS_HTML": "\n      ".join(f'<span class="kw">{esc(k)}</span>' for k in digest.get("keywords", [])),
        "CHAPTERS_HTML": render_chapters_html(video, chapters),
        "GEN_DATE": gen_date,
    })
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(page)

    with open(os.path.join(ASSETS, "wechat-template.html"), encoding="utf-8") as f:
        wx = f.read()
    wx = fill(wx, {
        "TITLE": esc(video.get("title", "未命名视频")),
        "PLATFORM": esc(platform_label),
        "UPLOADER": esc(video.get("uploader", "未知")),
        "DURATION_TEXT": duration_text,
        "TLDR": esc(digest.get("tldr", "")),
        "OVERVIEW_HTML": wx_overview(digest.get("overview", [])),
        "CHAPTERS_HTML": wx_chapters(chapters),
        "KEYWORDS_TEXT": esc(" / ".join(digest.get("keywords", []))),
    })
    with open(args.wechat, "w", encoding="utf-8") as f:
        f.write(wx)

    print(f"OK -> {args.html} , {args.wechat}")


if __name__ == "__main__":
    main()
