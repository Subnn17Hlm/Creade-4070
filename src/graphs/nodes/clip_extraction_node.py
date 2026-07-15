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

# 素材有效段落配置
# effective_start: 有效开始时间（秒），不设置则默认为0
# effective_end: 有效结束时间（秒），不设置则默认为素材总时长
# full_play_required: 是否必须完整播放（不裁剪）
# preferred_min_duration: 推荐最小使用时长（秒）
MATERIAL_EFFECTIVE_SEGMENT_RULES: Dict[str, Dict[str, Any]] = {
    # 屏显调温类素材：分屏/完整演示，需要尽量全时长使用
    "屏显调温_003": {"full_play_required": True, "preferred_min_duration": 3.0},
    "屏显调温_009": {"full_play_required": True, "preferred_min_duration": 3.0},
    "屏显调温_001": {"effective_start": 0.5, "preferred_min_duration": 2.0},
    "屏显调温_002": {"effective_start": 0.5, "preferred_min_duration": 2.0},
    "屏显调温_004": {"effective_start": 0.5, "preferred_min_duration": 2.0},
    "屏显调温_005": {"effective_start": 0.5, "preferred_min_duration": 2.0},
    "屏显调温_006": {"effective_start": 0.5, "preferred_min_duration": 2.0},
    "屏显调温_007": {"effective_start": 0.5, "preferred_min_duration": 2.0},
    "屏显调温_008": {"effective_start": 0.5, "preferred_min_duration": 2.0},
    # 风力展示类素材：有效展示段通常在中后段
    "风力展示_001": {"effective_start": 0.3},
    "风力展示_002": {"effective_start": 0.3},
    "风力展示_003": {"effective_start": 0.3},
    "风力展示_004": {"effective_start": 0.3},
    "风力展示_005": {"effective_start": 0.3},
    "风力展示_006": {"effective_start": 0.3},
    # 护发效果类素材：有效展示段通常在中后段
    "护发效果_001": {"effective_start": 0.5},
    "护发效果_002": {"effective_start": 0.5},
    "护发效果_003": {"effective_start": 0.5},
    "护发效果_004": {"effective_start": 0.5},
    "护发效果_005": {"effective_start": 0.5},
    "护发效果_006": {"effective_start": 0.5},
    "护发效果_007": {"effective_start": 0.5},
    "护发效果_008": {"effective_start": 0.5},
    "护发效果_009": {"effective_start": 0.5},
    "护发效果_010": {"effective_start": 0.5},
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
        material_url = shot.get("selected_url", "")  # 修复：使用正确的字段名 selected_url
        material_id = shot.get("selected_material_id", "")
        duration = shot.get("duration", 2.0)

        # 检测素材烧录文字
        file_name = shot.get("selected_file_name", material_id)  # 修复：使用正确的字段名 selected_file_name
        burned_info = _detect_burned_in_text(material_id, file_name)

        if not material_url:
            logger.warning("[Node5] 片段%d: 无素材URL (material_id=%s)", i + 1, material_id)
            clip_records.append({
                "sentence_id": i + 1,
                "material_id": material_id,
                "status": "skipped_no_url",
                "clip_path": "",
                "burned_in_text": burned_info,
            })
            continue

        clip_path = os.path.join(temp_dir, f"clip_{i+1:03d}.mp4")

        # 获取素材有效段落配置
        material_config = MATERIAL_EFFECTIVE_SEGMENT_RULES.get(material_id, {})
        effective_start = material_config.get("effective_start", 0.0)
        effective_end = material_config.get("effective_end", None)  # None 表示到素材结尾
        full_play_required = material_config.get("full_play_required", False)
        preferred_min_duration = material_config.get("preferred_min_duration", 0.0)
        
        # 获取素材总时长
        source_duration = get_media_duration(material_url)
        
        # 计算实际截取参数
        if full_play_required:
            # 必须完整播放：使用整个有效段落
            clip_start = effective_start
            clip_end = effective_end if effective_end is not None else source_duration
            clip_duration = clip_end - clip_start
            # 如果句子时长小于素材有效时长，仍然使用完整有效段落
            used_duration = clip_duration
        else:
            # 普通裁剪：从 effective_start 开始，截取句子时长
            clip_start = effective_start
            clip_duration = min(duration, 5.0)
            # 如果有推荐最小时长，且句子时长小于推荐时长，使用推荐时长
            if preferred_min_duration > 0 and duration < preferred_min_duration:
                clip_duration = min(preferred_min_duration, source_duration - effective_start)
            used_duration = clip_duration
            clip_end = clip_start + clip_duration
        
        # 确保不超过素材总时长
        if clip_end > source_duration:
            clip_end = source_duration
            clip_duration = clip_end - clip_start
            used_duration = clip_duration
        
        try:
            # 构建 ffmpeg 命令
            cmd = ["ffmpeg", "-y"]
            
            if clip_start > 0:
                cmd.extend(["-ss", f"{clip_start:.2f}"])
            
            cmd.extend(["-i", material_url])
            
            if clip_duration < source_duration - clip_start:
                cmd.extend(["-t", f"{clip_duration:.2f}"])
            
            cmd.extend([
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                clip_path
            ])
            
            run_ffmpeg(cmd, timeout=120)

            actual_dur = get_media_duration(clip_path)
            clip_paths.append(clip_path)
            
            # 记录裁剪详情
            clip_record = {
                "sentence_id": i + 1,
                "material_id": material_id,
                "url": material_url,
                "clip_path": clip_path,
                "source_duration": round(source_duration, 2),
                "clip_start": round(clip_start, 2),
                "clip_end": round(clip_end, 2),
                "used_duration": round(used_duration, 2),
                "requested_duration": round(duration, 2),
                "actual_duration": round(actual_dur, 2),
                "effective_start_used": clip_start > 0,
                "full_play_required": full_play_required,
                "cross_sentence_continuation": False,  # 跨句延续标记，后续可扩展
                "status": "ok",
                "frame_modified": False,
                "crop_applied": False,
                "resize_applied": False,
                "burned_in_text": burned_info,
            }
            clip_records.append(clip_record)
            logger.info("[Node5] 片段%d: %s -> %.2fs (从%.2fs开始, full_play=%s)", 
                       i + 1, material_id, actual_dur, clip_start, full_play_required)

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

    # 保存截取素材映射（包含完整裁剪详情）
    clipped_assets = []
    for i, clip_path in enumerate(clip_paths):
        clip_rec = clip_records[i] if i < len(clip_records) else {}
        clipped_assets.append({
            "sentence_id": i + 1,
            "asset_id": timeline_shots[i].get("selected_material_id", ""),
            "material_id": timeline_shots[i].get("selected_material_id", ""),
            "clip_path": clip_path,
            "source_duration": clip_rec.get("source_duration", 0),
            "clip_start": clip_rec.get("clip_start", 0),
            "clip_end": clip_rec.get("clip_end", 0),
            "used_duration": clip_rec.get("used_duration", 0),
            "effective_start_used": clip_rec.get("effective_start_used", False),
            "full_play_required": clip_rec.get("full_play_required", False),
            "cross_sentence_continuation": clip_rec.get("cross_sentence_continuation", False),
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