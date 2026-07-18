"""
火山引擎 TOS 素材存储客户端

提供对外部 TOS 存储桶的访问能力：
- HEAD 对象检查
- 生成 GET 预签名 URL
- 统一的素材 URL 解析（按优先级）

所有认证参数从环境变量读取，禁止硬编码或写入日志。
"""

import os
import logging
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 环境变量名
ENV_TOS_ACCESS_KEY = "TOS_ACCESS_KEY"
ENV_TOS_SECRET_KEY = "TOS_SECRET_KEY"
ENV_TOS_ENDPOINT = "TOS_ENDPOINT"
ENV_TOS_REGION = "TOS_REGION"
ENV_TOS_BUCKET = "TOS_BUCKET"

# 预签名 URL 默认有效期（秒）
PRESIGNED_URL_EXPIRES = 1800

# 素材对象必须位于此前缀内
MATERIAL_PREFIX = "materials_v2/"


@dataclass
class TosConfig:
    """TOS 配置（不包含敏感值的摘要）"""
    endpoint: str
    region: str
    bucket: str
    has_access_key: bool
    has_secret_key: bool

    @property
    def is_complete(self) -> bool:
        return self.has_access_key and self.has_secret_key and self.endpoint and self.region and self.bucket


class TosConfigError(Exception):
    """TOS 配置缺失错误（不包含敏感值）"""
    pass


class TosClientError(Exception):
    """TOS 客户端错误"""
    pass


def get_tos_config() -> TosConfig:
    """
    从环境变量获取 TOS 配置。
    
    Returns:
        TosConfig: 配置对象
        
    Raises:
        TosConfigError: 如果必需的环境变量缺失
    """
    access_key = os.environ.get(ENV_TOS_ACCESS_KEY, "")
    secret_key = os.environ.get(ENV_TOS_SECRET_KEY, "")
    endpoint = os.environ.get(ENV_TOS_ENDPOINT, "")
    region = os.environ.get(ENV_TOS_REGION, "")
    bucket = os.environ.get(ENV_TOS_BUCKET, "")

    missing = []
    if not access_key:
        missing.append(ENV_TOS_ACCESS_KEY)
    if not secret_key:
        missing.append(ENV_TOS_SECRET_KEY)
    if not endpoint:
        missing.append(ENV_TOS_ENDPOINT)
    if not region:
        missing.append(ENV_TOS_REGION)
    if not bucket:
        missing.append(ENV_TOS_BUCKET)

    if missing:
        raise TosConfigError(f"TOS 环境变量缺失: {', '.join(missing)}")

    return TosConfig(
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        has_access_key=bool(access_key),
        has_secret_key=bool(secret_key),
    )


def check_env_configured() -> Dict[str, bool]:
    """
    检查环境变量是否已配置（不返回值）。
    
    Returns:
        Dict[str, bool]: 各变量的存在性状态
    """
    return {
        ENV_TOS_ACCESS_KEY: bool(os.environ.get(ENV_TOS_ACCESS_KEY, "")),
        ENV_TOS_SECRET_KEY: bool(os.environ.get(ENV_TOS_SECRET_KEY, "")),
        ENV_TOS_ENDPOINT: bool(os.environ.get(ENV_TOS_ENDPOINT, "")),
        ENV_TOS_REGION: bool(os.environ.get(ENV_TOS_REGION, "")),
        ENV_TOS_BUCKET: bool(os.environ.get(ENV_TOS_BUCKET, "")),
    }


def is_env_configured() -> bool:
    """检查所有必需的环境变量是否已配置。"""
    return all(check_env_configured().values())


