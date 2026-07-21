"""
媒体文件上传工具
将本地文件上传到对象存储（S3），返回可公开访问的URL
"""
import os
import logging
from typing import Optional

from storage.s3.s3_storage import S3SyncStorage

logger = logging.getLogger(__name__)

# 全局单例
_storage: Optional[S3SyncStorage] = None


def _get_storage() -> S3SyncStorage:
    """获取 S3 存储客户端（单例）"""
    global _storage
    if _storage is None:
        _storage = S3SyncStorage(
            endpoint_url="",
            access_key="",
            secret_key="",
            bucket_name="",
            region="cn-beijing",
        )
    return _storage


def upload_local_file(local_path: str, content_type: str = "application/octet-stream") -> str:
    """
    上传本地文件到对象存储，返回可公开访问的 URL。

    Args:
        local_path: 本地文件绝对路径
        content_type: MIME类型，如 image/jpeg, audio/mp3, video/mp4

    Returns:
        可公开访问的 URL

    Raises:
        FileNotFoundError: 本地文件不存在
        RuntimeError: 上传失败
    """
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"本地文件不存在: {local_path}")

    storage = _get_storage()

    # 从路径提取文件名用于 S3 对象命名
    file_name = os.path.basename(local_path)

    with open(local_path, "rb") as f:
        file_content = f.read()

    try:
        object_key = storage.upload_file(
            file_content=file_content,
            file_name=file_name,
            content_type=content_type,
        )
        logger.info("文件上传成功，object_key=%s", object_key)
    except Exception as e:
        raise RuntimeError(f"上传到对象存储失败: {e}") from e

    # 生成签名 URL（可公开访问）
    try:
        url = storage.generate_presigned_url(key=object_key, expire_time=86400)  # 24小时有效期
        logger.info("生成签名URL成功")
        # 清理可能的 Markdown 格式
        url = _clean_url(url)
        return url
    except Exception as e:
        raise RuntimeError(f"生成签名URL失败: {e}") from e


def _clean_url(url: str) -> str:
    """
    清理 URL，提取纯 URL。
    如果 URL 是 Markdown 格式 [text](url)，提取括号中的 URL。
    """
    if not url:
        return url
    # 检查是否是 Markdown 格式 [text](url)
    import re
    match = re.match(r'^\[([^\]]*)\]\(([^)]+)\)$', url.strip())
    if match:
        return match.group(2)
    return url


def upload_bytes(data: bytes, file_name: str, content_type: str = "application/octet-stream") -> str:
    """
    上传二进制数据到对象存储，返回可公开访问的 URL。

    Args:
        data: 文件二进制数据
        file_name: 文件名（用于S3对象命名）
        content_type: MIME类型

    Returns:
        可公开访问的 URL
    """
    if not data:
        raise ValueError("上传数据为空")

    storage = _get_storage()

    try:
        object_key = storage.upload_file(
            file_content=data,
            file_name=file_name,
            content_type=content_type,
        )
        logger.info("数据上传成功，object_key=%s", object_key)
    except Exception as e:
        raise RuntimeError(f"上传到对象存储失败: {e}") from e

    try:
        url = storage.generate_presigned_url(key=object_key, expire_time=86400)
        logger.info("生成签名URL成功")
        # 清理可能的 Markdown 格式
        url = _clean_url(url)
        return url
    except Exception as e:
        raise RuntimeError(f"生成签名URL失败: {e}") from e