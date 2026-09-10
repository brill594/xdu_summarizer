# XDU 网课自动下载 + 智能总结系统架构

## 一、整体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                     XDU Auto Lecture Pipeline                         │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ① 视频下载层 ────→ ② 预处理层 ────→ ③ AI分析层 ────→ ④ 输出层    │
│                      (利用 XDU)                                       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 各层职责

| 层 | 组件 | 输入 | 输出 |
|---|---|---|---|
| **① 视频下载** | `XDUClassVideoDownloader`（运行时自动拉取） | liveId / UID + cookies 或 IDS helper | .mp4 视频及可用的 .srt 字幕 |
| **② 预处理** | `AudioExtractor` | 无有效字幕的 .mp4 视频 | .wav 音频 (16kHz mono) |
|  | `KeyFrameExtractor` | .mp4 (pptVideo) | 关键帧截图 (slide transitions) |
| **③ 逐字稿** | 下载器字幕优先，`ASREngine` (FunASR / Whisper) 回退 | .srt/.vtt 字幕或 .wav 音频 | 带时间戳的逐字稿 (JSON) |
|  | `SummaryEngine` (LLM) | 逐字稿 | 结构化重点摘要 |
| **④ 输出** | `MarkdownGenerator` | 摘要 + 截图 | **图文并茂的 .md 笔记** |

---

## 二、数据流

```
                    ┌──────────────────────┐
                    │   XDUClassVideoDownloader │
                    │   (Automation.py)     │
                    └──────────┬───────────┘
                               │ .mp4 + 可选 .srt
                               ▼
                   ┌───────────┴───────────┐
                   ▼                       ▼
          ┌────────────────┐      ┌────────────────┐
          │ 同名字幕可用？   │      │  关键帧提取      │
          └───────┬────────┘      │ (slide_01.jpg) │
             是   │   否          └───────┬────────┘
          ┌───────▼───┐ ┌──────────────┐  │
          │ 字幕解析器  │ │ 音频 → ASR   │  │
          └───────┬───┘ └──────┬───────┘  │
                  └──────┬─────┘          │
                         ▼                │
            ┌──────────────┐              │
            │ LLM 摘要引擎   │◄─────────────┘
            │ (OpenAI/     │
            │  Claude/本地) │
            └──────┬───────┘
                   │
                   ▼
            ┌──────────────────┐
            │ Markdown 生成器    │
            │                   │
            │ # 课程名称        │
            │ ## 第一周 重点     │
            │ ![slide](img.png) │
            │ - 关键概念1       │
            │ - 关键概念2       │
            └──────────────────┘
```

---

## 三、技术选型

### 3.1 语音识别 (ASR)

| 方案 | 优点 | 缺点 | 适用场景 |
|---|---|---|---|
| **FunASR** `FunAudioLLM/Fun-ASR-Nano-2512` | 中文课堂、方言/口音覆盖更好，支持本地 GPU | 首次下载模型较大 | 无字幕时的默认回退 |
| **Whisper (local)** `openai-whisper` | 免费、离线、依赖少 | 中文课堂/方言效果较弱 | fallback |
| **Whisper API** | 接入简单 | 按量付费、外部服务 | 临时 fallback |

**推荐：优先使用下载器字幕；无字幕时使用 FunASR-Nano + GPU/CPU auto**。

### 3.2 LLM 摘要

| 方案 | 优点 |
|---|---|
| OpenAI GPT-4o / GPT-4o-mini | 质量高、支持图片理解 |
| Claude Sonnet | 分析能力强 |
| 本地 Ollama (Qwen2.5) | 免费、隐私保护 |

**推荐：GPT-4o-mini** — 性价比最优。

### 3.3 关键帧提取

| 方案 | 方法 |
|---|---|
| **OpenCV 场景检测** | 计算帧间直方图差异，相似度骤降时截取 |
| **PySceneDetect** | 基于内容感知的场景切割 |
| **固定间隔截取** | 每 N 秒一帧（简单但冗余） |

**推荐：OpenCV 帧差法 + 固定间隔降噪**。

---

## 四、目录结构

```
xdu_summarizer/
├── ARCHITECTURE.md              # 本架构文档
├── README.md                    # 使用说明
├── requirements.txt             # 依赖
├── setup.py                     # 安装配置
│
├── xdu_summarizer/              # 核心包
│   ├── __init__.py
│   ├── config.py                # 配置管理
│   ├── audio_extractor.py       # 音频提取 (ffmpeg)
│   ├── asr_engine.py            # FunASR / Whisper 语音识别
│   ├── keyframe_extractor.py    # 关键帧提取 (OpenCV)
│   ├── summary_engine.py        # LLM 摘要生成
│   ├── md_generator.py          # Markdown 生成器
│   └── pipeline.py              # 主流水线编排
│
├── scripts/
│   ├── run_pipeline.py          # 对一个已下载课程运行
│   └── full_workflow.py         # 下载 + 总结一体化脚本
│
└── examples/
    └── sample_output.md         # 示例输出
```

---

## 五、关键设计决策

### 5.1 音频来源选择

课程视频通常包含两轨：
- **pptVideo**：屏幕录制（含教师讲解音频），是 ASR 的主要来源
- **teacherTrack**：教师摄像头画面（部分有独立音频）

**策略**：优先对 pptVideo 做 ASR，因为其画面也用于关键帧提取。teacherTrack 作为备选。

### 5.2 关键帧与摘要的融合

```
Markdown 结构示例：

# 计算机网络 - 第3章 传输层

## 📍 核心知识点

### 1. TCP 三次握手
![TCP 三次握手](Week3/slides/slide_0012.png)
- **SYN**：客户端发送同步序列号
- **SYN-ACK**：服务器确认并回复
- **ACK**：客户端确认，连接建立
- 解决了"确认对方接收能力"的问题

### 2. 流量控制
![滑动窗口](Week3/slides/slide_0024.png)
- ...每张幻灯片对应一个知识点
```

### 5.3 增量处理

- 已输出过的课程自动跳过（检查 `.md` 是否存在）
- 只处理新增视频
- 支持断点续传（ASR 结果缓存）

---

## 六、使用流程

```bash
# 1. 一键下载课程视频并总结（首次会自动拉取 XDUClassVideoDownloader）
python scripts/full_workflow.py --live-id <liveId> --cookies "_d=xxx; UID=xxx; vc3=xxx"

# 或自动发现课程后批量下载并总结
python scripts/full_workflow.py --auto --uid <UID>

# 2. 已有下载目录时，直接运行总结流水线
python -m xdu_summarizer.pipeline --course-dir ./下载的课程名
```

---

## 七、依赖清单

```txt
# 核心依赖
funasr>=1.3.9                   # 默认 ASR
openai-whisper>=20231117        # Whisper fallback
opencv-python>=4.8.0        # 关键帧提取
openai>=1.0.0               # LLM API (可选)

# 基础工具
ffmpeg-python               # 音频提取
pillow>=10.0.0              # 图片处理
tqdm                        # 进度条
```
