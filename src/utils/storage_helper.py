"""S3存储工具 - 素材下载与临时文件清理"""

import os
import logging
import shutil
from typing import Optional
from coze_coding_dev_sdk.s3 import S3SyncStorage

logger = logging.getLogger(__name__)

# 全局单例
_storage: Optional[S3SyncStorage] = None


def _get_storage() -> S3SyncStorage:
    """获取S3SyncStorage单例"""
    global _storage
    if _storage is None:
        _storage = S3SyncStorage(
            endpoint_url=os.getenv("COZE_BUCKET_ENDPOINT_URL", ""),
            access_key="",
            secret_key="",
            bucket_name=os.getenv("COZE_BUCKET_NAME", ""),
            region="cn-beijing",
        )
    return _storage


def is_s3_key(file_path: str) -> bool:
    """判断是否为S3 file_key（非本地路径）"""
    return not file_path.startswith("/") and not file_path.startswith(".")


def ensure_local_path(file_key: str, cache_dir: str) -> str:
    """
    确保本地有文件的缓存副本。
    - 如果已是本地路径，直接返回
    - 如果是S3 key，下载到cache_dir后返回本地路径
    """
    if not is_s3_key(file_key):
        return file_key

    os.makedirs(cache_dir, exist_ok=True)
    local_path = os.path.join(cache_dir, os.path.basename(file_key))

    if os.path.isfile(local_path):
        logger.info("缓存命中: %s", local_path)
        return local_path

    logger.info("从S3下载: %s → %s", file_key, local_path)
    storage = _get_storage()
    storage.read_file(file_key=file_key, local_path=local_path)
    logger.info("下载完成: %s (%.1fMB)", local_path, os.path.getsize(local_path) / 1024 / 1024)
    return local_path


def download_s3_file(file_key: str, target_path: str) -> str:
    """
    下载S3文件到指定路径。返回目标路径。
    """
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    logger.info("从S3下载: %s → %s", file_key, target_path)
    storage = _get_storage()
    data = storage.read_file(file_key=file_key)
    with open(target_path, "wb") as f:
        f.write(data)
    return target_path


def cleanup_temp_dirs(*dirs: str) -> None:
    """清理临时目录"""
    for d in dirs:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
                logger.info("清理临时目录: %s", d)
            except Exception as e:
                logger.warning("清理临时目录失败 %s: %s", d, str(e))