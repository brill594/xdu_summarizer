# XDU 网课下载 + 智能总结系统

[xdu_summarizer](https://github.com/yourname/xdu_summarizer)

将 [XDUClassVideoDownloader](https://github.com/lsy223622/XDUClassVideoDownloader) 下载的西电录播课程视频，自动转化为**图文并茂的 Markdown 笔记**。

## ✨ 功能

| 步骤 | 说明 | 技术 |
|---|---|---|
| 📥 视频下载 | 调用 XDUClassVideoDownloader 下载课程 | requests + ffmpeg |
| 📜 字幕读取 | 优先读取下载器生成的同名 SRT/VTT 字幕 | 内置解析器 |
| 🔊 音频提取 | 仅在没有可用字幕时提取音频 | ffmpeg |
| 🎯 语音识别 | 无字幕时生成带时间戳的逐字稿 | FunASR（默认回退）/ OpenAI Whisper |
| 🖼️ 关键帧提取 | 检测幻灯片切换，自动截图 | OpenCV |
| 🤖 AI 摘要 | LLM 从逐字稿提取核心知识点 | GPT-4o-mini / Claude / Ollama |
| 📝 笔记输出 | 生成图文并茂的 Markdown 笔记 | 自定义模板 |

## 🚀 快速开始

### 安装

```bash
# 1. 克隆本仓库
git clone https://github.com/yourname/xdu_summarizer.git
cd xdu_summarizer

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 安装 ffmpeg（已有则跳过）
# macOS: brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg
# Windows: 从 https://ffmpeg.org/download.html 下载
```

### 使用方式

#### 方式一：对已下载的课程直接总结

```bash
# 课程目录结构示例：
# ./下载的课程/
#   └── 计算机网络/
#       └── Week1/
#           └── pptVideo/
#               ├── 01_课程介绍.mp4
#               └── 02_TCPIP协议.mp4

python scripts/run_pipeline.py ./下载的课程/计算机网络 \
    --course-name "计算机网络" \
    --output ./我的笔记 \
    --llm-api-key sk-xxx \
    --llm-base-url https://api.example.com/v1 \
    --llm-model gpt-4o-mini

# 有同名字幕时自动跳过 ASR；无字幕时默认使用 FunASR，也可切换 Whisper
```

#### 方式二：一键下载 + 总结

```bash
# 首次运行会自动下载 XDUClassVideoDownloader 到 .xdu_downloader/
# 可直接传 Cookies；省略 --cookies 时使用下载器自己的 IDS/扫码/Cookies 认证向导

python scripts/full_workflow.py \
    --live-id 12345678 \
    --cookies "_d=xxx; UID=xxx; vc3=xxx" \
    --asr-engine funasr \
    --funasr-device cuda:0
```

也可以通过新版 IDS helper 登录。密码不会放入命令行参数；如触发二次认证，
脚本会在同一认证会话中提示输入短信、企业微信或邮箱验证码：

```bash
python scripts/full_workflow.py \
    --live-id 12345678 \
    --ids-username 你的学号 \
    --ids-reauth-channel email
```

首次使用会通过 Go 1.25.9+ 构建内置 helper。也可用
`XDU_IDS_AUTH_HELPER` 指向预编译程序；自动化环境可通过
`XDU_IDS_USERNAME` 和 `XDU_IDS_PASSWORD` 提供账号信息。

#### 方式三：自动发现课程、下载并总结

```bash
# UID 可在 https://i.mooc.chaoxing.com/settings/info 的 id 字段查看
python scripts/full_workflow.py --auto --uid 123456789 --video-type ppt
```

#### 方式四：WebUI

```bash
python scripts/webui.py --host 0.0.0.0 --port 7860
```
WebUI 支持填写超星 Cookies/UID、外部 LLM API Key、Endpoint/Base URL、模型名，以及选择无字幕时使用的 FunASR/Whisper 后端。生成的 Markdown 笔记包含逐字稿、LLM 摘要和课件关键帧图片。

### 输出示例

```
./lecture_notes/
└── 计算机网络/
    ├── README.md              ← 课程索引
    ├── 第3周_传输层.md        ← 图文笔记
    ├── 第4周_网络层.md
    └── images/
        ├── 第3周_传输层/
        │   ├── slide_0012.jpg  ← 关键帧截图
        │   └── slide_0024.jpg
        └── 第4周_网络层/
```

## ⚙️ 配置

通过环境变量或仓库根目录的 `.env` 文件配置（可从 `.env.example` 复制）：

```bash
export ASR_ENGINE=funasr
export FUNASR_MODEL=paraformer-zh
export FUNASR_DEVICE=auto          # auto / cpu / cuda:0
export FUNASR_PUNC_MODEL=ct-punc   # 留空可禁用标点模型
export WHISPER_MODEL=small        # tiny/base/small/medium/large
export WHISPER_DEVICE=cpu         # cpu 或 cuda
export LLM_API_KEY=sk-xxx         # OpenAI API Key
export LLM_MODEL=gpt-4o-mini      # 摘要模型
export LLM_BASE_URL=              # 自定义 API 端点（可选）

# 或用配置文件
python -c "
from xdu_summarizer.config import Settings
s = Settings.from_env()
s.save('config.json')
"
```

## 📝 笔记格式

每篇笔记包含：
- **课程元信息**：时长、语言、生成时间
- **时间轴目录**：带截图缩略图的导航
- **重点摘要**：LLM 从逐字稿提取的核心知识点
- **课件截图**：关键帧 + 时间戳，图文对照
- **完整逐字稿**：折叠式，可展开查看

## 📦 依赖

```txt
openai-whisper>=20231117
opencv-python>=4.8.0
openai>=1.0.0
pillow>=10.0.0
tqdm>=4.0.0
numpy>=1.24.0
requests>=2.32.5
beautifulsoup4>=4.13.5
pycryptodome>=3.23.0
psutil>=7.0.0
flask>=3.0.0
funasr>=1.3.9
modelscope>=1.37.1
transformers>=4.51.3,<5.0.0
soundfile>=0.13.1
librosa>=0.11.0
python-dotenv>=1.0.0
```

## 🙏 致谢

- 感谢 [lsy223622/XDUClassVideoDownloader](https://github.com/lsy223622/XDUClassVideoDownloader) 提供西电录播课程视频下载能力；本项目通过运行时自动拉取该工具完成课程下载集成。
