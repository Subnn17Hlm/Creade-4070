"""
Node0a: 文案来源路由
======================
根据 script_source 决定路由方向：
  generated → 进入"生成文案"节点
  manual    → 进入"手动文案"节点
同时创建运行目录并传递所有输入字段。
"""
import os
import glob
import hashlib
import logging
import tempfile
from typing import List, Dict, Any, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import ScriptSourceRouterInput, ScriptSourceRouterOutput
from graphs.shared_utils import ensure_dir

logger = logging.getLogger(__name__)

WORKSPACE = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
RUNS_BASE = os.path.join(tempfile.gettempdir(), "runs")
BGM_DIR = os.path.join(WORKSPACE, "assets", "bgm")


def _parse_bgm_tos_prefix() -> Tuple[str, str]:
    """
    解析 BGM_TOS_PREFIX 环境变量。
    格式: tos://bucket/prefix
    
    Returns:
        (bucket, prefix) 元组，解析失败返回 ("", "")
    """
    bgm_tos_prefix = os.getenv("BGM_TOS_PREFIX", "")
    if not bgm_tos_prefix:
        return "", ""
    
    # 解析 tos://bucket/prefix 格式
    if not bgm_tos_prefix.startswith("tos://"):
        logger.warning(f"BGM_TOS_PREFIX 格式错误，必须以 tos:// 开头: {bgm_tos_prefix}")
        return "", ""
    
    # 移除 tos:// 前缀
    remainder = bgm_tos_prefix[6:]
    
    # 分割 bucket 和 prefix
    slash_index = remainder.find("/")
    if slash_index == -1:
        # 只有 bucket，没有 prefix
        bucket = remainder
        prefix = ""
    else:
        bucket = remainder[:slash_index]
        prefix = remainder[slash_index + 1:]
    
    if not bucket:
        logger.warning(f"BGM_TOS_PREFIX 缺少 bucket: {bgm_tos_prefix}")
        return "", ""
    
    logger.info(f"解析 BGM_TOS_PREFIX: bucket={bucket}, prefix={prefix}")
    return bucket, prefix


def _list_bgm_from_tos(bucket: str, prefix: str) -> List[str]:
    """
    从 TOS 列举 BGM 对象。
    
    Args:
        bucket: TOS 桶名
        prefix: 对象前缀
        
    Returns:
        对象键列表（已过滤和排序）
    """
    try:
        from storage.tos.tos_client import get_client
        
        client = get_client()
        if client is None:
            logger.warning("TOS 客户端未配置，无法从 TOS 列举 BGM")
            return []
        
        # 列举对象
        objects = client.list_objects(bucket=bucket, prefix=prefix, max_keys=1000)
        
        # 过滤：只保留 .mp3 文件，大小 > 0，非目录占位
        valid_keys = []
        for obj in objects:
            key = obj.get("key", "")
            size = obj.get("size", 0)
            
            # 检查是否是 .mp3 文件
            if not key.lower().endswith(".mp3"):
                continue
            
            # 检查大小 > 0
            if size <= 0:
                continue
            
            # 检查是否是目录占位（以 / 结尾）
            if key.endswith("/"):
                continue
            
            valid_keys.append(key)
        
        # 排序
        valid_keys.sort()
        
        logger.info(f"从 TOS 列举到 {len(valid_keys)} 个 BGM 对象: bucket={bucket}, prefix={prefix}")
        return valid_keys
    
    except Exception as e:
        logger.error(f"从 TOS 列举 BGM 失败: {e}")
        return []


def _download_bgm_from_tos(bucket: str, object_key: str, temp_dir: str) -> str:
    """
    从 TOS 下载 BGM 到临时目录。
    
    Args:
        bucket: TOS 桶名
        object_key: 对象键
        temp_dir: 临时目录
        
    Returns:
        本地文件路径
        
    Raises:
        Exception: 如果下载失败
    """
    from storage.tos.tos_client import get_client
    
    client = get_client()
    if client is None:
        raise Exception("TOS 客户端未配置")
    
    # 生成本地文件名
    filename = os.path.basename(object_key)
    local_path = os.path.join(temp_dir, filename)
    
    # 下载
    client.download_object(bucket=bucket, object_key=object_key, local_path=local_path)
    
    logger.info(f"从 TOS 下载 BGM: bucket={bucket}, key={object_key}, local={local_path}")
    return local_path


