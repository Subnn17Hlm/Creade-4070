"""
共享工具函数 - 8节点流水线
"""
import os
import json
import tempfile
import logging
from typing import List, Dict, Any

from utils.ffmpeg_utils import (
    get_ffmpeg_path,
    get_ffprobe_path,
    run_ffmpeg as _run_ffmpeg,
    run_ffprobe,
    get_media_duration as _get_media_duration,
)

logger = logging.getLogger(__name__)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def atomic_json_write(path: str, data: Any) -> None:
    """Write UTF-8 JSON atomically and verify the serialized file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".json-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        with open(temp_path, "r", encoding="utf-8") as handle:
            json.load(handle)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def get_media_duration(file_path: str) -> float:
    """获取媒体文件时长（秒）- 使用统一 ffmpeg_utils"""
    return _get_media_duration(file_path)


def run_ffmpeg(cmd: List[str], timeout: int = 300) -> None:
    """运行 ffmpeg 命令 - 使用统一 ffmpeg_utils"""
    _run_ffmpeg(cmd, timeout=timeout)


def generate_contact_sheet(video_path: str, output_path: str, cols: int = 5, rows: int = 4):
    """从视频中提取帧生成联系图"""
    dur = get_media_duration(video_path)
    if dur <= 0:
        return
    total = cols * rows
    interval = dur / total
    run_ffmpeg([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps=1/{interval:.3f},scale=320:-1,setpts=N/Frame_RATE/TB,tile={cols}x{rows}",
        "-frames:v", "1",
        "-q:v", "2",
        output_path
    ], timeout=60)
