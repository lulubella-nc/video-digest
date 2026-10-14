---
name: video-digest
description: 视频内容摘要与双格式可视化交付。当用户提供 YouTube 或 Bilibili（B站）视频链接（含英文播客、访谈、课程等长视频），希望不全程观看就掌握内容时使用：自动抓取字幕（无字幕时语音转写兜底），梳理章节结构与章节内重要观点，提炼长视频主要内容，英文等非中文内容自动输出全中文摘要，最终生成两种交付物——单文件信息图风格 HTML 速览页（杂志暖色、可点击时间戳跳转原视频）和可直接粘贴进公众号编辑器的内联样式 HTML 片段。触发场景：视频总结、视频摘要、视频笔记、章节梳理、懒得看完、帮我看看这个视频、提炼视频观点、英文播客转中文，以及任何 youtube.com / youtu.be / bilibili.com / b23.tv / BV号 链接的内容整理请求。
---

# video-digest

把 YouTube / B站视频变成"不用看也能掌握"的两种阅读材料：信息图 HTML 速览页 + 公众号排版片段。

## 流水线总览

```
视频链接 → ① 获取文稿 → ② 预处理 → ③ 提炼 digest.json → ④ 渲染 → ⑤ 交付
           字幕优先      合并分段      AI 章节+观点        双格式模板
           ASR 兜底
```

工作目录：为每个任务建一个独立目录（如 `work/<视频名>/`），中间产物（transcript.json、digest.json）与最终产物都放里面。

## ⓪ 环境准备（幂等，缺什么装什么）

技能首次使用时运行一次：

```bash
python3 scripts/setup_env.py
```

脚本自动检测并只安装缺失项：Python 包（yt-dlp / requests / faster-whisper / soundfile）、ffmpeg 检查、Whisper 模型预下载。已存在的全部跳过，**不会重复安装**。

后续使用无需再跑；仅当脚本报 `ModuleNotFoundError` / 模型下载失败时再运行一次即可。沙箱类环境每次会话会重置系统环境，报缺依赖属正常现象，重跑 setup_env.py 即可恢复。

## ① 获取文稿

```bash
python3 scripts/fetch_transcript.py "<视频URL>" --out work/transcript.json
```

- 支持 youtube.com / youtu.be / bilibili.com / b23.tv / 裸 BV号；播客类内容（音频节目、访谈长视频）走同一条链路，内容形态不影响流程。
- 退出码 `3` = 无字幕，立即走 ASR 兜底：
  ```bash
  python3 scripts/transcribe_audio.py "<视频URL>" --out work/transcript.json --model small
  ```
- 脚本输出统一为 transcript JSON：`{platform, url, video_id, title, uploader, duration, source, chapters[], segments[{start,end,text}]}`。

**环境要点**

- 脚本依赖：`yt-dlp`、`requests`、`faster-whisper`、`soundfile`，以及系统级 `ffmpeg`（由 ⓪ 的 setup_env.py 统一检查安装）。
- YouTube 字幕：yt-dlp 自动按 `zh-Hans → zh → en` 优先级取人工字幕，其次自动字幕；`chapters` 取自视频自带章节。
- B站字幕：走 wbi 签名接口。未登录时大多数视频不返回 AI 字幕——提示用户设置 `BILIBILI_SESSDATA`（浏览器 Cookie 中的 SESSDATA 值）可解锁；否则直接 ASR。
- YouTube 网络不可达（连接超时）时：提示用户配置 `HTTPS_PROXY` 后重试，不要反复重试。
- ASR 已内置 HuggingFace 镜像兜底（脚本默认 `HF_ENDPOINT=https://hf-mirror.com` + `HF_HUB_DISABLE_XET=1`）；模型缓存在 `HF_HOME`（默认 `~/.cache/huggingface`），跨任务复用不重复下载。
- ASR 模型选择：`tiny/base` 快而粗，`small` 为默认平衡点，`medium/large-v3` 精度高但需大内存/GPU。CPU 上 `small` 转写耗时约为视频时长的 0.5–1 倍，超长视频（>1h）先告知用户预计耗时。
- 用户直接粘贴文稿/字幕文本时：跳过抓取，手工构造同 schema 的 transcript.json 后从 ② 继续。

## ② 预处理文稿

