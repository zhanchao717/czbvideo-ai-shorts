#!/usr/bin/env python3
"""柴主编 · MultiPost 一键发布（自研）：把发布包推送到抖音/小红书。

用法：
  MULTIPOST_API_KEY=xxx python3 scripts/chai_publish_multipost.py --setup            # 一次性配置
  python3 scripts/chai_publish_multipost.py <发布包目录> [--dry-run] [--auto-publish]

默认发草稿（isAutoPublish=false）；--auto-publish 才真正点发布。API key 取 env
MULTIPOST_API_KEY，否则 macOS Keychain service `dailyvideos-multipost-api-key`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

API_BASE = "https://api.multipost.app"
KEYCHAIN_SERVICE = "dailyvideos-multipost-api-key"
CONFIG_PATH = Path.home() / ".config" / "multipost" / "config.json"

# 平台枚举默认猜测；--setup 会用 GET /extension/clients 的真实 name 覆盖。
DEFAULT_PLATFORMS = {"douyin": "DYNAMIC_DOUYIN", "xiaohongshu": "REDNOTE"}


def api_key() -> str:
    if os.environ.get("MULTIPOST_API_KEY"):
        return os.environ["MULTIPOST_API_KEY"]
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", "multipost", "-w"],
            capture_output=True, text=True, check=True).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    raise SystemExit("未找到 MULTIPOST_API_KEY：请设环境变量或存入 macOS Keychain（service "
                     + KEYCHAIN_SERVICE + "）")


def http(method: str, path: str, key: str, body=None) -> dict:
    req = urllib.request.Request(
        API_BASE + path, method=method,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                 "Origin": "https://multipost.app",
                 "Referer": "https://multipost.app/"})
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} {path}: {e.read().decode('utf-8', 'replace')[:400]}")


def load_config() -> dict:
    if CONFIG_PATH.is_file():
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    return {"platforms": dict(DEFAULT_PLATFORMS)}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


def setup() -> None:
    key = api_key()
    resp = http("GET", "/extension/clients", key)
    clients = (resp.get("data") or {}).get("clients") or resp.get("data") or []
    cfg = load_config()
    print("== 客户端 ==")
    if isinstance(clients, list):
        for c in clients:
            cid = c.get("id") or c.get("clientId")
            name = c.get("name") or c.get("browserName") or c.get("deviceName")
            print(f"  {cid}  {name}")
            platforms = c.get("platforms") or c.get("platformInfos") or []
            for p in platforms:
                pname = p.get("name") if isinstance(p, dict) else p
                print(f"     平台: {pname}")
            if not cfg.get("targetClientId") and cid:
                cfg["targetClientId"] = cid
    if not cfg.get("targetClientId"):
        raise SystemExit("未识别到客户端；请先在浏览器扩展里完成「链接客户端」，再重跑 --setup。")
    save_config(cfg)
    print(f"\n已保存配置到 {CONFIG_PATH}（targetClientId={cfg['targetClientId']}）。")
    print("请核对上面打印的平台 name，据此修正 config 里 platforms.douyin / xiaohongshu。")


def upload(key: str, path: Path) -> str:
    resp = http("POST", "/v1/file/create", key, {"filename": path.name})
    data = (resp.get("data") or {})
    file_id = data.get("fileId")
    url = data.get("url")
    if not file_id or not url:
        raise SystemExit(f"file/create 返回异常：{resp}")
    mime = "video/mp4" if path.suffix.lower() == ".mp4" else "image/png"
    req = urllib.request.Request(url, method="PUT", data=path.read_bytes(),
                                 headers={"Content-Type": mime})
    with urllib.request.urlopen(req, timeout=300) as r:
        r.read()
    print(f"  上传 {path.name} → {file_id}")
    return file_id


def parse_copy(pkg: Path) -> dict:
    text = (pkg / "publish-copy.md").read_text("utf-8")
    out: dict = {}

    def grab(section: str, fields: dict) -> None:
        m = re.search(rf"##\s*{section}\n(.*?)(?=\n##\s|\Z)", text, re.S)
        block = m.group(1) if m else ""
        for fname, key in fields.items():
            fm = re.search(rf"-\s*{fname}：\s*(.+)", block)
            if fm:
                out[key] = fm.group(1).strip()

    grab("抖音", {"标题": "douyin_title", "简介": "douyin_body", "话题": "douyin_tags"})
    grab("小红书", {"标题": "xhs_title", "正文": "xhs_body", "话题": "xhs_tags"})
    return out


def pick_file(pkg: Path, *names: str) -> Path:
    for n in names:
        for p in pkg.glob(n):
            return p
    raise SystemExit(f"发布包缺文件：{names[0]}")


def build_payload(cfg: dict, pkg: Path) -> list[dict]:
    video = pick_file(pkg, "*.mp4")
    key = api_key()
    video_id = upload(key, video)
    cover9 = pick_file(pkg, "cover-9x16.png")
    cover3 = pick_file(pkg, "cover-3x4.png")
    cover9_id = upload(key, cover9)
    cover3_id = upload(key, cover3)
    copy = parse_copy(pkg)
    pf = cfg.get("platforms") or DEFAULT_PLATFORMS

    def post(name: str, title: str, body: str, tags: str, cover_id: str) -> dict:
        content = body + ("\n" + tags if tags else "")
        return {
            "name": name,
            "isAutoPublish": cfg.get("auto_publish", False),
            "data": {"title": title, "content": content,
                     "images": [cover_id], "videos": [video_id]},
        }

    return [
        post(pf.get("douyin", "DYNAMIC_DOUYIN"), copy.get("douyin_title", ""),
             copy.get("douyin_body", ""), copy.get("douyin_tags", ""), cover9_id),
        post(pf.get("xiaohongshu", "REDNOTE"), copy.get("xhs_title", ""),
             copy.get("xhs_body", ""), copy.get("xhs_tags", ""), cover3_id),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", nargs="?")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--auto-publish", action="store_true")
    args = ap.parse_args()

    if args.setup:
        setup()
        return 0
    if not args.pkg:
        ap.error("需要一个发布包目录，或 --setup")

    pkg = Path(args.pkg).resolve()
    cfg = load_config()
    cfg["auto_publish"] = args.auto_publish
    target = cfg.get("targetClientId")
    if not target and args.dry_run:
        target = "<targetClientId>"
    if not target:
        raise SystemExit("缺少 targetClientId：先跑 --setup。")

    key = None if args.dry_run else api_key()
    platforms = build_payload(cfg, pkg) if not args.dry_run else [
        {"name": cfg["platforms"]["douyin"], "isAutoPublish": args.auto_publish,
         "data": {"title": "<douyin_title>", "content": "<douyin_body + tags>", "images": ["<cover9>"], "videos": ["<video>"]}},
        {"name": cfg["platforms"]["xiaohongshu"], "isAutoPublish": args.auto_publish,
         "data": {"title": "<xhs_title>", "content": "<xhs_body + tags>", "images": ["<cover3>"], "videos": ["<video>"]}},
    ]
    body = {"targetClientId": target, "taskType": "PUBLISH_POST",
            "taskData": {"platforms": platforms}}
    if args.dry_run:
        print("== dry-run（不发送，不上传）==")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0
    print("== 创建发布任务 ==")
    resp = http("POST", "/extension/task", key, body)
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    if resp.get("success") and (resp.get("data") or {}).get("taskId"):
        print("任务已创建（PENDING）。用 GET /extension/task 查状态，勿把 PENDING 当已发布。")
        return 0
    print("任务创建失败，见上方错误。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
