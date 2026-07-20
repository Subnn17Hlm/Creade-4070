"""
节点追踪工具
提供统一的节点追踪写入功能
"""
import json
import os
from typing import Any


def write_node_trace(run_dir: str, entry: dict) -> None:
    """
    写入节点追踪文件
    
    Args:
        run_dir: 运行目录
        entry: 追踪条目，必须包含 node 和 phase 字段
    """
    if not run_dir:
        return
    
    trace_path = os.path.join(run_dir, "node_trace.jsonl")
    try:
        os.makedirs(run_dir, exist_ok=True)
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        # 追踪写入失败不应阻止节点执行
        import logging
        logging.getLogger(__name__).warning("Failed to write node trace: %s", e)


def write_trace_entered(run_dir: str, node_name: str, **kwargs) -> None:
    """写入 entered 阶段追踪"""
    entry = {"node": node_name, "phase": "entered", **kwargs}
    write_node_trace(run_dir, entry)


def write_trace_completed(run_dir: str, node_name: str, **kwargs) -> None:
    """写入 completed 阶段追踪"""
    entry = {"node": node_name, "phase": "completed", **kwargs}
    write_node_trace(run_dir, entry)


def write_trace_error(run_dir: str, node_name: str, error_type: str, error_message: str, **kwargs) -> None:
    """写入 error 阶段追踪"""
    entry = {
        "node": node_name, 
        "phase": "error", 
        "error_type": error_type,
        "error_message": error_message,
        **kwargs
    }
    write_node_trace(run_dir, entry)


def read_node_trace(run_dir: str) -> list[dict]:
    """读取节点追踪文件"""
    if not run_dir:
        return []
    
    trace_path = os.path.join(run_dir, "node_trace.jsonl")
    if not os.path.exists(trace_path):
        return []
    
    try:
        with open(trace_path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []


def get_executed_nodes_from_trace(entries: list[dict]) -> list[str]:
    """
    从追踪条目中提取已完成的节点列表
    只计算 completed 阶段的节点，避免 entered/completed 重复计算
    """
    completed_nodes = []
    for entry in entries:
        if entry.get("phase") == "completed":
            node = entry.get("node")
            if node and node not in completed_nodes:
                completed_nodes.append(node)
    return completed_nodes
