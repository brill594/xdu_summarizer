"""
ASR 语音识别引擎
封装 OpenAI Whisper（本地）和 Whisper API 两种模式
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ASREngine:
    """
    语音识别引擎

    mode="local": 使用本地 openai-whisper 模型
    mode="api":   使用 OpenAI Whisper API
    """

    def __init__(
        self,
        mode: str = "local",
        model_name: str = "base",
        device: str = "cpu",
        language: Optional[str] = "zh",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ):
        self.mode = mode
        self.model_name = model_name
        self.device = device
        self.language = language
        self._model = None

        if mode == "api":
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key or os.getenv("OPENAI_API_KEY"),
                base_url=api_base,
            )
        elif mode == "local":
            self._load_model()

    def _load_model(self):
        """加载本地 Whisper 模型（懒加载）"""
        if self._model is not None:
            return
        logger.info(f"加载 Whisper 模型: {self.model_name} (device={self.device})")
        import whisper
        self._model = whisper.load_model(self.model_name, device=self.device)
        logger.info("Whisper 模型加载完毕")

    def transcribe(self, audio_path: str, cache_path: Optional[str] = None) -> dict:
        """
        转录音频文件

        Args:
            audio_path: 音频文件路径
            cache_path: 缓存路径，存在时直接读取

        Returns:
            {
                "text": "完整文本",
                "segments": [
                    {"start": 0.0, "end": 2.5, "text": "..."},
                    ...
                ],
                "language": "zh"
            }
        """
        # 缓存命中
        if cache_path and Path(cache_path).exists():
            logger.info(f"从缓存读取 ASR 结果: {cache_path}")
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

        logger.info(f"开始语音识别: {audio_path}")

        if self.mode == "api":
            result = self._transcribe_api(audio_path)
        else:
            result = self._transcribe_local(audio_path)

        # 写入缓存
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"ASR 结果已缓存: {cache_path}")

        return result

    def _transcribe_local(self, audio_path: str) -> dict:
        """本地 Whisper 转录"""
        self._load_model()
        start = time.time()

        result = self._model.transcribe(
            audio_path,
            language=self.language,
            verbose=False,
            word_timestamps=False,
        )

        elapsed = time.time() - start
        duration = result.get("segments", [{}])[-1].get("end", 0) if result.get("segments") else 0
        logger.info(
            f"本地 ASR 完成: 音频时长={duration:.1f}s, "
            f"处理耗时={elapsed:.1f}s, "
            f"实时率={elapsed/max(duration,1):.2f}x"
        )

        return {
            "text": result["text"],
            "segments": [
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip(),
                }
                for seg in result.get("segments", [])
            ],
            "language": result.get("language", "unknown"),
        }

    def _transcribe_api(self, audio_path: str) -> dict:
        """OpenAI Whisper API 转录"""
        with open(audio_path, "rb") as f:
            transcript = self._client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=self.language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        segments = []
        for seg in transcript.segments:
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip(),
            })

        return {
            "text": transcript.text,
            "segments": segments,
            "language": transcript.language if hasattr(transcript, "language") else "unknown",
        }

    def transcribe_video_directly(
        self, video_path: str, cache_dir: Optional[str] = None
    ) -> dict:
        """
        直接从视频文件转录（先提音频再 ASR，内部使用 ffmpeg）

        Args:
            video_path: 视频路径
            cache_dir: 缓存目录

        Returns:
            同 transcribe()
        """
        from .audio_extractor import extract_audio, get_audio_cache_path

        audio_path = get_audio_cache_path(video_path, cache_dir)
        asr_cache = audio_path.replace(".wav", "_asr.json")

        # 如果 ASR 缓存已存在，跳过音频提取
        if Path(asr_cache).exists():
            return self.transcribe(audio_path, cache_path=asr_cache)

        # 提取音频
        if not Path(audio_path).exists():
            extract_audio(video_path, audio_path)

        return self.transcribe(audio_path, cache_path=asr_cache)
