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


def get_media_duration(file_path: str, material_record: dict = None) -> float:
    """获取媒体文件时长（秒）- 使用统一 ffmpeg_utils"""
    return _get_media_duration(file_path, material_record=material_record)


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


# ============================================================
# Safe download with .part file and verification
# ============================================================

def safe_download(url: str, dest_path: str, max_retries: int = 3, timeout: int = 120) -> str:
    """
    Download file safely with .part file and verification.
    
    1. Download to .part file first
    2. Verify HTTP status and Content-Length vs actual size
    3. Atomically rename on success
    4. Retry on failure
    
    Args:
        url: URL to download from
        dest_path: Final destination path
        max_retries: Maximum number of retry attempts
        timeout: Download timeout in seconds
        
    Returns:
        dest_path on success
        
    Raises:
        RuntimeError: If download fails after all retries
    """
    import urllib.request
    import urllib.error
    
    part_path = dest_path + ".part"
    dest_dir = os.path.dirname(dest_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            # Clean up any existing .part file
            if os.path.exists(part_path):
                os.remove(part_path)
            
            # Download to .part file
            logger.info("[Download] Attempt %d/%d: %s -> %s", attempt, max_retries, url[:80], part_path)
            
            req = urllib.request.Request(url, headers={
                "User-Agent": "Creade4070Workflow/1.0"
            })
            
            with urllib.request.urlopen(req, timeout=timeout) as response:
                # Check HTTP status
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status} for {url}")
                
                # Get expected size from Content-Length
                content_length = response.headers.get("Content-Length")
                expected_size = int(content_length) if content_length else None
                
                # Download content
                with open(part_path, "wb") as f:
                    downloaded = 0
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                
                # Verify actual size
                actual_size = os.path.getsize(part_path)
                
                if expected_size is not None and actual_size != expected_size:
                    raise RuntimeError(
                        f"Size mismatch: expected {expected_size}, got {actual_size}"
                    )
                
                if actual_size == 0:
                    raise RuntimeError("Downloaded file is empty (0 bytes)")
                
                # Atomic rename
                os.replace(part_path, dest_path)
                logger.info("[Download] Success: %s (%d bytes)", dest_path, actual_size)
                return dest_path
                
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError) as e:
            last_error = e
            logger.warning("[Download] Attempt %d failed: %s", attempt, e)
            # Clean up .part file on failure
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
    
    raise RuntimeError(f"Download failed after {max_retries} attempts: {last_error}")


# ============================================================
# Video validation with ffprobe
# ============================================================

def validate_video_file(file_path: str, min_duration: float = 0.1, 
                        min_width: int = 100, min_height: int = 100) -> Dict[str, Any]:
    """
    Validate video file using ffprobe.
    
    Checks:
    - File exists and is readable
    - Contains at least one valid video stream
    - Duration >= min_duration
    - Width >= min_width, Height >= min_height
    - Has valid codec
    
    Args:
        file_path: Path to video file
        min_duration: Minimum duration in seconds
        min_width: Minimum width in pixels
        min_height: Minimum height in pixels
        
    Returns:
        Dict with validation result:
        {
            "valid": bool,
            "error": str or None,
            "duration": float,
            "width": int,
            "height": int,
            "codec": str,
            "file_size": int
        }
    """
    result = {
        "valid": False,
        "error": None,
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "codec": "",
        "file_size": 0,
    }
    
    # Check file exists
    if not os.path.exists(file_path):
        result["error"] = f"File not found: {file_path}"
        return result
    
    # Check file size
    try:
        result["file_size"] = os.path.getsize(file_path)
    except OSError as e:
        result["error"] = f"Cannot stat file: {e}"
        return result
    
    if result["file_size"] == 0:
        result["error"] = "File is empty (0 bytes)"
        return result
    
    # Run ffprobe to get video info
    try:
        ffprobe_path = get_ffprobe_path()
        probe_cmd = [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,codec_name,duration:format=duration",
            "-of", "json",
            file_path
        ]
        
        import subprocess
        proc = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if proc.returncode != 0:
            result["error"] = f"ffprobe failed: {proc.stderr[:500]}"
            return result
        
        # Parse JSON output
        probe_data = json.loads(proc.stdout)
        
        # Check for video streams
        streams = probe_data.get("streams", [])
        if not streams:
            result["error"] = "No video streams found"
            return result
        
        video_stream = streams[0]
        
        # Extract info
        result["width"] = int(video_stream.get("width", 0))
        result["height"] = int(video_stream.get("height", 0))
        result["codec"] = video_stream.get("codec_name", "")
        
        # Duration from stream or format
        duration = video_stream.get("duration")
        if not duration:
            format_data = probe_data.get("format", {})
            duration = format_data.get("duration")
        
        if duration:
            result["duration"] = float(duration)
        
        # Validate
        if result["width"] < min_width:
            result["error"] = f"Width too small: {result['width']} < {min_width}"
            return result
        
        if result["height"] < min_height:
            result["error"] = f"Height too small: {result['height']} < {min_height}"
            return result
        
        if result["duration"] < min_duration:
            result["error"] = f"Duration too short: {result['duration']} < {min_duration}"
            return result
        
        if not result["codec"]:
            result["error"] = "No valid codec found"
            return result
        
        # All checks passed
        result["valid"] = True
        return result
        
    except subprocess.TimeoutExpired:
        result["error"] = "ffprobe timeout"
        return result
    except json.JSONDecodeError as e:
        result["error"] = f"ffprobe JSON parse error: {e}"
        return result
    except Exception as e:
        result["error"] = f"Validation error: {e}"
        return result