1. 合并碎句：把连续 segments 按语义拼成段落块（每块保留最早 start 作为时间锚点），ASR 文稿额外做去口头禅、补标点。
2. 章节划分：
   - `chapters` 非空（视频自带章节）→ 直接采用，按时间区间归并 segments。
   - 为空 → 按语义自动切分 5–10 章，每章约 2–8 分钟， chapter 标题概括该段主题。
3. 不要把原始文稿整段贴给用户；如需同步进度，只说章数与时长。

## ③ 提炼为 digest.json

通读文稿后产出 `work/digest.json`，schema（也是 render.py 的输入契约）：

```json
{
  "video": {"platform": "bilibili", "url": "...", "video_id": "...", "title": "...", "uploader": "...", "duration": 1047},
  "subtitle": "一句话副标题，点明视频主线（≤40字）",
  "tldr": "一句话总结全片核心结论（≤80字）",
  "overview": ["全片核心信息 3-5 条，每条 1-2 句，按重要性排序"],
  "keywords": ["4-8 个关键词"],
  "chapters": [
    {"title": "章节标题", "start": 0, "end": 210,
     "summary": "本章 2-3 句概要",
     "points": [{"ts": 15, "headline": "观点标题（≤20字）", "detail": "观点展开 1-2 句"}]}
  ]
}
```

**提炼规范**

- 忠实原文：只提炼视频中真实出现的观点与数据，禁止脑补、禁止引入外部知识；数字、人名、专有名词须与文稿一致。
- 每章 3–6 个 points；`ts` 取该观点在文稿中起始句的时间（秒），用于跳转链接。
- **输出语言默认全中文**：英文播客、英文长视频等一切非中文内容，摘要一律自动翻译提炼为中文（tldr / overview / 章节标题 / 观点 / 关键词全部中文）；首次出现的专有名词、人名、术语保留"中文（English）"格式，如"特里芬难题（Triffin Dilemma）"。仅当用户明确要求其他语言时才从其要求。
- `video` 字段直接沿用 transcript.json 的元数据（标题保留原语言）。

## ④ 渲染双格式产物

```bash
python3 scripts/render.py work/digest.json --html "<视频名>-速览.html" --wechat "<视频名>-公众号.html"
```

- `速览.html`：单文件自包含信息图页（杂志暖色，桌面/移动端自适应），时间戳均可点击跳转原视频对应位置。结构：TL;DR → 数据条 → 主要内容 → 章节时间轴。
- `公众号.html`：全内联样式片段。指导用户用法：浏览器打开后全选复制，粘贴进公众号后台编辑器（或 135 编辑器等）即可保留排版；公众号内不支持外链，故时间戳不可点击属正常现象。
- 模板在 `assets/` 下，用户要求换配色/风格时改模板后重新渲染即可。

## ⑤ 交付

- 同时交付两个 HTML 文件，并用 3–5 句话口头概括视频核心内容。
- 提醒：摘要为 AI 辅助整理，观点与事实以原视频为准（模板页脚已含此声明）。

## 异常处理

| 情况 | 处理 |
|---|---|
| YouTube 连接超时 | 提示配置 `HTTPS_PROXY`，不盲目重试 |
| B站接口 -101/-403/无字幕 | 建议设置 `BILIBILI_SESSDATA`，否则 ASR |
| 视频无可提取音频/ASR 失败 | 请用户粘贴文稿，手工构造 transcript.json 从 ② 继续 |
| 视频极长（>2h） | 先与用户确认范围（全片/指定时间段），ASR 换 `small` 以下模型 |
| 用户只要其中一种格式 | render.py 的 `--html` / `--wechat` 可只传一个 |

## 文件清单

- `scripts/setup_env.py` — 环境准备（幂等：缺什么装什么，首次使用跑一次）
- `scripts/fetch_transcript.py` — 字幕 + 章节抓取（YouTube yt-dlp / B站 wbi API）
- `scripts/transcribe_audio.py` — ASR 兜底（B站 playurl API / yt-dlp 拉音频 + faster-whisper）
- `scripts/render.py` — digest.json → 双格式 HTML 渲染
- `assets/digest-template.html` — 信息图页模板（杂志暖色）
- `assets/wechat-template.html` — 公众号内联样式片段模板
