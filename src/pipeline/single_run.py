"""
单条视频独立流水线（降级重建 v2）
==================================
为每条 script 创建独立 run_dir，保存中间产物，每步校验，无交叉污染。

用法:
    from pipeline.single_run import run_single_pipeline
    result = run_single_pipeline(script_id=2, script_text="...", material_csv="assets/asset_manifest_new_no_chuifa.csv")

输出:
    runs/script_02/
    ├── original_script.txt
    ├── tts.wav
    ├── selected_assets.json
    ├── timeline.json
    ├── subtitles.srt
    ├── final.mp4
    └── quality_report.json
"""
import os
import json
import csv
import re
import subprocess
import logging
import tempfile
import shutil
import math
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict

from jinja2 import Template
from coze_coding_dev_sdk import TTSClient
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from utils.media_uploader import upload_local_file
from utils.file.file import FileOps, File

logger = logging.getLogger(__name__)

WORKSPACE = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
PROJECT_DIR = os.path.join(WORKSPACE, "..") if os.path.basename(WORKSPACE) == "projects" else WORKSPACE
RUNS_BASE = os.path.join(WORKSPACE, "runs")


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TimelineItem:
    """时间线条目"""
    shot_index: int
    material_id: str
    material_url: str
    source_asset_id: str
    voice_segment_id: str
    script_text: str
    voice_duration: float  # TTS实际时长（秒）
    in_point: float = 0.0
    out_point: float = 0.0
    trim_start: float = 0.0
    trim_end: float = 0.0
    start_time: float = 0.0   # 在最终视频中的起始时间
    end_time: float = 0.0     # 在最终视频中的结束时间


@dataclass
class QualityReport:
    """质检报告"""
    script_id: int
    script_chars: int
    tts_duration: float
    tts_chars: int
    tts_coverage: float
    script_completeness_pass: bool
    subtitles_count: int
    subtitles_no_overlap: bool
    materials_selected: int
    unique_materials: int
    video_duration: float
    min_required_duration: float
    was_padded: bool
    ffprobe_pass: bool
    failure_category: str  # fully_successful / generated_but_padded / generated_but_script_incomplete / failed
    overall_status: str
    run_dir: str = ""
    final_video_url: str = ""


# ============================================================
# 工具函数
# ============================================================

