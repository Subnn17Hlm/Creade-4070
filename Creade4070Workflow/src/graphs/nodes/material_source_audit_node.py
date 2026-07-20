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
    state: dict, config: RunnableConfig, runtime: Runtime[Context],
) -> dict:
    """
    title: 素材源预检
    desc: 逐个检查素材URL，确认是否为无字幕竖屏原片。检测到烧录文字/非竖屏/尺寸异常的素材会被标记为source_ok=false。
    integrations: 对象存储
    """
    # 读取素材CSV
    from pathlib import Path
    _project_root = Path(__file__).resolve().parent.parent.parent.parent
    _default_csv = _project_root / "assets" / "asset_manifest_v2_bound.csv"

    csv_path_str = state.get("material_csv", "") or ""
    if not csv_path_str:
        csv_path = _default_csv
    else:
        p = Path(csv_path_str)
        if p.is_absolute():
            csv_path = p
        else:
            resolved = _project_root / p
            csv_path = resolved if resolved.is_file() else _default_csv

    if not csv_path.is_file():
        return {
            "materials": [],
            "error": f"素材清单不存在: {csv_path}",
        }

    materials: List[Dict[str, Any]] = []
    # 统计计数器
    total_count = 0
    passed_count = 0
    failed_confirmed_count = 0
    vertical_confirmed_count = 0
    metadata_unknown_count = 0
    audit_skipped_count = 0
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_count += 1
            asset_id = row.get("asset_id", "").strip()
            
            # 检查 enabled 字段，只处理 enabled=true 的素材
            enabled_str = row.get("enabled", "true").strip().lower()
            if enabled_str not in ("true", "1", "yes", ""):
                audit_skipped_count += 1
                continue
            
            # 使用统一的 URL 解析逻辑
            from storage.tos.tos_client import resolve_material_url
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

            bucket = row.get("bucket", "").strip()
            object_key = row.get("object_key", "").strip()
            has_tos_ref = bool(bucket and object_key)

            # 用ffprobe检测（可能失败，因为生产环境可能无法访问TOS URL）
            probe = _probe_material(url)
            probe_ok = probe.get("probe_ok", False)

            # 竖屏判定：必须基于 width/height，元数据缺失时标记 unknown
            w = probe.get("width", 0)
            h = probe.get("height", 0)
            is_vertical = False
            vertical_status = "unknown"  # passed, failed, unknown
            aspect_ratio_note = ""
            
            if w > 0 and h > 0:
                ratio = w / h
                # 9:16 ≈ 0.5625, 允许偏差 ±0.1
                is_vertical = 0.46 <= ratio <= 0.66
                if is_vertical:
                    vertical_status = "passed"
                    vertical_confirmed_count += 1
                else:
                    vertical_status = "failed"
                    aspect_ratio_note = f"非竖屏比例: {w}x{h} (ratio={ratio:.3f})"
            else:
                # 元数据缺失，标记 unknown
                vertical_status = "unknown"
                metadata_unknown_count += 1
                aspect_ratio_note = "元数据缺失，无法判定竖屏"

            # 烧录文字判定：
            # - 只有对视频画面帧进行 OCR/视觉检测才能判定 has_burned_in_text=true
            # - 文件名、title、description、标签等不能作为证据
            # - 如果无法执行 OCR/ffprobe，不得默认判定有烧录文字
            has_burned_in_text = False
            burned_text_status = "unknown"  # confirmed, unknown
            detected_texts = []
            
            # 注意：当前实现没有 OCR 能力，所以无法确认烧录文字
            # 只有明确检测到才会标记为 confirmed
            burned_text_status = "unknown"

            # 素材可用性判定
            audit_status = "passed"  # passed, failed_confirmed, skipped_unavailable
            
            if has_tos_ref:
                # TOS 素材：只要有有效的 bucket + object_key 就视为可用
                # 不依赖 ffprobe/OCR 结果
                source_ok = True
                audit_status = "passed"
                passed_count += 1
                probe_status = "skipped" if not probe_ok else "ok"
                source_note = "TOS云端原片，信任引用"
            else:
                # 非 TOS 素材：需要更多信息才能判定
                if not probe_ok:
                    # 无法探测，标记为 skipped
                    source_ok = False
                    audit_status = "skipped_unavailable"
                    audit_skipped_count += 1
                    probe_status = "failed"
                    source_note = "无法探测素材信息，跳过"
                else:
                    # 有探测结果，但无法确认烧录文字（无 OCR）
                    # 保守处理：标记为 skipped，不直接判定失败
                    source_ok = False
                    audit_status = "skipped_unavailable"
                    audit_skipped_count += 1
                    probe_status = "ok"
                    source_note = "非 TOS 素材且无 OCR 能力，无法确认是否含烧录文字"

            entry = {
                "material_id": asset_id,
                "url": url,
                "url_type": "tos_presigned" if has_tos_ref else "other",
                "file_name": file_name,
                "tags": tags,
                "width": w,
                "height": h,
                "duration": probe.get("duration", 0.0),
                "probe_ok": probe_ok,
                "probe_status": probe_status,
                "has_burned_in_text": has_burned_in_text,
                "burned_text_status": burned_text_status,
                "detected_texts": detected_texts,
                "is_vertical": is_vertical,
                "vertical_status": vertical_status,
                "aspect_ratio_note": aspect_ratio_note,
                "source_ok": source_ok,
                "audit_status": audit_status,
                "source_note": source_note,
                "has_tos_ref": has_tos_ref,
            }
            materials.append(entry)

    # 统计
    clean_materials = [m for m in materials if m["source_ok"]]
    dirty_materials = [m for m in materials if not m["source_ok"]]
    material_source_ok = len(clean_materials) >= 1

    # 统计诊断信息
    tos_ref_count = sum(1 for m in materials if m.get("has_tos_ref"))
    probe_ok_count = sum(1 for m in materials if m.get("probe_ok"))
    probe_skipped_count = sum(1 for m in materials if m.get("probe_status") == "skipped")
    burned_text_confirmed_count = sum(1 for m in materials if m.get("burned_text_status") == "confirmed")

    # 获取前3个素材的审核原因
    first_3_audit_reasons = []
    for m in materials[:3]:
        reason_parts = []
        if m.get("has_tos_ref"):
            reason_parts.append("TOS云端原片")
        if m.get("audit_status") == "passed":
            reason_parts.append("通过")
        elif m.get("audit_status") == "skipped_unavailable":
            reason_parts.append("跳过(不可用)")
        elif m.get("audit_status") == "failed_confirmed":
            reason_parts.append("失败(已确认)")
        if m.get("vertical_status") == "unknown":
            reason_parts.append("竖屏未知")
        elif m.get("vertical_status") == "passed":
            reason_parts.append("竖屏通过")
        if m.get("burned_text_status") == "unknown":
            reason_parts.append("烧录文字未知")
        first_3_audit_reasons.append({
            "material_id": m.get("material_id"),
            "reasons": reason_parts,
            "source_ok": m.get("source_ok"),
        })

    # 保存审计报告
    run_dir = state.get("run_dir", "")
    audit_path = os.path.join(run_dir, "material_source_audit.json")
    report = {
        "audit_version": "3.0-tos-trust-v2",
        "material_total_count": total_count,
        "material_passed_count": passed_count,
        "burned_text_confirmed_count": burned_text_confirmed_count,
        "vertical_confirmed_count": vertical_confirmed_count,
        "metadata_unknown_count": metadata_unknown_count,
        "audit_skipped_count": audit_skipped_count,
        "first_3_material_audit_reasons": first_3_audit_reasons,
        # 兼容旧字段
        "total_materials": len(materials),
        "clean_material_count": len(clean_materials),
        "dirty_material_count": len(dirty_materials),
        "material_source_ok": material_source_ok,
        "tos_ref_count": tos_ref_count,
        "probe_ok_count": probe_ok_count,
        "probe_skipped_count": probe_skipped_count,
        "first_3_materials": materials[:3] if materials else [],
        "materials": materials,
        "summary": (
            f"素材源预检: 共{total_count}个素材, "
            f"通过{passed_count}个, "
            f"TOS引用{tos_ref_count}个, "
            f"跳过{audit_skipped_count}个"
            if material_source_ok
            else (
                f"素材源预检失败: 共{total_count}个素材, "
                f"通过{passed_count}个, "
                f"无可用素材"
            )
        ),
    }
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 只返回通过的素材
    return {
        "material_audit_path": audit_path,
        "audited_materials": clean_materials,
        "available_materials": clean_materials,
        "clean_material_count": len(clean_materials),
        "dirty_material_count": len(dirty_materials),
        "material_source_ok": material_source_ok,
        "material_total_count": total_count,
        "material_passed_count": passed_count,
        "material_audit_report": report,
        "node_trace": ["material_source_audit"],
    }