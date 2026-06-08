#!/usr/bin/env python3
"""
对单个已下载课程目录运行总结流水线（给不想用全自动脚本的用户）
"""
import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    from xdu_summarizer.config import Settings, load_env_file
    load_env_file()

    parser = argparse.ArgumentParser(description="对已下载的课程视频运行总结流水线")
    parser.add_argument("course_dir", help="课程目录路径（包含 pptVideo/ 或 teacherTrack/ 子目录）")
    parser.add_argument("--course-name", help="课程名称（默认使用目录名）")
    parser.add_argument("--output", default=None, help="笔记输出目录")
    parser.add_argument("--asr-engine", choices=["funasr", "whisper", "whisper-api"], default=None,
                        help="ASR 引擎（默认 funasr，中文课堂优先）")
    parser.add_argument("--funasr-model", default=None, help="FunASR 模型名")
    parser.add_argument("--funasr-device", default=None, help="FunASR 推理设备 auto/cpu/cuda:0")
    parser.add_argument("--funasr-punc-model", default=None, help="FunASR 标点模型，设为空字符串可禁用")
    parser.add_argument("--whisper-model", default=None, help="Whisper 模型大小")
    parser.add_argument("--device", default=None, help="Whisper 推理设备 cpu/cuda")
    parser.add_argument("--llm-model", default=None, help="摘要 LLM 模型")
    parser.add_argument("--llm-api-key", help="外部 OpenAI-compatible API Key")
    parser.add_argument("--llm-base-url", help="外部 OpenAI-compatible Endpoint/Base URL")
    parser.add_argument("--video-type", choices=["pptVideo", "teacherTrack"], default="pptVideo",
                        help="处理哪种视频（默认 pptVideo）")

    args = parser.parse_args()

    if args.asr_engine:
        os.environ["ASR_ENGINE"] = args.asr_engine
    if args.funasr_model:
        os.environ["FUNASR_MODEL"] = args.funasr_model
    if args.funasr_device:
        os.environ["FUNASR_DEVICE"] = args.funasr_device
    if args.funasr_punc_model is not None:
        os.environ["FUNASR_PUNC_MODEL"] = args.funasr_punc_model
    if args.whisper_model:
        os.environ["WHISPER_MODEL"] = args.whisper_model
    if args.device:
        os.environ["WHISPER_DEVICE"] = args.device
    if args.llm_model:
        os.environ["LLM_MODEL"] = args.llm_model
    if args.llm_api_key:
        os.environ["LLM_API_KEY"] = args.llm_api_key
    if args.llm_base_url:
        os.environ["LLM_BASE_URL"] = args.llm_base_url
    if args.output:
        os.environ["OUTPUT_DIR"] = args.output

    from xdu_summarizer.pipeline import LecturePipeline

    settings = Settings.from_env()
    settings.use_ppt_video = (args.video_type == "pptVideo")

    pipeline = LecturePipeline(settings)
    notes = pipeline.process_course(args.course_dir, args.course_name)

    if notes:
        print(f"\n✅ 成功生成 {len(notes)} 篇笔记:")
        for n in notes:
            print(f"   📄 {n}")
    else:
        print("\n⚠️  未生成笔记。请检查课程目录结构。")


if __name__ == "__main__":
    main()
