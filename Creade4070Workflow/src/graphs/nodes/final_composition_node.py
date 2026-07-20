"""
Node7: 最终合成
职责：按timeline拼接素材片段，使用tts.wav作为主音频，添加BGM，渲染字幕，输出final.mp4和contact_sheet.jpg
禁止：裁切、缩放、遮挡、模糊、去字幕、补黑边、修改画幅

固定字幕参数：
- font_size=38, font_color=white, outline_color=black, outline_width=3
- subtitle_y_position_ratio=0.82, safe_margin_bottom>=180px
- subtitle_area_ratio<=0.18, max_lines=2, horizontal_align=center
- background_box=false
"""
import os
import json
import re
import shutil
import logging
import subprocess
from typing import List, Dict, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import FinalCompositionInput, FinalCompositionOutput
from graphs.shared_utils import (
    atomic_json_write,
    ensure_dir,
    generate_contact_sheet,
    get_media_duration,
    run_ffmpeg,
)
from utils.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path
from utils.media_uploader import upload_local_file
from graphs.node_trace_utils import write_trace_entered, write_trace_completed, write_trace_error

logger = logging.getLogger(__name__)


def _download_bgm(bgm_url: str, temp_dir: str) -> str:
    """下载BGM到本地，支持URL和本地路径"""
    import requests
    local_bgm = os.path.join(temp_dir, "bgm.mp3")
    
    # 检查是否是本地路径
    if os.path.exists(bgm_url):
        # 本地路径，直接复制
        import shutil
        shutil.copy2(bgm_url, local_bgm)
        return local_bgm
    
    # 检查是否是workspace相对路径
    workspace_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", ""), bgm_url)
    if os.path.exists(workspace_path):
        import shutil
        shutil.copy2(workspace_path, local_bgm)
        return local_bgm
    
    # URL路径，下载
    resp = requests.get(bgm_url, timeout=30)
    resp.raise_for_status()
    with open(local_bgm, "wb") as f:
        f.write(resp.content)
    return local_bgm


def _check_subtitle_filter(ffmpeg_path: str) -> bool:
    """检查 FFmpeg 是否支持 subtitles 滤镜"""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stdout + result.stderr
        # 检查是否支持 subtitles 或 ass 滤镜
        return "subtitles" in output or "ass" in output
    except Exception as e:
        logger.warning("[Node7] 检查字幕滤镜失败: %s", e)
        return False


