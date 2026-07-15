"""
Node6: 画面timeline组装
职责：将timing_debug和clipped_assets合并成最终视频timeline
      处理 full_play_required 素材的跨句视觉延续，确保总视觉时长 = TTS总时长
"""
import os
import json
import logging
import subprocess
from typing import List, Dict, Any, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import TimelineAssemblyInput, TimelineAssemblyOutput

logger = logging.getLogger(__name__)


def _get_media_duration(path: str) -> float:
    """获取媒体文件时长"""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _trim_clip(clip_path: str, target_duration: float, output_path: str) -> str:
    """裁剪clip到目标时长"""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", clip_path,
            "-t", f"{target_duration:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=60)
        return output_path
    except Exception as e:
        logger.error("[Node6] 裁剪clip失败: %s -> %s", clip_path, e)
        return clip_path


def _apply_cross_sentence_continuation(
    timeline: List[Dict[str, Any]],
    clipped_assets: List[Dict[str, Any]],
    run_dir: str,
) -> List[Dict[str, Any]]:
    """
    应用跨句视觉延续逻辑。
    
    如果某个素材标记为 full_play_required=true，且当前句TTS时长不足以完整播放，
    则允许该素材覆盖相邻句的视觉区间。
    
    被覆盖的相邻句仍然正常显示字幕和播放TTS，但画面延续同一个素材。
    覆盖句的clip_path被清空，full_play素材的clip被裁剪到刚好覆盖所有句子的TTS总时长。
    
    最终保证：sum(所有clip时长) = TTS总时长
    """
    if not timeline:
        return timeline
    
    # 建立 clipped_assets 按 sentence_id 的索引
    asset_by_sid: Dict[int, Dict[str, Any]] = {}
    for rec in clipped_assets:
        sid = rec.get("sentence_id", 0)
        asset_by_sid[sid] = rec
    
    # 找出需要 full_play 的素材位置（按timeline索引）
    full_play_indices: List[int] = []
    for i, entry in enumerate(timeline):
        sid = entry.get("sentence_id", i + 1)
        rec = asset_by_sid.get(sid, {})
        if rec.get("full_play_required", False):
            full_play_indices.append(i)
    
    if not full_play_indices:
        return timeline
    
    # 记录哪些句子已被覆盖（不再贡献自己的clip）
    covered_set: set = set()
    temp_dir = os.path.join(run_dir, "temp")
    
    for idx in full_play_indices:
        if idx in covered_set:
            # 如果当前full_play素材本身已被前面的full_play覆盖，跳过
            continue
        
        entry = timeline[idx]
        sid = entry.get("sentence_id", idx + 1)
        rec = asset_by_sid.get(sid, {})
        
        clip_path = entry.get("clip_path", "")
        if not clip_path or not os.path.exists(clip_path):
            continue
        
        # 素材实际clip时长
        clip_actual_duration = rec.get("actual_duration", 0.0)
        if clip_actual_duration <= 0:
            clip_actual_duration = _get_media_duration(clip_path)
        
        # 当前句TTS时长
        current_tts_duration = entry.get("duration", 0.0)
        
        # 如果clip时长 <= 句子TTS时长，不需要跨句延续
        if clip_actual_duration <= current_tts_duration + 0.05:
            continue
        
        # 需要延续的额外时长
        extra_needed = clip_actual_duration - current_tts_duration
        
        # 向后查找可以覆盖的相邻句
        j = idx + 1
        covered_duration = 0.0
        covered_sentence_indices: List[int] = []
        
        while j < len(timeline) and covered_duration < extra_needed - 0.01:
            if j in covered_set:
                j += 1
                continue
            
            next_entry = timeline[j]
            next_duration = next_entry.get("duration", 0.0)
            
            # 只覆盖使用不同素材的句子（同素材的不需要额外覆盖）
            next_sid = next_entry.get("sentence_id", j + 1)
            next_rec = asset_by_sid.get(next_sid, {})
            next_material = next_entry.get("selected_material_id", "")
            current_material = entry.get("selected_material_id", "")
            
            if next_material != current_material:
                covered_sentence_indices.append(j)
                covered_duration += next_duration
            
            j += 1
        
        if not covered_sentence_indices:
            continue
        
        # 计算实际需要覆盖的总TTS时长（当前句 + 被覆盖句）
        total_covered_tts = current_tts_duration + covered_duration
        
        # 关键修复：如果clip实际时长 < 总覆盖TTS时长，说明clip不够长
        # 需要回退，只覆盖clip能实际覆盖的句子
        if clip_actual_duration < total_covered_tts - 0.05:
            # 从后往前移除被覆盖的句子，直到clip能覆盖剩余的句子
            while covered_sentence_indices and clip_actual_duration < total_covered_tts - 0.05:
                removed_idx = covered_sentence_indices.pop()
                removed_duration = timeline[removed_idx].get("duration", 0.0)
                covered_duration -= removed_duration
                total_covered_tts = current_tts_duration + covered_duration
                logger.info(
                    "[Node6] 素材 %s (句%d) clip时长不足，回退句%d (TTS=%.2fs)，剩余覆盖TTS=%.2fs",
                    current_material, sid, timeline[removed_idx].get("sentence_id", removed_idx + 1),
                    removed_duration, total_covered_tts
                )
            
            if not covered_sentence_indices:
                # 如果回退后没有可覆盖的句子，跳过
                continue
        
        # 如果clip实际时长 > 总覆盖TTS时长，裁剪clip到总覆盖TTS时长
        if clip_actual_duration > total_covered_tts + 0.05:
            trimmed_path = os.path.join(temp_dir, f"clip_trimmed_{sid}.mp4")
            _trim_clip(clip_path, total_covered_tts, trimmed_path)
            actual_trimmed = _get_media_duration(trimmed_path)
            if actual_trimmed > 0:
                entry["clip_path"] = trimmed_path
                entry["visual_duration"] = actual_trimmed
                logger.info(
                    "[Node6] 素材 %s (句%d) 裁剪: %.2fs -> %.2fs (覆盖TTS总时长%.2fs)",
                    current_material, sid, clip_actual_duration, actual_trimmed, total_covered_tts
                )
            else:
                entry["visual_duration"] = clip_actual_duration
        else:
            entry["visual_duration"] = clip_actual_duration
        
        entry["cross_sentence_continuation"] = True
        entry["covered_sentence_ids"] = [
            timeline[ci].get("sentence_id", ci + 1) for ci in covered_sentence_indices
        ]
        
        # 标记被覆盖的句子：清空clip_path，标记视觉延续来源
        for ci in covered_sentence_indices:
            covered_set.add(ci)
            timeline[ci]["visual_continuation_from"] = sid
            timeline[ci]["visual_continuation_asset_id"] = current_material
            timeline[ci]["clip_path"] = ""  # 清空，不参与concat
            logger.info(
                "[Node6] 句%d 被素材 %s 跨句覆盖，clip_path已清空",
                timeline[ci].get("sentence_id", ci + 1), current_material
            )
    
    return timeline