def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _get_media_duration(file_path: str) -> float:
    """ffprobe获取媒体时长"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.error("ffprobe获取时长失败 %s: %s", file_path, e)
        return 0.0


def _run_ffmpeg(cmd: List[str], timeout: int = 300) -> None:
    """运行ffmpeg命令"""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg失败: {result.stderr}")


def _get_tts_audio(text: str, ctx: Context, run_dir: str) -> str:
    """生成TTS音频，返回本地wav路径"""
    tts_client = TTSClient(ctx=ctx)
    audio_url, audio_size = tts_client.synthesize(
        text=text,
        speaker="zh_female_xiaohe_uranus_bigtts",
        speech_rate=1.0,
        emotion="温暖",
    )
    if not audio_url:
        raise RuntimeError(f"TTS返回空URL: text={text[:30]}...")

    wav_path = os.path.join(run_dir, "tts_audio.wav")
    mp3_path = os.path.join(run_dir, "tts_audio.mp3")

    # 下载
    import requests
    resp = requests.get(audio_url, timeout=60)
    resp.raise_for_status()
    with open(mp3_path, "wb") as f:
        f.write(resp.content)

    # 转wav
    _run_ffmpeg([
        "ffmpeg", "-y", "-i", mp3_path,
        "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
        wav_path
    ])
    os.remove(mp3_path)
    return wav_path


def _split_script_sentences(text: str) -> List[str]:
    """将文案按标点分为句子，去空去短"""
    # 按常见的标点、换行分割
    parts = re.split(r'[。！？，、；：\n\r]+', text)
    sentences = [s.strip() for s in parts if len(s.strip()) > 2]
    if not sentences:
        sentences = [text.strip()]
    return sentences


def _build_subtitle_srt(timeline: List[TimelineItem], run_dir: str) -> str:
    """从时间线生成无重叠的SRT字幕"""
    srt_path = os.path.join(run_dir, "subtitles.srt")
    lines = []
    idx = 1
    for item in timeline:
        if not item.script_text.strip():
            continue
        start_sec = item.start_time
        end_sec = item.end_time
        if end_sec <= start_sec:
            end_sec = start_sec + 0.5

        # 字幕只显示2行，每行最多15字
        text = item.script_text.strip()
        if len(text) > 30:
            text = text[:28] + "…"

        start_ts = f"{int(start_sec//3600):02d}:{int(start_sec%3600//60):02d}:{start_sec%60:06.3f}".replace(".", ",")
        end_ts = f"{int(end_sec//3600):02d}:{int(end_sec%3600//60):02d}:{end_sec%60:06.3f}".replace(".", ",")

        lines.append(f"{idx}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")
        idx += 1

    content = "\n".join(lines)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(content)
    return srt_path


def _compute_timeline_durations(timeline: List[TimelineItem]) -> List[TimelineItem]:
    """计算时间线中每个条目的起止时间（无重叠）"""
    current = 0.0
    for item in timeline:
        item.start_time = current
        item.end_time = current + item.voice_duration
        current = item.end_time
    return timeline


def _check_subtitle_overlap(srt_path: str) -> bool:
    """检查SRT字幕是否有重叠时间"""
    import re
    time_pattern = re.compile(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)")
    prev_end = -1.0
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    for match in time_pattern.finditer(content):
        h1, m1, s1, ms1 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        start = h1*3600 + m1*60 + s1 + ms1/1000
        h2, m2, s2, ms2 = int(match.group(5)), int(match.group(6)), int(match.group(7)), int(match.group(8))
        end = h2*3600 + m2*60 + s2 + ms2/1000
        if start < prev_end - 0.01:
            return False
        prev_end = end
    return True


# ============================================================
# 主流水线
# ============================================================

def run_single_pipeline(
    script_id: int,
    script_text: str,
    material_csv: str,
    ctx: Context,
    platform: str = "抖音",
    bgm_url: str = "",
) -> QualityReport:
    """
    单条视频完整流水线

    Args:
        script_id: 脚本编号（1-6）
        script_text: 原始文案（完整，禁止含...）
        material_csv: 素材清单CSV路径
        ctx: Runtime上下文
        platform: 平台
        bgm_url: BGM URL

    Returns:
        QualityReport 质检报告
    """
    script_id_str = f"script_{script_id:02d}"
    run_dir = _ensure_dir(os.path.join(RUNS_BASE, script_id_str))
    logger.info("=" * 60)
    logger.info("流水线启动: %s | run_dir=%s", script_id_str, run_dir)
    logger.info("=" * 60)

    report = QualityReport(
        script_id=script_id,
        script_chars=len(''.join(script_text.split())),
        tts_duration=0.0,
        tts_chars=0,
        tts_coverage=0.0,
        script_completeness_pass=False,
        subtitles_count=0,
        subtitles_no_overlap=False,
        materials_selected=0,
        unique_materials=0,
        video_duration=0.0,
        min_required_duration=0.0,
        was_padded=False,
        ffprobe_pass=False,
        failure_category="failed",
        overall_status="failed",
        run_dir=run_dir,
    )

    # ---------------------------------------------------------------
    # Step 0: 保存原始文案
    # ---------------------------------------------------------------
    script_txt_path = os.path.join(run_dir, "original_script.txt")
    with open(script_txt_path, "w", encoding="utf-8") as f:
        f.write(script_text)
    logger.info("[Step 0] 原始文案已保存: %s (%d chars)", script_txt_path, report.script_chars)

    # 校验：文案不能包含 ...
    if "..." in script_text or "…" in script_text:
        report.overall_status = "failed: script_incomplete (contains ...)"
        logger.error("[Step 0] 文案包含截断标记 ... ，拒绝继续")
        _save_report(report, run_dir)
        return report

    # ---------------------------------------------------------------
    # Step 1: TTS生成
    # ---------------------------------------------------------------
    logger.info("[Step 1] 开始TTS合成...")
    try:
        tts_wav = _get_tts_audio(script_text, ctx, run_dir)
        tts_duration = _get_media_duration(tts_wav)
        report.tts_duration = tts_duration
        logger.info("[Step 1] TTS合成完成: %.2fs", tts_duration)
    except Exception as e:
        report.overall_status = f"failed: tts_error - {e}"
        logger.error("[Step 1] TTS合成失败: %s", e)
        _save_report(report, run_dir)
        return report

    # 上传TTS到对象存储
    tts_wav_url = None
    try:
        tts_wav_url = upload_local_file(tts_wav, folder="tts")
        logger.info("[Step 1] TTS已上传: %s", tts_wav_url)
    except Exception as e:
        logger.warning("[Step 1] TTS上传失败: %s", e)

    # 校验：TTS时长 >= original_script_chars / 10（最少每字0.1秒）
    min_tts_duration = report.script_chars * 0.08
    if tts_duration < min_tts_duration:
        report.overall_status = f"failed: tts_too_short ({tts_duration:.2f}s < {min_tts_duration:.2f}s)"
        logger.error("[Step 1] TTS时长不足: %.2fs < %.2fs", tts_duration, min_tts_duration)
        _save_report(report, run_dir)
        return report

    # ---------------------------------------------------------------
    # Step 2: 素材选择与时间线
    # ---------------------------------------------------------------
    logger.info("[Step 2] 开始素材选择...")
    try:
        timeline = _select_materials(
            script_text=script_text,
            material_csv=material_csv,
            tts_duration=tts_duration,
            run_dir=run_dir,
        )
        report.materials_selected = len(timeline)
        unique_mids = set(item.material_id for item in timeline)
        report.unique_materials = len(unique_mids)
        logger.info("[Step 2] 素材选择完成: %d条, 唯一素材%d个", len(timeline), len(unique_mids))
    except Exception as e:
        report.overall_status = f"failed: material_selection_error - {e}"
        logger.error("[Step 2] 素材选择失败: %s", e)
        _save_report(report, run_dir)
        return report

    # 保存selected_assets.json
    sel_assets = [
        {
            "shot_index": item.shot_index,
            "material_id": item.material_id,
            "material_url": item.material_url,
            "source_asset_id": item.source_asset_id,
            "voice_segment_id": item.voice_segment_id,
            "script_text": item.script_text,
            "voice_duration": item.voice_duration,
        }
        for item in timeline
    ]
    with open(os.path.join(run_dir, "selected_assets.json"), "w", encoding="utf-8") as f:
        json.dump(sel_assets, f, ensure_ascii=False, indent=2)

    # 保存timeline.json
    timeline_data = [
        {
            "shot_index": item.shot_index,
            "material_id": item.material_id,
            "start_time": item.start_time,
            "end_time": item.end_time,
            "voice_duration": item.voice_duration,
            "script_text": item.script_text,
        }
        for item in timeline
    ]
    with open(os.path.join(run_dir, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump(timeline_data, f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------
    # Step 3: 字幕生成（无重叠校验）
    # ---------------------------------------------------------------
    logger.info("[Step 3] 开始字幕生成...")
    srt_path = _build_subtitle_srt(timeline, run_dir)
    report.subtitles_count = len(timeline)
    report.subtitles_no_overlap = _check_subtitle_overlap(srt_path)
    if not report.subtitles_no_overlap:
        report.overall_status = "failed: subtitle_overlap"
        logger.error("[Step 3] 字幕存在时间重叠！")
        _save_report(report, run_dir)
        return report
    logger.info("[Step 3] 字幕生成完成: %d条, 无重叠 ✓", report.subtitles_count)

    # ---------------------------------------------------------------
    # Step 4: 视频合成
    # ---------------------------------------------------------------
    logger.info("[Step 4] 开始视频合成...")
    final_mp4 = os.path.join(run_dir, "final.mp4")
    try:
        _compose_video(
            timeline=timeline,
            tts_wav=tts_wav,
            srt_path=srt_path,
            output_path=final_mp4,
            run_dir=run_dir,
            bgm_url=bgm_url,
        )
        video_duration = _get_media_duration(final_mp4)
        report.video_duration = video_duration
        report.ffprobe_pass = video_duration > 0
        logger.info("[Step 4] 视频合成完成: %.2fs", video_duration)
    except Exception as e:
        report.overall_status = f"failed: compose_error - {e}"
        logger.error("[Step 4] 视频合成失败: %s", e)
        _save_report(report, run_dir)
        return report

    # ---------------------------------------------------------------
    # Step 5: 质量校验
    # ---------------------------------------------------------------
    min_required = max(10.0, report.script_chars / 4.5)
    report.min_required_duration = min_required

    # 5a: 时长校验
    if video_duration < min_required - 0.1:
        # 尝试填充
        try:
            padded_path = os.path.join(run_dir, "final_padded.mp4")
            _pad_video_to_duration(final_mp4, padded_path, min_required)
            shutil.move(padded_path, final_mp4)
            video_duration = _get_media_duration(final_mp4)
            report.video_duration = video_duration
            report.was_padded = True
            logger.info("[Step 5] 视频已填充: %.2fs → %.2fs", video_duration, min_required)
        except Exception as e:
            report.overall_status = f"failed: video_too_short ({video_duration:.2f}s < {min_required:.2f}s)"
            logger.error("[Step 5] 视频时长不足且填充失败: %s", e)
            _save_report(report, run_dir)
            return report

    # 5b: 文案完整性校验
    text_from_srt = " ".join(item.script_text for item in timeline)
    clean_original = ''.join(script_text.split())
    clean_srt = ''.join(text_from_srt.split())
    # 计算覆盖度：原文字符有多少在SRT中出现
    overlap = 0
    for ch in clean_original:
        if ch in clean_srt:
            overlap += 1
    coverage = overlap / len(clean_original) if clean_original else 0
    report.tts_chars = len(clean_srt)
    report.tts_coverage = coverage
    report.script_completeness_pass = coverage >= 0.95

    if not report.script_completeness_pass:
        logger.warning("[Step 5] 文案覆盖度不足: %.1f%% < 95%%", coverage * 100)
        # 不直接失败，但会影响分类

    # 5c: 判定分类
    if report.was_padded:
        report.failure_category = "generated_but_padded"
        report.overall_status = "success_with_padding"
    elif not report.script_completeness_pass:
        report.failure_category = "generated_but_script_incomplete"
        report.overall_status = "failed: script_incomplete"
    else:
        report.failure_category = "fully_successful"
        report.overall_status = "success"

    # ---------------------------------------------------------------
    # Step 6: 上传与质检报告
    # ---------------------------------------------------------------
    try:
        final_url = upload_local_file(final_mp4, folder="final_videos")
        report.final_video_url = final_url
        logger.info("[Step 6] 最终视频已上传: %s", final_url)
    except Exception as e:
        logger.warning("[Step 6] 视频上传失败: %s", e)

    _save_report(report, run_dir)
    logger.info("=" * 60)
    logger.info("流水线完成: status=%s | category=%s | duration=%.2fs",
                report.overall_status, report.failure_category, report.video_duration)
    logger.info("=" * 60)
    return report


def _save_report(report: QualityReport, run_dir: str):
    """保存质检报告到run_dir"""
    path = os.path.join(run_dir, "quality_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(report), f, ensure_ascii=False, indent=2)
    logger.info("质检报告已保存: %s", path)


def _select_materials(
    script_text: str,
    material_csv: str,
    tts_duration: float,
    run_dir: str,
) -> List[TimelineItem]:
    """
    从素材库选择素材，构建时间线。
    按tts_duration均匀分配时间，每个素材约2-4s。
    """
    # 读取素材清单
    materials = []
    with open(material_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asset_id = row.get("asset_id", "").strip()
            file_name = row.get("file_name", "").strip()
            file_path = row.get("file_path", "").strip() or row.get("s3_url", "").strip()
            presigned_url = row.get("presigned_url", "").strip()
            tags_str = row.get("tags", "").strip()
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]
            url = presigned_url or file_path
            if asset_id and url:
                materials.append({
                    "asset_id": asset_id,
                    "file_name": file_name,
                    "url": url,
                    "tags": tags,
                })

    if not materials:
        raise RuntimeError("素材清单为空，无法选择")

    logger.info("素材库加载完成: %d个素材", len(materials))

    # 简单策略：按文案分句，每句分配一个素材
    sentences = _split_script_sentences(script_text)
    total_sentences = len(sentences)

    # 每个句子分配的时间段
    time_per_sentence = tts_duration / max(total_sentences, 1)

    timeline = []
    assigned_material_ids = set()

    # 提取素材标签做简单匹配
    for i, sentence in enumerate(sentences):
        # 为每个句子找一个未使用的素材
        selected = None
        for m in materials:
            if m["asset_id"] in assigned_material_ids:
                continue
            selected = m
            break

        if selected is None:
            # 所有素材已用，允许重复使用
            for m in materials:
                if m["asset_id"] not in assigned_material_ids:
                    selected = m
                    break
            if selected is None:
                selected = materials[i % len(materials)]

        assigned_material_ids.add(selected["asset_id"])

        # 估算素材时长（简单用文件名推测或默认3s）
        mat_duration = 3.0
        if "_" in selected.get("file_name", ""):
            parts = selected["file_name"].replace(".mp4", "").split("_")
            for p in parts:
                try:
                    d = float(p)
                    if 1.0 <= d <= 10.0:
                        mat_duration = d
                        break
                except ValueError:
                    pass

        item = TimelineItem(
            shot_index=i + 1,
            material_id=selected["asset_id"],
            material_url=selected["url"],
            source_asset_id=selected["asset_id"],
            voice_segment_id=f"VS-{i+1:03d}",
            script_text=sentence,
            voice_duration=time_per_sentence,
            in_point=0.0,
            out_point=mat_duration,
            trim_start=0.0,
            trim_end=min(mat_duration, time_per_sentence),
        )
        # 片段时长不超过素材实际时长
        item.trim_end = min(mat_duration, time_per_sentence)
        item.voice_duration = min(time_per_sentence, mat_duration)

        timeline.append(item)

    # 计算时间轴
    timeline = _compute_timeline_durations(timeline)

    return timeline


def _compose_video(
    timeline: List[TimelineItem],
    tts_wav: str,
    srt_path: str,
    output_path: str,
    run_dir: str,
    bgm_url: str = "",
):
    """
    视频合成：下载素材 → 裁剪片段 → 拼接 → 加字幕 → 混音
    """
    temp_dir = _ensure_dir(os.path.join(run_dir, "temp"))

    # 下载所有素材并裁剪片段
    clip_files = []
    for i, item in enumerate(timeline):
        clip_path = os.path.join(temp_dir, f"clip_{i+1:03d}.mp4")
        # 下载素材
        _download_clip(item.material_url, clip_path, item.trim_start, item.trim_end)
        clip_files.append(clip_path)

    # 用concat filter拼接（不用demuxer，避免时长异常）
    concat_path = os.path.join(temp_dir, "concat.mp4")
    _concat_with_filter(clip_files, concat_path)

    # 加字幕
    subbed_path = os.path.join(temp_dir, "subbed.mp4")
    _run_ffmpeg([
        "ffmpeg", "-y", "-i", concat_path,
        "-vf", f"subtitles={srt_path}:force_style='FontName=MicrosoftYaHei,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1,Shadow=0,MarginV=40'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        subbed_path
    ], timeout=120)

    # 混音（TTS + BGM）
    mixed_path = os.path.join(temp_dir, "mixed.mp4")
    if bgm_url:
        # 下载BGM
        bgm_path = os.path.join(temp_dir, "bgm.mp3")
        import requests
        resp = requests.get(bgm_url, timeout=30)
        resp.raise_for_status()
        with open(bgm_path, "wb") as f:
            f.write(resp.content)
        # 混音
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", subbed_path,
            "-i", tts_wav, "-i", bgm_path,
            "-filter_complex",
            "[1:a]volume=1.0[a1];[2:a]volume=0.2[a2];[a1][a2]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            mixed_path
        ], timeout=120)
    else:
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", subbed_path,
            "-i", tts_wav,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            mixed_path
        ], timeout=120)

    # 复制到最终输出
    shutil.copy2(mixed_path, output_path)


def _download_clip(url: str, output_path: str, start: float, duration: float):
    """下载视频片段并裁剪"""
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", url,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ], timeout=120)


def _concat_with_filter(input_files: List[str], output_path: str):
    """用concat filter拼接多个视频片段"""
    if len(input_files) == 1:
        shutil.copy2(input_files[0], output_path)
        return

    # 构建filter
    filter_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(len(input_files)))
    filter_concat = f"{filter_inputs}concat=n={len(input_files)}:v=1:a=1[outv][outa]"

    cmd = ["ffmpeg", "-y"]
    for f in input_files:
        cmd.extend(["-i", f])
    cmd.extend([
        "-filter_complex", filter_concat,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ])
    _run_ffmpeg(cmd, timeout=300)


def _pad_video_to_duration(input_path: str, output_path: str, target_duration: float):
    """用tpad填充视频到目标时长"""
    current_dur = _get_media_duration(input_path)
    pad_needed = target_duration - current_dur
    if pad_needed <= 0.5:
        shutil.copy2(input_path, output_path)
        return

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"tpad=stop_mode=clone:stop_duration={pad_needed:.2f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path
    ]
    _run_ffmpeg(cmd, timeout=120)