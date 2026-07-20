"""
FFmpeg/FFprobe 路径解析与命令执行工具

统一处理 ffmpeg/ffprobe 二进制路径解析，支持：
1. 环境变量 FFMPEG_BINARY / FFPROBE_BINARY
2. 系统 PATH 中的 ffmpeg/ffprobe
3. imageio-ffmpeg 打包的 ffmpeg

禁止业务代码直接调用字符串 "ffmpeg" 或 "ffprobe"。
"""

import os
import shutil
import subprocess
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 缓存解析结果
_ffmpeg_path: Optional[str] = None
_ffprobe_path: Optional[str] = None
_ffmpeg_source: Optional[str] = None
_ffmpeg_version: Optional[str] = None


def get_ffmpeg_path() -> str:
    """
    解析 ffmpeg 可执行文件路径
    
    优先级：
    1. 环境变量 FFMPEG_BINARY
    2. 系统 PATH 中的 ffmpeg
    3. imageio-ffmpeg 打包的 ffmpeg
    
    Returns:
        ffmpeg 可执行文件的绝对路径
        
    Raises:
        RuntimeError: 无法找到可用的 ffmpeg
    """
    global _ffmpeg_path, _ffmpeg_source, _ffmpeg_version
    
    if _ffmpeg_path is not None:
        return _ffmpeg_path
    
    # 1. 环境变量
    env_path = os.environ.get("FFMPEG_BINARY", "").strip()
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        _ffmpeg_path = env_path
        _ffmpeg_source = "env"
        logger.info("[ffmpeg_utils] 使用环境变量 FFMPEG_BINARY: %s", _ffmpeg_path)
        _ffmpeg_version = _get_version(_ffmpeg_path)
        return _ffmpeg_path
    
    # 2. 系统 PATH
    system_path = shutil.which("ffmpeg")
    if system_path:
        _ffmpeg_path = system_path
        _ffmpeg_source = "system"
        logger.info("[ffmpeg_utils] 使用系统 PATH 中的 ffmpeg: %s", _ffmpeg_path)
        _ffmpeg_version = _get_version(_ffmpeg_path)
        return _ffmpeg_path
    
    # 3. imageio-ffmpeg
    try:
        import imageio_ffmpeg
        imageio_path = imageio_ffmpeg.get_ffmpeg_exe()
        if imageio_path and os.path.isfile(imageio_path) and os.access(imageio_path, os.X_OK):
            _ffmpeg_path = imageio_path
            _ffmpeg_source = "imageio-ffmpeg"
            logger.info("[ffmpeg_utils] 使用 imageio-ffmpeg 打包的 ffmpeg: %s", _ffmpeg_path)
            _ffmpeg_version = _get_version(_ffmpeg_path)
            return _ffmpeg_path
    except ImportError:
        logger.warning("[ffmpeg_utils] imageio-ffmpeg 未安装")
    except Exception as e:
        logger.warning("[ffmpeg_utils] imageio-ffmpeg 解析失败: %s", e)
    
    raise RuntimeError(
        "无法找到可用的 ffmpeg。请设置环境变量 FFMPEG_BINARY，"
        "安装系统 ffmpeg，或安装 imageio-ffmpeg Python 包。"
    )


def get_ffprobe_path() -> Optional[str]:
    """
    解析 ffprobe 可执行文件路径
    
    优先级：
    1. 环境变量 FFPROBE_BINARY
    2. 系统 PATH 中的 ffprobe
    3. 与 ffmpeg 同目录的 ffprobe（imageio-ffmpeg 可能不包含）
    
    Returns:
        ffprobe 可执行文件的绝对路径，如果无法找到则返回 None
    """
    global _ffprobe_path
    
    if _ffprobe_path is not None:
        return _ffprobe_path
    
    # 1. 环境变量
    env_path = os.environ.get("FFPROBE_BINARY", "").strip()
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        _ffprobe_path = env_path
        logger.info("[ffmpeg_utils] 使用环境变量 FFPROBE_BINARY: %s", _ffprobe_path)
        return _ffprobe_path
    
    # 2. 系统 PATH
    system_path = shutil.which("ffprobe")
    if system_path:
        _ffprobe_path = system_path
        logger.info("[ffmpeg_utils] 使用系统 PATH 中的 ffprobe: %s", _ffprobe_path)
        return _ffprobe_path
    
    # 3. 与 ffmpeg 同目录
    ffmpeg_path = get_ffmpeg_path()
    ffmpeg_dir = os.path.dirname(ffmpeg_path)
    same_dir_path = os.path.join(ffmpeg_dir, "ffprobe")
    if os.path.isfile(same_dir_path) and os.access(same_dir_path, os.X_OK):
        _ffprobe_path = same_dir_path
        logger.info("[ffmpeg_utils] 使用与 ffmpeg 同目录的 ffprobe: %s", _ffprobe_path)
        return _ffprobe_path
    
    logger.warning("[ffmpeg_utils] 无法找到 ffprobe，部分功能可能不可用")
    return None


