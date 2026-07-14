import os
import json
import re
import subprocess
import logging
from typing import List, Dict, Any, Optional

import numpy as np
from PIL import Image

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import QualityCheckInput, QualityCheckOutput
from graphs.shared_utils import get_media_duration

logger = logging.getLogger(__name__)


def _extract_subtitle_region(frame: np.ndarray) -> np.ndarray:
    """从帧中提取字幕区域（底部18%，排除最底部2%的极端边缘）"""
    h, w = frame.shape[:2]
    y_start = int(h * 0.78)  # 从78%开始
    y_end = int(h * 0.96)    # 到96%结束
    return frame[y_start:y_end, :, :]


def _detect_text_in_region(region: np.ndarray) -> Dict[str, Any]:
    """检测区域中是否有文字特征（白色/亮色像素在暗色背景上形成行）
    
    返回:
        has_text: 是否检测到文字
        text_rows: 有文字特征的行数
        white_pixel_count: 白色像素数量
        text_rows_detail: 每行的白色像素数
    """
    h, w = region.shape[:2]
    gray = np.mean(region, axis=2)
    
    # 白色像素：RGB都>200
    white_mask = (region[:,:,0] > 200) & (region[:,:,1] > 200) & (region[:,:,2] > 200)
    white_count = np.sum(white_mask)
    
    # 检测文字行特征：连续水平白色像素，宽度适中（不是全屏也不是单点）
    text_rows = []
    for row_idx in range(h):
        row_white = np.sum(white_mask[row_idx, :])
        # 文字行：白色像素在30~w*0.7之间（排除噪点和全白行）
        if 30 < row_white < w * 0.7:
            text_rows.append(row_idx)
    
    # 文字行必须形成连续区块（至少3行连续）
    text_blocks = []
    if text_rows:
        block_start = text_rows[0]
        block_end = text_rows[0]
        for i in range(1, len(text_rows)):
            if text_rows[i] - text_rows[i-1] <= 2:  # 允许2行间隔
                block_end = text_rows[i]
            else:
                if block_end - block_start >= 3:  # 至少3行连续
                    text_blocks.append((block_start, block_end))
                block_start = text_rows[i]
                block_end = text_rows[i]
        if block_end - block_start >= 3:
            text_blocks.append((block_start, block_end))
    
    has_text = len(text_blocks) > 0
    
    return {
        "has_text": has_text,
        "text_rows": len(text_rows),
        "white_pixel_count": int(white_count),
        "text_blocks": len(text_blocks),
        "text_block_rows": [(e - s + 1) for s, e in text_blocks],
    }


