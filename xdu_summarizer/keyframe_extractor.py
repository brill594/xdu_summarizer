"""
关键帧提取模块
用 OpenCV 检测场景变化，提取 pptVideo 中的幻灯片切换帧
"""
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def extract_keyframes(
    video_path: str,
    output_dir: str,
    threshold: float = 30.0,
    min_interval: float = 5.0,
    resize_width: int = 1280,
    skip_black_frames: bool = True,
) -> List[dict]:
    """
    从视频中提取关键帧（场景切换帧）

    算法：计算连续帧的直方图差异，差异超过 threshold 时视为场景切换。
    同时限制同一场景的最小间隔避免冗余。

    Args:
        video_path: 输入视频路径
        output_dir: 截图输出目录
        threshold: 场景切换阈值（帧间差异 > threshold 视为切换）
        min_interval: 同一场景最小间隔（秒），降噪用
        resize_width: 输出图片宽度（高度按比例缩放）
        skip_black_frames: 跳过纯黑帧（转场时的黑屏）

    Returns:
        [{"time": float, "path": str, "frame_index": int}, ...]
    """
    video_path = str(video_path)
    output_dir = str(output_dir)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0

    logger.info(
        f"关键帧提取: {Path(video_path).name} "
        f"({total_frames}帧, {fps:.2f}fps, {duration:.1f}s)"
    )

    keyframes = []
    last_keyframe_time = -min_interval  # 保证第一帧能被选中
    prev_hist = None
    frame_idx = 0
    saved_count = 0

    # 每帧检查太慢，跳帧采样提高速度
    sample_interval = max(1, int(fps / 2))  # 每秒 2 帧采样

    video_name = Path(video_path).stem
    # 清理文件名中的非法字符
    safe_name = "".join(c if c.isalnum() or c in " _-()（），。" else "_" for c in video_name)
    safe_name = safe_name[:80]

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # 跳帧采样
        if frame_idx % sample_interval != 0:
            frame_idx += 1
            continue

        current_time = frame_idx / fps

        # 跳过黑帧
        if skip_black_frames and _is_black_frame(frame):
            frame_idx += 1
            continue

        # 计算当前帧的 HSV 直方图
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

        if prev_hist is not None:
            # 用相关性比较检测场景切换
            diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)
            time_since_last = current_time - last_keyframe_time

            is_scene_change = diff > threshold and time_since_last >= min_interval

            if is_scene_change:
                out_path = _save_frame(
                    frame, output_dir, safe_name, current_time,
                    saved_count, resize_width,
                )
                keyframes.append({
                    "time": round(current_time, 2),
                    "path": out_path,
                    "frame_index": frame_idx,
                    "diff_score": round(diff, 1),
                })
                saved_count += 1
                last_keyframe_time = current_time
                logger.debug(f"  场景切换 @ {current_time:.1f}s (diff={diff:.1f})")

        prev_hist = hist
        frame_idx += 1

    cap.release()
    logger.info(f"关键帧提取完成: 共 {len(keyframes)} 帧")
    return keyframes


def _is_black_frame(frame: np.ndarray, black_threshold: int = 15, black_ratio: float = 0.85) -> bool:
    """判断是否为黑帧（转场黑屏）"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    dark_pixels = np.sum(gray < black_threshold)
    total_pixels = gray.shape[0] * gray.shape[1]
    return (dark_pixels / total_pixels) > black_ratio


def _save_frame(
    frame: np.ndarray,
    output_dir: str,
    video_name: str,
    time_sec: float,
    index: int,
    resize_width: int,
) -> str:
    """保存帧为图片文件"""
    # 按比例缩放
    h, w = frame.shape[:2]
    if resize_width and w > resize_width:
        ratio = resize_width / w
        new_size = (resize_width, int(h * ratio))
        frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

    # 时间戳格式：01m23s
    minutes = int(time_sec // 60)
    seconds = int(time_sec % 60)
    time_str = f"{minutes:02d}m{seconds:02d}s"

    filename = f"{video_name}_{time_str}_{index:04d}.jpg"
    out_path = str(Path(output_dir) / filename)
    cv2.imwrite(out_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out_path


def find_all_ppt_videos(course_dir: str) -> List[str]:
    """找到课程目录下所有 pptVideo"""
    from .audio_extractor import find_video_files
    return find_video_files(course_dir, "pptVideo")


def get_keyframe_cache_dir(video_path: str, base_cache: str = None) -> str:
    """计算关键帧缓存目录"""
    video = Path(video_path)
    if base_cache:
        base = Path(base_cache)
    else:
        base = video.parent.parent / "_keyframes"
    rel = video.relative_to(video.anchor) if video.is_absolute() else video
    return str(base / rel.with_suffix(""))
