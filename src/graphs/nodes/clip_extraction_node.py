"""
Node5: 素材片段截取（非素材预处理）
职责：读取selected_assets中的素材URL，按时间截取片段，保存到run_dir
禁止：裁切、缩放、遮挡、模糊、去字幕、补黑边、修改画幅
"""
import os
import json
import re
import logging
from typing import List, Dict, Any, Set

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import ClipExtractInput, ClipExtractOutput
from graphs.shared_utils import ensure_dir, get_media_duration, run_ffmpeg

logger = logging.getLogger(__name__)

# 已知烧录文字素材检测规则
# 素材文件名包含特定关键词 → 已知该素材有烧录文字
KNOWN_BURNED_IN_TEXT_RULES: Dict[str, List[str]] = {
    "小钢炮": ["吹风机里的小钢炮", "还能号称吹风机里的小钢炮", "吹风机圈的小钢炮"],
    "出差神器": ["不挑包包", "不挑行李", "出差旅行必备", "出差神器"],
    "折叠便携": ["这么小的吹风机", "超mini的机身里", "巴掌大"],
    "旅行好物": ["旅行好物", "旅行出差"],
    "高速性能": ["11万转", "高速性能", "Creade终于把高性能的风"],
    "精致小巧": ["长发党三五分钟", "精致小巧", "Creade"],
}


def _detect_burned_in_text(material_id: str, file_name: str) -> Dict[str, Any]:
    """检测素材是否有已知烧录文字"""
    detected_texts: List[str] = []
    matched_rules: Set[str] = set()

    combined = (material_id + " " + file_name).lower()

    for keyword, texts in KNOWN_BURNED_IN_TEXT_RULES.items():
        if keyword.lower() in combined:
            detected_texts.extend(texts)
            matched_rules.add(keyword)

    return {
        "has_burned_in_text": len(detected_texts) > 0,
        "detected_texts": detected_texts,
        "matched_rules": list(matched_rules),
    }


def clip_extraction_node(
    state: ClipExtractInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> ClipExtractOutput:
    """
    title: 素材片段截取
    desc: 根据timeline从素材URL截取对应时长片段，保持原竖屏画幅和原始画面内容
    """
    ctx = runtime.context
    timeline_shots = state.timeline_shots
    run_dir = state.run_dir

    logger.info("[Node5] 素材片段截取...")

    temp_dir = ensure_dir(os.path.join(run_dir, "temp"))
    clip_paths = []
    clip_records = []
    has_issues = False

    for i, shot in enumerate(timeline_shots):
        material_url = shot.get("material_url", "")
        material_id = shot.get("selected_material_id", "")
        duration = shot.get("duration", 2.0)

        # 检测素材烧录文字
        file_name = shot.get("material_file_name", material_id)
        burned_info = _detect_burned_in_text(material_id, file_name)

        if not material_url:
            logger.warning("[Node5] 片段%d: 无素材URL", i + 1)
            clip_records.append({
                "sentence_id": i + 1,
                "material_id": material_id,
                "status": "skipped_no_url",
                "clip_path": "",
                "burned_in_text": burned_info,
            })
            continue

        clip_path = os.path.join(temp_dir, f"clip_{i+1:03d}.mp4")

        # 直接截取，不做任何画面处理
        # 禁止：crop, scale, drawbox, pad, 等任何画面修改
        trim_dur = min(duration, 5.0)
        try:
            run_ffmpeg([
                "ffmpeg", "-y", "-ss", "0",
                "-i", material_url,
                "-t", f"{trim_dur:.2f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                clip_path
            ], timeout=120)

            actual_dur = get_media_duration(clip_path)
            clip_paths.append(clip_path)
            clip_records.append({
                "sentence_id": i + 1,
                "material_id": material_id,
                "url": material_url,
                "clip_path": clip_path,
                "requested_duration": round(trim_dur, 2),
                "actual_duration": round(actual_dur, 2),
                "status": "ok",
                "frame_modified": False,
                "crop_applied": False,
                "resize_applied": False,
                "burned_in_text": burned_info,
            })
            logger.info("[Node5] 片段%d: %s -> %.2fs", i + 1, material_id, actual_dur)

        except Exception as e:
            logger.error("[Node5] 片段%d截取失败: %s", i + 1, e)
            clip_records.append({
                "sentence_id": i + 1,
                "material_id": material_id,
                "status": "failed",
                "error": str(e),
            })
            has_issues = True

    # 统计烧录文字素材
    burned_in_materials = []
    for rec in clip_records:
        bi = rec.get("burned_in_text", {})
        if bi.get("has_burned_in_text"):
            burned_in_materials.append({
                "material_id": rec.get("material_id", ""),
                "detected_texts": bi.get("detected_texts", []),
            })
    has_burned_in_text = len(burned_in_materials) > 0

    # 保存截取报告
    clip_report = {
        "total_clips": len(timeline_shots),
        "successful_clips": len(clip_paths),
        "failed_clips": len(timeline_shots) - len(clip_paths),
        "has_issues": has_issues,
        "has_burned_in_text": has_burned_in_text,
        "burned_in_material_count": len(burned_in_materials),
        "burned_in_materials": burned_in_materials,
        "frame_modifications": {
            "crop_applied": False,
            "resize_applied": False,
            "drawbox_applied": False,
            "pad_applied": False,
            "blur_applied": False,
        },
        "clips": clip_records,
    }
    clip_report_path = os.path.join(run_dir, "clip_extract_report.json")
    with open(clip_report_path, "w", encoding="utf-8") as f:
        json.dump(clip_report, f, ensure_ascii=False, indent=2)

    # 保存截取素材映射
    clipped_assets = []
    for i, clip_path in enumerate(clip_paths):
        clipped_assets.append({
            "sentence_id": i + 1,
            "material_id": timeline_shots[i].get("selected_material_id", ""),
            "clip_path": clip_path,
        })
    clipped_assets_path = os.path.join(run_dir, "clipped_assets.json")
    with open(clipped_assets_path, "w", encoding="utf-8") as f:
        json.dump(clipped_assets, f, ensure_ascii=False, indent=2)

    logger.info("[Node5] 完成: %d/%d 成功", len(clip_paths), len(timeline_shots))

    return ClipExtractOutput(
        clip_paths=clip_paths,
        clipped_assets_path=clipped_assets_path,
        clip_report_path=clip_report_path,
    )