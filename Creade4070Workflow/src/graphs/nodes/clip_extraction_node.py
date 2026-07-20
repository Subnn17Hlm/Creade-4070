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

from graphs.state import ClipExtractionInput, ClipExtractionOutput
from graphs.shared_utils import ensure_dir, get_media_duration, run_ffmpeg
from graphs.node_trace_utils import write_trace_entered, write_trace_completed, write_trace_error

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
    # 手持大小对比类素材：变形/转换类型，需要从产品已经出现的位置开始
    "手持大小对比_003": {"effective_start": 1.5, "effective_start_source": "rule_by_filename_keyword"},  # 手拿瓶装水变吹风机，从变成吹风机后开始
    # 屏显调温类素材：分屏/完整演示，需要尽量全时长使用
    "屏显调温_003": {"full_play_required": True, "preferred_min_duration": 3.0, "effective_start_source": "manual_config"},
    "屏显调温_009": {"full_play_required": True, "preferred_min_duration": 3.0, "effective_start_source": "manual_config"},
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
    state: dict,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> dict:
    """
    title: 素材片段截取
    desc: 根据timeline从素材URL截取对应时长片段，保持原竖屏画幅和原始画面内容。支持视觉组合并和相邻同素材连续播放。
    """
    ctx = runtime.context
    timeline_shots = state.get("timeline_shots", [])
    run_dir = state.get("run_dir", "")
    matched_materials = state.get("matched_materials", [])

    # Phase: entered
    write_trace_entered(run_dir, "clip_extraction",
        timeline_shots_count=len(timeline_shots),
        matched_materials_count=len(matched_materials),
    )

    # 检查是否有匹配的素材
    if not matched_materials and not timeline_shots:
        error_msg = "没有匹配的素材 (matched_materials为空且timeline_shots为空)"
        logger.error("[Node5] %s", error_msg)
        write_trace_error(run_dir, "clip_extraction", "NoMaterialError", error_msg)
        raise RuntimeError(f"素材截取失败: {error_msg}")

    logger.info("[Node5] 素材片段截取...")

    # 构建 material_id -> matched_material 映射，用于获取 bucket/object_key
    material_id_to_info = {}
    for mat in matched_materials:
        mat_id = mat.get("asset_id", "") or mat.get("material_id", "")
        if mat_id:
            material_id_to_info[mat_id] = mat

    temp_dir = ensure_dir(os.path.join(run_dir, "temp"))
    clip_paths = []
    clip_records = []
    has_issues = False

    # 跟踪每个素材的当前使用位置（用于相邻同素材连续播放）
    asset_current_position: Dict[str, float] = {}
    # 跟踪每个素材的使用次数
    asset_usage_count: Dict[str, int] = {}
    # 跟踪每个visual_group的主clip（用于视觉组合并）
    visual_group_main_clip: Dict[int, str] = {}  # visual_group_id -> clip_path
    visual_group_main_sentence: Dict[int, int] = {}  # visual_group_id -> sentence_id

    for i, shot in enumerate(timeline_shots):
        material_url = shot.get("selected_url", "")
        material_id = shot.get("selected_material_id", "")
        duration = shot.get("duration", 2.0)
        sentence_id = shot.get("sentence_id", i + 1)
        visual_group_id = shot.get("visual_group_id", 0)
        visual_group_sentence_ids = shot.get("visual_group_sentence_ids", [sentence_id])

        # 检测素材烧录文字
        file_name = shot.get("selected_file_name", material_id)
        burned_info = _detect_burned_in_text(material_id, file_name)

        # 如果 material_url 为空或可能过期，尝试从 matched_materials 重新生成签名 URL
        signed_url_generated = False
        bucket = ""
        object_key = ""
        if material_id in material_id_to_info:
            mat_info = material_id_to_info[material_id]
            bucket = mat_info.get("bucket", "")
            object_key = mat_info.get("object_key", "")
            if bucket and object_key:
                try:
                    from storage.tos.tos_client import resolve_material_url
                    regenerated_url, _ = resolve_material_url(
                        source_url=mat_info.get("source_url", ""),
                        s3_url=mat_info.get("s3_url", ""),
                        bucket=bucket,
                        object_key=object_key,
                        local_path=mat_info.get("local_path", ""),
                    )
                    if regenerated_url:
                        material_url = regenerated_url
                        signed_url_generated = True
                        logger.info("[Node5] 片段%d: 重新生成签名URL (material_id=%s)", i + 1, material_id)
                except Exception as e:
                    logger.warning("[Node5] 片段%d: 重新生成签名URL失败: %s", i + 1, e)

        if not material_url:
            error_msg = f"无素材URL (material_id={material_id}, bucket={bucket}, object_key={object_key})"
            logger.warning("[Node5] 片段%d: %s", i + 1, error_msg)
            clip_records.append({
                "sentence_id": sentence_id,
                "material_id": material_id,
                "status": "skipped_no_url",
                "error": error_msg,
                "bucket": bucket,
                "object_key": object_key,
                "signed_url_generated": signed_url_generated,
                "clip_path": "",
                "visual_continuation": False,
                "burned_in_text": burned_info,
            })
            continue

        # 检查是否是视觉组合并中的非主句
        is_visual_continuation = False
        visual_continuation_from = 0
        if visual_group_id > 0 and len(visual_group_sentence_ids) > 1:
            if visual_group_id in visual_group_main_sentence:
                # 这个visual_group已经有主句了，当前句是延续
                is_visual_continuation = True
                visual_continuation_from = visual_group_main_sentence[visual_group_id]
            else:
                # 这是visual_group的主句
                visual_group_main_sentence[visual_group_id] = sentence_id

        # 如果是视觉延续，不生成独立clip
        if is_visual_continuation:
            logger.info("[Node5] 片段%d (sid=%d): 视觉延续自sid=%d, 不生成独立clip", 
                       i + 1, sentence_id, visual_continuation_from)
            clip_records.append({
                "sentence_id": sentence_id,
                "material_id": material_id,
                "status": "visual_continuation",
                "clip_path": "",
                "visual_continuation": True,
                "visual_continuation_from": visual_continuation_from,
                "visual_group_id": visual_group_id,
                "burned_in_text": burned_info,
            })
            continue

        # 记录这是visual_group的主句
        if visual_group_id > 0:
            visual_group_main_clip[visual_group_id] = f"clip_{i+1:03d}.mp4"

        clip_path = os.path.join(temp_dir, f"clip_{i+1:03d}.mp4")

        # 获取素材有效段落配置
        material_config = MATERIAL_EFFECTIVE_SEGMENT_RULES.get(material_id, {})
        effective_start = float(material_config.get("effective_start", 0.0))
        effective_end_raw = material_config.get("effective_end", None)
        effective_end = float(effective_end_raw) if effective_end_raw is not None else None
        full_play_required = material_config.get("full_play_required", False)
        
        # 先下载素材到本地文件
        materials_dir = ensure_dir(os.path.join(run_dir, "materials"))
        local_material_path = os.path.join(materials_dir, f"{material_id or f'material_{i+1}'}.mp4")
        
        download_status = 0
        downloaded_size = 0
        download_error = ""
        
        try:
            import httpx
            logger.info("[Node5] 片段%d: 下载素材到本地: %s", i + 1, material_url[:100])
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                response = client.get(material_url)
                download_status = response.status_code
                if download_status == 200:
                    with open(local_material_path, 'wb') as f:
                        f.write(response.content)
                    downloaded_size = len(response.content)
                    logger.info("[Node5] 片段%d: 下载成功, size=%d", i + 1, downloaded_size)
                else:
                    download_error = f"HTTP {download_status}: {response.text[:500]}"
                    logger.error("[Node5] 片段%d: 下载失败: %s", i + 1, download_error)
        except Exception as e:
            download_error = str(e)
            logger.error("[Node5] 片段%d: 下载异常: %s", i + 1, download_error)
        
        # 验证下载结果
        if download_status != 200 or downloaded_size == 0 or not os.path.exists(local_material_path):
            error_msg = f"素材下载失败 (status={download_status}, size={downloaded_size}, error={download_error})"
            logger.error("[Node5] 片段%d: %s", i + 1, error_msg)
            clip_records.append({
                "sentence_id": sentence_id,
                "material_id": material_id,
                "status": "download_failed",
                "error": error_msg,
                "bucket": bucket,
                "object_key": object_key,
                "source_url": material_url,
                "signed_url_generated": signed_url_generated,
                "download_status": download_status,
                "downloaded_size": downloaded_size,
                "source_duration": 0,
                "source_start": 0,
                "source_end": 0,
                "ffmpeg_returncode": None,
                "ffmpeg_stderr": "",
                "clip_path": "",
                "visual_continuation": False,
                "burned_in_text": burned_info,
            })
            continue
        
        # 获取本地素材时长
        source_duration = float(get_media_duration(local_material_path))
        if source_duration <= 0:
            error_msg = f"无法获取素材时长 (local_path={local_material_path})"
            logger.error("[Node5] 片段%d: %s", i + 1, error_msg)
            clip_records.append({
                "sentence_id": sentence_id,
                "material_id": material_id,
                "status": "probe_failed",
                "error": error_msg,
                "bucket": bucket,
                "object_key": object_key,
                "source_url": material_url,
                "signed_url_generated": signed_url_generated,
                "download_status": download_status,
                "downloaded_size": downloaded_size,
                "source_duration": 0,
                "source_start": 0,
                "source_end": 0,
                "ffmpeg_returncode": None,
                "ffmpeg_stderr": "",
                "clip_path": "",
                "visual_continuation": False,
                "burned_in_text": burned_info,
            })
            continue
        
        # 计算实际截取参数
        # 检查是否是相邻同素材连续播放
        prev_position = float(asset_current_position.get(material_id, effective_start))
        replay_allowed = shot.get("replay_allowed", False)
        
        # 初始化clip_start为float类型
        clip_start = effective_start
        clip_end = 0.0
        clip_duration = 0.0
        used_duration = 0.0
        
        if full_play_required:
            # 必须完整播放：使用整个有效段落
            clip_start = effective_start
            clip_end = effective_end if effective_end is not None else source_duration
            clip_duration = float(clip_end - clip_start)
            used_duration = clip_duration
        elif material_id in asset_current_position and not replay_allowed:
            # 相邻同素材连续播放：从上次结束位置继续
            clip_start = prev_position
            # 计算visual_group的总时长（如果是visual_group主句）
            if visual_group_id > 0:
                # 计算组内所有句子的时长之和
                group_duration = 0.0
                for sid in visual_group_sentence_ids:
                    for s in timeline_shots:
                        if s.get("sentence_id") == sid:
                            group_duration += float(s.get("duration", 1.0))
                            break
                clip_duration = group_duration
            else:
                clip_duration = min(duration, 5.0)
            used_duration = clip_duration
            clip_end = clip_start + clip_duration
            logger.info("[Node5] 片段%d (sid=%d): 相邻同素材连续播放, 从%.2fs继续", 
                       i + 1, sentence_id, clip_start)
        else:
            # 普通裁剪：从 effective_start 开始
            clip_start = effective_start
            # 计算visual_group的总时长（如果是visual_group主句）
            if visual_group_id > 0:
                group_duration = 0.0
                for sid in visual_group_sentence_ids:
                    for s in timeline_shots:
                        if s.get("sentence_id") == sid:
                            group_duration += float(s.get("duration", 1.0))
                            break
                clip_duration = group_duration
            else:
                clip_duration = min(duration, 5.0)
            used_duration = clip_duration
            clip_end = clip_start + clip_duration
        
        # 确保不超过素材总时长
        if clip_end > source_duration:
            clip_end = source_duration
            clip_duration = max(0.0, float(clip_end - clip_start))
            used_duration = clip_duration
        
        # 更新素材当前使用位置
        asset_current_position[material_id] = clip_end
        asset_usage_count[material_id] = asset_usage_count.get(material_id, 0) + 1
        
        try:
            # 构建 ffmpeg 命令（使用本地文件）
            cmd = ["ffmpeg", "-y", "-threads", "1"]
            
            if clip_start > 0:
                cmd.extend(["-ss", f"{clip_start:.2f}"])
            
            cmd.extend(["-i", local_material_path])
            
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
                "sentence_id": sentence_id,
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
                "visual_continuation": False,
                "visual_group_id": visual_group_id,
                "visual_group_is_main": True,
                "asset_usage_count": asset_usage_count.get(material_id, 1),
                "source_start": round(clip_start, 2),
                "source_end": round(clip_end, 2),
                "continuation_mode": "continue" if material_id in asset_current_position and asset_current_position[material_id] != effective_start else "fresh",
                "replay_allowed": replay_allowed,
                "status": "ok",
                "frame_modified": False,
                "crop_applied": False,
                "resize_applied": False,
                "burned_in_text": burned_info,
                "bucket": bucket,
                "object_key": object_key,
                "signed_url_generated": signed_url_generated,
                "download_status": download_status,
                "downloaded_size": downloaded_size,
                "local_material_path": local_material_path,
            }
            clip_records.append(clip_record)
            logger.info("[Node5] 片段%d (sid=%d): %s -> %.2fs (从%.2fs开始, vg=%d, usage=%d)", 
                       i + 1, sentence_id, material_id, actual_dur, clip_start, visual_group_id, asset_usage_count.get(material_id, 1))

        except Exception as e:
            logger.error("[Node5] 片段%d截取失败: %s", i + 1, e)
            # 获取 ffmpeg stderr（如果有）
            ffmpeg_stderr = ""
            ffmpeg_returncode = None
            if hasattr(e, 'stderr'):
                ffmpeg_stderr = str(e.stderr) if e.stderr else ""
            if hasattr(e, 'returncode'):
                ffmpeg_returncode = e.returncode
            
            clip_records.append({
                "sentence_id": sentence_id,
                "material_id": material_id,
                "status": "failed",
                "error": str(e),
                "bucket": bucket,
                "object_key": object_key,
                "source_url": material_url,
                "signed_url_generated": signed_url_generated,
                "download_status": "skipped",
                "downloaded_size": 0,
                "source_duration": round(source_duration, 2) if source_duration else 0,
                "source_start": round(clip_start, 2),
                "source_end": round(clip_end, 2),
                "ffmpeg_returncode": ffmpeg_returncode,
                "ffmpeg_stderr": ffmpeg_stderr,
                "clip_path": "",
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

    # 同时保存clip_records.json供timeline_assembly_node使用
    clip_records_path = os.path.join(run_dir, "clip_records.json")
    with open(clip_records_path, "w", encoding="utf-8") as f:
        json.dump(clip_records, f, ensure_ascii=False, indent=2)

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

    # 构建 extracted_clips（保留完整截取信息）
    extracted_clips = []
    for i, clip_path in enumerate(clip_paths):
        clip_rec = clip_records[i] if i < len(clip_records) else {}
        extracted_clips.append({
            "sentence_id": clip_rec.get("sentence_id", i + 1),
            "asset_id": clip_rec.get("material_id", ""),
            "clip_path": clip_path,
            "source_start": clip_rec.get("source_start", clip_rec.get("clip_start", 0)),
            "source_end": clip_rec.get("source_end", clip_rec.get("clip_end", 0)),
            "duration": clip_rec.get("actual_duration", clip_rec.get("used_duration", 0)),
            "source_duration": clip_rec.get("source_duration", 0),
            "status": clip_rec.get("status", "ok"),
            "error": clip_rec.get("error", ""),
        })

    # 如果没有成功截取任何片段，抛出异常
    if not clip_paths:
        # 构建详细的错误信息
        error_details = []
        for idx, clip_rec in enumerate(clip_records[:2]):  # 只取前两条
            error_details.append({
                "index": idx + 1,
                "asset_id": clip_rec.get("material_id", ""),
                "status": clip_rec.get("status", ""),
                "error": clip_rec.get("error", ""),
                "bucket": clip_rec.get("bucket", ""),
                "object_key": clip_rec.get("object_key", ""),
                "source_url_exists": bool(clip_rec.get("source_url", "")),
                "signed_url_generated": clip_rec.get("signed_url_generated", False),
                "download_status": clip_rec.get("download_status", ""),
                "downloaded_size": clip_rec.get("downloaded_size", 0),
                "source_duration": clip_rec.get("source_duration", 0),
                "source_start": clip_rec.get("source_start", clip_rec.get("clip_start", 0)),
                "source_end": clip_rec.get("source_end", clip_rec.get("clip_end", 0)),
                "ffmpeg_returncode": clip_rec.get("ffmpeg_returncode", None),
                "ffmpeg_stderr_tail": (clip_rec.get("ffmpeg_stderr", "") or "")[-1000:],
            })
        
        error_msg = f"未截取任何片段 (timeline_shots={len(timeline_shots)}, clip_records={len(clip_records)})"
        error_detail_str = json.dumps(error_details, ensure_ascii=False, indent=2)
        logger.error("[Node5] %s\n详细错误:\n%s", error_msg, error_detail_str)
        write_trace_error(run_dir, "clip_extraction", "NoClipsExtractedError", 
                         f"{error_msg}\n详细错误:\n{error_detail_str}")
        raise RuntimeError(f"素材截取失败: {error_msg}\n详细错误:\n{error_detail_str}")

    # Phase: completed
    write_trace_completed(run_dir, "clip_extraction",
        extracted_clip_count=len(clip_paths),
        total_shots=len(timeline_shots),
        has_issues=has_issues,
    )

    return {
        "clip_paths": clip_paths,
        "clipped_assets_path": clipped_assets_path,
        "clip_report_path": clip_report_path,
        "extracted_clips": extracted_clips,
        "node_trace": ["clip_extraction"],
    }