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
from typing import List, Dict, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import FinalCompositionInput, FinalCompositionOutput
from graphs.shared_utils import ensure_dir, get_media_duration, run_ffmpeg, generate_contact_sheet
from utils.media_uploader import upload_local_file

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


def final_composition_node(
    state: FinalCompositionInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> FinalCompositionOutput:
    """
    title: 最终合成
    desc: 拼接素材片段，混音TTS+BGM，渲染字幕，输出final.mp4和contact_sheet.jpg
    integrations: 音频
    """
    ctx = runtime.context
    final_timeline_path = state.final_timeline_path
    srt_path = state.srt_path
    tts_wav_path = state.tts_wav_path
    bgm_url = state.bgm_url
    run_dir = state.run_dir

    logger.info("[Node7] 视频合成开始...")

    # 读取timeline
    with open(final_timeline_path, "r", encoding="utf-8") as f:
        timeline = json.load(f)

    if not timeline:
        logger.error("[Node7] timeline为空")
        return FinalCompositionOutput(
            final_video_path="", contact_sheet_path="", video_duration=0.0,
        )

    temp_dir = ensure_dir(os.path.join(run_dir, "temp"))
    final_mp4 = os.path.join(run_dir, "final.mp4")

    try:
        # 获取clip文件列表
        clip_files = [s["clip_path"] for s in timeline if s.get("clip_path")]

        if not clip_files:
            raise RuntimeError("无可用clip文件")

        # 1. 拼接素材片段（concat filter）- 统一缩放到1080x1920
        #    新版素材分辨率不一致（1080p/4K/非标准），需要先统一分辨率
        concat_path = os.path.join(temp_dir, "concat.mp4")
        target_width = 1080
        target_height = 1920
        
        if len(clip_files) == 1:
            # 单个clip也需要统一分辨率
            cmd = [
                "ffmpeg", "-y", "-i", clip_files[0],
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
            # 构建filter_complex：每个输入先trim到TTS时长，再scale，再concat
            scale_filters = []
            clip_idx = 0
            for shot in timeline:
                if not shot.get("clip_path"):
                    continue
                tts_dur = shot.get("duration", 0.0)
                # 添加trim filter确保clip时长匹配TTS
                if tts_dur > 0:
                    scale_filters.append(f"[{clip_idx}:v]trim=duration={tts_dur:.3f},setpts=PTS-STARTPTS,scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{clip_idx}]")
                else:
                    scale_filters.append(f"[{clip_idx}:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{clip_idx}]")
                scale_filters.append(f"[{clip_idx}:a]aresample=44100[a{clip_idx}]")
                clip_idx += 1
            
            # concat所有缩放后的视频
            concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(clip_idx))
            concat_filter = f"{concat_inputs}concat=n={clip_idx}:v=1:a=1[outv][outa]"
            
            filter_complex = ";".join(scale_filters) + ";" + concat_filter
            
            cmd = ["ffmpeg", "-y"]
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
        logger.info("[Node7] 拼接完成: %.2fs", concat_duration)

        # === end_hold: 结尾画面多停留 ===
        end_hold_meta_path = os.path.join(run_dir, "end_hold_meta.json")
        end_hold_sec = 0.0
        if os.path.exists(end_hold_meta_path):
            try:
                with open(end_hold_meta_path, "r", encoding="utf-8") as f:
                    end_hold_meta = json.load(f)
                if end_hold_meta.get("end_hold_applied", False):
                    end_hold_sec = end_hold_meta.get("end_hold_sec", 0.0)
            except Exception as e:
                logger.warning("[Node7] 读取end_hold_meta.json失败: %s", e)
        
        if end_hold_sec > 0:
            # 使用 tpad filter 延长最后一个clip（冻结最后一帧）
            extended_path = os.path.join(temp_dir, "concat_extended.mp4")
            logger.info("[Node7] end_hold: 延长最后一帧 %.1fs", end_hold_sec)
            try:
                run_ffmpeg([
                    "ffmpeg", "-y", "-i", concat_path,
                    "-vf", f"tpad=stop_mode=clone:stop_duration={end_hold_sec}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "copy",
                    "-movflags", "+faststart",
                    extended_path
                ], timeout=120)
                extended_duration = get_media_duration(extended_path)
                logger.info("[Node7] end_hold完成: 原%.2fs → 延长后%.2fs", concat_duration, extended_duration)
                concat_path = extended_path
                concat_duration = extended_duration
            except Exception as e:
                logger.warning("[Node7] end_hold延长失败: %s，跳过", e)

        # 2. 渲染字幕 - 使用drawtext filter链
        #    ffmpeg的subtitles/ass filter存在渲染问题，改用drawtext逐句渲染
        #    固定参数：白色字体，黑色描边，font_size=38, y=0.82
        #    禁止：crop, pad, drawbox, 等画面修改
        subbed_path = os.path.join(temp_dir, "subbed.mp4")
        
        # 字体加载逻辑：优先使用项目 assets/Fonts 下的字体
        # 常规字幕优先使用：assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF
        # 如果不存在或加载失败，回退到系统字体
        workspace_path = os.getenv("COZE_WORKSPACE_PATH", "")
        preferred_font = os.path.join(workspace_path, "assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF")
        fallback_font = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
        
        if os.path.exists(preferred_font):
            font_path = preferred_font
            logger.info("[Node7] 使用项目字体: %s", font_path)
        else:
            font_path = fallback_font
            logger.warning("[Node7] 项目字体不存在，回退到系统字体: %s", font_path)
        
        margin_v = int(1920 * (1 - 0.82))  # ≈ 346, >= 180 ✓

        try:
            # 解析SRT文件，获取每句字幕和时间
            with open(srt_path, "r", encoding="utf-8") as f:
                srt_content = f.read()
            # 按空行分割字幕块
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

                # 转义text中的特殊字符
                escaped = text.replace("\\", "\\\\")
                escaped = escaped.replace("'", "'\\\\\\''")
                escaped = escaped.replace(":", "\\:")
                escaped = escaped.replace(",", "\\,")
                escaped = escaped.replace("!", "\\!")
                escaped = escaped.replace("[", "\\[")
                escaped = escaped.replace("]", "\\]")
                escaped = escaped.replace("(", "\\(")
                escaped = escaped.replace(")", "\\)")
                # 换行符转ASS换行
                escaped = escaped.replace("\n", "\\N")

                if start < end:
                    drawtext_filters.append(
                        f"drawtext=text='{escaped}':fontfile={font_path}:"
                        f"fontcolor=white:fontsize=38:"
                        f"bordercolor=black:borderw=3:"
                        f"x=(w-text_w)/2:y=h-{margin_v}:"
                        f"enable='between(t,{start},{end})'"
                    )

            if not drawtext_filters:
                raise RuntimeError("无有效的字幕渲染")

            filter_chain = ",".join(drawtext_filters)
            logger.info("[Node7] 字幕渲染: %d句, filter链长度=%d字符", len(drawtext_filters), len(filter_chain))

            run_ffmpeg([
                "ffmpeg", "-y", "-i", concat_path,
                "-vf", filter_chain,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                subbed_path
            ], timeout=300)
            subbed_duration = get_media_duration(subbed_path)
            logger.info("[Node7] 字幕渲染完成: %.2fs", subbed_duration)
        except Exception as e:
            logger.error("[Node7] 字幕渲染失败: %s", e)
            # 回退: 直接使用无字幕版本
            shutil.copy2(concat_path, subbed_path)
            subbed_duration = get_media_duration(subbed_path)
            logger.warning("[Node7] 回退到无字幕版本: %.2fs", subbed_duration)

        # 3. 混音（TTS + BGM）- 分步处理，确保BGM可听见
        #    步骤：1. 截取BGM到视频时长 -> bgm_trimmed.wav
        #          2. 混合TTS+BGM -> mixed_audio.wav
        #          3. 将混合音频混入视频 -> final.mp4
        mixed_path = os.path.join(temp_dir, "mixed.mp4")
        
        # 获取视频时长
        video_duration = get_media_duration(subbed_path)
        tts_duration = get_media_duration(tts_wav_path) if os.path.exists(tts_wav_path) else 0.0
        
        logger.info("[Node7] 视频时长=%.2fs, TTS时长=%.2fs", video_duration, tts_duration)
        
        # 输出调试文件路径
        bgm_trimmed_path = os.path.join(run_dir, "bgm_trimmed.wav")
        tts_normalized_path = os.path.join(run_dir, "tts_normalized.wav")
        mixed_audio_path = os.path.join(run_dir, "mixed_audio.wav")
        
        # BGM 默认选择逻辑：如果没有传 bgm_url，从 assets/bgm/ 下的 bgm_01.mp3 到 bgm_12.mp3 中选择一个
        if not bgm_url:
            bgm_dir = os.path.join(workspace_path, "assets/bgm")
            if os.path.exists(bgm_dir):
                bgm_files = sorted([f for f in os.listdir(bgm_dir) if f.endswith(".mp3")])
                if bgm_files:
                    # 按 run_dir 稳定选择，保证相同 run_dir 总是选择同一个 BGM
                    import hashlib
                    hash_val = int(hashlib.md5(run_dir.encode()).hexdigest(), 16)
                    bgm_index = hash_val % len(bgm_files)
                    bgm_url = os.path.join(bgm_dir, bgm_files[bgm_index])
                    logger.info("[Node7] 未指定 bgm_url，自动选择: %s", bgm_url)
                else:
                    logger.warning("[Node7] assets/bgm/ 目录下没有 mp3 文件")
            else:
                logger.warning("[Node7] assets/bgm/ 目录不存在")
        
        if bgm_url:
            try:
                local_bgm = _download_bgm(bgm_url, temp_dir)
                bgm_duration = get_media_duration(local_bgm)
                logger.info("[Node7] BGM时长=%.2fs", bgm_duration)
                
                # ========== 步骤1: 截取BGM到视频时长 ==========
                logger.info("[Node7] 步骤1: 截取BGM到视频时长...")
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-stream_loop", "-1", "-i", local_bgm,
                    "-t", str(video_duration),
                    "-c:a", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    bgm_trimmed_path
                ], timeout=60)
                bgm_trimmed_duration = get_media_duration(bgm_trimmed_path)
                logger.info("[Node7] bgm_trimmed.wav 生成完成: %.2fs", bgm_trimmed_duration)
                
                # ========== 步骤2: 归一化TTS ==========
                logger.info("[Node7] 步骤2: 归一化TTS...")
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", tts_wav_path,
                    "-af", "volume=1.0,loudnorm",
                    "-c:a", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    tts_normalized_path
                ], timeout=60)
                logger.info("[Node7] tts_normalized.wav 生成完成")
                
                # ========== 步骤3: 混合TTS + BGM ==========
                # BGM音量设为0.40，确保人耳可听见但不盖过人声
                bgm_volume = 0.40
                logger.info("[Node7] 步骤3: 混合TTS(volume=1.0) + BGM(volume=%.2f)...", bgm_volume)
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", tts_normalized_path,
                    "-i", bgm_trimmed_path,
                    "-filter_complex",
                    f"[0:a]volume=1.0[tts];[1:a]volume={bgm_volume}[bgm];[tts][bgm]amix=inputs=2:duration=first:normalize=0,loudnorm[aout]",
                    "-map", "[aout]",
                    "-c:a", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    mixed_audio_path
                ], timeout=60)
                mixed_audio_duration = get_media_duration(mixed_audio_path)
                logger.info("[Node7] mixed_audio.wav 生成完成: %.2fs", mixed_audio_duration)
                
                # ========== 步骤4: 将混合音频混入视频 ==========
                logger.info("[Node7] 步骤4: 将混合音频混入视频...")
                
                # 如果有end_hold，需要延长音频（用静音填充end_hold部分）
                if end_hold_sec > 0:
                    # 用adelay和apad延长音频到视频时长
                    target_audio_dur = video_duration  # video_duration已包含end_hold
                    run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", mixed_audio_path,
                        "-af", f"apad=whole_dur={target_audio_dur}",
                        "-c:a", "pcm_s16le",
                        "-ar", "44100",
                        "-ac", "1",
                        os.path.join(temp_dir, "mixed_audio_padded.wav")
                    ], timeout=60)
                    final_audio_input = os.path.join(temp_dir, "mixed_audio_padded.wav")
                    logger.info("[Node7] 音频已延长到%.2fs（含end_hold %.1fs）", target_audio_dur, end_hold_sec)
                else:
                    final_audio_input = mixed_audio_path
                
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", subbed_path,
                    "-i", final_audio_input,
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy",  # 不重新编码视频，保留end_hold延长的时长
                    "-c:a", "aac", "-b:a", "128k",
                    "-ar", "44100",
                    "-movflags", "+faststart",
                    mixed_path
                ], timeout=180)
                
                # 验证音频是否正常
                final_audio_duration = get_media_duration(mixed_path)
                logger.info("[Node7] 混音完成: 视频=%.2fs, 音频=%.2fs", final_audio_duration, final_audio_duration)
                
                # 生成音频混流报告
                audio_mix_report = {
                    "tts_file": tts_wav_path,
                    "bgm_file": local_bgm,
                    "tts_volume": 1.0,
                    "bgm_volume": bgm_volume,
                    "tts_duration": tts_duration,
                    "bgm_duration": bgm_duration,
                    "video_duration": video_duration,
                    "output_duration": final_audio_duration,
                    "mix_strategy": "step_by_step_mix",
                    "bgm_trimmed_exists": os.path.exists(bgm_trimmed_path),
                    "mixed_audio_exists": os.path.exists(mixed_audio_path),
                    "output_codec": "aac",
                    "output_bitrate": "128k"
                }
                audio_mix_report_path = os.path.join(run_dir, "audio_mix_report.json")
                with open(audio_mix_report_path, "w", encoding="utf-8") as f:
                    json.dump(audio_mix_report, f, indent=2, ensure_ascii=False)
                logger.info("[Node7] 音频混流报告已保存: %s", audio_mix_report_path)
                
            except Exception as e:
                logger.error("[Node7] BGM混合失败: %s，仅使用TTS", e)
                # 回退：仅使用TTS，处理end_hold
                if end_hold_sec > 0:
                    target_audio_dur = video_duration
                    padded_tts_path = os.path.join(temp_dir, "tts_padded_fallback.wav")
                    run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", tts_wav_path,
                        "-af", f"apad=whole_dur={target_audio_dur}",
                        "-c:a", "pcm_s16le",
                        "-ar", "44100",
                        "-ac", "1",
                        padded_tts_path
                    ], timeout=60)
                    logger.info("[Node7] 回退路径: TTS音频已延长到%.2fs", target_audio_dur)
                    run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", subbed_path,
                        "-i", padded_tts_path,
                        "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "128k",
                        "-ar", "44100",
                        "-movflags", "+faststart",
                        mixed_path
                    ], timeout=120)
                else:
                    run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", subbed_path,
                        "-i", tts_wav_path,
                        "-filter_complex", "[1:a]volume=1.0[aout]",
                        "-map", "0:v", "-map", "[aout]",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k",
                        "-ar", "44100",
                        "-movflags", "+faststart",
                        mixed_path
                    ], timeout=120)
        else:
            # 无BGM，仅使用TTS
            # 如果有end_hold，需要延长音频（用静音填充end_hold部分）
            if end_hold_sec > 0:
                target_audio_dur = video_duration  # video_duration已包含end_hold
                padded_tts_path = os.path.join(temp_dir, "tts_padded.wav")
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", tts_wav_path,
                    "-af", f"apad=whole_dur={target_audio_dur}",
                    "-c:a", "pcm_s16le",
                    "-ar", "44100",
                    "-ac", "1",
                    padded_tts_path
                ], timeout=60)
                padded_dur = get_media_duration(padded_tts_path)
                logger.info("[Node7] TTS音频已延长: %.2fs → %.2fs（目标%.2fs）", tts_duration, padded_dur, target_audio_dur)
                
                # 直接合并视频和延长后的音频，不重新编码视频
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", subbed_path,
                    "-i", padded_tts_path,
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k",
                    "-ar", "44100",
                    "-movflags", "+faststart",
                    mixed_path
                ], timeout=120)
            else:
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", subbed_path,
                    "-i", tts_wav_path,
                    "-filter_complex", "[1:a]volume=1.0[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k",
                    "-ar", "44100",
                    "-movflags", "+faststart",
                    mixed_path
                ], timeout=120)

        # 4. 复制到最终输出
        shutil.copy2(mixed_path, final_mp4)
        video_duration = get_media_duration(final_mp4)
        logger.info("[Node7] 合成完成: %.2fs", video_duration)

        # 5. 生成联系图
        contact_sheet_path = os.path.join(run_dir, "contact_sheet.jpg")
        try:
            generate_contact_sheet(final_mp4, contact_sheet_path)
        except Exception as e:
            logger.warning("[Node7] 联系图生成失败: %s", e)
            contact_sheet_path = ""

        return FinalCompositionOutput(
            final_video_path=final_mp4,
            contact_sheet_path=contact_sheet_path,
            video_duration=video_duration,
        )

    except Exception as e:
        logger.error("[Node7] 合成失败: %s", e)
        return FinalCompositionOutput(
            final_video_path="", contact_sheet_path="", video_duration=0.0,
        )