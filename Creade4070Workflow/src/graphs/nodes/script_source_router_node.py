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
from typing import List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import ScriptSourceRouterInput, ScriptSourceRouterOutput
from graphs.shared_utils import ensure_dir

logger = logging.getLogger(__name__)

WORKSPACE = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
RUNS_BASE = os.path.join(tempfile.gettempdir(), "runs")
BGM_DIR = os.path.join(WORKSPACE, "assets", "bgm")


def _select_bgm_stable(script_id: str) -> str:
    """
    从BGM目录中稳定选择一个BGM文件。
    使用 script_id 的 SHA256 hash 确保同一 script_id 总是选择相同的 BGM。
    """
    if not os.path.exists(BGM_DIR):
        logger.warning(f"BGM目录不存在: {BGM_DIR}")
        return ""
    
    bgm_files = sorted(glob.glob(os.path.join(BGM_DIR, "*.mp3")))
    if not bgm_files:
        logger.warning(f"BGM目录中没有MP3文件: {BGM_DIR}")
        return ""
    
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
        logger.warning(f"BGM目录中没有有效的MP3文件: {BGM_DIR}")
        return ""
    
    # 使用 script_id 的 SHA256 hash 稳定选择（跨进程稳定）
    digest = hashlib.sha256(script_id.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(valid_bgm_files)
    selected = valid_bgm_files[index]
    logger.info(f"稳定选择BGM (script_id={script_id}): {os.path.basename(selected)}")
    return selected


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
    if not bgm_url:
        bgm_url = _select_bgm_stable(script_id)
        if bgm_url:
            logger.info(f"未指定BGM，稳定选择: {bgm_url}")
        else:
            bgm_warnings.append("BGM 选择失败：BGM 目录不存在或为空，将仅使用 TTS 音频")
            logger.warning("[Node0a] BGM 选择失败，将仅使用 TTS 音频")

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
    }
    
    # 如果有 BGM 警告，合并到 warnings 中
    if bgm_warnings:
        existing_warnings = list(state.get("warnings") or [])
        existing_warnings.extend(bgm_warnings)
        result["warnings"] = existing_warnings
    
    return result