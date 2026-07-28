"""
共享工具函数 - 8节点流水线
"""
import os
import subprocess
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def get_media_duration(file_path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def run_ffmpeg(cmd: List[str], timeout: int = 300) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg失败: {result.stderr[:500]}")


def generate_contact_sheet(video_path: str, output_path: str, cols: int = 5, rows: int = 4):
    """从视频中提取帧生成联系图"""
    dur = get_media_duration(video_path)
    if dur <= 0:
        return
    total = cols * rows
    interval = dur / total
    run_ffmpeg([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps=1/{interval:.3f},scale=320:-1,setpts=N/FRAME_RATE/TB,tile={cols}x{rows}",
        "-frames:v", "1",
        "-q:v", "2",
        output_path
    ], timeout=60)