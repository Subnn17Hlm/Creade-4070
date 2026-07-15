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
import random
import logging
from typing import List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import ScriptSourceRouterInput, ScriptSourceRouterOutput
from graphs.shared_utils import ensure_dir

logger = logging.getLogger(__name__)

WORKSPACE = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
RUNS_BASE = os.path.join(WORKSPACE, "runs")
BGM_DIR = os.path.join(WORKSPACE, "assets", "bgm")


def _select_random_bgm() -> str:
    """从BGM目录中随机选择一个BGM文件"""
    if not os.path.exists(BGM_DIR):
        logger.warning(f"BGM目录不存在: {BGM_DIR}")
        return ""
    
    bgm_files = glob.glob(os.path.join(BGM_DIR, "*.mp3"))
    if not bgm_files:
        logger.warning(f"BGM目录中没有MP3文件: {BGM_DIR}")
        return ""
    
    selected = random.choice(bgm_files)
    logger.info(f"随机选择BGM: {os.path.basename(selected)}")
    return selected


def script_source_router_node(
    state: ScriptSourceRouterInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> ScriptSourceRouterOutput:
    """
    title: 文案来源路由
    desc: 根据 script_source 决定进入"生成文案"还是"手动文案"分支。同时创建运行目录并传递所有输入字段。
    """
    ctx = runtime.context
    script_source = state.script_source
    script_id = state.script_id

    # 创建运行目录
    run_dir = ensure_dir(os.path.join(RUNS_BASE, script_id))

    logger.info("[Node0a] script_source=%s, script_id=%s, run_dir=%s", script_source, script_id, run_dir)

    # 校验：必须指定有效的来源
    if script_source not in ("generated", "manual"):
        logger.error("[Node0a] 无效的script_source: %s", script_source)
        # 仍然返回，让条件判断路由到失败处理

    # 处理 core_selling_points：如果传入的是字符串，转为列表
    csp = state.core_selling_points
    if isinstance(csp, str):
        csp = [s.strip() for s in csp.split(",") if s.strip()]

    # 处理BGM：如果没有指定，随机选择一个
    bgm_url = state.bgm_url or ""
    if not bgm_url:
        bgm_url = _select_random_bgm()
        if bgm_url:
            logger.info(f"未指定BGM，自动选择: {bgm_url}")

    return ScriptSourceRouterOutput(
        script_source=script_source,
        script_text=state.script_text or "",
        product_name=state.product_name or "",
        core_selling_points=csp if isinstance(csp, list) else [],
        target_audience=state.target_audience or "",
        video_style=state.video_style or "",
        material_csv=state.material_csv or "",
        platform=state.platform or "",
        bgm_url=bgm_url,
        run_dir=run_dir,
    )