"""
Node0c: 手动文案（Mode B）
============================
仅当 script_source=manual 时进入此节点。
直接使用用户提供的文案，不做任何改写、扩写、摘要。
输出：
  - manual_script.txt（用户原文）
  - original_script.txt（统一入口，等于manual_script.txt）
"""
import os
import json
import logging

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import ManualScriptInput, ManualScriptOutput

logger = logging.getLogger(__name__)


def manual_script_node(
    state: ManualScriptInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> ManualScriptOutput:
    """
    title: 手动文案
    desc: 直接使用用户提供的文案，不做任何改写、扩写、摘要。仅当script_source=manual时进入此分支。
    """
    ctx = runtime.context
    script_text = state.script_text.strip()
    run_dir = state.run_dir

    logger.info("[Node0c] 手动文案: run_dir=%s", run_dir)

    # 校验：文案不能为空
    if not script_text:
        logger.error("[Node0c] 手动文案为空")
        raise RuntimeError("手动文案为空，无法继续")

    # 保存manual_script.txt（用户原文，完全一致）
    manual_script_path = os.path.join(run_dir, "manual_script.txt")
    with open(manual_script_path, "w", encoding="utf-8") as f:
        f.write(script_text)
    logger.info("[Node0c] 手动文案已保存: %s (%d chars)", manual_script_path, len(script_text))

    # 保存original_script.txt（统一入口，等于manual_script.txt）
    original_script_path = os.path.join(run_dir, "original_script.txt")
    with open(original_script_path, "w", encoding="utf-8") as f:
        f.write(script_text)

    logger.info("[Node0c] 完成: source=manual, chars=%d, ok=true", len(script_text))

    # 写入节点追踪文件
    trace_path = os.path.join(run_dir, "node_trace.jsonl")
    trace_entry = {
        "node": "manual_script",
        "input_script_text_chars": len(script_text),
        "input_raw_script_chars": 0,
        "output_raw_script_chars": len(script_text),
        "output_script_text_chars": len(script_text),
        "return_type": "ManualScriptOutput",
    }
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace_entry, ensure_ascii=False) + "\n")

    return ManualScriptOutput(
        raw_script=script_text,
        script_text=script_text,  # 保留 script_text 防止被覆盖为空
        script_source="manual",
        manual_script_path=manual_script_path,
        original_script_path=original_script_path,
        node_trace=["manual_script"],
    )