def timeline_assembly_node(
    state: TimelineAssemblyInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> TimelineAssemblyOutput:
    """
    title: 画面timeline组装
    desc: 将时间轴、素材片段和截取结果合并为最终视频timeline JSON，处理跨句视觉延续
    """
    ctx = runtime.context
    timeline_shots = state.timeline_shots
    clip_paths = state.clip_paths
    timing = state.timing
    run_dir = state.run_dir

    logger.info("[Node6] 画面timeline组装...")

    # 构建初始timeline
    final_timeline: List[Dict[str, Any]] = []
    for i, shot in enumerate(timeline_shots):
        clip_path = clip_paths[i] if i < len(clip_paths) else ""
        time_info = timing[i] if i < len(timing) else {}

        entry: Dict[str, Any] = {
            "sentence_id": shot.get("sentence_id", i + 1),
            "text": shot.get("text", ""),
            "start_time": time_info.get("start_time", 0.0),
            "end_time": time_info.get("end_time", 0.0),
            # 使用 shot 的 duration（visual_group 总时长），而不是 time_info（单句 TTS 时长）
            "duration": shot.get("duration", 0.0) or time_info.get("duration", 0.0),
            "selected_material_id": shot.get("selected_material_id", ""),
            "clip_path": clip_path,
            "match_confidence": shot.get("match_confidence", "low"),
            "match_reason": shot.get("match_reason", ""),
            "semantic_tags": shot.get("semantic_tags", []),
            "visual_intent": shot.get("visual_intent", ""),
            "cross_sentence_continuation": False,
            "visual_continuation_from": None,
            "visual_continuation_asset_id": "",
            "visual_group_id": shot.get("visual_group_id", 0),
            "visual_group_merged": shot.get("visual_group_merged", False),
            "subtitle_start_end_list": shot.get("subtitle_start_end_list", []),
        }
        final_timeline.append(entry)

    # 读取 clipped_assets.json 获取 full_play 信息
    clipped_assets_path = os.path.join(run_dir, "clipped_assets.json")
    clipped_assets: List[Dict[str, Any]] = []
    if os.path.exists(clipped_assets_path):
        try:
            with open(clipped_assets_path, "r", encoding="utf-8") as f:
                clipped_assets = json.load(f)
            logger.info("[Node6] 读取clipped_assets.json: %d条记录", len(clipped_assets))
        except Exception as e:
            logger.warning("[Node6] 读取clipped_assets.json失败: %s", e)

    # 应用跨句视觉延续
    if clipped_assets:
        final_timeline = _apply_cross_sentence_continuation(
            final_timeline, clipped_assets, run_dir
        )

    # 计算并记录视觉时长统计
    total_tts_duration = sum(e.get("duration", 0.0) for e in final_timeline)
    total_visual_duration = 0.0
    active_clips = 0
    for entry in final_timeline:
        cp = entry.get("clip_path", "")
        if cp and os.path.exists(cp):
            dur = _get_media_duration(cp)
            total_visual_duration += dur
            active_clips += 1

    logger.info(
        "[Node6] 时长校验: TTS总时长=%.3fs, 视觉clip总时长=%.3fs, 活跃clip数=%d/%d",
        total_tts_duration, total_visual_duration, active_clips, len(final_timeline)
    )

    if abs(total_visual_duration - total_tts_duration) > 0.5:
        logger.warning(
            "[Node6] ⚠️ 视觉时长(%.3fs)与TTS时长(%.3fs)偏差%.3fs，可能存在问题",
            total_visual_duration, total_tts_duration,
            total_visual_duration - total_tts_duration
        )

    # === end_hold: 结尾画面多停留1秒 ===
    END_HOLD_SEC = 1.0  # 默认1.0秒，允许范围0.8-1.2秒
    
    # 找到最后一个有clip的条目
    last_clip_idx = -1
    for i in range(len(final_timeline) - 1, -1, -1):
        if final_timeline[i].get("clip_path", ""):
            last_clip_idx = i
            break
    
    end_hold_applied = False
    end_hold_sec = 0.0
    end_hold_tag = ""
    
    if last_clip_idx >= 0:
        last_entry = final_timeline[last_clip_idx]
        last_clip_path = last_entry.get("clip_path", "")
        last_tag = last_entry.get("selected_primary_scene_tag", "")
        
        # 只在特定标签类型上生效
        end_hold_eligible_tags = {"CTA促单", "价格促销", "产品展示", "包装展示", 
                                   "赠品展示", "折叠动作", "放进包包", "放进行李箱"}
        
        if last_tag in end_hold_eligible_tags or not last_tag:
            # 获取clip的实际时长
            clip_dur = _get_media_duration(last_clip_path) if last_clip_path else 0
            
            if clip_dur > 0:
                # 记录end_hold信息
                end_hold_applied = True
                end_hold_sec = END_HOLD_SEC
                end_hold_tag = last_tag
                last_entry["end_hold_sec"] = END_HOLD_SEC
                last_entry["end_hold_eligible"] = True
                
                logger.info(
                    "[Node6] end_hold: 最后一个clip(句%d, 标签=%s)延长%.1fs",
                    last_entry.get("sentence_id", 0), last_tag, END_HOLD_SEC
                )
        else:
            logger.info(
                "[Node6] end_hold: 最后一个clip标签=%s，不在适用标签列表中，跳过",
                last_tag
            )
    
    # 保存end_hold信息到timeline元数据
    end_hold_meta = {
        "end_hold_applied": end_hold_applied,
        "end_hold_sec": end_hold_sec,
        "end_hold_tag": end_hold_tag,
        "total_tts_duration": total_tts_duration,
        "expected_final_duration": total_tts_duration + end_hold_sec,
    }

    # 保存最终timeline
    final_timeline_path = os.path.join(run_dir, "timeline.json")
    with open(final_timeline_path, "w", encoding="utf-8") as f:
        json.dump(final_timeline, f, ensure_ascii=False, indent=2)
    
    # 保存end_hold元数据
    end_hold_meta_path = os.path.join(run_dir, "end_hold_meta.json")
    with open(end_hold_meta_path, "w", encoding="utf-8") as f:
        json.dump(end_hold_meta, f, ensure_ascii=False, indent=2)

    logger.info("[Node6] 完成: %d个片段, %d个活跃clip, end_hold=%.1fs", 
                len(final_timeline), active_clips, end_hold_sec)

    return TimelineAssemblyOutput(
        final_timeline_path=final_timeline_path,
    )