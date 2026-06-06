"""
配置管理模块
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Settings:
    # === Whisper ASR 配置 ===
    whisper_model: str = "base"          # tiny/base/small/medium/large
    whisper_device: str = "cpu"          # cpu / cuda
    whisper_language: str = "zh"         # 语言代码，None 自动检测

    # === LLM 摘要配置 ===
    llm_provider: str = "openai"         # openai / claude / ollama
    llm_model: str = "gpt-4o-mini"       # 模型名
    llm_api_key: Optional[str] = None    # API Key，None 则从环境变量读取
    llm_base_url: Optional[str] = None   # 自定义 API 端点

    # === 关键帧提取 ===
    keyframe_threshold: float = 30.0     # 场景检测阈值（帧间差异>此值视为场景切换）
    keyframe_min_interval: float = 5.0   # 同一场景的最小间隔（秒），减少冗余
    keyframe_resize_width: int = 1280    # 截图输出宽度

    # === 音频处理 ===
    audio_sample_rate: int = 16000       # Whisper 采样率
    audio_channels: int = 1              # 单声道

    # === 输出 ===
    output_dir: str = "./lecture_notes"  # Markdown 输出根目录
    embed_images: bool = True            # Markdown 中嵌入图片路径
    max_image_width: int = 800           # Markdown 图片显示宽度

    # === 流水线控制 ===
    skip_existing: bool = True           # 已处理的视频自动跳过
    use_ppt_video: bool = True           # 对 pptVideo 做 ASR
    use_teacher_audio: bool = False      # 是否同时用 teacherTrack 音频（备选）

    # === XDU 下载器配置 ===
    xdu_uid: Optional[str] = None
    xdu_cookies: Optional[str] = None

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量加载配置"""
        return cls(
            whisper_model=os.getenv("WHISPER_MODEL", "base"),
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
            llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            llm_base_url=os.getenv("LLM_BASE_URL"),
        )

    def save(self, path: str):
        """保存配置到文件"""
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.__dict__, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Settings":
        """从文件加载配置"""
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
