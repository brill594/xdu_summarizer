import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from xdu_summarizer.config import Settings

# The integration exercised here mocks keyframe extraction, so importing the
# pipeline must not require the unrelated OpenCV runtime in a lightweight test.
try:
    import cv2  # noqa: F401
except ModuleNotFoundError:
    sys.modules["cv2"] = Mock()
try:
    import numpy  # noqa: F401
except ModuleNotFoundError:
    sys.modules["numpy"] = Mock(ndarray=object)

from xdu_summarizer.pipeline import LecturePipeline
from xdu_summarizer.xdu_downloader import load_downloaded_subtitle


class DownloadedSubtitleTests(unittest.TestCase):
    def test_matches_downloader_name_and_parses_srt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "高等数学2026年9月1日第1周星期二第1节-pptVideo.mp4"
            subtitle = root / "高等数学2026年9月1日第1周星期二第1节.srt"
            video.touch()
            subtitle.write_text(
                "1\n00:00:01,250 --> 00:00:03,500\n第一行\n第二行\n\n"
                "2\n00:01:00,000 --> 00:01:02,000\n下一段\n",
                encoding="utf-8",
            )

            result = load_downloaded_subtitle(video)

            self.assertEqual(result["engine"], "subtitle")
            self.assertEqual(result["segments"][0], {"start": 1.25, "end": 3.5, "text": "第一行 第二行"})
            self.assertEqual(result["segments"][1]["start"], 60.0)

    def test_subtitle_prevents_audio_extraction_and_asr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "课程第1节-pptVideo.mp4"
            subtitle = root / "课程第1节.srt"
            video.touch()
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n有字幕，不应转写。\n",
                encoding="utf-8",
            )

            pipeline = LecturePipeline(Settings(output_dir=str(root / "notes"), skip_existing=False))
            pipeline._asr = Mock()
            pipeline._summarizer = Mock()
            pipeline._summarizer.summarize_with_timestamps.return_value = "摘要"
            pipeline._md_gen = Mock()
            pipeline._md_gen.generate_lecture_note.return_value = str(root / "notes" / "课程第1节.md")

            with patch("xdu_summarizer.pipeline.extract_audio") as extract_audio, patch(
                "xdu_summarizer.pipeline.extract_keyframes", return_value=[]
            ):
                result = pipeline._process_single_video(str(video), "课程")

            self.assertIsNotNone(result)
            extract_audio.assert_not_called()
            pipeline._asr.transcribe.assert_not_called()
            segments = pipeline._summarizer.summarize_with_timestamps.call_args.kwargs["segments"]
            self.assertEqual(segments[0]["text"], "有字幕，不应转写。")

    def test_invalid_subtitle_falls_back_to_asr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "课程第2节-pptVideo.mp4"
            subtitle = root / "课程第2节.srt"
            video.touch()
            subtitle.write_text("这不是有效的 SRT", encoding="utf-8")

            pipeline = LecturePipeline(Settings(output_dir=str(root / "notes"), skip_existing=False))
            pipeline._asr = Mock()
            pipeline._asr.transcribe.return_value = {
                "text": "ASR 回退",
                "segments": [{"start": 0.0, "end": 1.0, "text": "ASR 回退"}],
                "engine": "funasr",
            }
            pipeline._summarizer = Mock()
            pipeline._summarizer.summarize_with_timestamps.return_value = "摘要"
            pipeline._md_gen = Mock()
            pipeline._md_gen.generate_lecture_note.return_value = str(root / "notes" / "课程第2节.md")

            with patch("xdu_summarizer.pipeline.extract_audio") as extract_audio, patch(
                "xdu_summarizer.pipeline.extract_keyframes", return_value=[]
            ):
                pipeline._process_single_video(str(video), "课程")

            extract_audio.assert_called_once()
            pipeline._asr.transcribe.assert_called_once()


if __name__ == "__main__":
    unittest.main()
