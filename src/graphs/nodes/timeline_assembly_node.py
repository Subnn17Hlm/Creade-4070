"""
Node6: 画面timeline组装
职责：将timing_debug和clipped_assets合并成最终视频timeline
"""
import os
import json
import logging
from typing import List, Dict, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import TimelineAssemblyInput, TimelineAssemblyOutput

logger = logging.getLogger(__name__)


def timeline_assembly_node(
    state: TimelineAssemblyInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> TimelineAssemblyOutput:
    """
    title: 画面timeline组装
    desc: 将时间轴、素材片段和截取结果合并为最终视频timeline JSON
    """
    ctx = runtime.context
    timeline_shots = state.timeline_shots
    clip_paths = state.clip_paths
    timing = state.timing
    run_dir = state.run_dir

    logger.info("[Node6] 画面timeline组装...")

    final_timeline = []
    for i, shot in enumerate(timeline_shots):
        clip_path = clip_paths[i] if i < len(clip_paths) else ""
        # 从timing获取时间轴数据
        time_info = timing[i] if i < len(timing) else {}

        entry = {
            "sentence_id": shot.get("sentence_id", i + 1),
            "text": shot.get("text", ""),
            "start_time": time_info.get("start_time", 0.0),
            "end_time": time_info.get("end_time", 0.0),
            "duration": time_info.get("duration", 0.0),
            "selected_material_id": shot.get("selected_material_id", ""),
            "clip_path": clip_path,
            "match_confidence": shot.get("match_confidence", "low"),
            "match_reason": shot.get("match_reason", ""),
            "semantic_tags": shot.get("semantic_tags", []),
            "visual_intent": shot.get("visual_intent", ""),
        }
        final_timeline.append(entry)

    # 保存最终timeline
    final_timeline_path = os.path.join(run_dir, "timeline.json")
    with open(final_timeline_path, "w", encoding="utf-8") as f:
        json.dump(final_timeline, f, ensure_ascii=False, indent=2)

    logger.info("[Node6] 完成: %d个片段", len(final_timeline))

    return TimelineAssemblyOutput(
        final_timeline_path=final_timeline_path,
    )