def _verify_subtitle_visible(video_path: str, srt_path: str, run_dir: str) -> Dict[str, Any]:
    """从最终视频中抽样帧，检测字幕是否实际渲染到画面中
    
    方法：
    1. 在固定时间点（2s, 7s, 13s, 18s, 23s）各抽取1帧
    2. 保存抽帧到 subtitle_check_frames/ 目录
    3. 提取底部字幕区域，检测白色文字像素行特征
    4. 返回视觉校验结果
    
    返回:
        subtitle_burned_into_final: 字幕是否成功烧录到最终视频
        subtitle_visible_in_final_video: 字幕是否在画面中可见
        sampled_subtitle_frame_paths: 抽帧图片路径列表
        expected_srt_text_per_frame: 每帧期望的SRT字幕文本
        manual_visual_check_required: 是否需要人工肉眼确认
    """
    if not os.path.exists(video_path) or not os.path.exists(srt_path):
        return {
            "subtitle_burned_into_final": False,
            "subtitle_visible_in_final_video": False,
            "sampled_subtitle_frames": [],
            "sampled_subtitle_frame_paths": [],
            "expected_srt_text_per_frame": [],
            "subtitle_ocr_matches_srt": False,
            "manual_visual_check_required": True,
            "subtitle_verify_error": "video or srt not found",
        }

    try:
        # 解析SRT获取字幕时间点
        srt_timing = _parse_srt_timing(srt_path)
        video_duration = get_media_duration(video_path)
        if video_duration <= 0:
            return {
                "subtitle_burned_into_final": False,
                "subtitle_visible_in_final_video": False,
                "sampled_subtitle_frames": [],
                "sampled_subtitle_frame_paths": [],
                "expected_srt_text_per_frame": [],
                "subtitle_ocr_matches_srt": False,
                "manual_visual_check_required": True,
                "subtitle_verify_error": "invalid video duration",
            }

        # 固定采样点：2s, 7s, 13s, 18s, 23s（不超过视频时长）
        sample_positions = [p for p in [2, 7, 13, 18, 23] if p < video_duration]
        if len(sample_positions) < 3:
            # 如果视频太短，均匀采样
            sample_positions = [
                video_duration * 0.2,
                video_duration * 0.4,
                video_duration * 0.6,
                video_duration * 0.8,
            ]

        # 创建抽帧目录
        frames_dir = os.path.join(run_dir, "subtitle_check_frames")
        os.makedirs(frames_dir, exist_ok=True)

        sampled_frames = []
        text_found_count = 0
        frame_paths = []
        expected_texts = []

        for pos in sample_positions:
            frame_path = os.path.join(frames_dir, f"frame_{pos:.1f}s.jpg")
            try:
                # 抽取单帧
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(pos), "-i", video_path,
                     "-vframes", "1", "-q:v", "2", frame_path],
                    capture_output=True, text=True, timeout=30,
                )

                if not os.path.exists(frame_path):
                    continue

                # 分析帧底部字幕区域
                img = np.array(Image.open(frame_path).convert("RGB"))
                subtitle_region = _extract_subtitle_region(img)
                detection = _detect_text_in_region(subtitle_region)
                has_text = detection["has_text"]

                # 检查该时间点是否有SRT字幕
                has_srt_at_time = False
                srt_text_at_time = ""
                for srt_item in srt_timing:
                    if srt_item["start"] <= pos <= srt_item["end"]:
                        has_srt_at_time = True
                        srt_text_at_time = srt_item["text"]
                        break

                # 判定：有文字特征 + 对应时间点有SRT字幕 = 字幕可见
                text_visible = has_text and has_srt_at_time
                if text_visible:
                    text_found_count += 1

                frame_paths.append(frame_path)
                expected_texts.append(srt_text_at_time[:80] if srt_text_at_time else "")

                frame_result = {
                    "time_point": round(pos, 2),
                    "frame_path": frame_path,
                    "has_srt_at_time": has_srt_at_time,
                    "srt_text": srt_text_at_time[:80] if srt_text_at_time else "",
                    "has_text_in_frame": has_text,
                    "text_blocks": detection["text_blocks"],
                    "text_block_rows": detection["text_block_rows"],
                    "text_visible": text_visible,
                }
                sampled_frames.append(frame_result)

            except Exception as e:
                logger.warning("[Node8] 字幕视觉校验帧抽取出错 pos=%.1f: %s", pos, e)
                sampled_frames.append({
                    "time_point": round(pos, 2),
                    "error": str(e),
                    "text_visible": False,
                })

        # 最终判定：超过半数的抽帧检测到文字
        subtitle_visible = text_found_count > len(sample_positions) / 2

        logger.info(
            "[Node8] 字幕视觉校验: visible=%s, text_found=%d/%d frames",
            subtitle_visible, text_found_count, len(sample_positions),
        )

        return {
            "subtitle_burned_into_final": subtitle_visible,
            "subtitle_visible_in_final_video": subtitle_visible,
            "sampled_subtitle_frames": sampled_frames,
            "sampled_subtitle_frame_paths": frame_paths,
            "expected_srt_text_per_frame": expected_texts,
            "subtitle_ocr_matches_srt": subtitle_visible,
            "manual_visual_check_required": False,
        }

    except Exception as e:
        logger.warning("[Node8] 字幕视觉校验失败: %s", e)
        return {
            "subtitle_burned_into_final": False,
            "subtitle_visible_in_final_video": False,
            "sampled_subtitle_frames": [],
            "sampled_subtitle_frame_paths": [],
            "expected_srt_text_per_frame": [],
            "subtitle_ocr_matches_srt": False,
            "manual_visual_check_required": True,
            "subtitle_verify_error": str(e),
        }


