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
    """下载BGM到本地"""
    import requests
    local_bgm = os.path.join(temp_dir, "bgm.mp3")
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

        # 1. 拼接素材片段（concat filter）- 不做任何画面处理
        concat_path = os.path.join(temp_dir, "concat.mp4")
        if len(clip_files) == 1:
            shutil.copy2(clip_files[0], concat_path)
        else:
            f_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(len(clip_files)))
            f_concat = f"{f_inputs}concat=n={len(clip_files)}:v=1:a=1[outv][outa]"
            cmd = ["ffmpeg", "-y"]
            for cf in clip_files:
                cmd.extend(["-i", cf])
            cmd.extend([
                "-filter_complex", f_concat,
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

        # 2. 渲染字幕 - 使用drawtext filter链
        #    ffmpeg的subtitles/ass filter存在渲染问题，改用drawtext逐句渲染
        #    固定参数：白色字体，黑色描边，font_size=38, y=0.82
        #    禁止：crop, pad, drawbox, 等画面修改
        subbed_path = os.path.join(temp_dir, "subbed.mp4")
        font_path = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
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

        # 3. 混音（TTS + BGM）
        mixed_path = os.path.join(temp_dir, "mixed.mp4")
        if bgm_url:
            try:
                local_bgm = _download_bgm(bgm_url, temp_dir)
                run_ffmpeg([
                    "ffmpeg", "-y", "-i", subbed_path,
                    "-i", tts_wav_path, "-i", local_bgm,
                    "-filter_complex",
                    "[1:a]volume=1.0[a1];[2:a]volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first[aout]",
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    mixed_path
                ], timeout=120)
            except Exception as e:
                logger.warning("[Node7] BGM混合失败，仅使用TTS: %s", e)
                run_ffmpeg([
                    "ffmpeg", "-y", "-i", subbed_path,
                    "-i", tts_wav_path,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    mixed_path
                ], timeout=120)
        else:
            run_ffmpeg([
                "ffmpeg", "-y", "-i", subbed_path,
                "-i", tts_wav_path,
                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                "-shortest",
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