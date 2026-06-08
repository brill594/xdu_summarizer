"""
ASR 语音识别引擎
支持 FunASR、本地 Whisper 和 Whisper API。
"""
import json
import logging
import os
import time
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ASREngine:
    """
    语音识别引擎

    mode="funasr":      使用 FunASR AutoModel，本项目默认中文课堂识别后端
    mode="whisper":     使用本地 openai-whisper 模型
    mode="whisper-api": 使用 OpenAI Whisper API

    兼容旧配置：mode="local" 等同 "whisper"，mode="api" 等同 "whisper-api"。
    """

    def __init__(
        self,
        mode: str = "funasr",
        model_name: str = "paraformer-zh",
        device: str = "auto",
        language: Optional[str] = "auto",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        vad_model: Optional[str] = "fsmn-vad",
        punc_model: Optional[str] = "ct-punc",
        batch_size_s: int = 60,
        merge_length_s: int = 15,
    ):
        self.mode = self._normalize_mode(mode)
        self.model_name = model_name
        self.device = self._resolve_device(device)
        self.language = language
        self.vad_model = vad_model
        self.punc_model = punc_model
        self.batch_size_s = batch_size_s
        self.merge_length_s = merge_length_s
        self.vad_max_segment_time_ms = int(os.getenv("FUNASR_VAD_MAX_SEGMENT_TIME_MS", "30000"))
        self._model = None

        if self.mode == "whisper-api":
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key or os.getenv("OPENAI_API_KEY"),
                base_url=api_base,
            )
        elif self.mode in {"funasr", "whisper"}:
            self._load_model()
        else:
            raise ValueError(f"Unsupported ASR mode: {mode}")

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = (mode or "funasr").strip().lower().replace("_", "-")
        if normalized == "local":
            return "whisper"
        if normalized == "api":
            return "whisper-api"
        return normalized

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            pass
        return "cpu"

    def _load_model(self):
        """加载本地 ASR 模型。"""
        if self._model is not None:
            return

        if self.mode == "funasr":
            logger.info(f"加载 FunASR 模型: {self.model_name} (device={self.device})")
            from funasr import AutoModel

            kwargs = {
                "model": self.model_name,
                "device": self.device,
            }
            if self.vad_model:
                kwargs["vad_model"] = self.vad_model
                kwargs["vad_kwargs"] = {"max_single_segment_time": self.vad_max_segment_time_ms}
            if self.punc_model:
                kwargs["punc_model"] = self.punc_model
            self._model = AutoModel(**kwargs)
            logger.info("FunASR 模型加载完毕")
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
            cache_path: 缓存路径，存在且 ASR 引擎匹配时直接读取

        Returns:
            {
                "text": "完整文本",
                "segments": [
                    {"start": 0.0, "end": 2.5, "text": "..."},
                    ...
                ],
                "language": "zh",
                "engine": "funasr"
            }
        """
        if cache_path and Path(cache_path).exists():
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            cached_engine = cached.get("engine")
            cached_model = cached.get("model")
            engine_matches = cached_engine == self.mode or (self.mode == "whisper" and not cached_engine)
            model_matches = not cached_model or cached_model == self.model_name or self.mode == "whisper-api"
            config_matches = self._cache_config_matches(cached)
            if engine_matches and model_matches and config_matches:
                logger.info(f"从缓存读取 ASR 结果: {cache_path}")
                return cached
            logger.info(
                f"忽略不同 ASR 配置的缓存: {cache_path} "
                f"(cache={cached_engine or 'legacy'}/{cached_model or 'unknown'}, "
                f"current={self.mode}/{self.model_name})"
            )

        logger.info(f"开始语音识别: {audio_path}")

        if self.mode == "whisper-api":
            result = self._transcribe_api(audio_path)
        elif self.mode == "funasr":
            result = self._transcribe_funasr(audio_path)
        else:
            result = self._transcribe_whisper(audio_path)

        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            logger.info(f"ASR 结果已缓存: {cache_path}")

        return result

    def _transcribe_funasr(self, audio_path: str) -> dict:
        """FunASR 转录。"""
        self._load_model()
        start = time.time()

        try:
            raw = self._generate_funasr(audio_path)
        except NotImplementedError as exc:
            if not self.punc_model or "batch decoding" not in str(exc):
                raise
            logger.warning(
                "FunASR 标点模型 %s 不支持当前批量解码路径，禁用标点模型后重试: %s",
                self.punc_model,
                exc,
            )
            self.punc_model = None
            self._model = None
            self._load_model()
            raw = self._generate_funasr(audio_path)

        elapsed = time.time() - start
        duration = self._audio_duration_seconds(audio_path)
        result = self._normalize_funasr_result(raw, duration)
        result.update({
            "engine": "funasr",
            "model": self.model_name,
            "device": self.device,
            "language": self.language or "auto",
            "vad_model": self.vad_model,
            "punc_model": self.punc_model,
            "batch_size_s": self.batch_size_s,
            "merge_length_s": self.merge_length_s,
            "vad_max_segment_time_ms": self.vad_max_segment_time_ms,
            "raw": raw,
        })
        logger.info(
            f"FunASR 完成: 音频时长={duration:.1f}s, "
            f"处理耗时={elapsed:.1f}s, "
            f"实时率={elapsed/max(duration,1):.2f}x"
        )
        return result

    def _generate_funasr(self, audio_path: str):
        return self._model.generate(
            input=str(audio_path),
            cache={},
            language=self.language or "auto",
            use_itn=True,
            batch_size_s=self.batch_size_s,
            merge_vad=bool(self.vad_model),
            merge_length_s=self.merge_length_s,
        )

    def _cache_config_matches(self, cached: dict) -> bool:
        """Return whether cached ASR output matches config that affects text."""
        if self.mode != "funasr":
            return True

        expected = {
            "device": self.device,
            "language": self.language or "auto",
            "vad_model": self.vad_model,
            "punc_model": self.punc_model,
            "batch_size_s": self.batch_size_s,
            "merge_length_s": self.merge_length_s,
            "vad_max_segment_time_ms": self.vad_max_segment_time_ms,
        }
        return all(cached.get(key) == value for key, value in expected.items())

    def _transcribe_whisper(self, audio_path: str) -> dict:
        """本地 Whisper 转录。"""
        self._load_model()
        start = time.time()

        result = self._model.transcribe(
            audio_path,
            language=None if self.language == "auto" else self.language,
            verbose=False,
            word_timestamps=False,
        )

        elapsed = time.time() - start
        duration = result.get("segments", [{}])[-1].get("end", 0) if result.get("segments") else 0
        logger.info(
            f"本地 Whisper ASR 完成: 音频时长={duration:.1f}s, "
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
            "engine": "whisper",
            "model": self.model_name,
            "device": self.device,
        }

    def _transcribe_api(self, audio_path: str) -> dict:
        """OpenAI Whisper API 转录。"""
        with open(audio_path, "rb") as f:
            transcript = self._client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=None if self.language == "auto" else self.language,
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
            "engine": "whisper-api",
            "model": "whisper-1",
        }

    @staticmethod
    def _audio_duration_seconds(audio_path: str) -> float:
        try:
            with wave.open(audio_path, "rb") as wav:
                rate = wav.getframerate()
                return wav.getnframes() / rate if rate else 0.0
        except Exception:
            try:
                import torchaudio
                info = torchaudio.info(audio_path)
                return info.num_frames / info.sample_rate if info.sample_rate else 0.0
            except Exception:
                return 0.0

    @staticmethod
    def _clean_funasr_text(text: str) -> str:
        try:
            from funasr.utils.postprocess_utils import rich_transcription_postprocess
            return rich_transcription_postprocess(text or "").strip()
        except Exception:
            return (text or "").strip()

    def _normalize_funasr_result(self, raw, duration: float) -> dict:
        item = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(item, dict):
            text = self._clean_funasr_text(str(item))
            return {
                "text": text,
                "segments": [{"start": 0.0, "end": duration, "text": text}] if text else [],
            }

        segments = []
        for sent in item.get("sentence_info") or []:
            text = self._clean_funasr_text(sent.get("text", ""))
            if not text:
                continue
            segment = {
                "start": round((sent.get("start", 0) or 0) / 1000.0, 3),
                "end": round((sent.get("end", 0) or 0) / 1000.0, 3),
                "text": text,
            }
            if "spk" in sent:
                segment["speaker"] = sent["spk"]
            segments.append(segment)

        text = self._clean_funasr_text(item.get("text", ""))
        if not segments and text:
            segments.append({"start": 0.0, "end": duration, "text": text})

        return {
            "text": text or "".join(seg["text"] for seg in segments),
            "segments": segments,
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

        if Path(asr_cache).exists():
            return self.transcribe(audio_path, cache_path=asr_cache)

        if not Path(audio_path).exists():
            extract_audio(video_path, audio_path)

        return self.transcribe(audio_path, cache_path=asr_cache)
