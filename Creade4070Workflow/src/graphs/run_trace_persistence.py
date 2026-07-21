"""
运行追踪持久化模块
提供 run_id 到 script_id 的映射和追踪数据持久化
"""
import json
import os
import logging
from datetime import datetime
from typing import Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# 本地缓存目录
LOCAL_CACHE_DIR = "/tmp/run_traces"

# 进程内映射：run_id -> {script_id, trace_file_path, status, ...}
_run_mapping: dict[str, dict] = {}


def _get_storage():
    """获取 S3 存储客户端（复用项目已验证的存储）"""
    try:
        from utils.storage_helper import _get_storage as get_storage
        return get_storage()
    except Exception as e:
        logger.error(f"获取存储客户端失败: {e}")
        return None


def register_run(run_id: str, script_id: str, trace_file_path: str) -> None:
    """注册运行映射"""
    _run_mapping[run_id] = {
        "run_id": run_id,
        "script_id": script_id,
        "trace_file_path": trace_file_path,
        "status": "running",
        "created_at": datetime.now().isoformat(),
    }


def update_run_status(run_id: str, status: str, **kwargs) -> None:
    """更新运行状态"""
    if run_id in _run_mapping:
        _run_mapping[run_id]["status"] = status
        _run_mapping[run_id]["completed_at"] = datetime.now().isoformat()
        _run_mapping[run_id].update(kwargs)


def get_run_mapping(run_id: str) -> Optional[dict]:
    """获取运行映射"""
    return _run_mapping.get(run_id)


def get_latest_run_by_script(script_id: str) -> Optional[str]:
    """获取指定 script_id 的最新 run_id"""
    latest = None
    latest_time = None
    for run_id, mapping in _run_mapping.items():
        if mapping.get("script_id") == script_id:
            created_at = mapping.get("created_at")
            if created_at and (latest_time is None or created_at > latest_time):
                latest = run_id
                latest_time = created_at
    return latest


def _sanitize_trace_data(data: dict) -> dict:
    """清理追踪数据，移除敏感信息"""
    def sanitize_value(v):
        if isinstance(v, str):
            # 移除签名 URL 的 query 参数
            if "X-Tos-Algorithm" in v or "X-Amz-Credential" in v:
                # 只保留 URL 路径部分
                if "?" in v:
                    return v.split("?")[0]
            # 移除可能的密钥
            if "Authorization" in v or "Bearer " in v:
                return "[REDACTED]"
        elif isinstance(v, dict):
            return {k: sanitize_value(val) for k, val in v.items()}
        elif isinstance(v, list):
            return [sanitize_value(item) for item in v]
        return v
    
    return sanitize_value(data)


