"""
音频提取模块
用 ffmpeg 从视频中提取 16kHz 单声道 WAV 音频
"""
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_audio(
    video_path: str,
    output_path: str,
    sample_rate: int = 16000,
    channels: int = 1,
    ffmpeg_bin: str = "ffmpeg",
) -> str:
    """
    从视频中提取音频

    Args:
        video_path: 输入视频路径
        output_path: 输出音频路径 (.wav)
        sample_rate: 采样率 (Hz)，Whisper 推荐 16000
        channels: 声道数，1=单声道
        ffmpeg_bin: ffmpeg 可执行文件路径

    Returns:
        输出音频的绝对路径
    """
    video_path = str(video_path)
    output_path = str(output_path)

    # 确保输出目录存在
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_bin,
        "-i", video_path,
        "-vn",                    # 去掉视频流
        "-acodec", "pcm_s16le",   # PCM 16-bit 编码
        "-ar", str(sample_rate),  # 采样率
        "-ac", str(channels),     # 声道
        "-y",                     # 覆盖输出
        output_path,
    ]

    logger.info(f"提取音频: {video_path} → {output_path}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 提取音频失败 (code={result.returncode}):\n"
            f"stderr: {result.stderr[:500]}"
        )

    logger.info(f"音频提取完成: {output_path}")
    return output_path


def find_video_files(course_dir: str, video_type: str = "pptVideo") -> list:
    """
    在课程目录中查找指定类型的视频文件

    Args:
        course_dir: 课程根目录
        video_type: "pptVideo" 或 "teacherTrack"

    Returns:
        视频文件路径列表，按文件名排序
    """
    course_path = Path(course_dir)
    layout_pattern = f"**/{video_type}/**/*.mp4"
    suffix_pattern = f"**/*-{video_type}.mp4"

    files = sorted({*course_path.glob(layout_pattern), *course_path.glob(suffix_pattern)})
    logger.info(f"在 {course_dir} 中找到 {len(files)} 个 {video_type} 文件")
    return [str(f) for f in files]


def get_audio_cache_path(video_path: str, cache_dir: str = None) -> str:
    """计算缓存音频路径"""
    video = Path(video_path)
    if cache_dir:
        base = Path(cache_dir)
    else:
        base = video.parent.parent / "_audio_cache"
    rel = video.relative_to(video.anchor) if video.is_absolute() else video
    out = base / rel.with_suffix(".wav")
    return str(out)
