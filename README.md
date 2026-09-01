<p align="center"><img src="docs/hero.png" alt="czbvideo-ai-shorts cover samples" width="100%"></p>

# czbvideo-ai-shorts · AI 单热点日更短视频流水线

> 柴主编（**czbvideo**）的日更视频工业线，已连产 110+ 期。[English](README.en.md)

<p align="center">
<a href="README.en.md">English</a> · <a href="#安装">安装</a> · <a href="#脚本一览-scripts">脚本</a> · <a href="#设计要点为什么值得抄">设计要点</a> · <a href="#license">MIT</a>
</p>


一个可直接安装的 [ZCode Skill](https://code.z.ai)：从选题到成片，端到端生产 25–45 秒竖屏 AI 知识短视频（抖音 / 小红书 / 视频号规格）。从一条真实量产管线（连产 110+ 期的「柴主编｜只讲一件事」）中提炼，核心方法论 + 五个零依赖自研脚本全部开源。

## 它解决什么

做知识类日更短视频，难的不是拍，是**每天重复的整条工业链**：选题有没有依据？脚本有没有 AI 腔？配音读音对不对？成片有没有错字/断词/静帧？发布包齐不齐？本技能把每一环都变成**带门禁的流水线阶段**，验收不过不交付。

## 流程总览

```
选题评分(≥70) → 事实核验 sources.md → 脚本+质检四件套 → MiniMax TTS+词级时间轴
    → HyperFrames 竖屏合成(卡拉OK字幕+口播头像) → 三比例封面 → 自动验收三脚本+独立评审P0门 → 发布包15件
```

## 安装

```bash
git clone https://github.com/zhanchao717/czbvideo-ai-shorts.git ~/.agents/skills/daily-ai-shorts
```

重启会话即被自动发现；或显式 `/skill daily-ai-shorts 跑今天这期`。

## 前置依赖

- Python ≥ 3.9（五个脚本**纯标准库，零 pip 依赖**）
- `ffmpeg` / `ffprobe` 在 PATH
- MiniMax TTS API Key（`export MINIMAX_API_KEY=...`，macOS 也可存钥匙串）
- 可选：`GEMINI_API_KEY`（成片视觉快检，gemini-3.1-flash-lite）
- 视频合成：[HyperFrames](https://hyperframes.dev)（HTML→视频渲染器；设计契约也可用其他渲染器复现）

## 脚本一览（scripts/）

| 脚本 | 作用 |
|---|---|
| `minimax_tts.py` | MiniMax T2A 配音，输出 mp3 + 词级时间戳 JSON |
| `build_chai_timeline.py` | 词级 JSON → 全局字幕组 + 音频元数据（含去重与标点伪影保护） |
| `chai_check_motion.py` | 成片规格/长静帧/黑帧/异常静音验收 |
| `chai_qa_frames.py` | 按字幕时间轴抽锚点帧（含数字卡点帧与场景三连拍）+ contact sheet |
| `chai_visual_review.py` | 视觉快检：抽帧 + 读图查错字/裁切/重叠（Gemini 直连） |

## 文档结构（渐进式披露）

- `SKILL.md` — 总览 + 八阶段门禁 + 快速开始
- `references/workflow.md` — 选题评分 / 核验纪律 / 脚本规则 / 验收与复盘全流程
- `references/lessons.md` — **踩坑清单**：TTS 分词四陷阱、低对比皮肤"假死"静帧、安全区契约、评审误报甄别、渲染纪律
- `references/design-system.md` — 两套已验证皮肤（Cobalt Grid / Neumorphism Soft Slate）+ 版式契约 + 三封面排版
- `references/research.md` — 调研渠道与 sources.md 骨架
- `references/release-package.md` — 15 文件发布包 + 复盘规则
- `assets/examples/` — 一期真实产物的完整样例（核验/脚本/分镜/文案/质检报告）

## 设计要点（为什么值得抄）

1. **验收不过不交付**：自动三脚本 + 未参与制作的独立评审 P0 门，评审结论逐条对照契约甄别（实测约 1/3 是误报）。
2. **事实/口径/观点三分**：数字标来源口径，编辑判断在画面上标注"本期观点"。
3. **词级时间轴驱动一切**：字幕、嘴型、数字卡点帧全从同一份 JSON 派生。
4. **皮肤是契约不是美术**：坐标/安全区/字体层级固定，强调色与主题随选题变化。

## License

MIT