def _build_trace_summary(run_id: str, script_id: str, trace_entries: list[dict], 
                         status: str, quality_status: Optional[str] = None,
                         executed_nodes: Optional[list[str]] = None) -> dict:
    """构建追踪摘要"""
    # 提取节点信息
    nodes = []
    node_status = {}
    
    for entry in trace_entries:
        node = entry.get("node")
        phase = entry.get("phase")
        if not node:
            continue
        
        if node not in node_status:
            node_status[node] = {
                "node": node,
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "duration_ms": None,
                "input_summary": {},
                "output_summary": {},
                "error_message": None,
            }
        
        ns = node_status[node]
        
        if phase == "entered":
            ns["status"] = "running"
            ns["started_at"] = entry.get("timestamp") or datetime.now().isoformat()
            # 收集输入摘要
            for key in ["input_chars", "input_path", "cleaned_script_chars"]:
                if key in entry:
                    ns["input_summary"][key] = entry[key]
        
        elif phase == "completed":
            ns["status"] = "success"
            ns["completed_at"] = entry.get("timestamp") or datetime.now().isoformat()
            # 计算耗时
            if ns["started_at"]:
                try:
                    start = datetime.fromisoformat(ns["started_at"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(ns["completed_at"].replace("Z", "+00:00"))
                    ns["duration_ms"] = int((end - start).total_seconds() * 1000)
                except:
                    pass
            # 收集输出摘要
            for key in ["tts_duration", "srt_path", "clip_count", "timeline_duration", 
                       "final_video_path", "final_video_duration", "subtitle_burned"]:
                if key in entry:
                    ns["output_summary"][key] = entry[key]
        
        elif phase == "error":
            ns["status"] = "failed"
            ns["completed_at"] = entry.get("timestamp") or datetime.now().isoformat()
            ns["error_message"] = f"{entry.get('error_type', 'Unknown')}: {entry.get('error_message', '')}"
        
        elif not phase:
            # 没有 phase 字段的记录，如果存在且无错误，判定为 success
            if ns["status"] == "pending" and not ns.get("error_message"):
                ns["status"] = "success"
                ns["completed_at"] = entry.get("timestamp") or datetime.now().isoformat()
    
    # 对于 executed_nodes 中存在但 trace_entries 中没有的节点，补充 success 状态
    # 无论 status 是什么，只要 executed_nodes 中有该节点，就补充
    if executed_nodes:
        for node_name in executed_nodes:
            if node_name not in node_status:
                node_status[node_name] = {
                    "node": node_name,
                    "status": "success",
                    "started_at": None,
                    "completed_at": None,
                    "duration_ms": None,
                    "input_summary": {},
                    "output_summary": {},
                    "error_message": None,
                }
    
    # 固定拓扑顺序
    TOPOLOGY_ORDER = [
        "manual_script",
        "input_normalization",
        "tts_generation",
        "subtitle_timing",
        "material_source_audit",
        "material_matching",
        "clip_extraction",
        "timeline_assembly",
        "final_composition",
        "quality_check",
    ]
    
    # 按固定拓扑顺序排序节点
    def get_node_order(node_dict):
        node_name = node_dict.get("node", "")
        try:
            return TOPOLOGY_ORDER.index(node_name)
        except ValueError:
            return len(TOPOLOGY_ORDER)  # 未知节点排在最后
    
    nodes = sorted(node_status.values(), key=get_node_order)
    
    # 对于 quality_check，根据 quality_report.status 判断最终状态
    for node in nodes:
        if node["node"] == "quality_check" and node["status"] in ("running", "pending"):
            if quality_status == "success":
                node["status"] = "success"
                node["completed_at"] = node["completed_at"] or datetime.now().isoformat()
            elif quality_status:
                node["status"] = "failed"
    
    return {
        "run_id": run_id,
        "script_id": script_id,
        "status": status,
        "created_at": _run_mapping.get(run_id, {}).get("created_at"),
        "completed_at": datetime.now().isoformat() if status != "running" else None,
        "quality_status": quality_status,
        "nodes": nodes,
    }


def persist_trace_to_local(run_id: str, summary: dict) -> Optional[str]:
    """持久化追踪到本地缓存"""
    try:
        os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)
        local_path = os.path.join(LOCAL_CACHE_DIR, f"{run_id}.json")
        sanitized = _sanitize_trace_data(summary)
        with open(local_path, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, ensure_ascii=False, indent=2)
        return local_path
    except Exception as e:
        logger.warning("Failed to persist trace to local: %s", e)
        return None


def persist_trace_to_tos(run_id: str, summary: dict) -> dict:
    """
    持久化追踪到 TOS（使用项目已验证的 S3 存储）
    返回持久化诊断信息
    """
    result = {
        "tos_upload_attempted": False,
        "tos_uploaded": False,
        "tos_object_key": f"run_traces/{run_id}.json",
        "tos_verified": False,
        "tos_error": "",
    }
    
    try:
        from utils.storage_helper import _get_storage as get_storage
        storage = get_storage()
        if not storage:
            result["tos_error"] = "无法获取存储客户端"
            return result
        
        object_key = result["tos_object_key"]
        sanitized = _sanitize_trace_data(summary)
        content = json.dumps(sanitized, ensure_ascii=False, indent=2).encode("utf-8")
        
        result["tos_upload_attempted"] = True
        
        # 上传
        storage.upload_file(
            file_content=content,
            file_name=object_key,
            content_type="application/json",
        )
        logger.info("Persisted trace to TOS: %s", object_key)
        result["tos_uploaded"] = True
        
        # 立即读回验证
        read_data = storage.read_file(file_key=object_key)
        read_json = json.loads(read_data.decode("utf-8"))
        if read_json.get("run_id") != run_id:
            result["tos_error"] = f"读回验证失败: run_id 不匹配，期望 {run_id}，实际 {read_json.get('run_id')}"
            return result
        
        result["tos_verified"] = True
        logger.info("Trace read-back verification succeeded: %s", object_key)
        
    except Exception as e:
        result["tos_error"] = str(e)
        logger.warning("Failed to persist trace to TOS: %s", e)
    
    return result


def load_trace_from_local(run_id: str) -> Optional[dict]:
    """从本地缓存加载追踪"""
    local_path = os.path.join(LOCAL_CACHE_DIR, f"{run_id}.json")
    if not os.path.exists(local_path):
        return None
    try:
        with open(local_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load trace from local: %s", e)
        return None


def load_trace_from_tos(run_id: str) -> tuple[Optional[dict], str]:
    """
    从 TOS 加载追踪（使用项目已验证的 S3 存储）
    返回 (追踪数据, 错误类型)
    错误类型: "" (成功), "object_not_found", "storage_read_error"
    """
    try:
        from utils.storage_helper import _get_storage as get_storage
        storage = get_storage()
        if not storage:
            return None, "storage_read_error"
        
        object_key = f"run_traces/{run_id}.json"
        data = storage.read_file(file_key=object_key)
        trace_data = json.loads(data.decode("utf-8"))
        logger.info("Loaded trace from TOS: %s", object_key)
        return trace_data, ""
    except Exception as e:
        error_str = str(e).lower()
        # 区分 object_not_found 和 storage_read_error
        if "not found" in error_str or "nosuchkey" in error_str or "404" in error_str:
            return None, "object_not_found"
        logger.warning("Failed to load trace from TOS: %s", e)
        return None, "storage_read_error"


def persist_run_trace(run_id: str, script_id: str, trace_entries: list[dict],
                      status: str, quality_status: Optional[str] = None,
                      executed_nodes: Optional[list[str]] = None) -> dict:
    """持久化运行追踪，返回包含诊断信息的摘要"""
    summary = _build_trace_summary(run_id, script_id, trace_entries, status, quality_status, executed_nodes)
    
    # 持久化到本地
    local_path = persist_trace_to_local(run_id, summary)
    local_saved = local_path is not None and os.path.isfile(local_path)
    
    # 持久化到 TOS
    tos_key = f"run_traces/{run_id}.json"
    tos_uploaded, tos_error = persist_trace_to_tos(run_id, summary)
    
    # 添加诊断信息
    summary["local_cache_path"] = local_path
    summary["tos_object_key"] = tos_key
    summary["trace_persistence"] = {
        "local_saved": local_saved,
        "tos_uploaded": tos_uploaded,
        "object_key": tos_key,
        "verified": tos_uploaded,  # persist_trace_to_tos 已验证
        "error": tos_error or ""
    }
    
    return summary


def get_trace(run_id: str) -> Optional[dict]:
    """获取追踪数据，按优先级查询"""
    # 1. 进程内映射
    mapping = get_run_mapping(run_id)
    if mapping and mapping.get("status") != "running":
        # 如果有完整的映射，从 trace 文件构建
        trace_file = mapping.get("trace_file_path")
        if trace_file:
            run_dir = os.path.dirname(trace_file)
            from graphs.node_trace_utils import read_node_trace
            entries = read_node_trace(run_dir)
            if entries:
                return _build_trace_summary(
                    run_id, 
                    mapping.get("script_id", ""),
                    entries,
                    mapping.get("status", "unknown"),
                    mapping.get("quality_status"),
                    mapping.get("executed_nodes"),
                )
    
    # 2. 本地缓存
    local_data = load_trace_from_local(run_id)
    if local_data:
        return local_data
    
    # 3. TOS
    tos_data, tos_error = load_trace_from_tos(run_id)
    if tos_data:
        # 回填本地缓存
        try:
            save_trace_to_local(run_id, tos_data)
        except Exception as e:
            logger.warning("Failed to cache trace locally: %s", e)
        return tos_data
    
    # 如果 TOS 读取失败但不是 object_not_found，记录错误
    if tos_error and tos_error != "object_not_found":
        logger.warning("TOS read error for run_id %s: %s", run_id, tos_error)
    
    return None
