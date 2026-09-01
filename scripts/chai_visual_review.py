#!/usr/bin/env python3
"""柴主编渲染后视觉复核（自研）：用 Gemini 直连对锚点帧 + 封面做风格/事实/几何快检。

替代「读不了图的子代理 + modlens 桥」的慢路径——一个发布包几秒出 P0 结论。

用法：
  python3 scripts/chai_visual_review.py <publish_package_dir> [--frames N] [--model 型号]

约定：
  - 读取 <pkg>/qa-frames/index.json，按 scene 均匀抽 N 张 group-start/mid 帧（默认每 scene 1 张 + 首/末各 1 张）。
  - 加上 <pkg>/cover-9x16.png、cover-3x4.png、cover-4x3.png 三封面。
  - API key 取 env GEMINI_API_KEY，否则读 ~/.modlens/config.json 的 gemini-api key。
  - 输出 <pkg>/visual-review.md + 打印 P0 汇总。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "gemini-3.1-flash-lite"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

STYLE_PROMPT = (
    "你是竖屏科普短视频的成片质检员。看这张图（一帧或一张封面），只回答 4 句客观结论，每句一行：\n"
    "背景色：xxx / 主文字色：xxx（是否衬线）\n"
    "橙色强调：出现在哪几个词（几处）\n"
    "排版：左对齐还是居中，右上角有无大序号\n"
    "问题：只列确凿的（错字、文字被裁切、元素重叠、出现不该有的内容）；没有就写「无」\n"
    "注意：字幕条自带的行末标点（、，。；）不是错误，不要报；转场瞬间的残字不是错误，不要报。"
)


def load_key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    cfg = Path.home() / ".modlens" / "config.json"
    if cfg.is_file():
        try:
            return json.loads(cfg.read_text("utf-8"))["providers"]["gemini-api"]["apiKey"]
        except (KeyError, json.JSONDecodeError):
            pass
    raise SystemExit("未找到 Gemini key：请设 GEMINI_API_KEY 或 ~/.modlens/config.json")


def ask(key: str, model: str, path: Path) -> str:
    img = base64.b64encode(path.read_bytes()).decode()
    body = {"contents": [{"parts": [
        {"text": STYLE_PROMPT},
        {"inline_data": {"mime_type": "image/png", "data": img}},
    ]}]}
    req = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return d["candidates"][0]["content"]["parts"][0]["text"]


def pick_frames(index: dict, per_scene: int) -> list[Path]:
    frames = index["frames"]
    scenes: dict[int, list[dict]] = {}
    for f in frames:
        if f.get("extracted"):
            scenes.setdefault(f["scene_frame"], []).append(f)
    picked: list[dict] = []
    for n in sorted(scenes):
        fs = scenes[n]
        mids = [f for f in fs if "mid" in f["kind"]]
        pool = mids or fs  # 静止态优先，避开转场瞬间
        if len(pool) <= per_scene:
            picked += pool
        else:
            step = len(pool) / per_scene
            picked += [pool[int(i * step)] for i in range(per_scene)]
    # 去重保序
    seen, out = set(), []
    for f in picked:
        if f["file"] not in seen:
            seen.add(f["file"])
            out.append(f)
    return [Path(f["file"]) for f in out]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    pkg = Path(sys.argv[1]).resolve()
    argv = sys.argv[2:]
    per_scene = 1
    model = DEFAULT_MODEL
    while argv:
        a = argv.pop(0)
        if a == "--frames" and argv:
            per_scene = int(argv.pop(0))
        elif a == "--model" and argv:
            model = argv.pop(0)
    index_path = pkg / "qa-frames" / "index.json"
    if not index_path.is_file():
        raise SystemExit(f"缺少 {index_path}")
    index = json.loads(index_path.read_text("utf-8"))
    frames = [index_path.parent / p for p in pick_frames(index, per_scene)]
    covers = [pkg / n for n in ("cover-9x16.png", "cover-3x4.png", "cover-4x3.png")
              if (pkg / n).is_file()]
    targets = frames + covers
    key = load_key()
    report = [f"# visual-review（自动）", f"", f"模型 {model} · 帧 {len(frames)} 张 + 封面 {len(covers)} 张", ""]
    for t in targets:
        try:
            report.append(f"## {t.name}")
            report.append(ask(key, model, t).strip())
        except Exception as e:  # noqa: BLE001
            report.append(f"ERROR: {e}")
        report.append("")
    (pkg / "visual-review.md").write_text("\n".join(report), "utf-8")
    print("\n".join(report))
    print(f"[写出] {pkg / 'visual-review.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
