"""Console entry point for processing an already downloaded course directory."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="对已下载的课程视频运行总结流水线")
    parser.add_argument("course_dir", help="课程目录路径（包含 pptVideo/ 或 teacherTrack/ 视频）")
    parser.add_argument("--course-name", help="课程名称（默认使用目录名）")
    parser.add_argument("--output", default="./lecture_notes", help="笔记输出目录")
    parser.add_argument("--whisper-model", default="base", help="Whisper 模型大小")
    parser.add_argument("--device", default="cpu", help="推理设备 cpu/cuda")
    parser.add_argument("--llm-model", default="gpt-4o-mini", help="摘要 LLM 模型")
    parser.add_argument("--llm-api-key", help="外部 OpenAI-compatible API Key")
    parser.add_argument("--llm-base-url", help="外部 OpenAI-compatible Endpoint/Base URL")
    parser.add_argument(
        "--video-type",
        choices=["pptVideo", "teacherTrack"],
        default="pptVideo",
        help="处理哪种视频（默认 pptVideo）",
    )
    args = parser.parse_args()

    os.environ["WHISPER_MODEL"] = args.whisper_model
    os.environ["WHISPER_DEVICE"] = args.device
    os.environ["LLM_MODEL"] = args.llm_model
    os.environ["OUTPUT_DIR"] = args.output
    if args.llm_api_key:
        os.environ["LLM_API_KEY"] = args.llm_api_key
    if args.llm_base_url:
        os.environ["LLM_BASE_URL"] = args.llm_base_url

    from .config import Settings
    from .pipeline import LecturePipeline

    settings = Settings.from_env()
    settings.use_ppt_video = args.video_type == "pptVideo"

    notes = LecturePipeline(settings).process_course(args.course_dir, args.course_name)
    if notes:
        print(f"\n成功生成 {len(notes)} 篇笔记:")
        for note in notes:
            print(f"   {note}")
    else:
        print("\n未生成笔记。请检查课程目录结构。")


if __name__ == "__main__":
    main()
