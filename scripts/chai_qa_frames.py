#!/usr/bin/env python3
"""柴主编 QA 锚点帧提取（自研实现）。

从成片按全局字幕时间轴（caption_groups.json）提取待检帧：
- 每条字幕组 2 帧：组起始 +0.2s、组中点；
- 含数字的字幕组：数字词起始 +0.15s 的「数字卡点帧」；
- 每个 scene（frame 字段）首条字幕组：3 连拍（t、t+1/fps、t+2/fps），
  专抓单帧看不见的短命错位与抖动。

方法学参考 video-talkcraft 的「锚点帧连拍验收」思想；代码为本仓库独立实现，
未复制第三方代码（其 PolyForm Noncommercial 许可不适用本文件）。

用法：
  python3 scripts/chai_qa_frames.py 成片.mp4 caption_groups.json \
      --out qa-frames [--fps 30]

输出：
  qa-frames/frame-NNN-t<秒>.jpg
  qa-frames/index.json          每帧的用途、时间、字幕文本
  qa-frames/contact-sheet.html  零依赖网格总览页（浏览器打开）
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FALLBACK_BIN = ROOT / "videos" / "qwen38-max-explained" / "bin"


def resolve(name: str) -> str:
    """PATH 优先；找不到时兜底到模板项目的已验证 ffmpeg/ffprobe 包装器。"""
    found = shutil.which(name)
    if found:
        return found
    cand = FALLBACK_BIN / name
    if cand.is_file() and (cand.stat().st_mode & 0o111):
        return str(cand)
    return name


DIGIT_RE = re.compile(r"[0-9０-９]|[零一二两三四五六七八九十百千万亿]+(?:\.[0-9]+)?")


def ffprobe_duration(path: Path) -> float:
    cmd = [resolve("ffprobe"), "-v", "error", "-show_entries", "format=duration",
           "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        duration = float(json.loads(out).get("format", {}).get("duration", 0))
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError(f"ffprobe 读取时长失败（检查 ffmpeg/ffprobe 包装器）: {out[:120]}")
    if duration <= 0:
        raise RuntimeError("ffprobe 返回时长 ≤ 0，成片文件可能不可读")
    return duration


def extract_frame(mp4: Path, t: float, out: Path) -> bool:
    cmd = [resolve("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", "-q:v", "2",
           str(out)]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="柴主编 QA 锚点帧提取")
    parser.add_argument("mp4", type=Path)
    parser.add_argument("timeline", type=Path, help="caption_groups.json（全局时间）")
    parser.add_argument("--out", type=Path, default=Path("qa-frames"))
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args()

    if not args.mp4.is_file() or not args.timeline.is_file():
        print("FAIL: mp4 或 timeline 文件不存在")
        return 1
    # 渲染包装器会 cd 进 compositor 目录，必须全程使用绝对路径
    args.mp4 = args.mp4.resolve()
    args.timeline = args.timeline.resolve()
    args.out = args.out.resolve()

    data = json.loads(args.timeline.read_text("utf-8"))
    groups = data.get("groups") or []
    if not groups:
        print("FAIL: timeline 无 groups")
        return 1
    duration = ffprobe_duration(args.mp4)

    picks: list[dict] = []  # {t, kind, frame, group, text}

    def add(t: float, kind: str, g: dict) -> None:
        t = max(0.05, min(t, duration - 0.10))
        if all(abs(t - p["t"]) > 0.08 for p in picks):
            picks.append({"t": round(t, 3), "kind": kind,
                          "frame": g.get("frame"), "group": g.get("id"),
                          "text": g.get("text", "")})

    seen_scene_first: set = set()
    for g in groups:
        start, end = float(g.get("start", 0)), float(g.get("end", 0))
        if end <= start:
            continue
        add(start + 0.20, "group-start", g)
        add((start + end) / 2, "group-mid", g)
        # 数字卡点：本组第一个含数字的词
        for w in g.get("words") or []:
            if DIGIT_RE.search(w.get("text", "")):
                add(float(w["start"]) + 0.15, "accent-number", g)
                break
        # scene 起始连拍
        scene = g.get("frame")
        if scene not in seen_scene_first:
            seen_scene_first.add(scene)
            base = start + 0.20
            for k in range(3):
                add(base + k / args.fps, f"burst-scene-{k + 1}", g)

    picks.sort(key=lambda p: p["t"])
    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.jpg"):
        old.unlink()

    index = []
    for i, p in enumerate(picks, 1):
        fname = f"frame-{i:03d}-t{p['t']:.2f}s.jpg"
        ok = extract_frame(args.mp4, p["t"], args.out / fname)
        index.append({"file": fname, "t_s": p["t"], "kind": p["kind"],
                      "scene_frame": p["frame"], "group": p["group"],
                      "caption": p["text"], "extracted": ok})

    (args.out / "index.json").write_text(
        json.dumps({"video": str(args.mp4.resolve()), "duration_s": duration,
                    "count": len(index), "frames": index},
                   ensure_ascii=False, indent=2), "utf-8")

    bad = [f for f in index if not f["extracted"]]

    # HTML contact sheet（零依赖）：评审人/浏览器打开即总览
    cells = "".join(
        f"<figure><img src='{f['file']}' loading='lazy'><figcaption>"
        f"#{i} t={f['t_s']}s · {f['kind']} · scene {f['scene_frame']}"
        f"<br>{f['caption']}</figcaption></figure>"
        for i, f in enumerate(index, 1))
    (args.out / "contact-sheet.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>QA 锚点帧</title>"
        "<style>body{background:#111;color:#eee;font:12px/1.4 sans-serif}"
        "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}"
        "figure{margin:0}img{width:100%;border:1px solid #333}"
        "figcaption{color:#9ab;margin-top:2px}</style>"
        f"<h1>{len(index)} 帧 · {args.mp4.name}</h1><main>{cells}</main>", "utf-8")

    print(f"锚点帧 {len(index)} 张（数字卡点 "
          f"{sum(1 for f in index if f['kind'] == 'accent-number')}，"
          f"连拍组 {len(seen_scene_first)} 个 scene），提取失败 {len(bad)}")
    print(f"输出：{args.out.resolve()}/index.json + contact-sheet.html")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
