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


def _generate_subtitle_suppression(
    timeline: List[Dict[str, Any]],
    run_dir: str,
) -> None:
    """
    检查timeline中是否使用了白名单素材（suppress_generated_subtitle=true）。
    如果有，生成：
    1. subtitle_suppression_intervals.json - 关闭区间信息
    2. render_subtitles.srt - 裁切后的字幕文件（关闭区间内不渲染系统字幕）
    """
    import re as _re

    # 读取白名单
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "")
    whitelist_path = os.path.join(workspace_path, "素材质量优化", "native_text_whitelist.json")
    if not os.path.exists(whitelist_path):
        logger.info("[Node6] 白名单文件不存在，跳过字幕关闭")
        return

    try:
        with open(whitelist_path, "r", encoding="utf-8") as f:
            whitelist = json.load(f)
    except Exception as e:
        logger.warning("[Node6] 读取白名单失败: %s", e)
        return

    whitelist_ids = set()
    # whitelist is a list of entries
    whitelist_entries = whitelist if isinstance(whitelist, list) else whitelist.get("entries", [])
    for entry in whitelist_entries:
        if entry.get("suppress_generated_subtitle", False):
            whitelist_ids.add(entry.get("asset_id", ""))

    if not whitelist_ids:
        logger.info("[Node6] 白名单中无需关闭字幕的素材")
        return

    # 查找timeline中使用白名单素材的区间
    suppression_intervals: List[Dict[str, Any]] = []
    current_interval: Optional[Dict[str, Any]] = None

    for entry in timeline:
        material_id = entry.get("selected_material_id", "")
        start_time = entry.get("start_time", 0.0)
        end_time = entry.get("end_time", 0.0)

        if material_id in whitelist_ids:
            if current_interval is None:
                current_interval = {
                    "asset_id": material_id,
                    "visual_group_id": entry.get("visual_group_id", 0),
                    "sentence_ids": [entry.get("sentence_id", 0)],
                    "output_start": start_time,
                    "output_end": end_time,
                }
            else:
                current_interval["sentence_ids"].append(entry.get("sentence_id", 0))
                current_interval["output_end"] = end_time
        else:
            if current_interval is not None:
                suppression_intervals.append(current_interval)
                current_interval = None

    if current_interval is not None:
        suppression_intervals.append(current_interval)

    if not suppression_intervals:
        logger.info("[Node6] timeline中未使用白名单素材，无需关闭字幕")
        return

    # 保存关闭区间信息
    suppression_path = os.path.join(run_dir, "subtitle_suppression_intervals.json")
    with open(suppression_path, "w", encoding="utf-8") as f:
        json.dump(suppression_intervals, f, ensure_ascii=False, indent=2)
    logger.info("[Node6] 生成字幕关闭区间: %d个", len(suppression_intervals))

    # 生成 render_subtitles.srt
    canonical_srt_path = os.path.join(run_dir, "subtitles.srt")
    render_srt_path = os.path.join(run_dir, "render_subtitles.srt")

    if not os.path.exists(canonical_srt_path):
        logger.warning("[Node6] canonical subtitles.srt 不存在")
        return

    with open(canonical_srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    cues = _parse_srt(srt_content)
    render_cues: List[Dict[str, Any]] = []

    for cue in cues:
        cue_start = cue["start"]
        cue_end = cue["end"]
        cue_text = cue["text"]
        remaining_parts: List[tuple] = [(cue_start, cue_end)]

        for interval in suppression_intervals:
            sup_start = interval["output_start"]
            sup_end = interval["output_end"]
            new_parts: List[tuple] = []
            for part_start, part_end in remaining_parts:
                if part_start >= sup_start and part_end <= sup_end:
                    continue
                if part_end <= sup_start or part_start >= sup_end:
                    new_parts.append((part_start, part_end))
                    continue
                if part_start < sup_start:
                    new_parts.append((part_start, sup_start))
                if part_end > sup_end:
                    new_parts.append((sup_end, part_end))
            remaining_parts = new_parts

        for part_start, part_end in remaining_parts:
            if part_end - part_start >= 0.3:
                render_cues.append({"start": part_start, "end": part_end, "text": cue_text})

    with open(render_srt_path, "w", encoding="utf-8") as f:
        for i, cue in enumerate(render_cues, 1):
            start_str = _format_srt_time(cue["start"])
            end_str = _format_srt_time(cue["end"])
            f.write(f"{i}\n{start_str} --> {end_str}\n{cue['text']}\n\n")

    logger.info("[Node6] 生成 render_subtitles.srt: %d条cue (原%d条)", len(render_cues), len(cues))


def _parse_srt(content: str) -> List[Dict[str, Any]]:
    """解析SRT内容为cue列表"""
    import re as _re
    cues: List[Dict[str, Any]] = []
    blocks = content.strip().split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        time_line = lines[1]
        match = _re.match(r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})", time_line)
        if not match:
            continue
        start_str, end_str = match.groups()
        start = _parse_srt_time(start_str)
        end = _parse_srt_time(end_str)
        text = "\n".join(lines[2:])
        cues.append({"start": start, "end": end, "text": text})
    return cues


