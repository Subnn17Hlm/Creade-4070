"""
最小化 TOS 健康检查预览服务

仅用于预览环境，不依赖完整工作流模块。
"""
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse


# 最小化 TOS 客户端实现（不依赖 coze_coding_dev_sdk）
class MinimalTOSClient:
    """最小化 TOS 客户端，仅用于健康检查"""
    
    def __init__(self, endpoint: str, region: str, access_key: str, secret_key: str):
        self.endpoint = endpoint
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self._client = None
    
    def _get_client(self):
        if self._client is None:
            import tos
            from tos import Credentials
            cred = Credentials(self.access_key, self.secret_key, self.region)
            self._client = tos.TosClientV2(
                endpoint=self.endpoint,
                region=self.region,
                credentials=cred
            )
        return self._client
    
    def head_object(self, bucket: str, key: str):
        """检查对象是否存在"""
        client = self._get_client()
        return client.head_object(bucket=bucket, key=key)
    
    def get_presigned_url(self, bucket: str, key: str, expires: int = 1800) -> str:
        """生成预签名 URL"""
        client = self._get_client()
        return client.pre_signed_url(
            method="GET",
            bucket=bucket,
            key=key,
            expires=expires
        )


def check_tos_health() -> dict:
    """
    检查 TOS 连接健康状态
    
    Returns:
        dict: 包含 env_configured, head_ok, range_ok, error_type
    """
    result = {
        "env_configured": False,
        "head_ok": False,
        "range_ok": False,
        "error_type": None
    }
    
    # 1. 检查环境变量
    required_vars = [
        "TOS_ACCESS_KEY",
        "TOS_SECRET_KEY",
        "TOS_ENDPOINT",
        "TOS_REGION",
        "TOS_BUCKET"
    ]
    
    missing_vars = [v for v in required_vars if not os.environ.get(v)]
    if missing_vars:
        result["error_type"] = "missing_env_vars"
        return result
    
    result["env_configured"] = True
    
    # 2. 初始化客户端
    try:
        client = MinimalTOSClient(
            endpoint=os.environ["TOS_ENDPOINT"],
            region=os.environ["TOS_REGION"],
            access_key=os.environ["TOS_ACCESS_KEY"],
            secret_key=os.environ["TOS_SECRET_KEY"]
        )
    except Exception as e:
        result["error_type"] = "client_init_failed"
        return result
    
    # 3. 获取测试对象
    bucket = os.environ["TOS_BUCKET"]
    # 使用一个已知存在的对象进行测试（如果存在）
    test_key = "materials_v2/test_object.txt"
    
    # 4. HEAD 检查
    try:
        client.head_object(bucket=bucket, key=test_key)
        result["head_ok"] = True
    except Exception as e:
        error_str = str(e).lower()
        if "nosuchkey" in error_str or "notfound" in error_str:
            result["error_type"] = "object_not_found"
        elif "accessdenied" in error_str or "forbidden" in error_str:
            result["error_type"] = "permission_denied"
        else:
            result["error_type"] = "head_failed"
        return result
    
    # 5. Range 读取测试
    try:
        presigned_url = client.get_presigned_url(bucket=bucket, key=test_key, expires=60)
        
        import requests
        response = requests.get(
            presigned_url,
            headers={"Range": "bytes=0-1023"},
            timeout=10
        )
        
        if response.status_code in (200, 206):
            result["range_ok"] = True
        else:
            result["error_type"] = f"range_status_{response.status_code}"
    except Exception as e:
        result["error_type"] = "range_request_failed"
    
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    print("[preview] TOS health check service started")
    yield
    print("[preview] TOS health check service stopped")


# 创建 FastAPI 应用
app = FastAPI(
    title="TOS Health Check Preview",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/internal/tos-health")
async def tos_health():
    """TOS 健康检查端点"""
    result = check_tos_health()
    return JSONResponse(content=result)


@app.get("/")
async def root():
    """根路径 - 服务信息"""
    return {
        "service": "TOS Health Check Preview",
        "version": "1.0.0",
        "endpoints": ["/internal/tos-health", "/health"]
    }


@app.get("/health")
async def health():
    """基础健康检查"""
    return {"status": "ok", "service": "tos-health-preview"}


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", "5000"))
    
    print(f"[preview] Starting TOS health check service on 0.0.0.0:{port}")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
