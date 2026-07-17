"""
素材源预检节点
职责：在进入剪辑前逐个检查素材URL，确认是否无字幕竖屏原片
"""
import os
import json
import csv
import subprocess
from typing import List, Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from pydantic import BaseModel, Field
from graphs.state import MaterialAuditInput, MaterialAuditOutput

# 已知烧录文字模式 → 素材文件名关键词映射
KNOWN_BURNED_IN_TEXT_PATTERNS: Dict[str, List[str]] = {
    "出差神器": ["不挑包包", "不挑行李", "出差旅行必备", "出差神器", "旅行好物", "旅行出差"],
    "小钢炮": [
        "吹风机里的小钢炮", "还能号称吹风机里的小钢炮",
        "吹风机圈的小钢炮", "小钢炮", "这么小的吹风机",
        "超mini的机身里", "巴掌大",
    ],
    "折叠便携": ["折叠便携", "折叠", "便携", "这么小的吹风机", "超mini", "巴掌大"],
    "旅行好物": ["旅行好物", "旅行出差", "出差旅行必备", "不挑包包", "不挑行李"],
    "高速性能": ["11万转", "高速性能", "速干", "Creade终于把高性能的风", "Creade"],
    "精致小巧": ["长发党三五分钟", "精致小巧", "Creade", "这么小的吹风机", "巴掌大"],
}


def _probe_material(url: str) -> Dict[str, Any]:
    """用ffprobe检测素材基本信息"""
    info: Dict[str, Any] = {"width": 0, "height": 0, "duration": 0.0, "probe_ok": False}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json",
             url],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            streams = data.get("streams", [])
            if streams:
                info["width"] = streams[0].get("width", 0)
                info["height"] = streams[0].get("height", 0)
            fmt = data.get("format", {})
            info["duration"] = float(fmt.get("duration", 0))
            info["probe_ok"] = True
    except Exception:
        pass
    return info


def _detect_burned_in_text(file_name: str) -> Dict[str, Any]:
    """检测素材文件名是否匹配已知烧录文字模式"""
    detected_texts: List[str] = []
    for keyword, texts in KNOWN_BURNED_IN_TEXT_PATTERNS.items():
        if keyword in file_name:
            detected_texts.extend(texts)
    # 去重
    seen = set()
    unique_texts: List[str] = []
    for t in detected_texts:
        if t not in seen:
            seen.add(t)
            unique_texts.append(t)
    return {
        "has_burned_in_text": len(unique_texts) > 0,
        "detected_texts": unique_texts,
    }


def material_source_audit_node(
    state: MaterialAuditInput, config: RunnableConfig, runtime: Runtime[Context],
) -> MaterialAuditOutput:
    """
    title: 素材源预检
    desc: 逐个检查素材URL，确认是否为无字幕竖屏原片。检测到烧录文字/非竖屏/尺寸异常的素材会被标记为source_ok=false。
    integrations: 对象存储
    """
    # 读取素材CSV
    csv_path = state.material_csv
    if not os.path.exists(csv_path):
        csv_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), csv_path)

    materials: List[Dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asset_id = row.get("asset_id", "").strip()
            # 使用统一的 URL 解析逻辑
            from src.storage.tos.tos_client import resolve_material_url
            url, _ = resolve_material_url(
                source_url=row.get("source_url", ""),
                s3_url=row.get("s3_url", ""),
                bucket=row.get("bucket", ""),
                object_key=row.get("object_key", ""),
                local_path=row.get("local_path", ""),
            )
            file_name = row.get("file_name", "").strip()
            tags_str = row.get("tags", "").strip()
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]

            # 用ffprobe检测
            probe = _probe_material(url)

            # 检测烧录文字
            text_check = _detect_burned_in_text(file_name)

            # 判断是否为竖屏(9:16)
            w = probe.get("width", 0)
            h = probe.get("height", 0)
            is_vertical = False
            aspect_ratio_note = ""
            if w > 0 and h > 0:
                ratio = w / h
                # 9:16 ≈ 0.5625, 允许偏差 ±0.1
                is_vertical = 0.46 <= ratio <= 0.66
                if not is_vertical:
                    aspect_ratio_note = f"非竖屏比例: {w}x{h} (ratio={ratio:.3f})"

            # 判断素材是否可用
            source_ok = (
                probe.get("probe_ok", False)
                and not text_check["has_burned_in_text"]
                and is_vertical
            )

            # 源说明
            source_note = ""
            if "assets/output" in file_name or file_name.startswith("吹风机_"):
                source_note = "来自旧流水线输出视频，含烧录文字/营销文案，非原始无字幕素材"
            elif "seg_" in file_name:
                source_note = "来自旧流水线片段，含烧录文字"
            else:
                source_note = "素材来源未知，需人工确认"

            entry = {
                "material_id": asset_id,
                "url": url,
                "file_name": file_name,
                "tags": tags,
                "width": probe.get("width", 0),
                "height": probe.get("height", 0),
                "duration": probe.get("duration", 0.0),
                "probe_ok": probe.get("probe_ok", False),
                "has_burned_in_text": text_check["has_burned_in_text"],
                "detected_texts": text_check["detected_texts"],
                "is_vertical_1080x1920_or_9_16": is_vertical,
                "aspect_ratio_note": aspect_ratio_note,
                "source_ok": source_ok,
                "source_note": source_note,
            }
            materials.append(entry)

    # 统计
    clean_materials = [m for m in materials if m["source_ok"]]
    dirty_materials = [m for m in materials if not m["source_ok"]]
    material_source_ok = len(clean_materials) >= 1

    # 保存审计报告
    run_dir = state.run_dir
    audit_path = os.path.join(run_dir, "material_source_audit.json")
    report = {
        "total_materials": len(materials),
        "clean_material_count": len(clean_materials),
        "dirty_material_count": len(dirty_materials),
        "material_source_ok": material_source_ok,
        "materials": materials,
        "summary": (
            f"素材源预检: 共{len(materials)}个素材, "
            f"无字可用{len(clean_materials)}个, "
            f"含烧录文字{dirty_materials}个"
            if material_source_ok
            else (
                f"素材源预检失败: 共{len(materials)}个素材, "
                f"无字可用{len(clean_materials)}个, "
                f"含烧录文字{len(dirty_materials)}个. "
                "所有素材均含烧录文字/营销文案, 非原始无字幕素材. "
                "请提供原始无字幕竖屏素材URL."
            )
        ),
    }
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 只返回无字素材
    return MaterialAuditOutput(
        material_audit_path=audit_path,
        audited_materials=clean_materials,
        clean_material_count=len(clean_materials),
        dirty_material_count=len(dirty_materials),
        material_source_ok=material_source_ok,
    )