class TosMaterialClient:
    """
    TOS 素材存储客户端
    
    提供对素材对象的 HEAD 检查和预签名 URL 生成。
    """

    def __init__(self, config: Optional[TosConfig] = None):
        """
        初始化客户端。
        
        Args:
            config: TOS 配置，为 None 时从环境变量读取
        """
        self._config = config or get_tos_config()
        self._client = None

    def _get_client(self):
        """延迟初始化 TOS 客户端。"""
        if self._client is None:
            try:
                import tos
            except ImportError as e:
                raise TosClientError(f"火山引擎 TOS SDK 未安装: {e}")

            access_key = os.environ.get(ENV_TOS_ACCESS_KEY, "").strip()
            secret_key = os.environ.get(ENV_TOS_SECRET_KEY, "").strip()

            if not access_key or not secret_key:
                raise TosConfigError("TOS 认证凭据缺失")

            # 安全校验：仅记录布尔值，不记录原值
            import re as _re
            ak_clean = bool(access_key)
            sk_clean = bool(secret_key)
            ak_has_invalid_char = bool(_re.search(r'[\s=/]', access_key))
            logger.info(
                "TOS 凭据校验: ak_present=%s, sk_present=%s, ak_has_invalid_chars=%s",
                ak_clean, sk_clean, ak_has_invalid_char,
            )
            if ak_has_invalid_char:
                logger.warning("TOS AK 含空白/换行/等号/斜杠，可能导致签名格式错误")

            self._client = tos.TosClientV2(
                ak=access_key,
                sk=secret_key,
                endpoint=self._config.endpoint,
                region=self._config.region,
            )
        return self._client

    @property
    def bucket(self) -> str:
        return self._config.bucket

    @property
    def config(self) -> TosConfig:
        return self._config

    def head_object(self, bucket: str, object_key: str) -> Dict[str, Any]:
        """
        检查对象是否存在并返回元数据。
        
        Args:
            bucket: 桶名
            object_key: 对象键
            
        Returns:
            Dict 包含 exists, content_length, content_type, error_type
            
        Raises:
            不会抛出异常，错误通过返回的 dict 表示
        """
        result = {
            "exists": False,
            "content_length": 0,
            "content_type": "",
            "error_type": "",
        }

        try:
            client = self._get_client()
            head = client.head_object(bucket=bucket, key=object_key)
            result["exists"] = True
            result["content_length"] = head.content_length
            result["content_type"] = head.content_type or ""
        except Exception as e:
            err_str = str(e)
            # 提取错误类型，不包含敏感信息
            if "NoSuchKey" in err_str or "404" in err_str:
                result["error_type"] = "not_found"
            elif "AccessDenied" in err_str or "403" in err_str:
                result["error_type"] = "access_denied"
            elif "NoSuchBucket" in err_str:
                result["error_type"] = "bucket_not_found"
            elif "Timeout" in err_str or "timeout" in err_str:
                result["error_type"] = "timeout"
            elif "Connection" in err_str or "connection" in err_str:
                result["error_type"] = "connection_error"
            else:
                result["error_type"] = "unknown"
            logger.debug(f"TOS HEAD 失败: bucket={bucket}, key={object_key}, error_type={result['error_type']}")

        return result

    def generate_presigned_url(
        self,
        bucket: str,
        object_key: str,
        expires: int = PRESIGNED_URL_EXPIRES,
    ) -> str:
        """
        生成 GET 预签名 URL。
        
        Args:
            bucket: 桶名
            object_key: 对象键
            expires: 有效期（秒），默认 1800
            
        Returns:
            预签名 URL 字符串
            
        Raises:
            TosClientError: 如果生成失败
        """
        try:
            from tos.enum import HttpMethodType
            client = self._get_client()
            url = client.pre_signed_url(
                http_method=HttpMethodType.Http_Method_Get,
                bucket=bucket,
                key=object_key,
                expires=expires,
            )
            return url.signed_url
        except Exception as e:
            raise TosClientError(f"生成预签名 URL 失败: {type(e).__name__}")

    def get_material_url(
        self,
        source_url: str = "",
        s3_url: str = "",
        bucket: str = "",
        object_key: str = "",
        local_path: str = "",
    ) -> Tuple[str, str]:
        """
        按优先级解析素材 URL。
        
        优先级: source_url > s3_url > TOS 预签名 URL > local_path
        
        Args:
            source_url: 直接可用的源 URL
            s3_url: S3/TOS URL
            bucket: TOS 桶名（为空时使用默认桶）
            object_key: 对象键
            local_path: 本地文件路径
            
        Returns:
            (url, url_type) 元组
            url_type: "source_url" | "s3_url" | "tos_presigned" | "local_path" | ""
        """
        # 1. source_url
        if source_url and source_url.strip():
            return source_url.strip(), "source_url"

        # 2. s3_url
        if s3_url and s3_url.strip():
            return s3_url.strip(), "s3_url"

        # 3. TOS bucket + object_key -> 运行时预签名
        effective_bucket = bucket.strip() if bucket else self._config.bucket
        if object_key and object_key.strip():
            key = object_key.strip()
            # 安全检查：确保 object_key 在 materials_v2/ 前缀内
            if not key.startswith(MATERIAL_PREFIX):
                logger.warning(f"object_key 不在 {MATERIAL_PREFIX} 前缀内: {key}")
                return "", ""
            try:
                url = self.generate_presigned_url(effective_bucket, key)
                return url, "tos_presigned"
            except TosClientError as e:
                logger.warning(f"TOS 预签名 URL 生成失败: {e}")
                # 继续尝试 local_path

        # 4. local_path
        if local_path and local_path.strip():
            return local_path.strip(), "local_path"

        return "", ""


def validate_object_key(object_key: str) -> bool:
    """
    验证 object_key 是否在允许的素材前缀内。
    
    Args:
        object_key: 对象键
        
    Returns:
        True 如果合法，False 否则
    """
    if not object_key or not object_key.strip():
        return False
    return object_key.strip().startswith(MATERIAL_PREFIX)


# 全局客户端实例（延迟初始化）
_client: Optional[TosMaterialClient] = None


def get_client() -> Optional[TosMaterialClient]:
    """
    获取全局 TOS 客户端实例。
    
    Returns:
        TosMaterialClient 或 None（如果环境变量未配置）
    """
    global _client
    if _client is None:
        if not is_env_configured():
            return None
        try:
            _client = TosMaterialClient()
        except TosConfigError:
            return None
    return _client


def resolve_material_url(
    source_url: str = "",
    s3_url: str = "",
    bucket: str = "",
    object_key: str = "",
    local_path: str = "",
) -> Tuple[str, str]:
    """
    模块级 URL 解析函数，供各节点统一调用。
    
    如果 TOS 客户端不可用，跳过 TOS 预签名步骤。
    
    Returns:
        (url, url_type) 元组
    """
    # 1. source_url
    if source_url and source_url.strip():
        return source_url.strip(), "source_url"

    # 2. s3_url
    if s3_url and s3_url.strip():
        return s3_url.strip(), "s3_url"

    # 3. TOS presigned
    client = get_client()
    effective_bucket = bucket.strip() if bucket else (client.bucket if client else "")
    if object_key and object_key.strip() and validate_object_key(object_key):
        if client is not None:
            try:
                url = client.generate_presigned_url(effective_bucket, object_key.strip())
                return url, "tos_presigned"
            except TosClientError:
                pass

    # 4. local_path
    if local_path and local_path.strip():
        return local_path.strip(), "local_path"

    return "", ""
