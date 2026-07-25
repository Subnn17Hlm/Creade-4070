"""
Node: timeline_dedup_node
职责：在 timeline_assembly 完成后、final_composition 之前，
      检查当前时间线是否与历史成品重复。
      如果重复，触发 reroll（最多3次）。
"""
import json
import logging
import os
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from generation.hash_utils import (
    compute_material_sequence_hash,
    compute_timeline_hash,
    compute_segment_signature_hash,
)
from generation.history_dedup import (
    compute_normalized_script_hash,
    check_history_duplication,
    create_reroll_seed,
    MAX_REROLL_ATTEMPTS,
)

logger = logging.getLogger(__name__)

MAX_REROLL = 3


async def timeline_dedup_node(
    state: Dict[str, Any],
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> Dict[str, Any]:
    """
    时间线去重节点。
    
    在 timeline_assembly 之后、final_composition 之前执行。
    计算 material_sequence_hash / timeline_hash / segment_signature_hash，
    查询同文案历史成功成品，判断是否重复。
    
    如果重复且 reroll_count < 3：
      - 创建新 variation_seed
      - 设置 needs_reroll=True
      - 图的条件边将路由回 material_matching
    
    如果不重复或 reroll_count >= 3：
      - 设置 needs_reroll=False
      - 图的条件边将路由到 final_composition
      - 如果 reroll_count >= 3 且仍重复，添加 warning
    """
    run_dir = state.get("run_dir", "")
    final_timeline_path = state.get("final_timeline_path", "")
    variation_seed = state.get("variation_seed", 0)
    generation_id = state.get("generation_id", "")
    task_id = state.get("task_id", "")
    script_text = state.get("script_text", "")
    reroll_count = state.get("reroll_count", 0)
    warnings = list(state.get("warnings", []))

    # 读取时间线
    timeline = []
    if final_timeline_path and os.path.exists(final_timeline_path):
        try:
            with open(final_timeline_path, "r", encoding="utf-8") as f:
                timeline = json.load(f)
        except Exception as e:
            logger.warning("[Dedup] 读取时间线失败: %s", e)

    # 计算哈希
    mat_hash = compute_material_sequence_hash(timeline)
    tl_hash = compute_timeline_hash(timeline)
    seg_hash = compute_segment_signature_hash(timeline)
    script_hash = compute_normalized_script_hash(script_text)

    logger.info(
        "[Dedup] task=%s gen=%s reroll=%d mat_hash=%s tl_hash=%s",
        task_id[:8] if task_id else "?",
        generation_id[:8] if generation_id else "?",
        reroll_count,
        mat_hash[:8] if mat_hash else "?",
        tl_hash[:8] if tl_hash else "?",
    )

    # 查询历史（需要数据库访问）
    # 在 pipeline 节点中，我们通过 runtime.ctx 获取数据库会话
    is_duplicate = False
    try:
        db = runtime.ctx.get_db_session()
        if db:
            from generation.history_dedup import get_historical_results_for_script
            history = await get_historical_results_for_script(db, script_hash)
            is_duplicate, dup_reason = check_history_duplication(
                script_hash, mat_hash, tl_hash, generation_id, history
            )
            if is_duplicate:
                logger.info(
                    "[Dedup] 检测到重复: reason=%s, reroll_count=%d",
                    dup_reason, reroll_count,
                )
    except Exception as e:
        logger.warning("[Dedup] 历史查询失败（可能无数据库）: %s", e)
        # 无数据库时不做去重检查，直接通过
        is_duplicate = False

    # 决策
    if is_duplicate and reroll_count < MAX_REROLL:
        # 需要 reroll
        new_seed = create_reroll_seed(variation_seed)
        logger.info(
            "[Dedup] 触发 reroll: old_seed=%d -> new_seed=%d, reroll_count=%d -> %d",
            variation_seed, new_seed, reroll_count, reroll_count + 1,
        )
        return {
            "variation_seed": new_seed,
            "reroll_count": reroll_count + 1,
            "needs_reroll": True,
            "material_sequence_hash": mat_hash,
            "timeline_hash": tl_hash,
            "segment_signature_hash": seg_hash,
            "node_trace": ["timeline_dedup"],
        }
    else:
        # 通过去重，或已达 reroll 上限
        if is_duplicate and reroll_count >= MAX_REROLL:
            warnings.append("insufficient_material_variation")
            logger.warning(
                "[Dedup] 达到 reroll 上限 (%d)，添加 warning: insufficient_material_variation",
                MAX_REROLL,
            )

        return {
            "needs_reroll": False,
            "material_sequence_hash": mat_hash,
            "timeline_hash": tl_hash,
            "segment_signature_hash": seg_hash,
            "warnings": warnings,
            "node_trace": ["timeline_dedup"],
        }


def route_after_dedup(state: Dict[str, Any]) -> str:
    """条件路由：根据 needs_reroll 决定下一步"""
    if state.get("needs_reroll", False):
        return "reroll"
    return "proceed"
