#!/usr/bin/env python3
"""柴主编时间轴构建（自研）：MiniMax 词级 JSON → audio_meta.json + caption_groups.json。

用法（在 episode 目录执行，例如 content/episode-105/）：
  python3 scripts/build_chai_timeline.py <episode_dir>

约定输入：<episode_dir>/audio/NN.mp3 与 <episode_dir>/audio/NN-words.json（NN=01..N，
来自 minimax_tts.py --subtitle-output 的原始输出，timestamped_words 时间为毫秒）。
约定输出：
  <episode_dir>/publish-timeline/audio_meta.json     （voices 内词组时间为片段内相对时间）
  <episode_dir>/publish-timeline/caption_groups.json （全局时间，一条词组一个字幕组）

词组切分规则：连续 timestamped_words 拼接，遇到以句读标点（、，。？！；：—…）结尾的词即收束，
标点保留在词组内——与既有各期 schema 一致。
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FALLBACK_BIN = ROOT / "videos" / "qwen38-max-explained" / "bin"
PUNCT_ENDS = ("、", "，", "。", "？", "！", "；", "：", "—", "…")
CONTENT_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def resolve(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    cand = FALLBACK_BIN / name
    if cand.is_file() and (cand.stat().st_mode & 0o111):
        return str(cand)
    return name


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [resolve("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True).stdout
    return round(float(json.loads(out)["format"]["duration"]), 6)


def phrases_from_words(raw: dict | list, source_text: str | None = None) -> list[dict]:
    """从 MiniMax timestamped_words 重建词组。

    MiniMax 对英文/数字 token 会在每个发音 tick 重复整个词（如 '27'×3 / '55GB'×6），
    连续相同 ASCII token 需去重；显示文本优先用台词原文对齐还原（保留空格与写法）。
    """
    segments = raw if isinstance(raw, list) else raw.get("segments") or [raw]
    phrases: list[dict] = []
    for seg in segments:
        words = seg.get("timestamped_words") or []
        text = source_text if source_text is not None else (seg.get("text") or "")
        toks: list[dict] = []
        for w in words:
            t = w.get("word") or w.get("pronounce_word") or ""
            if not t:
                continue
            pw = w.get("pronounce_word") or t
            # 发音字典会把一个字拆成多个同字 token（pronounce 是拼音片段，如 长→ch/á/n/g）。
            # 仅当 pronounce_word != 字面 时视为「发音拆分重复」合并；合法连字（如「天天」）pronounce==字面，不合并。
            if (toks and t == toks[-1]["t"]
                    and (pw != t or toks[-1].get("pw") != toks[-1]["t"])):
                toks[-1]["end"] = float(w.get("time_end", 0)) / 1000.0
                continue
            toks.append({"t": t, "pw": pw,
                         "start": float(w.get("time_begin", 0)) / 1000.0,
                         "end": float(w.get("time_end", 0)) / 1000.0})
        # 在原文中对齐每个 token，保留原始空格与写法
        aligned = True
        pos = 0
        prev: dict | None = None
        for tk in toks:
            idx = text.find(tk["t"], pos)
            if idx < 0:
                # 发音字典把同一字符拆成多个同字 token（如 长/(cháng) → 长×5），
                # 源文只有一个字：复用前一 token 的源区间，不重复拼接。
                if prev is not None and tk["t"] == prev["t"] and "cs" in prev:
                    tk["cs"], tk["ce"] = prev["cs"], prev["ce"]
                    pos = prev["ce"]
                    continue
                aligned = False
                break
            tk["cs"], tk["ce"] = idx, idx + len(tk["t"])
            pos = tk["ce"]
            prev = tk
        cur: dict | None = None

        def finish(cur: dict) -> dict:
            if aligned and "cs" in cur:
                display = text[cur["cs"]:cur["ce"]]
            else:
                display = cur.get("parts", "")
            return {"text": display, "start": round(cur["start"], 3),
                    "end": round(cur["end"], 3)}

        for tk in toks:
            if cur is None:
                cur = {"start": tk["start"], "end": tk["end"],
                       "cs": tk.get("cs"), "ce": tk.get("ce"),
                       "parts": tk["t"]}
            else:
                cur["end"] = tk["end"]
                if "ce" in tk:
                    cur["ce"] = tk["ce"]
                cur["parts"] += tk["t"]
            if tk["t"].endswith(PUNCT_ENDS):
                if CONTENT_RE.search(cur.get("parts", "")):
                    phrases.append(finish(cur))
                cur = None
    return [p for p in phrases if CONTENT_RE.search(p["text"])]


def check_clip_coverage(ep: Path, idx: int) -> bool:
    """MiniMax 词级 JSON 在输入超过约 70 字时静默截断（音频完整、words 只剩前半）。
    逐段比对 words 拼接文本与口播原文，覆盖率不足即报错——fail fast，别等字幕阶段。"""
    n = f"{idx:02d}"
    txt = ep / "audio" / f"{n}.txt"
    wj = ep / "audio" / f"{n}-words.json"
    if not txt.exists() or not wj.exists():
        return True
    want = "".join(txt.read_text("utf-8").split())
    data = json.loads(wj.read_text("utf-8"))

    def _find(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "timestamped_words":
                    return v
                r = _find(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for it in o:
                r = _find(it)
                if r is not None:
                    return r
        return None

    words = _find(data) or []
    got = "".join(str(w.get("word", "")) for w in words).replace(" ", "")
    strip = lambda s: re.sub(r"[—–\.\s]", "", s)
    if len(strip(got)) < len(strip(want)) * 0.9:
        print(f"FAIL: {n}-words.json 疑似截断（词级覆盖 {len(strip(got))}/{len(strip(want))} 字）。"
              f"口播单句请控制在 65 字以内，或拆成多段后重新合成。")
        return False
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    ep = Path(sys.argv[1]).resolve()
    audio = ep / "audio"
    clips = sorted(audio.glob("[0-9][0-9].mp3"))
    if not clips:
        print(f"FAIL: {audio} 下没有 NN.mp3")
        return 1
    for i in range(1, len(clips) + 1):
        if not check_clip_coverage(ep, i):
            return 2

    voices, groups = [], []
    offset = 0.0
    gid = 0
    for clip in clips:
        frame = int(clip.stem)
        words_file = audio / f"{clip.stem}-words.json"
        duration = probe_duration(clip)
        raw = json.loads(words_file.read_text("utf-8")) if words_file.is_file() else {}
        line_file = audio / f"{clip.stem}.txt"
        source_text = line_file.read_text("utf-8").strip() if line_file.is_file() else None
        phrases = phrases_from_words(raw, source_text)
        rel_words = [{"id": i + 1, "text": p["text"], "start": p["start"],
                      "end": p["end"]} for i, p in enumerate(phrases)]
        voices.append({"frame": frame, "path": f"assets/voice/{clip.name}",
                       "duration_s": duration, "words": rel_words})
        for p in phrases:
            gs, ge = round(offset + p["start"], 3), round(offset + p["end"], 3)
            groups.append({
                "id": f"caption-group-{gid}", "frame": frame,
                "start": gs, "end": ge, "text": p["text"],
                "words": [{"id": f"caption-word-{gid}-0", "text": p["text"],
                           "start": gs, "end": ge}],
            })
            gid += 1
        offset += duration

    out_dir = ep / "publish-timeline"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"bgm": None, "bgm_pending": False, "voices": voices, "sfx": []}
    (out_dir / "audio_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    (out_dir / "caption_groups.json").write_text(
        json.dumps({"total_duration_s": round(offset, 4), "width": 1080,
                    "height": 1920, "groups": groups},
                   ensure_ascii=False, indent=2), "utf-8")
    print(f"clips {len(voices)} · 词组 {len(groups)} · 总时长 {offset:.3f}s")
    print(f"写出 {out_dir}/audio_meta.json, caption_groups.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