def _find_chinese_font() -> str:
    """查找支持中文的字体"""
    # 优先使用项目字体
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "")
    preferred_font = os.path.join(workspace_path, "assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF")
    if os.path.exists(preferred_font):
        return preferred_font
    
    # 扫描系统字体
    font_dirs = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
    ]
    
    chinese_font_patterns = [
        "wqy", "noto", "cjk", "chinese", "hans", "hei", "song", "kai",
        "puhuiti", "alibaba", "source", "fang", "yuan"
    ]
    
    for font_dir in font_dirs:
        if not os.path.exists(font_dir):
            continue
        for root, dirs, files in os.walk(font_dir):
            for f in files:
                if f.lower().endswith(('.ttf', '.ttc', '.otf')):
                    f_lower = f.lower()
                    if any(p in f_lower for p in chinese_font_patterns):
                        return os.path.join(root, f)
    
    # 回退到常见中文字体路径
    fallback_fonts = [
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for f in fallback_fonts:
        if os.path.exists(f):
            return f
    
    return ""


def _escape_srt_path(srt_path: str) -> str:
    """转义 SRT 路径用于 FFmpeg subtitles 滤镜"""
    # FFmpeg subtitles 滤镜需要转义特殊字符
    escaped = srt_path.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    return escaped


def final_composition_node(
    state: dict,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> dict:
    """
    title: 最终合成
    desc: 拼接素材片段，混音TTS+BGM，渲染字幕，输出final.mp4和contact_sheet.jpg
    integrations: 音频
    """
    ctx = runtime.context
    final_timeline_path = state.get("final_timeline_path", "")
    srt_path = state.get("srt_path", "")
    tts_wav_path = state.get("tts_wav_path", "")
    bgm_url = state.get("bgm_url", "")
    run_dir = state.get("run_dir", "")

    # Phase: entered
    write_trace_entered(run_dir, "final_composition",
        final_timeline_path=final_timeline_path,
        srt_path=srt_path,
        tts_wav_path=tts_wav_path,
    )

    # 检查必要文件
    if not final_timeline_path or not os.path.exists(final_timeline_path):
        error_msg = f"timeline文件不存在: {final_timeline_path}"
        logger.error("[Node7] %s", error_msg)
        write_trace_error(run_dir, "final_composition", "TimelineNotFoundError", error_msg)
        raise RuntimeError(f"最终合成失败: {error_msg}")

    if not tts_wav_path or not os.path.exists(tts_wav_path):
        error_msg = f"TTS音频文件不存在: {tts_wav_path}"
        logger.error("[Node7] %s", error_msg)
        write_trace_error(run_dir, "final_composition", "TTSNotFoundError", error_msg)
        raise RuntimeError(f"最终合成失败: {error_msg}")

    logger.info("[Node7] 视频合成开始...")

    # 读取timeline
    with open(final_timeline_path, "r", encoding="utf-8") as f:
        timeline = json.load(f)

    if not timeline:
        error_msg = "timeline为空"
        logger.error("[Node7] %s", error_msg)
        write_trace_error(run_dir, "final_composition", "EmptyTimelineError", error_msg)
        raise RuntimeError(f"最终合成失败: {error_msg}")

    temp_dir = ensure_dir(os.path.join(run_dir, "temp"))
    final_mp4 = os.path.join(run_dir, "final.mp4")
    end_hold_sec = 0.0  # 初始化 end_hold_sec
    
    # 获取 FFmpeg 路径
    ffmpeg_path = get_ffmpeg_path()
    
    # 检查字幕滤镜支持
    subtitle_filter_supported = _check_subtitle_filter(ffmpeg_path)
    logger.info("[Node7] 字幕滤镜支持: %s", subtitle_filter_supported)
    
    # 查找中文字体
    font_path = _find_chinese_font()
    logger.info("[Node7] 使用字体: %s", font_path)
    
    # 获取TTS时长
    tts_duration = get_media_duration(tts_wav_path) if os.path.exists(tts_wav_path) else 0.0
    if tts_duration <= 0:
        raise RuntimeError(f"TTS时长无效: {tts_duration}")

    try:
        # 获取clip文件列表 - 只包含active clips（跳过visual continuation）
        clip_files = []
        for s in timeline:
            clip_path = s.get("clip_path", "")
            is_visual_continuation = s.get("visual_continuation", False)
            # 只包含有clip_path且不是visual continuation的条目
            if clip_path and not is_visual_continuation and os.path.exists(clip_path):
                clip_files.append(clip_path)
            elif clip_path and not is_visual_continuation:
                logger.warning("[Node7] clip文件不存在: %s", clip_path)

        if not clip_files:
            raise RuntimeError("无可用clip文件")
        
        logger.info("[Node7] 活跃clip数: %d/%d", len(clip_files), len(timeline))

        # 1. 拼接素材片段（concat filter）- 统一缩放到1080x1920
        concat_path = os.path.join(temp_dir, "concat.mp4")
        target_width = 1080
        target_height = 1920
        
        if len(clip_files) == 1:
            # 单个clip也需要统一分辨率
            cmd = [
                ffmpeg_path, "-y", "-i", clip_files[0],
                "-vf", f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                concat_path
            ]
            run_ffmpeg(cmd, timeout=120)
        else:
            # 多个clip：先scale每个视频，再concat
            scale_filters = []
            for i in range(len(clip_files)):
                scale_filters.append(f"[{i}:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]")
                scale_filters.append(f"[{i}:a]aresample=44100[a{i}]")
            
            concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(clip_files)))
            concat_filter = f"{concat_inputs}concat=n={len(clip_files)}:v=1:a=1[outv][outa]"
            
            filter_complex = ";".join(scale_filters) + ";" + concat_filter
            
            cmd = [ffmpeg_path, "-y"]
            for cf in clip_files:
                cmd.extend(["-i", cf])
            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                concat_path
            ])
            run_ffmpeg(cmd, timeout=300)

        concat_duration = get_media_duration(concat_path)
        logger.info("[Node7] 拼接完成: %.2fs, TTS时长: %.2fs", concat_duration, tts_duration)

        # 1.4 精确Trim: 确保主体视频时长与TTS时长一致
        trim_tolerance = 0.05
        if concat_duration > tts_duration + trim_tolerance:
            trimmed_path = os.path.join(temp_dir, "trimmed.mp4")
            logger.info("[Node7] 精确Trim: %.2fs -> %.2fs", concat_duration, tts_duration)
            run_ffmpeg([
                ffmpeg_path, "-y", "-i", concat_path,
                "-t", str(tts_duration),
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                trimmed_path
            ], timeout=120)
            concat_path = trimmed_path
            concat_duration = get_media_duration(concat_path)
            logger.info("[Node7] Trim完成: %.2fs", concat_duration)
        elif concat_duration < tts_duration - trim_tolerance:
            logger.warning("[Node7] 主体视频时长不足: %.2fs < TTS %.2fs", concat_duration, tts_duration)

        # 1.5 End Hold: 延长最后一帧
        end_hold_sec = 1.0
        if end_hold_sec > 0:
            tpad_path = os.path.join(temp_dir, "tpad.mp4")
            logger.info("[Node7] End Hold: 延长最后一帧 %.1fs...", end_hold_sec)
            run_ffmpeg([
                ffmpeg_path, "-y", "-i", concat_path,
                "-vf", f"tpad=stop_mode=clone:stop_duration={end_hold_sec}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                tpad_path
            ], timeout=120)
            tpad_duration = get_media_duration(tpad_path)
            logger.info("[Node7] End Hold完成: %.2fs (原 %.2fs + %.1fs)", tpad_duration, concat_duration, end_hold_sec)
            concat_path = tpad_path
            concat_duration = tpad_duration
            
            end_hold_meta = {
                "end_hold_sec": end_hold_sec,
                "original_video_duration": concat_duration - end_hold_sec,
                "extended_video_duration": concat_duration,
            }
            end_hold_meta_path = os.path.join(run_dir, "end_hold_meta.json")
            atomic_json_write(end_hold_meta_path, end_hold_meta)

        # 2. 渲染字幕 - 使用 subtitles 滤镜
        subbed_path = os.path.join(temp_dir, "subbed.mp4")
        
        # 优先使用 render_subtitles.srt
        render_srt_path = os.path.join(run_dir, "render_subtitles.srt")
        actual_srt_path = render_srt_path if os.path.exists(render_srt_path) else srt_path
        
        subtitle_burned = False
        subtitle_filter_used = ""
        
        if subtitle_filter_supported and font_path and os.path.exists(actual_srt_path):
            try:
                escaped_srt_path = _escape_srt_path(actual_srt_path)
                escaped_font_path = _escape_srt_path(font_path)
                
                # 使用 subtitles 滤镜烧录字幕
                subtitle_filter = f"subtitles='{escaped_srt_path}':fontsdir='{os.path.dirname(font_path)}':force_style='FontName={os.path.basename(font_path)},FontSize=19,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,MarginV=346'"
                subtitle_filter_used = subtitle_filter
                
                logger.info("[Node7] 使用 subtitles 滤镜烧录字幕: %s", actual_srt_path)
                
                run_ffmpeg([
                    ffmpeg_path, "-y", "-i", concat_path,
                    "-vf", subtitle_filter,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "copy",
                    "-movflags", "+faststart",
                    subbed_path
                ], timeout=300)
                
                # 验证字幕是否成功烧录
                subbed_duration = get_media_duration(subbed_path)
                if subbed_duration > 0:
                    subtitle_burned = True
                    logger.info("[Node7] 字幕烧录成功: %.2fs", subbed_duration)
                else:
                    raise RuntimeError("字幕烧录后视频时长为0")
                    
            except Exception as e:
                logger.error("[Node7] subtitles 滤镜失败: %s", e)
                # 尝试 drawtext 作为备选
                try:
                    logger.info("[Node7] 尝试 drawtext 滤镜...")
                    # 解析 SRT 文件
                    with open(actual_srt_path, "r", encoding="utf-8") as f:
                        srt_content = f.read()
                    srt_blocks = re.split(r'\n\n+', srt_content.strip())
                    drawtext_filters = []
                    
                    for block in srt_blocks:
                        lines = block.strip().split('\n')
                        if len(lines) < 3:
                            continue
                        time_match = re.match(r'(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)', lines[1])
                        if not time_match:
                            continue

                        def _to_seconds(t: str) -> float:
                            t = t.replace(',', '.')
                            parts = t.split(':')
                            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

                        start = _to_seconds(time_match.group(1))
                        end = _to_seconds(time_match.group(2))
                        text = '\n'.join(lines[2:])

                        escaped = text.replace("\\", "\\\\")
                        escaped = escaped.replace("'", "'\\\\\\''")
                        escaped = escaped.replace(":", "\\:")
                        escaped = escaped.replace(",", "\\,")
                        escaped = escaped.replace("!", "\\!")
                        escaped = escaped.replace("\n", "\\N")

                        if start < end:
                            drawtext_filters.append(
                                f"drawtext=text='{escaped}':fontfile={font_path}:"
                                f"fontcolor=white:fontsize=38:"
                                f"bordercolor=black:borderw=3:"
                                f"x=(w-text_w)/2:y=h-346:"
                                f"enable='between(t,{start},{end})'"
                            )

                    if drawtext_filters:
                        filter_chain = ",".join(drawtext_filters)
                        run_ffmpeg([
                            ffmpeg_path, "-y", "-i", concat_path,
                            "-vf", filter_chain,
                            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                            "-pix_fmt", "yuv420p",
                            "-c:a", "copy",
                            "-movflags", "+faststart",
                            subbed_path
                        ], timeout=300)
                        subbed_duration = get_media_duration(subbed_path)
                        if subbed_duration > 0:
                            subtitle_burned = True
                            subtitle_filter_used = "drawtext"
                            logger.info("[Node7] drawtext 字幕烧录成功: %.2fs", subbed_duration)
                        else:
                            raise RuntimeError("drawtext 烧录后视频时长为0")
                    else:
                        raise RuntimeError("无有效的字幕渲染")
                        
                except Exception as e2:
                    logger.error("[Node7] drawtext 也失败: %s", e2)
                    # 最终回退：无字幕版本
                    shutil.copy2(concat_path, subbed_path)
                    subtitle_burned = False
                    logger.warning("[Node7] 回退到无字幕版本")
        else:
            # 字幕滤镜不支持或字体不存在
            if not subtitle_filter_supported:
                logger.error("[Node7] FFmpeg 不支持 subtitles 滤镜")
            if not font_path:
                logger.error("[Node7] 未找到中文字体")
            if not os.path.exists(actual_srt_path):
                logger.error("[Node7] SRT 文件不存在: %s", actual_srt_path)
            
            # 回退到无字幕版本
            shutil.copy2(concat_path, subbed_path)
            subtitle_burned = False
        
        subbed_duration = get_media_duration(subbed_path)
        video_duration = subbed_duration

        # 3. 混音（TTS + 可选 BGM）
        mixed_path = os.path.join(temp_dir, "mixed.mp4")
        
        # 获取视频时长
        video_duration = get_media_duration(subbed_path)
        tts_duration = get_media_duration(tts_wav_path) if os.path.exists(tts_wav_path) else 0.0
        
        logger.info("[Node7] 视频时长=%.2fs, TTS时长=%.2fs", video_duration, tts_duration)
        
        # BGM 处理
        bgm_used = False
        local_bgm = ""
        
        if not bgm_url:
            bgm_dir = os.path.join(os.getenv("COZE_WORKSPACE_PATH", ""), "assets/bgm")
            if os.path.exists(bgm_dir):
                bgm_files = sorted([f for f in os.listdir(bgm_dir) if f.endswith(".mp3")])
                if bgm_files:
                    import hashlib
                    hash_val = int(hashlib.md5(run_dir.encode()).hexdigest(), 16)
                    bgm_index = hash_val % len(bgm_files)
                    bgm_url = os.path.join(bgm_dir, bgm_files[bgm_index])
        
        if bgm_url:
            try:
                local_bgm = _download_bgm(bgm_url, temp_dir)
                bgm_duration = get_media_duration(local_bgm)
                logger.info("[Node7] BGM时长=%.2fs", bgm_duration)
                
                # 混合 TTS + BGM
                bgm_volume = 0.40
                run_ffmpeg([
                    ffmpeg_path, "-y",
                    "-i", subbed_path,
                    "-i", tts_wav_path,
                    "-i", local_bgm,
                    "-filter_complex",
                    f"[1:a]volume=1.0[tts];[2:a]volume={bgm_volume},aloop=loop=-1:size=2e+09[bgm];[tts][bgm]amix=inputs=2:duration=first:normalize=0[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k",
                    "-ar", "44100",
                    "-movflags", "+faststart",
                    "-t", str(video_duration),
                    mixed_path
                ], timeout=180)
                bgm_used = True
                logger.info("[Node7] TTS+BGM 混音完成")
                
            except Exception as e:
                logger.error("[Node7] BGM混合失败: %s，仅使用TTS", e)
                bgm_used = False
        
        if not bgm_used:
            # 仅使用 TTS
            run_ffmpeg([
                ffmpeg_path, "-y",
                "-i", subbed_path,
                "-i", tts_wav_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-ar", "44100",
                "-movflags", "+faststart",
                "-t", str(video_duration),
                mixed_path
            ], timeout=120)
            logger.info("[Node7] 仅使用 TTS 音轨")

        # 4. 复制到最终输出
        shutil.copy2(mixed_path, final_mp4)
        video_duration = get_media_duration(final_mp4)
        logger.info("[Node7] 合成完成: %.2fs", video_duration)

        # 5. 验证最终输出
        final_size = os.path.getsize(final_mp4) if os.path.exists(final_mp4) else 0
        if final_size == 0:
            raise RuntimeError("最终视频大小为0")
        
        # 检查音频流
        try:
            probe_cmd = [ffmpeg_path, "-i", final_mp4, "-f", "null", "-"]
            result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            stderr_output = result.stderr
            has_audio = "Audio:" in stderr_output
            has_video = "Video:" in stderr_output
            
            if not has_video:
                raise RuntimeError("最终视频无视频流")
            if not has_audio:
                raise RuntimeError("最终视频无音频流")
            
            logger.info("[Node7] 验证通过: 视频流=%s, 音频流=%s", has_video, has_audio)
        except Exception as e:
            logger.warning("[Node7] 验证失败: %s", e)

        # 6. 生成联系图
        contact_sheet_path = os.path.join(run_dir, "contact_sheet.jpg")
        try:
            generate_contact_sheet(final_mp4, contact_sheet_path)
        except Exception as e:
            logger.warning("[Node7] 联系图生成失败: %s", e)
            contact_sheet_path = ""

        # Phase: completed
        write_trace_completed(run_dir, "final_composition",
            final_video_path=final_mp4,
            video_duration=video_duration,
            end_hold_sec=end_hold_sec if end_hold_sec > 0 else 0.0,
            contact_sheet_path=contact_sheet_path,
            subtitle_burned=subtitle_burned,
            subtitle_filter_used=subtitle_filter_used,
            font_path=font_path,
            bgm_used=bgm_used,
        )

        return {
            "final_video_path": final_mp4,
            "contact_sheet_path": contact_sheet_path,
            "video_duration": video_duration,
            "end_hold_sec": end_hold_sec if end_hold_sec > 0 else 0.0,
            "final_video_duration": video_duration,
            "final_audio_duration": video_duration,
            "mixed_audio_path": mixed_path,
            "node_trace": ["final_composition"],
        }

    except Exception as e:
        logger.error("[Node7] 合成失败: %s", e)
        write_trace_error(run_dir, "final_composition", "CompositionError", str(e))
        raise RuntimeError(f"最终合成失败: {e}")