def _get_version(ffmpeg_path: str) -> str:
    """获取 ffmpeg 版本信息"""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # 提取第一行版本信息
            first_line = result.stdout.split("\n")[0]
            return first_line.strip()
    except Exception as e:
        logger.warning("[ffmpeg_utils] 获取 ffmpeg 版本失败: %s", e)
    return "unknown"


def get_ffmpeg_info() -> dict:
    """
    获取 ffmpeg 诊断信息
    
    Returns:
        包含以下字段的字典：
        - ffmpeg_resolved_path: 解析后的 ffmpeg 路径
        - ffmpeg_exists: 文件是否存在
        - ffmpeg_version: 版本信息
        - ffmpeg_source: 来源 (env/system/imageio-ffmpeg)
        - ffprobe_resolved_path: 解析后的 ffprobe 路径（可能为 None）
        - ffprobe_exists: ffprobe 文件是否存在
    """
    try:
        ffmpeg_path = get_ffmpeg_path()
        ffmpeg_exists = os.path.isfile(ffmpeg_path)
    except RuntimeError:
        ffmpeg_path = None
        ffmpeg_exists = False
    
    ffprobe_path = get_ffprobe_path()
    ffprobe_exists = ffprobe_path is not None and os.path.isfile(ffprobe_path)
    
    return {
        "ffmpeg_resolved_path": ffmpeg_path,
        "ffmpeg_exists": ffmpeg_exists,
        "ffmpeg_version": _ffmpeg_version or "unknown",
        "ffmpeg_source": _ffmpeg_source or "not_found",
        "ffprobe_resolved_path": ffprobe_path,
        "ffprobe_exists": ffprobe_exists,
    }


def run_ffmpeg(cmd: list, timeout: int = 300) -> subprocess.CompletedProcess:
    """
    运行 ffmpeg 命令
    
    Args:
        cmd: 命令列表，第一个元素应为 "ffmpeg" 或完整路径
        timeout: 超时时间（秒）
        
    Returns:
        subprocess.CompletedProcess 对象
        
    Raises:
        RuntimeError: ffmpeg 执行失败
    """
    # 替换第一个元素为解析后的路径
    if cmd and cmd[0] == "ffmpeg":
        cmd[0] = get_ffmpeg_path()
    
    logger.debug("[ffmpeg_utils] 执行命令: %s", " ".join(cmd[:5]) + "..." if len(cmd) > 5 else " ".join(cmd))
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    
    if result.returncode != 0:
        stderr_preview = result.stderr[:500] if result.stderr else ""
        raise RuntimeError(f"ffmpeg 执行失败 (code={result.returncode}): {stderr_preview}")
    
    return result


def run_ffprobe(cmd: list, timeout: int = 30) -> Optional[subprocess.CompletedProcess]:
    """
    运行 ffprobe 命令
    
    Args:
        cmd: 命令列表，第一个元素应为 "ffprobe" 或完整路径
        timeout: 超时时间（秒）
        
    Returns:
        subprocess.CompletedProcess 对象，如果 ffprobe 不可用则返回 None
        
    Raises:
        RuntimeError: ffprobe 执行失败（非不可用）
    """
    ffprobe_path = get_ffprobe_path()
    if ffprobe_path is None:
        return None
    
    # 替换第一个元素为解析后的路径
    if cmd and cmd[0] == "ffprobe":
        cmd[0] = ffprobe_path
    
    logger.debug("[ffmpeg_utils] 执行命令: %s", " ".join(cmd))
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    
    if result.returncode != 0:
        stderr_preview = result.stderr[:500] if result.stderr else ""
        raise RuntimeError(f"ffprobe 执行失败 (code={result.returncode}): {stderr_preview}")
    
    return result


def get_media_duration(file_path: str) -> float:
    """
    获取媒体文件时长（秒）
    
    优先使用 ffprobe，回退到 Python wave 模块（仅支持 WAV）
    
    Args:
        file_path: 媒体文件路径
        
    Returns:
        时长（秒），如果无法获取则返回 0.0
    """
    # 尝试 ffprobe
    ffprobe_path = get_ffprobe_path()
    if ffprobe_path:
        try:
            result = run_ffprobe([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ])
            if result and result.stdout.strip():
                return float(result.stdout.strip())
        except Exception as e:
            logger.warning("[ffmpeg_utils] ffprobe 获取时长失败: %s", e)
    
    # 回退到 wave 模块（仅支持 WAV）
    if file_path.lower().endswith(".wav"):
        try:
            import wave
            with wave.open(file_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception as e:
            logger.warning("[ffmpeg_utils] wave 模块获取时长失败: %s", e)
    
    return 0.0
