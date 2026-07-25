"""
主图编排 - 12节点视频流水线（含文案来源选择分支结构）
====================================================
GraphInput → script_source_router
  ├── (generated) → generate_script
  └── (manual) → manual_script
  ↓
input_normalization → tts_generation → subtitle_timing
  → material_source_audit → (素材通过→material_matching / 素材不合格→material_fail→END)
  → material_matching → clip_extraction → timeline_assembly
  → final_composition → quality_check → GraphOutput
"""
import logging
import os
import json

from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput,
    MaterialAuditInput,
    MaterialAuditOutput,
)

logger = logging.getLogger(__name__)


def state_get(state, key, default=None):
    """
    统一读取 LangGraph State 字段，兼容 dict 和 Pydantic Model
    """
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def route_script_source(state) -> str:
    """
    title: 文案来源路由
    desc: 根据script_source决定进入生成文案分支还是手动文案分支
    """
    script_source = state_get(state, "script_source", "")
    if script_source == "generated":
        return "生成文案"
    else:
        return "手动文案"


# material_source_ok_router 和 material_fail_node 已停用（可信TOS素材模式）
# 保留代码供参考，但不再注册到图中
"""
def material_source_ok_router(state) -> str:
    material_source_ok = state_get(state, "material_source_ok", False)
    if material_source_ok:
        return "素材通过"
    else:
        return "素材不合格"

def material_fail_node(state, config: RunnableConfig, runtime: Runtime[Context]) -> dict:
    ...
"""


# 创建状态图
builder = StateGraph(
    GlobalState,
    input_schema=GraphInput,
    output_schema=GraphOutput,
)

# 导入节点函数
from graphs.nodes.script_source_router_node import script_source_router_node
from graphs.nodes.generate_script_node import generate_script_node
from graphs.nodes.manual_script_node import manual_script_node
from graphs.nodes.input_normalization_node import input_normalization_node
from graphs.nodes.tts_generation_node import tts_generation_node
from graphs.nodes.subtitle_timing_node import subtitle_timing_node
from graphs.nodes.material_source_audit_node import material_source_audit_node
from graphs.nodes.material_matching_node import material_matching_node
from graphs.nodes.clip_extraction_node import clip_extraction_node
from graphs.nodes.timeline_assembly_node import timeline_assembly_node
from graphs.nodes.timeline_dedup_node import timeline_dedup_node, route_after_dedup
from graphs.nodes.final_composition_node import final_composition_node
from graphs.nodes.quality_check_node import quality_check_node

# 添加12个节点
builder.add_node("script_source_router", script_source_router_node, metadata={"type": "task"})
builder.add_node("generate_script", generate_script_node, metadata={"type": "agent", "llm_cfg": "config/script_generate_llm_cfg.json"})
builder.add_node("manual_script", manual_script_node, metadata={"type": "task"})
builder.add_node("input_normalization", input_normalization_node, metadata={"type": "task"})
builder.add_node("tts_generation", tts_generation_node, metadata={"type": "task"})
builder.add_node("subtitle_timing", subtitle_timing_node, metadata={"type": "task"})
builder.add_node("material_source_audit", material_source_audit_node, metadata={"type": "task"})
# material_fail_node 保留在文件中但不再注册到图中（可信TOS素材模式）
builder.add_node("material_matching", material_matching_node, metadata={"type": "task"})
builder.add_node("clip_extraction", clip_extraction_node, metadata={"type": "task"})
builder.add_node("timeline_assembly", timeline_assembly_node, metadata={"type": "task"})
builder.add_node("timeline_dedup", timeline_dedup_node, metadata={"type": "task"})
builder.add_node("final_composition", final_composition_node, metadata={"type": "task"})
builder.add_node("quality_check", quality_check_node, metadata={"type": "task"})

# 设置入口点
builder.set_entry_point("script_source_router")

# 文案来源条件分支
builder.add_conditional_edges(
    source="script_source_router",
    path=route_script_source,
    path_map={
        "生成文案": "generate_script",
        "手动文案": "manual_script",
    },
)

# 两个分支分别汇聚到输入规范化（条件分支只有一条路径被执行）
builder.add_edge("generate_script", "input_normalization")
builder.add_edge("manual_script", "input_normalization")

# 线性流水线 + 素材源预检分支
builder.add_edge("input_normalization", "tts_generation")
builder.add_edge("tts_generation", "subtitle_timing")
builder.add_edge("subtitle_timing", "material_source_audit")

# 素材源预检 -> 素材匹配（固定边，可信TOS素材模式）
# material_fail_node 保留但不再连接到图中
builder.add_edge("material_source_audit", "material_matching")
builder.add_edge("material_matching", "clip_extraction")
builder.add_edge("clip_extraction", "timeline_assembly")
# timeline_assembly -> timeline_dedup (条件边: reroll 或 proceed)
builder.add_edge("timeline_assembly", "timeline_dedup")
builder.add_conditional_edges(
    source="timeline_dedup",
    path=route_after_dedup,
    path_map={
        "reroll": "material_matching",  # 重复 → 回到素材匹配重新选择
        "proceed": "final_composition",  # 通过 → 继续渲染
    },
)
builder.add_edge("final_composition", "quality_check")
builder.add_edge("quality_check", END)

# 编译图
main_graph = builder.compile()

logger.info("12节点流水线图编译完成（含文案来源选择分支结构）")