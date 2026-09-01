---
name: daily-ai-shorts
description: 端到端生产「AI 单热点日更短视频」的完整流水线：选题核验 → 脚本质检 → TTS 配音 + 词级时间轴 → HyperFrames 竖屏合成（卡拉OK字幕+口播头像）→ 三比例封面 → 自动验收三脚本 + 独立评审 P0 门 → 发布包。凡是用户想「做一期 AI 新闻/工具类短视频」「跑今天这期日更」「选题到成片一条龙」「给短视频做验收质检」，甚至只提到 25–45 秒竖屏视频、AI 日报视频、抖音/小红书知识类短视频，都应使用本技能——即使用户没有明说"日更"。
---

# AI 单热点日更短视频流水线

一天一期（或多期）25–45 秒竖屏短视频：回答普通用户一个具体问题，给出一个当天可执行的动作。本技能是从真实量产管线（连产 100+ 期）中提炼的完整方法，核心是**每一阶段都有门禁，验收不过不交付**。

## 成片长什么样

- 1080×1920 @30fps，H.264/AAC，25–45 秒
- 5 个场景，每场景一句口播（结果/避坑/对比/问题四类钩子轮换）
- 逐词卡拉OK字幕 + 口播头像嘴型同步
- 三种比例封面（9:16 / 3:4 / 4:3）独立排版，标题逐字一致
- 完整发布包：成片 + 封面 + 事实核验 + 质检报告 + 双平台文案 + 数据回填表

## 八个阶段（每阶段一门禁，顺序不可换）

| # | 阶段 | 门禁 | 详读 |
|---|---|---|---|
| 1 | 选题调研与评分 | 评分 ≥70/100 才生产 | [references/research.md](references/research.md) |
| 2 | 事实核验 sources.md | 每条事实有 URL + 定性（事实/口径/观点） | [references/research.md](references/research.md) |
| 3 | 脚本 + 文案质检 | 非空白字符数落在时长档位内；质检四件套过 | [references/workflow.md](references/workflow.md) |
| 4 | TTS 配音 + 词级时间轴 | 词级 JSON 自检：无逐字母拼读、无重复 token | [references/lessons.md](references/lessons.md) |
| 5 | 视频工程 + 渲染 | lint/check 0 error；**渲染前先做前 2 秒动效自检** | [references/design-system.md](references/design-system.md) |
| 6 | 三比例封面 | 三比例标题逐字一致、独立排版不裁切 | [references/design-system.md](references/design-system.md) |
| 7 | 自动验收三脚本 + 独立评审 | 三脚本全 PASS + 独立评审 P0=0 | [references/lessons.md](references/lessons.md) |
| 8 | 发布包 + 复盘记录 | 15 文件齐全，数据回填区留空 | [references/release-package.md](references/release-package.md) |

**硬规则（每期都要过一遍）：**

1. **不自动发布**。产出可人工发布的完整包；发布动作（含勾选平台 AI 生成标识）永远留给人。
2. **不覆盖旧文件**。每期独立目录（`episode-XXX/` + `publish-package-YYYY-MM-DD/`）。
3. **禁止自评**。成片评审必须交给未参与制作的 subagent 或全新会话；评审发现要逐条对照版式契约甄别后再采纳（实测约三分之一评审发现是误报）。
4. **事实、口径、观点三分**。数字标来源口径（"官方自述""CEO 口径"），编辑判断在画面上标注"本期观点"。
5. **安全区是契约**。字幕带、头像、来源行的坐标一旦定下就不改，所有场景共用；评审者常用错误的线来判"越界"，要自己核对契约原文。

## 新机器适配清单（首次使用先过一遍）

- [ ] `ffmpeg` / `ffprobe` 在 PATH（验收脚本用 subprocess 调用）
- [ ] MiniMax TTS：`export MINIMAX_API_KEY=...`（scripts/minimax_tts.py 也支持 macOS 钥匙串）
- [ ] 视觉快检（可选）：`export GEMINI_API_KEY=...`，模型默认 `gemini-3.1-flash-lite`
- [ ] 渲染器：安装 [HyperFrames](https://hyperframes.dev)（`npm i -g hyperframes`），工程内 pin 固定版本保证复现性
- [ ] 品牌资产：头像图 + 字体放入工程 `assets/`（首次没有可先全用系统字体）
- [ ] Python ≥3.9，五个脚本全部只用标准库，无 pip 依赖

## 快速开始（最小可用路径）

```bash
# 1. 配音（输出 mp3 + 词级 JSON）
python3 scripts/minimax_tts.py line01.txt line01.mp3 \
  --subtitle-output line01-words.json --voice-id male-qn-qingse \
  --model speech-2.8-hd --speed 1.08 --emotion calm

# 2. 词级 JSON → 全局字幕时间轴（在 episode 目录执行）
python3 scripts/build_chai_timeline.py content/episode-112

# 3. 渲染后验收
python3 scripts/chai_check_motion.py final.mp4 --json pkg/motion-report.json
python3 scripts/chai_qa_frames.py final.mp4 caption_groups.json --out pkg/qa-frames
python3 scripts/chai_visual_review.py pkg --frames 1   # 需 GEMINI_API_KEY，可跳过
```

按 `references/workflow.md` 的场景模板组装 HyperFrames 工程（或用任何 HTML→视频渲染器复现同一契约：DOM 声明 `data-start/data-duration`，动画全部 seek-safe）。

## 本技能沉淀自哪

真实量产管线「柴主编｜只讲一件事」（czbvideo，日更，累计 110+ 期），五脚本为管线自研实现。方法学参考了公开的验收思想，未复制任何第三方代码。
