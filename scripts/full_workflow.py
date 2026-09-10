#!/usr/bin/env python3
"""
全自动工作流：下载 → 总结 一站式脚本

将 XDUClassVideoDownloader 与本总结系统无缝集成。

三种使用方式：

1) 指定 liveId 下载并总结：
   python full_workflow.py --live-id 12345678 --cookies "你的cookies"

2) 指定已下载的课程目录直接总结：
   python full_workflow.py --course-dir ./下载/计算机网络

3) 由 Automation.py 自动发现并下载课程，再总结下载结果：
   python full_workflow.py --auto --uid 123456789
"""
import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

# 确保包在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xdu_summarizer.xdu_downloader import run_auto_download, run_live_download

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("full_workflow")


def _snippet(text: str, limit: int = 2000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _log_download_failure(result) -> None:
    logger.error(f"下载失败 (exit={result.returncode})")
    stderr = _snippet(result.stderr)
    stdout = _snippet(result.stdout)
    if stderr:
        logger.error(f"downloader stderr:\n{stderr}")
    if stdout:
        logger.error(f"downloader stdout:\n{stdout}")


def _has_downloaded_video(course_dir: Path) -> bool:
    return any(course_dir.glob("**/*-pptVideo.mp4")) or any(course_dir.glob("**/pptVideo/**/*.mp4"))



def _has_direct_downloaded_video(course_dir: Path) -> bool:
    return any(course_dir.glob("*-pptVideo.mp4")) or any(course_dir.glob("pptVideo/**/*.mp4"))

def find_downloaded_course_dirs(download_root):
    """Return direct downloaded course directories under a workflow output root."""
    root = Path(download_root)
    ignored = {"logs", "__pycache__"}

    if not root.exists():
        return []
    if _has_direct_downloaded_video(root):
        return [root]

    course_dirs = []
    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        if child.name.startswith(".") or child.name in ignored:
            continue
        if _has_downloaded_video(child):
            course_dirs.append(child)
    return course_dirs


def run_summarize(course_dir: str, course_name: str = None):
    """对已下载的课程目录运行总结流水线"""
    from xdu_summarizer.pipeline import LecturePipeline
    from xdu_summarizer.config import Settings

    settings = Settings.from_env()
    pipeline = LecturePipeline(settings)

    logger.info(f"开始总结课程: {course_dir}")
    notes = pipeline.process_course(course_dir, course_name)

    if notes:
        logger.info(f"总结完成！共生成 {len(notes)} 篇笔记:")
        for n in notes:
            logger.info(f"  📄 {n}")
    else:
        logger.warning("未生成任何笔记")

    return notes


def main():
    from xdu_summarizer.config import load_env_file
    load_env_file()

    parser = argparse.ArgumentParser(
        description="XDU 网课自动下载 + 智能总结 — 一站式工作流",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 指定 liveId 下载并总结；无 cookies 时进入下载器认证向导
  python full_workflow.py --live-id 12345678 --cookies "_d=xxx; UID=xxx; vc3=xxx"

  # 对已下载的目录总结
  python full_workflow.py --course-dir ./下载的课程/计算机网络

  # 自动发现并下载课程，再总结下载结果
  python full_workflow.py --auto --uid 123456789 --video-type ppt
        """
    )

    parser.add_argument("--live-id", help="课程 liveId（用于下载）")
    parser.add_argument("--cookies", help="超星平台 cookies；省略则使用下载器认证向导或已保存认证")
    parser.add_argument("--ids-username", help="西电统一身份认证账号；密码从安全提示或 XDU_IDS_PASSWORD 读取")
    parser.add_argument(
        "--ids-reauth-channel",
        choices=["sms", "wechat", "email"],
        default="sms",
        help="IDS 二次认证通道（默认 sms）",
    )
    parser.add_argument("--course-dir", help="已下载的课程目录（直接总结，跳过下载）")
    parser.add_argument("--auto", action="store_true", help="自动模式：使用 Automation.py 发现课程并下载")
    parser.add_argument("--uid", help="超星 UID（自动模式扫描课程用）")
    parser.add_argument("--year", type=int, help="学年，例如 2025 表示 2025-2026 学年")
    parser.add_argument("--term", type=int, choices=[1, 2], help="学期：1=秋季，2=春季")
    parser.add_argument("--skip-weeks", default="", help="下载时跳过的周数，如 1-3,7")
    parser.add_argument("--no-merge", action="store_true", help="下载器不合并相邻节次视频")
    parser.add_argument("--downloader-dir", help="XDUClassVideoDownloader 缓存/安装目录")
    parser.add_argument("--update-downloader", action="store_true", help="重新下载/更新 XDUClassVideoDownloader")
    parser.add_argument("--debug-downloader", action="store_true", help="启用下载器 debug 日志")
    parser.add_argument("--video-type", choices=["both", "ppt", "teacher"], default="ppt",
                        help="下载的视频类型（默认 ppt，并同时获取可用字幕）")
    parser.add_argument("--output", default=None, help="笔记输出目录")
    parser.add_argument("--asr-engine", choices=["funasr", "whisper", "whisper-api"], default=None,
                        help="ASR 引擎（默认 funasr，中文课堂优先）")
    parser.add_argument("--funasr-model", default=None, help="FunASR 模型名")
    parser.add_argument("--funasr-device", default=None, help="FunASR 推理设备 auto/cpu/cuda:0")
    parser.add_argument("--funasr-punc-model", default=None, help="FunASR 标点模型，设为空字符串可禁用")
    parser.add_argument("--whisper-model", default=None,
                        help="Whisper 模型: tiny/base/small/medium/large（默认 base）")
    parser.add_argument("--llm-model", default=None, help="摘要 LLM 模型")
    parser.add_argument("--llm-api-key", help="外部 OpenAI-compatible API Key")
    parser.add_argument("--llm-base-url", help="外部 OpenAI-compatible Endpoint/Base URL")

    args = parser.parse_args()

    download_requested = bool(args.live_id or args.auto)
    ids_username = (args.ids_username or os.getenv("XDU_IDS_USERNAME")) if download_requested else None
    if args.cookies and ids_username:
        parser.error("--cookies 与 --ids-username 不能同时使用")
    ids_password = None
    if ids_username:
        ids_password = os.getenv("XDU_IDS_PASSWORD") or getpass.getpass("西电统一身份认证密码: ")

    def prompt_reauth_code(sent_to: str) -> str:
        destination = f"（{sent_to}）" if sent_to else ""
        return getpass.getpass(f"请输入 IDS 二次认证验证码{destination}: ")

    # 设置环境变量
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
    if args.llm_model:
        os.environ["LLM_MODEL"] = args.llm_model
    if args.llm_api_key:
        os.environ["LLM_API_KEY"] = args.llm_api_key
    if args.llm_base_url:
        os.environ["LLM_BASE_URL"] = args.llm_base_url
    if args.output:
        os.environ["OUTPUT_DIR"] = args.output

    # === 模式 1: 指定 liveId 下载 + 总结 ===
    if args.live_id:
        download_dir = Path("./xdu_downloads") / str(args.live_id)

        logger.info(f"开始下载课程: liveId={args.live_id}")
        result = run_live_download(
            live_id=args.live_id,
            output_dir=download_dir,
            cookies=args.cookies,
            ids_username=ids_username,
            ids_password=ids_password,
            ids_reauth_channel=args.ids_reauth_channel,
            reauth_code_provider=prompt_reauth_code,
            video_type=args.video_type,
            skip_weeks=args.skip_weeks,
            merge=not args.no_merge,
            downloader_dir=args.downloader_dir,
            update=args.update_downloader,
            debug=args.debug_downloader,
        )
        if result.returncode != 0:
            _log_download_failure(result)
            sys.exit(1)

        logger.info("下载完成")
        course_dirs = find_downloaded_course_dirs(download_dir)
        course_dir = course_dirs[0] if course_dirs else download_dir
        if not course_dirs:
            logger.warning(f"未识别到课程子目录，直接尝试总结: {download_dir}")
        run_summarize(str(course_dir), course_dir.name)

    # === 模式 2: 直接总结已下载的目录 ===
    elif args.course_dir:
        run_summarize(args.course_dir)

    # === 模式 3: 自动下载所有课程 + 总结 ===
    elif args.auto:
        download_dir = Path("./xdu_downloads/auto")

        logger.info("自动模式：运行 Automation.py 下载课程...")
        result = run_auto_download(
            output_dir=download_dir,
            uid=args.uid,
            year=args.year,
            term=args.term,
            cookies=args.cookies,
            ids_username=ids_username,
            ids_password=ids_password,
            ids_reauth_channel=args.ids_reauth_channel,
            reauth_code_provider=prompt_reauth_code,
            video_type=args.video_type,
            downloader_dir=args.downloader_dir,
            update=args.update_downloader,
            debug=args.debug_downloader,
        )
        if result.returncode != 0:
            _log_download_failure(result)
            sys.exit(1)

        logger.info("下载完成")
        course_dirs = find_downloaded_course_dirs(download_dir)
        if not course_dirs:
            logger.warning(f"自动下载完成，但未在 {download_dir} 下识别到课程视频目录")
        for course_dir in course_dirs:
            run_summarize(str(course_dir), course_dir.name)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
