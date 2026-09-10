"""
主流水线编排
串联视频下载 → 音频提取 → ASR → 关键帧 → LLM摘要 → Markdown
"""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from .config import Settings
from .audio_extractor import extract_audio, find_video_files, get_audio_cache_path
from .asr_engine import ASREngine
from .keyframe_extractor import extract_keyframes, get_keyframe_cache_dir
from .summary_engine import SummaryEngine
from .md_generator import MarkdownGenerator
from .xdu_downloader import load_downloaded_subtitle

logger = logging.getLogger(__name__)


class LecturePipeline:
    """
    课程视频总结流水线

    用法:
        pipeline = LecturePipeline()
        pipeline.process_course("/path/to/course_dir", course_name="计算机网络")
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings.from_env()
        self._asr: Optional[ASREngine] = None
        self._summarizer: Optional[SummaryEngine] = None
        self._md_gen: Optional[MarkdownGenerator] = None

    @property
    def asr(self) -> ASREngine:
        if self._asr is None:
            if self.settings.asr_engine == "funasr":
                self._asr = ASREngine(
                    mode="funasr",
                    model_name=self.settings.funasr_model,
                    device=self.settings.funasr_device,
                    language=self.settings.funasr_language,
                    vad_model=self.settings.funasr_vad_model,
                    punc_model=self.settings.funasr_punc_model,
                    batch_size_s=self.settings.funasr_batch_size_s,
                    merge_length_s=self.settings.funasr_merge_length_s,
                )
            else:
                self._asr = ASREngine(
                    mode=self.settings.asr_engine,
                    model_name=self.settings.whisper_model,
                    device=self.settings.whisper_device,
                    language=self.settings.whisper_language,
                )
        return self._asr

    @property
    def summarizer(self) -> SummaryEngine:
        if self._summarizer is None:
            self._summarizer = SummaryEngine(
                provider=self.settings.llm_provider,
                model=self.settings.llm_model,
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
            )
        return self._summarizer

    @property
    def md_gen(self) -> MarkdownGenerator:
        if self._md_gen is None:
            self._md_gen = MarkdownGenerator(
                output_dir=self.settings.output_dir,
                embed_images=self.settings.embed_images,
                max_image_width=self.settings.max_image_width,
            )
        return self._md_gen

    def process_course(
        self,
        course_dir: str,
        course_name: Optional[str] = None,
    ) -> list:
        """
        处理课程目录下所有视频

        Args:
            course_dir: 课程目录（如 ./下载的课程/计算机网络）
            course_name: 课程名称，默认用目录名

        Returns:
            生成的笔记文件路径列表
        """
        course_dir = str(course_dir)
        course_name = course_name or Path(course_dir).name

        # 找到 pptVideo 文件
        if self.settings.use_ppt_video:
            videos = find_video_files(course_dir, "pptVideo")
        else:
            videos = find_video_files(course_dir, "teacherTrack")

        if not videos:
            logger.warning(f"在 {course_dir} 中未找到视频文件")
            return []

        logger.info(f"找到 {len(videos)} 个视频，开始处理课程: {course_name}")

        notes = []
        for video_path in videos:
            note = self._process_single_video(video_path, course_name)
            if note:
                notes.append(note)

        # 生成课程索引
        if notes:
            self.md_gen.generate_course_index(course_name, notes)

        return notes

    def _process_single_video(
        self,
        video_path: str,
        course_name: str,
    ) -> Optional[str]:
        """处理单个视频文件"""
        video_name = Path(video_path).stem

        # 检测是否已有笔记（增量处理）
        output_dir = Path(self.settings.output_dir) / course_name
        md_path = output_dir / f"{video_name}.md"
        if self.settings.skip_existing and md_path.exists():
            logger.info(f"跳过已有笔记: {md_path}")
            return str(md_path)

        logger.info(f"处理视频: {video_path}")

        # === Step 1: 优先使用下载器字幕 ===
        cache_dir = str(Path(course_name) / ".cache")
        audio_path = get_audio_cache_path(video_path, cache_dir)
        asr_cache = audio_path.replace(".wav", "_asr.json")
        asr_result = load_downloaded_subtitle(video_path)

        # === Step 2: 无可用字幕时才提取音频并运行 ASR ===
        if asr_result is None:
            if not Path(audio_path).exists():
                try:
                    extract_audio(video_path, audio_path, self.settings.audio_sample_rate)
                except Exception as e:
                    logger.error(f"音频提取失败: {video_path} — {e}")
                    return None

            try:
                asr_result = self.asr.transcribe(audio_path, cache_path=asr_cache)
            except Exception as e:
                logger.error(f"ASR 识别失败: {video_path} — {e}")
                return None

        if not asr_result.get("segments"):
            logger.warning(f"逐字稿结果为空: {video_path}")
            return None

        # === Step 3: 提取关键帧 ===
        kf_output = get_keyframe_cache_dir(video_path, cache_dir)
        keyframes = []
        try:
            keyframes = extract_keyframes(
                video_path,
                kf_output,
                threshold=self.settings.keyframe_threshold,
                min_interval=self.settings.keyframe_min_interval,
                resize_width=self.settings.keyframe_resize_width,
            )
        except Exception as e:
            logger.warning(f"关键帧提取失败 (不影响主要流程): {e}")

        # === Step 4: LLM 摘要 ===
        try:
            summary = self.summarizer.summarize_with_timestamps(
                segments=asr_result["segments"],
                title=video_name,
            )
        except Exception as e:
            logger.error(f"摘要生成失败: {e}")
            summary = "*（摘要生成失败）*"

        # === Step 5: 生成 Markdown 笔记 ===
        chapter_name = video_name
        note_path = self.md_gen.generate_lecture_note(
            course_name=course_name,
            chapter_name=chapter_name,
            summary_md=summary,
            keyframes=keyframes,
            asr_data=asr_result,
        )


        logger.info(f"笔记生成完成: {note_path}")
        return note_path
