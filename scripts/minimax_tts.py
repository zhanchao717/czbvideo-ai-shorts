#!/usr/bin/env python3
"""Generate a MiniMax voiceover without exposing the API key in logs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


KEYCHAIN_SERVICE = "dailyvideos-minimax-api-key"
DEFAULT_API_BASE = "https://api.minimaxi.com"
DEFAULT_MODEL = "speech-2.8-hd"
DEFAULT_VOICE = "male-qn-qingse"


def load_api_key() -> str:
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if api_key:
        return api_key

    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            "minimax",
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    raise SystemExit(
        "未找到 MiniMax API Key。请设置 MINIMAX_API_KEY，或保存到 macOS "
        f"Keychain 服务 {KEYCHAIN_SERVICE}。"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MiniMax T2A voiceover generator")
    parser.add_argument("input", type=Path, help="UTF-8 narration text file")
    parser.add_argument("output", type=Path, help="output .mp3 file")
    parser.add_argument(
        "--subtitle-output",
        type=Path,
        help="optional MiniMax subtitle JSON output path",
    )
    parser.add_argument(
        "--voice-id",
        default=os.environ.get("MINIMAX_VOICE_ID", DEFAULT_VOICE),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--speed", type=float, default=1.08)
    parser.add_argument("--vol", type=float, default=1.0)
    parser.add_argument("--pitch", type=int, default=0)
    parser.add_argument(
        "--emotion",
        default=os.environ.get("MINIMAX_EMOTION", "calm"),
        choices=["happy", "sad", "angry", "fearful", "disgusted", "surprised", "neutral", "calm"],
    )
    parser.add_argument(
        "--pronunciation",
        action="append",
        default=[],
        metavar="文本/读音",
        help="发音字典条目（可多次），如 'GB/个G'；只影响读音，不影响字幕文本",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = args.input.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("口播文本为空")

    api_base = os.environ.get("MINIMAX_API_BASE", DEFAULT_API_BASE).rstrip("/")
    request_body = {
        "model": args.model,
        "text": text,
        "stream": False,
        "language_boost": "Chinese",
        "output_format": "hex",
        "subtitle_enable": args.subtitle_output is not None,
        "subtitle_type": "word" if args.subtitle_output is not None else "sentence",
        "voice_setting": {
            "voice_id": args.voice_id,
            "speed": args.speed,
            "vol": args.vol,
            "pitch": args.pitch,
            "emotion": args.emotion,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    if args.pronunciation:
        request_body["pronunciation_dict"] = {"tone": list(args.pronunciation)}
    request = urllib.request.Request(
        f"{api_base}/v1/t2a_v2",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json",
            "User-Agent": "dailyvideos-minimax-tts/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"MiniMax TTS 请求失败（HTTP {exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"无法连接 MiniMax TTS：{exc.reason}") from exc

    base_resp = payload.get("base_resp") or {}
    if base_resp.get("status_code", 0) != 0:
        raise SystemExit(
            f"MiniMax TTS 返回错误 {base_resp.get('status_code')}："
            f"{base_resp.get('status_msg', 'unknown error')}"
        )
    audio_hex = (payload.get("data") or {}).get("audio", "")
    if not audio_hex:
        raise SystemExit("MiniMax TTS 返回成功，但没有音频数据")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bytes.fromhex(audio_hex))
    subtitle_path = None
    if args.subtitle_output is not None:
        subtitle_url = (payload.get("data") or {}).get("subtitle_file", "")
        if not subtitle_url:
            raise SystemExit("MiniMax 返回了音频，但没有词级字幕下载地址")
        try:
            with urllib.request.urlopen(subtitle_url, timeout=60) as response:
                subtitle_bytes = response.read()
        except urllib.error.URLError as exc:
            raise SystemExit(f"无法下载 MiniMax 词级字幕：{exc.reason}") from exc
        args.subtitle_output.parent.mkdir(parents=True, exist_ok=True)
        args.subtitle_output.write_bytes(subtitle_bytes)
        subtitle_path = str(args.subtitle_output.resolve())
    duration_ms = (payload.get("extra_info") or {}).get("audio_length")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "subtitle": subtitle_path,
                "duration_ms": duration_ms,
            }
        )
    )


if __name__ == "__main__":
    main()
