import os
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as async_sessionmaker_cls
import logging
logger = logging.getLogger(__name__)

MAX_RETRY_TIME = 20  # 连接最大重试时间（秒）
# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def get_db_url() -> str:
    """Build database URL from environment. Returns empty string if not configured."""
    url = os.getenv("PGDATABASE_URL") or ""
    if url is not None and url != "":
        print(f"[DB] Got PGDATABASE_URL from environment (length: {len(url)})", flush=True)
        return url
    # Try to load from workload identity
    try:
        from coze_workload_identity import Client
        client = Client()
        env_vars = client.get_project_env_vars()
        client.close()
        for env_var in env_vars:
            if env_var.key == "PGDATABASE_URL":
                url = env_var.value.replace("'", "'\\''")
                print(f"[DB] Got PGDATABASE_URL from workload identity (length: {len(url)})", flush=True)
                return url
    except Exception as e:
        print(f"[DB] Failed to get PGDATABASE_URL from workload identity: {e}", flush=True)
    print("[DB] ERROR: PGDATABASE_URL not found in environment or workload identity", flush=True)
    return ""
_engine = None
_SessionLocal = None

def _create_engine_with_retry():
    url = get_db_url()
    if url is None or url == "":
        logger.error("PGDATABASE_URL is not set")
        raise ValueError("PGDATABASE_URL is not set")
    size = 100
    overflow = 100
    recycle = 1800
    timeout = 30
    engine = create_engine(
        url,
        pool_size=size,
        max_overflow=overflow,
        pool_pre_ping=True,
        pool_recycle=recycle,
        pool_timeout=timeout,
    )
    # 验证连接，带重试
    start_time = time.time()
    last_error = None
    while time.time() - start_time < MAX_RETRY_TIME:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except OperationalError as e:
            last_error = e
            elapsed = time.time() - start_time
            logger.warning(f"Database connection failed, retrying... (elapsed: {elapsed:.1f}s)")
            time.sleep(min(1, MAX_RETRY_TIME - elapsed))
    logger.error(f"Database connection failed after {MAX_RETRY_TIME}s: {last_error}")
    raise last_error  # pyright: ignore [reportGeneralTypeIssues]

def get_engine():
    global _engine
    if _engine is None:
        _engine = _create_engine_with_retry()
    return _engine

def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal

def get_session():
    return get_sessionmaker()()

# Async engine and session for batch operations
_async_engine = None
_AsyncSessionLocal = None

def get_async_db_url() -> str:
    """Convert sync DB URL to async URL for PostgreSQL."""
    sync_url = get_db_url()
    print(f"[DB-ASYNC] get_db_url() returned (length: {len(sync_url)})", flush=True)
    if not sync_url:
        print("[DB-ASYNC] ERROR: sync_url is empty", flush=True)
        return ""
    
    # Convert postgresql:// to postgresql+asyncpg://
    if sync_url.startswith("postgresql://"):
        async_url = sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        print(f"[DB-ASYNC] Converted postgresql:// to postgresql+asyncpg:// (length: {len(async_url)})", flush=True)
    elif sync_url.startswith("postgres://"):
        async_url = sync_url.replace("postgres://", "postgresql+asyncpg://", 1)
        print(f"[DB-ASYNC] Converted postgres:// to postgresql+asyncpg:// (length: {len(async_url)})", flush=True)
    elif sync_url.startswith("postgresql+asyncpg://"):
        # Already async URL
        async_url = sync_url
        print(f"[DB-ASYNC] URL already in asyncpg format (length: {len(async_url)})", flush=True)
    else:
        # Unknown format, log and try to use as-is
        print(f"[DB-ASYNC] WARNING: Unknown database URL format: {sync_url[:50]}... (length: {len(sync_url)})", flush=True)
        async_url = sync_url
    
    return async_url

def get_async_engine():
    global _async_engine
    if _async_engine is None:
        url = get_async_db_url()
        if not url:
            raise ValueError("PGDATABASE_URL is not set")
        _async_engine = create_async_engine(
            url,
            pool_size=50,
            max_overflow=50,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
        )
    return _async_engine

def get_async_sessionmaker():
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker_cls(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _AsyncSessionLocal

async def get_db_session():
    """FastAPI dependency for async database sessions."""
    async with get_async_sessionmaker()() as session:
        yield session

__all__ = [
    "get_db_url",
    "get_engine",
    "get_sessionmaker",
    "get_session",
    "get_async_engine",
    "get_async_sessionmaker",
    "get_db_session",
]
