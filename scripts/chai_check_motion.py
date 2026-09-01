#!/usr/bin/env python3
"""柴主编成片自动验收：规格 / 静帧 / 黑帧 / 异常静音检测（自研实现）。

方法学参考开源项目 video-talkcraft 的「渲染后自动验收」思想（思想与参数口径参考，
代码为本仓库独立实现，未复制任何第三方代码；video-talkcraft 采用 PolyForm
Noncommercial 1.0.0 许可，生产管线不得复制其代码）。

实现说明（本机 Remotion 精简版 ffmpeg 无 freezedetect/blackdetect 滤镜）：
- 规格 / 时长 / 音轨：ffprobe。
- 静音：ffmpeg silencedetect 滤镜。
- 长静帧 / 黑帧：ffmpeg 逐帧输出缩略 PNG（image2 muxer），本脚本用
  zlib+numpy 解码灰度像素后逐帧差分判定（自研，无第三方代码）。

用法：
  python3 scripts/chai_check_motion.py 成片.mp4 \
      [--min-freeze 0.8] [--min-black 0.5] [--max-silence 2.5] \
      [--min-duration 25] [--max-duration 45] \
      [--width 1080] [--height 1920] [--fps 30] \
      [--freeze-diff 1.2] [--sample-every 5] \
      [--json 报告.json]

退出码：0 = 通过（可含 WARN）；1 = 存在 FAIL，不得交付。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FALLBACK_BIN = ROOT / "videos" / "qwen38-max-explained" / "bin"
W, H = 192, 344  # 缩略帧尺寸（比例近似 9:16，仅用于差分/亮度统计）

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def resolve(name: str) -> str:
    """PATH 优先；找不到时兜底到模板项目的已验证 ffmpeg/ffprobe 包装器。"""
    found = shutil.which(name)
    if found:
        return found
    cand = FALLBACK_BIN / name
    if cand.is_file() and (cand.stat().st_mode & 0o111):
        return str(cand)
    return name


def ffprobe_specs(path: Path) -> dict:
    cmd = [
        resolve("ffprobe"), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if not proc.stdout:
        raise RuntimeError(f"ffprobe 无输出: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    num, _, den = (stream.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    proc2 = subprocess.run(
        [resolve("ffprobe"), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "json", str(path)],
        capture_output=True, text=True)
    has_audio, acodec = False, None
    if proc2.stdout:
        try:
            astreams = json.loads(proc2.stdout).get("streams") or []
            has_audio = bool(astreams)
            acodec = astreams[0].get("codec_name") if astreams else None
        except json.JSONDecodeError:
            pass
    return {
        "video_codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": round(fps, 3),
        "duration_s": round(float(fmt.get("duration", 0)), 3),
        "has_audio": has_audio,
        "audio_codec": acodec,
    }


def analyze_silence(path: Path) -> list[dict]:
    """silencedetect：返回静音事件列表。"""
    cmd = [resolve("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
           "-vn", "-af", "silencedetect=noise=-45dB:d=0.8",
           "-c:a", "pcm_s16le", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    pattern = (r"silence_start:\s*([\d.]+)\s*.*?"
               r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")
    return [{"start_s": round(float(m.group(1)), 3),
             "end_s": round(float(m.group(2)), 3),
             "duration_s": round(float(m.group(3)), 3)}
            for m in re.finditer(pattern, proc.stderr)]


def decode_png_gray(data: bytes) -> np.ndarray:
    """解码 8-bit 灰度 PNG 为 (H, W) uint8 数组（stdlib zlib + numpy）。"""
    if data[:8] != PNG_SIG:
        raise ValueError("非 PNG 文件")
    pos, idat, w, h = 8, bytearray(), None, None
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            w, h, depth, color = struct.unpack(">IIBB", chunk[:10])
            if depth != 8 or color != 0:
                raise ValueError(f"不支持的 PNG 格式 depth={depth} color={color}")
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if not w or not h:
        raise ValueError("PNG 缺少 IHDR")
    raw = zlib.decompress(bytes(idat))
    stride = w
    rows = np.frombuffer(raw, np.uint8)
    if rows.size != h * (stride + 1):
        raise ValueError(f"PNG 数据尺寸不符 {rows.size} ≠ {h}×{stride + 1}")
    rows = rows.reshape(h, stride + 1)
    out = np.empty((h, stride), np.int16)
    prev = np.zeros(stride, np.int16)
    for r in range(h):
        ft = int(rows[r, 0])
        line = rows[r, 1:].astype(np.int32)
        if ft == 0:
            cur = line
        elif ft == 1:  # Sub：cumsum 取模
            cur = np.cumsum(line) & 0xFF
        elif ft == 2:  # Up：加上一行重建值
            cur = (line + prev) & 0xFF
        elif ft == 3:  # Average：顺序依赖，逐像素
            cur = line.copy()
            for x in range(stride):
                left = cur[x - 1] if x else 0
                cur[x] = (cur[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth：顺序依赖，逐像素
            cur = line.copy()
            for x in range(stride):
                a = int(cur[x - 1]) if x else 0
                b = int(prev[x])
                c = int(prev[x - 1]) if x else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[x] = (cur[x] + pr) & 0xFF
        else:
            raise ValueError(f"未知 PNG 行滤波 {ft}")
        out[r] = cur
        prev = out[r].astype(np.int16)
    return out


def analyze_frames(path: Path, fps: float, sample_every: int,
                   freeze_diff: float, min_black_luma: float) -> dict:
    """逐帧输出灰度缩略 PNG，采样解码后做帧间差分与亮度统计（自研）。"""
    tmp = Path(tempfile.mkdtemp(prefix="chai-motion-"))
    cmd = [resolve("ffmpeg"), "-hide_banner", "-nostats", "-loglevel", "error",
           "-i", str(path),
           "-vf", f"scale={W}:{H},format=gray",
           "-f", "image2", "-c:v", "png", str(tmp / "f-%06d.png")]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        files = sorted(tmp.glob("f-*.png"))
        if not files:
            raise RuntimeError(f"缩略帧输出为空: {proc.stderr.strip()[-200:]}")
        step_s = sample_every / (fps or 30.0)
        freezes: list[dict] = []
        blacks: list[dict] = []
        run_start: float | None = None
        kind_now: str | None = None

        def close_run(t_end: float) -> None:
            nonlocal run_start, kind_now
            if run_start is None or kind_now is None:
                return
            duration = t_end - run_start + step_s
            bucket = freezes if kind_now == "freeze" else blacks
            bucket.append({"start_s": round(run_start, 3),
                           "duration_s": round(duration, 3)})
            run_start, kind_now = None, None

        prev_arr: np.ndarray | None = None
        sampled = 0
        for i, f in enumerate(files):
            if i % max(1, sample_every):
                f.unlink(missing_ok=True)
                continue
            arr = decode_png_gray(f.read_bytes())
            f.unlink(missing_ok=True)
            sampled += 1
            t = i / (fps or 30.0)
            if prev_arr is not None:
                diff = float(np.abs(arr.astype(np.int16) - prev_arr).mean())
                luma = float(arr.mean())
                kind = None
                if diff < freeze_diff:
                    kind = "freeze"
                elif luma < min_black_luma:
                    kind = "black"
                if kind == kind_now:
                    continue
                close_run(t - step_s)
                if kind:
                    run_start, kind_now = t - step_s, kind
            prev_arr = arr
        close_run((sampled - 1) * step_s)
        return {"freezes": freezes, "blacks": blacks,
                "sampled": sampled, "total_frames": len(files)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="柴主编成片自动验收")
    parser.add_argument("mp4", type=Path)
    parser.add_argument("--min-freeze", type=float, default=0.8,
                        help="静止段判定阈值（秒），默认 0.8")
    parser.add_argument("--min-black", type=float, default=0.5,
                        help="黑段判定阈值（秒），默认 0.5")
    parser.add_argument("--max-silence", type=float, default=2.5,
                        help="中段异常静音判定阈值（秒），默认 2.5")
    parser.add_argument("--min-duration", type=float, default=25.0)
    parser.add_argument("--max-duration", type=float, default=45.0)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--freeze-diff", type=float, default=1.2,
                        help="帧间平均差低于该值（0-255）视为静止，默认 1.2")
    parser.add_argument("--sample-every", type=int, default=5,
                        help="每 N 帧采样一次做差分，默认 5")
    parser.add_argument("--json", type=Path, default=None, help="写出 JSON 报告")
    args = parser.parse_args()

    if not args.mp4.is_file():
        print(f"FAIL: 文件不存在 {args.mp4}")
        return 1
    # 渲染包装器会 cd 进 compositor 目录，必须全程使用绝对路径
    args.mp4 = args.mp4.resolve()
    if args.json:
        args.json = args.json.resolve()

    report: dict = {"file": str(args.mp4), "fails": [], "warns": [], "info": []}

    try:
        specs = ffprobe_specs(args.mp4)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}")
        return 1
    report["specs"] = specs

    if not specs["has_audio"]:
        report["fails"].append("无音频流")
    elif specs["audio_codec"] != "aac":
        report["fails"].append(f"音频编码 {specs['audio_codec']} ≠ aac")
    if specs["video_codec"] not in ("h264", "avc1"):
        report["fails"].append(f"视频编码 {specs['video_codec']} ≠ h264")
    if (specs["width"], specs["height"]) != (args.width, args.height):
        report["fails"].append(
            f"分辨率 {specs['width']}x{specs['height']} ≠ {args.width}x{args.height}")
    if abs(specs["fps"] - args.fps) > 0.5:
        report["fails"].append(f"帧率 {specs['fps']} ≠ {args.fps}")
    if not args.min_duration <= specs["duration_s"] <= args.max_duration:
        report["fails"].append(
            f"时长 {specs['duration_s']}s 不在 {args.min_duration}-{args.max_duration}s 档")

    silences = analyze_silence(args.mp4)
    frames = analyze_frames(args.mp4, specs["fps"] or args.fps,
                            max(1, args.sample_every), args.freeze_diff, 20.0)
    report["silence_events"] = silences
    report["frame_analysis"] = frames

    for f in frames["freezes"]:
        if f["duration_s"] >= args.min_freeze:
            report["fails"].append(
                f"长静帧 {f['duration_s']}s（{f['start_s']}s 起）——动效冻结，必修")
        else:
            report["info"].append(f"短静帧 {f['duration_s']}s（{f['start_s']}s 起）")
    for b in frames["blacks"]:
        if b["duration_s"] >= args.min_black:
            report["fails"].append(f"黑段 {b['duration_s']}s（{b['start_s']}s 起）")
        else:
            report["info"].append(f"短黑段 {b['duration_s']}s（{b['start_s']}s 起）")

    for s in silences:
        if s["duration_s"] >= args.max_silence:
            is_tail = s["start_s"] >= specs["duration_s"] - 2.0
            entry = (f"静音 {s['duration_s']}s（{s['start_s']}s 起）"
                     + ("，位于结尾缓冲区" if is_tail else "，位于中段——疑似配音截断"))
            (report["warns"] if is_tail else report["fails"]).append(entry)
        elif s["duration_s"] >= 1.5:
            report["info"].append(f"句间停顿 {s['duration_s']}s（{s['start_s']}s 起）")

    status = "FAIL" if report["fails"] else ("WARN" if report["warns"] else "PASS")
    report["status"] = status
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    print(f"specs: {specs['width']}x{specs['height']}@{specs['fps']} "
          f"{specs['duration_s']}s v={specs['video_codec']} a={specs['audio_codec']}")
    print(f"帧采样 {frames['sampled']}/{frames['total_frames']} 帧"
          f"（每 {args.sample_every} 帧 1 采样，差分阈值 {args.freeze_diff}/255）")
    for level in ("fails", "warns", "info"):
        for item in report[level]:
            print(f"{level.upper()[:-1]}: {item}")
    print(f"静帧事件 {len(frames['freezes'])} · 黑段事件 {len(frames['blacks'])} "
          f"· 静音事件 {len(silences)}")
    print(f"chai_check_motion: {status}")
    return 1 if report["fails"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