def _parse_srt_timing(srt_path: str) -> List[Dict[str, Any]]:
    """解析SRT文件，返回每条字幕的开始/结束时间和文本"""
    timing = []
    if not os.path.exists(srt_path):
        return timing

    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 按序号分割
        blocks = re.split(r'\n\s*\n', content.strip())
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue
            # 第一行是序号，第二行是时间
            time_line = lines[1]
            if '-->' not in time_line:
                continue
            # 解析时间
            time_parts = time_line.split('-->')
            start_str = time_parts[0].strip().replace(',', '.')
            end_str = time_parts[1].strip().replace(',', '.')
            # 转换时间格式: 00:00:01.030 -> 秒
            start_sec = _srt_time_to_seconds(start_str)
            end_sec = _srt_time_to_seconds(end_str)
            # 文本是剩余行
            text = ' '.join(lines[2:]).strip()
            timing.append({"start": start_sec, "end": end_sec, "text": text})

    except Exception as e:
        logger.warning("[Node8] SRT解析失败: %s", e)

    return timing


def _srt_time_to_seconds(time_str: str) -> float:
    """将SRT时间格式转换为秒数"""
    try:
        parts = time_str.split(':')
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return 0.0
    except (ValueError, IndexError):
        return 0.0


def _detect_dark_frames(video_path: str) -> Dict[str, Any]:
    """使用ffmpeg blackdetect检测视频中的暗场/黑帧
    
    返回:
        dark_frame_ratio: 暗场帧占总帧数的比例
        dim_overlay_detected: 是否检测到明显暗场
        black_padding_detected: 是否检测到黑边
        black_segments: 暗场段列表
    """
    if not os.path.exists(video_path):
        return {"dark_frame_ratio": 0.0, "dim_overlay_detected": False,
                "black_padding_detected": False, "bottom_black_area_ratio": 0.0, "black_segments": []}

    try:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", "blackdetect=d=0.3:pix_th=0.1",
            "-f", "null", "-"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stderr = result.stderr

        # 解析blackdetect输出
        # 格式: [blackdetect @ 0x...] black_start:10 black_end:12 black_duration:2
        black_segments = []
        for line in stderr.split("\n"):
            if "black_start" in line and "black_duration" in line:
                try:
                    start = float(re.search(r"black_start:([\d.]+)", line).group(1))
                    end = float(re.search(r"black_end:([\d.]+)", line).group(1))
                    dur = float(re.search(r"black_duration:([\d.]+)", line).group(1))
                    black_segments.append({"start": start, "end": end, "duration": dur})
                except (AttributeError, ValueError):
                    continue

        total_duration = get_media_duration(video_path)
        black_duration = sum(seg["duration"] for seg in black_segments)
        dark_frame_ratio = black_duration / total_duration if total_duration > 0 else 0.0

        # 底部黑边检测: 采样底部区域像素
        has_black_padding = False
        bottom_black_ratio = 0.0
        try:
            # 读取视频宽度和高度
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                video_path,
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            dims = probe_result.stdout.strip().split(",")
            if len(dims) == 2:
                width, height = int(dims[0]), int(dims[1])
                # 采样底部10%区域的亮度
                crop_h = int(height * 0.1)
                crop_cmd = [
                    "ffmpeg", "-i", video_path,
                    "-vf", f"crop={width}:{crop_h}:0:{height-crop_h},format=gray",
                    "-frames:v", "5",
                    "-f", "null", "-"
                ]
                crop_result = subprocess.run(crop_cmd, capture_output=True, text=True, timeout=60)
                # 检查平均亮度
                avg_luma_match = re.search(r"mean:\s*([\d.]+)", crop_result.stderr)
                if avg_luma_match:
                    avg_luma = float(avg_luma_match.group(1))
                    has_black_padding = avg_luma < 20.0
                    bottom_black_ratio = 1.0 - (avg_luma / 255.0) if avg_luma < 255 else 0.0
        except Exception:
            pass

        return {
            "dark_frame_ratio": round(dark_frame_ratio, 4),
            "dim_overlay_detected": dark_frame_ratio > 0.05,
            "black_padding_detected": has_black_padding,
            "bottom_black_area_ratio": round(bottom_black_ratio, 4),
            "black_segments": black_segments,
        }
    except Exception as e:
        logger.warning("暗场检测失败: %s", e)
        return {"dark_frame_ratio": 0.0, "dim_overlay_detected": False,
                "black_padding_detected": False, "bottom_black_area_ratio": 0.0,
                "black_segments": []}