def _parse_srt_time(time_str: str) -> float:
    """解析SRT时间格式为秒数"""
    time_str = time_str.replace(",", ".")
    parts = time_str.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def _format_srt_time(seconds: float) -> str:
    """将秒数格式化为SRT时间格式"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def timeline_assembly_node(
    state: TimelineAssemblyInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> TimelineAssemblyOutput:
    """
    title: 画面timeline组装
    desc: 将时间轴、素材片段和截取结果合并为最终视频timeline JSON，处理跨句视觉延续和视觉组合并
    """
    ctx = runtime.context
    timeline_shots = state.timeline_shots
    clip_paths = state.clip_paths
    timing = state.timing
    run_dir = state.run_dir

    logger.info("[Node6] 画面timeline组装...")

    # 读取 clip_records.json 获取每个clip的详细信息（包括visual_continuation状态）
    clip_records_path = os.path.join(run_dir, "clip_records.json")
    clip_records: List[Dict[str, Any]] = []
    if os.path.exists(clip_records_path):
        try:
            with open(clip_records_path, "r", encoding="utf-8") as f:
                clip_records = json.load(f)
            logger.info("[Node6] 读取clip_records.json: %d条记录", len(clip_records))
        except Exception as e:
            logger.warning("[Node6] 读取clip_records.json失败: %s", e)

    # 构建 sentence_id -> clip_record 映射
    clip_record_by_sid: Dict[int, Dict[str, Any]] = {}
    for rec in clip_records:
        sid = rec.get("sentence_id", 0)
        if sid > 0:
            clip_record_by_sid[sid] = rec

    # 构建初始timeline
    final_timeline: List[Dict[str, Any]] = []
    for i, shot in enumerate(timeline_shots):
        sentence_id = shot.get("sentence_id", i + 1)
        clip_record = clip_record_by_sid.get(sentence_id, {})
        
        # 检查是否是视觉延续
        is_visual_continuation = clip_record.get("visual_continuation", False)
        visual_continuation_from = clip_record.get("visual_continuation_from", 0)
        
        # 获取clip_path：如果是视觉延续，则clip_path为空
        if is_visual_continuation:
            clip_path = ""
        else:
            # 从clip_paths列表中获取，但需要找到对应的active clip索引
            # 因为clip_paths只包含active clips，需要计算当前是第几个active clip
            active_clip_index = 0
            for j in range(i):
                prev_sid = timeline_shots[j].get("sentence_id", j + 1)
                prev_rec = clip_record_by_sid.get(prev_sid, {})
                if not prev_rec.get("visual_continuation", False):
                    active_clip_index += 1
            clip_path = clip_paths[active_clip_index] if active_clip_index < len(clip_paths) else ""

        time_info = timing[i] if i < len(timing) else {}

        entry: Dict[str, Any] = {
            "sentence_id": sentence_id,
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
            "visual_continuation": is_visual_continuation,
            "visual_continuation_from": visual_continuation_from,
            "visual_group_id": shot.get("visual_group_id", 0),
            "source_start": clip_record.get("source_start", 0.0),
            "source_end": clip_record.get("source_end", 0.0),
            "asset_usage_count": clip_record.get("asset_usage_count", 1),
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

    # 应用跨句视觉延续（保留原有逻辑）
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

    # 保存最终timeline
    final_timeline_path = os.path.join(run_dir, "timeline.json")
    with open(final_timeline_path, "w", encoding="utf-8") as f:
        json.dump(final_timeline, f, ensure_ascii=False, indent=2)

    logger.info("[Node6] 完成: %d个片段, %d个活跃clip", len(final_timeline), active_clips)

    # === 字幕关闭区间生成（白名单素材） ===
    _generate_subtitle_suppression(final_timeline, run_dir)

    return TimelineAssemblyOutput(
        final_timeline_path=final_timeline_path,
    )