def _parse_bgm_urls() -> List[str]:
    """
    解析 BGM_URLS 环境变量，支持 JSON 数组或逗号分隔的 URL 列表。
    返回过滤空值后的 URL 列表。
    """
    import json
    bgm_urls_env = os.getenv("BGM_URLS", "")
    if not bgm_urls_env:
        return []
    
    # 尝试解析为 JSON 数组
    try:
        urls = json.loads(bgm_urls_env)
        if isinstance(urls, list):
            return [url.strip() for url in urls if url and url.strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    
    # 回退到逗号分隔解析
    return [url.strip() for url in bgm_urls_env.split(",") if url and url.strip()]


def _select_bgm_stable(
    script_id: str,
    temp_dir: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    从 TOS、BGM_URLS 或本地 BGM 目录中稳定选择一个 BGM。
    使用 script_id 的 SHA256 hash 确保同一 script_id 总是选择相同的 BGM。
    
    优先级：
    1. BGM_TOS_PREFIX（TOS 对象存储）
    2. BGM_URLS（远程 URL 列表）
    3. 本地 assets/bgm 目录（开发环境 fallback）
    
    Args:
        script_id: 脚本 ID
        temp_dir: 临时目录（用于下载 TOS BGM）
        
    Returns:
        (bgm_path, trace_info) 元组
        - bgm_path: BGM 文件路径或 URL
        - trace_info: 追踪信息字典
    """
    trace_info = {
        "bgm_source": "",
        "bgm_bucket": "",
        "bgm_object_key": "",
        "bgm_used": False,
        "warning": "",
    }
    
    # 1. 优先从 BGM_TOS_PREFIX 选择
    bucket, prefix = _parse_bgm_tos_prefix()
    if bucket:
        bgm_keys = _list_bgm_from_tos(bucket, prefix)
        if bgm_keys:
            # 使用 script_id 的 SHA256 hash 稳定选择
            digest = hashlib.sha256(script_id.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % len(bgm_keys)
            selected_key = bgm_keys[index]
            
            try:
                # 下载到临时目录
                local_path = _download_bgm_from_tos(bucket, selected_key, temp_dir)
                trace_info["bgm_source"] = "tos"
                trace_info["bgm_bucket"] = bucket
                trace_info["bgm_object_key"] = selected_key
                trace_info["bgm_used"] = True
                logger.info(f"从 TOS 稳定选择 BGM (script_id={script_id}): {selected_key}")
                return local_path, trace_info
            except Exception as e:
                logger.error(f"从 TOS 下载 BGM 失败: {e}")
                trace_info["warning"] = f"BGM 下载失败: {e}"
                # 继续尝试下一个优先级
    
    # 2. 从 BGM_URLS 环境变量选择远程 URL
    bgm_urls = _parse_bgm_urls()
    if bgm_urls:
        digest = hashlib.sha256(script_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(bgm_urls)
        selected_url = bgm_urls[index]
        trace_info["bgm_source"] = "bgm_urls"
        trace_info["bgm_used"] = True
        logger.info(f"从 BGM_URLS 稳定选择远程 BGM (script_id={script_id}): {selected_url}")
        return selected_url, trace_info
    
    # 3. Fallback 到本地 assets/bgm 目录（开发环境）
    if not os.path.exists(BGM_DIR):
        logger.warning(f"BGM_TOS_PREFIX 和 BGM_URLS 均未配置，且 BGM 目录不存在: {BGM_DIR}")
        trace_info["warning"] = "BGM 配置缺失：BGM_TOS_PREFIX 和 BGM_URLS 均未配置，且本地 BGM 目录不存在"
        return "", trace_info
    
    bgm_files = sorted(glob.glob(os.path.join(BGM_DIR, "*.mp3")))
    if not bgm_files:
        logger.warning(f"BGM_TOS_PREFIX 和 BGM_URLS 均未配置，且 BGM 目录中没有 MP3 文件: {BGM_DIR}")
        trace_info["warning"] = "BGM 配置缺失：BGM_TOS_PREFIX 和 BGM_URLS 均未配置，且本地 BGM 目录中没有 MP3 文件"
        return "", trace_info
    
    # 验证所有BGM文件的有效性
    valid_bgm_files = []
    for bgm_file in bgm_files:
        try:
            file_size = os.path.getsize(bgm_file)
            if file_size > 0:
                valid_bgm_files.append(bgm_file)
        except Exception as e:
            logger.warning(f"BGM文件验证失败 {bgm_file}: {e}")
    
    if not valid_bgm_files:
        logger.warning(f"BGM_TOS_PREFIX 和 BGM_URLS 均未配置，且 BGM 目录中没有有效的 MP3 文件: {BGM_DIR}")
        trace_info["warning"] = "BGM 配置缺失：BGM_TOS_PREFIX 和 BGM_URLS 均未配置，且本地 BGM 目录中没有有效的 MP3 文件"
        return "", trace_info
    
    # 使用 script_id 的 SHA256 hash 稳定选择（跨进程稳定）
    digest = hashlib.sha256(script_id.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(valid_bgm_files)
    selected = valid_bgm_files[index]
    trace_info["bgm_source"] = "local"
    trace_info["bgm_used"] = True
    logger.info(f"从本地目录稳定选择 BGM (script_id={script_id}): {os.path.basename(selected)}")
    return selected, trace_info


def script_source_router_node(
    state: dict,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> dict:
    """
    title: 文案来源路由
    desc: 根据 script_source 决定进入"生成文案"还是"手动文案"分支。同时创建运行目录并传递所有输入字段。
    """
    ctx = runtime.context
    script_source = state.get("script_source", "")
    script_id = state.get("script_id", "")
    
    # Read run_id from state first, then fallback to ctx.run_id
    # This ensures batch tasks (which pass run_id in state) and HTTP requests (which use ctx.run_id) both work
    run_id = state.get("run_id", "") or getattr(ctx, "run_id", "") or ""
    
    # Create run directory - use run_id for isolation between runs
    # Fallback to script_id for backward compatibility
    if run_id:
        run_dir = ensure_dir(os.path.join(RUNS_BASE, run_id))
    elif script_id:
        run_dir = ensure_dir(os.path.join(RUNS_BASE, script_id))
    else:
        # Last resort: generate a unique ID to prevent shared directory
        import uuid
        fallback_id = str(uuid.uuid4())[:8]
        run_dir = ensure_dir(os.path.join(RUNS_BASE, f"unknown_{fallback_id}"))
        logger.warning("[Node0a] No run_id or script_id, using fallback: %s", run_dir)

    logger.info("[Node0a] script_source=%s, script_id=%s, run_id=%s, run_dir=%s", script_source, script_id, run_id, run_dir)

    # 校验：必须指定有效的来源
    if script_source not in ("generated", "manual"):
        logger.error("[Node0a] 无效的script_source: %s", script_source)
        # 仍然返回，让条件判断路由到失败处理

    # 处理 core_selling_points：如果传入的是字符串，转为列表
    csp = state.get("core_selling_points", [])
    if isinstance(csp, str):
        csp = [s.strip() for s in csp.split(",") if s.strip()]

    # 处理BGM：如果没有指定，稳定选择一个
    bgm_url = state.get("bgm_url", "") or ""
    bgm_warnings = []
    bgm_trace = {
        "bgm_source": "",
        "bgm_bucket": "",
        "bgm_object_key": "",
        "bgm_used": False,
    }
    if not bgm_url:
        bgm_url, bgm_trace = _select_bgm_stable(script_id, run_dir)
        if bgm_url:
            logger.info(f"未指定BGM，稳定选择: {bgm_url} (source={bgm_trace['bgm_source']})")
        else:
            warning_msg = bgm_trace.get("warning", "BGM 选择失败，将仅使用 TTS 音频")
            bgm_warnings.append(warning_msg)
            logger.warning("[Node0a] BGM 选择失败，将仅使用 TTS 音频: %s", warning_msg)

    # 返回 dict 而非 Pydantic Model，确保 LangGraph 正确合并到 State
    result = {
        "script_source": script_source,
        "script_text": state.get("script_text", "") or "",
        "product_name": state.get("product_name", "") or "",
        "core_selling_points": csp if isinstance(csp, list) else [],
        "target_audience": state.get("target_audience", "") or "",
        "video_style": state.get("video_style", "") or "",
        "material_csv": state.get("material_csv", "") or "",
        "platform": state.get("platform", "") or "",
        "bgm_url": bgm_url,
        "run_dir": run_dir,
        "run_id": run_id,  # Pass run_id to state for downstream nodes
        # BGM trace 信息
        "bgm_source": bgm_trace.get("bgm_source", ""),
        "bgm_bucket": bgm_trace.get("bgm_bucket", ""),
        "bgm_object_key": bgm_trace.get("bgm_object_key", ""),
        "bgm_used": bgm_trace.get("bgm_used", False),
    }
    
    # 如果有 BGM 警告，合并到 warnings 中
    if bgm_warnings:
        existing_warnings = list(state.get("warnings") or [])
        existing_warnings.extend(bgm_warnings)
        result["warnings"] = existing_warnings
    
    return result