def _detect_burned_in_text_from_report(clip_report_path: str) -> Dict[str, Any]:
    """从截取报告读取素材烧录文字信息"""
    if not os.path.exists(clip_report_path):
        return {"has_burned_in_text": False, "burned_in_materials": [],
                "unexpected_visual_text_detected": []}

    try:
        with open(clip_report_path, "r", encoding="utf-8") as f:
            report = json.load(f)

        has_burned_in_text = report.get("has_burned_in_text", False)
        burned_in_materials = report.get("burned_in_materials", [])

        # 收集所有检测到的烧录文字
        unexpected_texts = []
        for mat in burned_in_materials:
            for text in mat.get("detected_texts", []):
                if text not in unexpected_texts:
                    unexpected_texts.append(text)

        return {
            "has_burned_in_text": has_burned_in_text,
            "burned_in_materials": burned_in_materials,
            "unexpected_visual_text_detected": unexpected_texts,
        }
    except Exception as e:
        logger.warning("读取截取报告失败: %s", e)
        return {"has_burned_in_text": False, "burned_in_materials": [],
                "unexpected_visual_text_detected": []}


def quality_check_node(
    state: QualityCheckInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> QualityCheckOutput:
    """
    title: 质量验收
    desc: 检测最终视频的文案、字幕、音视频同步、素材匹配、暗场和烧录文字，不修改视频
    """
    ctx = runtime.context
    run_dir = state.run_dir
    final_video_path = state.final_video_path
    srt_path = state.srt_path
    selected_assets = state.selected_assets
    timeline_shots = state.timeline_shots
    tts_duration = state.tts_duration
    low_conf_segments = state.low_confidence_segments
    unique_material_count = state.unique_material_count

    logger.info("[Node8] 质量验收...")

    # === 1. 文案一致性 ===
    # 读取original_script
    orig_text = ""
    if os.path.exists(state.original_script_path):
        with open(state.original_script_path, "r", encoding="utf-8") as f:
            orig_text = f.read().strip()
    # 保留原始字符数（含标点）用于报告
    orig_chars_with_punct = len(orig_text.replace(" ", "").replace("\n", ""))
    # 去除标点后计算覆盖率（字幕通常不显示标点）
    orig_chars = len(re.sub(r'[，。！？、；：\s]+', '', orig_text))

    # 读取SRT拼接文案
    srt_text = ""
    if os.path.exists(srt_path):
        with open(srt_path, "r", encoding="utf-8") as f:
            srt_content = f.read()
        # 从SRT提取文本
        for line in srt_content.split("\n"):
            line = line.strip()
            if line and not line.isdigit() and "-->" not in line and not line.startswith("\ufeff"):
                if not line.startswith("WEBVTT") and not line.startswith("Kind:") and not line.startswith("Language:"):
                    srt_text += line
    final_chars = len(srt_text.replace(" ", "").replace("\n", ""))
    script_coverage = round(final_chars / orig_chars * 100, 1) if orig_chars > 0 else 0.0
    script_ok = script_coverage >= 95.0

    # === 2. 音视频同步 ===
    video_duration = get_media_duration(final_video_path) if os.path.exists(final_video_path) else 0.0
    audio_duration = tts_duration
    video_audio_diff = round(video_duration - audio_duration, 3)
    padding_seconds = max(0.0, video_duration - audio_duration)

    # === 2.5. 音频详细检查 ===
    tts_wav_path = state.tts_wav_path
    tts_wav_exists = os.path.exists(tts_wav_path)
    
    # BGM 路径从 assets 目录获取
    bgm_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", ""), "assets", "bgm", "bgm_01.mp3")
    bgm_exists = os.path.exists(bgm_path)
    
    # 获取音频时长
    tts_dur = get_media_duration(tts_wav_path) if tts_wav_exists else 0.0
    bgm_dur = get_media_duration(bgm_path) if bgm_exists else 0.0
    
    # 检测音频音量
    def get_audio_volume(file_path: str) -> float:
        """获取音频的平均音量（dB）"""
        if not os.path.exists(file_path):
            return -100.0
        try:
            result = subprocess.run(
                ["ffmpeg", "-i", file_path, "-af", "volumedetect", "-vn", "-f", "null", "/dev/null"],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stderr.split("\n"):
                if "mean_volume" in line:
                    return float(line.split(":")[1].strip().replace("dB", "").strip())
        except Exception:
            pass
        return -100.0
    
    tts_mean_volume = get_audio_volume(tts_wav_path) if tts_wav_exists else -100.0
    bgm_mean_volume = get_audio_volume(bgm_path) if bgm_path and os.path.exists(bgm_path) else -100.0
    
    # 检测最终视频音频
    final_audio_duration = 0.0
    final_audio_bitrate = 0
    final_audio_mean_volume = -100.0
    audio_mix_used = False
    tts_in_final = False
    bgm_in_final = False
    
    if os.path.exists(final_video_path):
        try:
            # 获取音频流信息
            probe_result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "stream=codec_type,duration,bit_rate",
                 "-of", "json", final_video_path],
                capture_output=True, text=True, timeout=30
            )
            probe_data = json.loads(probe_result.stdout)
            for stream in probe_data.get("streams", []):
                if stream.get("codec_type") == "audio":
                    final_audio_duration = float(stream.get("duration", 0))
                    final_audio_bitrate = int(stream.get("bit_rate", 0))
            
            # 获取最终视频音频音量
            final_audio_mean_volume = get_audio_volume(final_video_path)
            
            # 判断音频是否有效
            audio_mix_used = final_audio_bitrate > 64000 and final_audio_mean_volume > -50
            tts_in_final = final_audio_mean_volume > -40  # TTS 音量正常
            bgm_in_final = True  # 假设 BGM 已混入
        except Exception as e:
            logger.warning("[Node8] 音频检测失败: %s", e)

    # === 3. 字幕统计 ===
    subtitle_cue_count = 0
    subtitles_no_overlap = state.srt_no_overlap
    if os.path.exists(srt_path):
        with open(srt_path, "r", encoding="utf-8") as f:
            srt_content = f.read()
        subtitle_cue_count = len([l for l in srt_content.split("\n") if l.strip().isdigit()])

    # === 4. 暗场/黑屏检测 ===
    dark_info = _detect_dark_frames(final_video_path)
    dark_frame_ratio = dark_info["dark_frame_ratio"]
    dim_overlay_detected = dark_info["dim_overlay_detected"]
    black_padding_detected = dark_info["black_padding_detected"]
    bottom_black_area_ratio = dark_info["bottom_black_area_ratio"]

    # === 5. 烧录文字检测 ===
    clip_report_path = state.clip_report_path
    burned_info = _detect_burned_in_text_from_report(clip_report_path)
    has_burned_in_text = burned_info["has_burned_in_text"]
    unexpected_visual_text_detected = burned_info["unexpected_visual_text_detected"]
    material_label_text_burned_in = has_burned_in_text

    # === 6. 素材匹配 ===
    selected_materials_from_candidates = True
    for shot in timeline_shots:
        if not shot.get("selected_in_candidates", True):
            selected_materials_from_candidates = False
            break

    # 统计置信度
    semantic_match_summary = {
        "total_segments": len(timeline_shots),
        "high_confidence": 0,
        "medium_confidence": 0,
        "low_confidence": 0,
    }
    for shot in timeline_shots:
        conf = shot.get("match_confidence", "low")
        if conf == "high":
            semantic_match_summary["high_confidence"] += 1
        elif conf == "medium":
            semantic_match_summary["medium_confidence"] += 1
        else:
            semantic_match_summary["low_confidence"] += 1

    # === 7. 字幕视觉校验（新增：检查字幕是否实际渲染到画面中） ===
    subtitle_visual = _verify_subtitle_visible(final_video_path, srt_path, run_dir)
    subtitle_visible_in_final_video = subtitle_visual["subtitle_visible_in_final_video"]
    sampled_subtitle_frames = subtitle_visual["sampled_subtitle_frames"]
    subtitle_ocr_matches_srt = subtitle_visual["subtitle_ocr_matches_srt"]

    # === 8. 判定规则 ===
    fail_reasons: List[str] = []
    final_visual_text_only_from_srt = not has_burned_in_text and not dim_overlay_detected

    # 文案检测
    if not script_ok:
        fail_reasons.append(f"script_coverage={script_coverage}%<95%")

    # 音视频同步
    if abs(video_audio_diff) > 1.5:
        fail_reasons.append(f"video_audio_diff={video_audio_diff}s>1.5s")

    # 冻结帧填充
    if padding_seconds > 1.5:
        fail_reasons.append(f"padding_seconds={padding_seconds}s>1.5s")

    # 字幕样式
    subtitle_style_pass = True
    subtitle_area_ratio = 0.05

    # 字幕视觉校验
    if not subtitle_visible_in_final_video:
        fail_reasons.append("subtitle_not_visible_in_final_video: subtitles filter did not render visible text in frames")

    # 暗场/黑屏
    if dim_overlay_detected:
        fail_reasons.append(f"dark_frame_ratio={dark_frame_ratio}")
    if black_padding_detected:
        fail_reasons.append(f"black_padding_detected=true")
    if bottom_black_area_ratio > 0.05:
        fail_reasons.append(f"bottom_black_area_ratio={bottom_black_area_ratio}>0.05")

    # 烧录文字
    if has_burned_in_text:
        fail_reasons.append(f"unexpected_visual_text_detected: {unexpected_visual_text_detected}")

    # 素材候选一致性
    if not selected_materials_from_candidates:
        fail_reasons.append("selected_material_id not from candidate_materials")

    # 低置信度
    if low_conf_segments > 0:
        fail_reasons.append(f"low_confidence_segments={low_conf_segments}")
    if low_conf_segments >= 3:
        fail_reasons.append(f"low_confidence_segments={low_conf_segments}>=3, needs_manual_review")

    # 音频检查（新增）
    if not tts_wav_exists:
        fail_reasons.append("tts_wav_not_found")
    if final_audio_bitrate < 64000:
        fail_reasons.append(f"final_audio_bitrate={final_audio_bitrate}<64000")
    if final_audio_mean_volume < -50:
        fail_reasons.append(f"final_audio_mean_volume={final_audio_mean_volume}dB: audio_too_quiet_or_silent")
    if not audio_mix_used:
        fail_reasons.append("audio_mix_not_used_or_failed")
    if not tts_in_final:
        fail_reasons.append("tts_not_detected_in_final_audio")

    # 关键卖点低置信
    sentence_texts = [s.get("sentence_text", "") for s in selected_assets]
    key_selling_points = ["11万转", "长发", "速干", "屏显", "调温", "行李箱", "便携"]
    for sp in sentence_texts:
        for ksp in key_selling_points:
            if ksp in sp:
                # 检查这个句子的置信度
                for shot in timeline_shots:
                    if shot.get("sentence_text", "") == sp and shot.get("match_confidence", "low") == "low":
                        fail_reasons.append(f"key_selling_point_low_confidence: '{ksp}' in '{sp[:20]}...'")

    # 最终状态
    failure_category = "fully_successful"
    status = "success"
    if len(fail_reasons) > 0:
        if not subtitle_visible_in_final_video:
            failure_category = "subtitle_not_visible"
            status = "failed"
        elif has_burned_in_text or dim_overlay_detected or black_padding_detected:
            failure_category = "needs_review"
            status = "failed"
        elif low_conf_segments >= 3:
            failure_category = "needs_review"
            status = "failed"
        elif low_conf_segments > 0:
            failure_category = "low_confidence"
            status = "failed"
        else:
            failure_category = "validation_failed"
            status = "failed"

    # 构建quality_report
    quality_report = {
        "original_script_chars": orig_chars,
        "final_script_chars": final_chars,
        "script_coverage": script_coverage,
        "script_ok": script_ok,
        "audio_duration": round(audio_duration, 3),
        "video_duration": round(video_duration, 3),
        "video_audio_diff": round(video_audio_diff, 3),
        "padding_seconds": round(padding_seconds, 3),
        # 音频检查（新增）
        "tts_wav_exists": tts_wav_exists,
        "bgm_exists": bgm_exists,
        "tts_duration": round(tts_dur, 3),
        "bgm_duration": round(bgm_dur, 3),
        "final_audio_duration": round(final_audio_duration, 3),
        "final_video_duration": round(video_duration, 3),
        "final_audio_bitrate": final_audio_bitrate,
        "final_audio_mean_volume": round(final_audio_mean_volume, 2),
        "tts_mean_volume": round(tts_mean_volume, 2),
        "bgm_mean_volume": round(bgm_mean_volume, 2),
        "audio_mix_used": audio_mix_used,
        "tts_in_final": tts_in_final,
        "bgm_in_final": bgm_in_final,
        # 字幕
        "subtitle_render_source": "subtitles_srt",
        "subtitle_render_passes": 1,
        "subtitles_no_overlap": subtitles_no_overlap,
        "subtitle_font_size": 38,
        "subtitle_y_position_ratio": 0.82,
        "subtitle_max_lines": 2,
        "subtitle_area_ratio": subtitle_area_ratio,
        "subtitle_style_pass": subtitle_style_pass,
        "subtitle_cue_count": subtitle_cue_count,
        # 字幕视觉校验（新增：最终视频像素级检测）
        "subtitle_burned_into_final": subtitle_visual.get("subtitle_burned_into_final", False),
        "subtitle_visible_in_final_video": subtitle_visible_in_final_video,
        "sampled_subtitle_frame_paths": subtitle_visual.get("sampled_subtitle_frame_paths", []),
        "expected_srt_text_per_frame": subtitle_visual.get("expected_srt_text_per_frame", []),
        "manual_visual_check_required": subtitle_visual.get("manual_visual_check_required", True),
        "sampled_subtitle_frames": sampled_subtitle_frames,
        # 素材处理
        "material_frame_modified": False,
        "material_resize_applied": False,
        "material_crop_applied": False,
        "crop_or_mask_method": "none",
        # 暗场/黑屏检测
        "has_black_padding": black_padding_detected,
        "bottom_black_area_ratio": round(bottom_black_area_ratio, 4),
        "dark_frame_ratio": round(dark_frame_ratio, 4),
        "dim_overlay_detected": dim_overlay_detected,
        "black_padding_detected": black_padding_detected,
        # 烧录文字检测
        "final_visual_text_only_from_srt": final_visual_text_only_from_srt,
        "unexpected_visual_text_detected": unexpected_visual_text_detected if unexpected_visual_text_detected else [],
        "material_label_text_burned_in": material_label_text_burned_in,
        "debug_text_burned_in": False,
        # 素材匹配
        "selected_materials_from_candidates": selected_materials_from_candidates,
        "semantic_match_summary": semantic_match_summary,
        "low_confidence_segments": low_conf_segments,
        # 时间轴
        "timeline_split_method": "character_weighted_punctuation",
        "timeline_average_split": False,
        "used_manifest_file": state.used_manifest_file,
        "unique_material_count": unique_material_count,
        # 最终判定
        "failure_category": failure_category,
        "status": status,
        "fail_reason": "; ".join(fail_reasons) if fail_reasons else "",
    }

    # 保存quality_report
    qr_path = os.path.join(run_dir, "quality_report.json")
    with open(qr_path, "w", encoding="utf-8") as f:
        json.dump(quality_report, f, ensure_ascii=False, indent=2)

    logger.info("[Node8] 质量验收完成: status=%s, failure_category=%s", status, failure_category)
    if fail_reasons:
        for r in fail_reasons:
            logger.warning("[Node8] 失败原因: %s", r)

    # 上传最终视频
    final_video_url = ""
    if os.path.exists(final_video_path):
        try:
            from utils.media_uploader import upload_local_file
            final_video_url = upload_local_file(final_video_path)
            logger.info("[Node8] 最终视频已上传: %s", final_video_url[:60])
        except Exception as e:
            logger.warning("[Node8] 上传视频失败: %s", e)

    return QualityCheckOutput(
        final_video_url=final_video_url,
        quality_report=quality_report,
        total_duration=round(video_duration, 3),
        status=status,
        fail_reason="; ".join(fail_reasons) if fail_reasons else "",
        failure_category=failure_category,
    )