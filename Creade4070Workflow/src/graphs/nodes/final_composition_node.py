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
    safe_download,
    validate_video_file,
)
from utils.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path
from utils.media_uploader import upload_local_file
from graphs.node_trace_utils import write_trace_entered, write_trace_completed, write_trace_error

logger = logging.getLogger(__name__)


def _download_bgm(bgm_url: str, temp_dir: str) -> str:
    """下载BGM到本地，支持URL和本地路径。使用安全下载（.part文件 + 校验）"""
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
    
    # URL路径，使用安全下载
    safe_download(bgm_url, local_bgm, max_retries=3, timeout=30)
    return local_bgm


def _sanitize_cmd_for_log(cmd: list) -> str:
    """对命令进行脱敏处理，URL只保留域名和文件名"""
    sanitized = []
    for arg in cmd:
        if arg.startswith("http://") or arg.startswith("https://"):
            # URL: 只保留域名和文件名
            from urllib.parse import urlparse
            parsed = urlparse(arg)
            filename = os.path.basename(parsed.path)
            sanitized.append(f"{parsed.scheme}://{parsed.netloc}/.../{filename}")
        elif len(arg) > 100:
            # 长路径：只保留文件名
            sanitized.append(f".../{os.path.basename(arg)}")
        else:
            sanitized.append(arg)
    return " ".join(sanitized)


def _log_ffmpeg_diagnostics(cmd: list, run_id: str, node_name: str):
    """记录 ffmpeg 调用前的诊断信息"""
    # 提取输入文件
    input_files = []
    for i, arg in enumerate(cmd):
        if arg == "-i" and i + 1 < len(cmd):
            input_files.append(cmd[i + 1])
    
    # 提取输出文件（通常是最后一个参数）
    output_file = cmd[-1] if cmd else None
    
    # 记录输入文件状态
    for f in input_files:
        if os.path.exists(f):
            size = os.path.getsize(f)
            logger.info("[ffmpeg-diag] run_id=%s node=%s 输入文件存在: %s (size=%d bytes)", 
                       run_id, node_name, f, size)
        else:
            logger.error("[ffmpeg-diag] run_id=%s node=%s 输入文件不存在: %s", 
                        run_id, node_name, f)
    
    # 记录输出目录空间
    if output_file:
        output_dir = os.path.dirname(output_file) or "."
        if os.path.exists(output_dir):
            try:
                stat = os.statvfs(output_dir)
                free_bytes = stat.f_bavail * stat.f_frsize
                free_gb = free_bytes / (1024**3)
                logger.info("[ffmpeg-diag] run_id=%s node=%s 输出目录剩余空间: %.2f GB (%s)", 
                           run_id, node_name, free_gb, output_dir)
            except Exception as e:
                logger.warning("[ffmpeg-diag] run_id=%s node=%s 无法获取目录空间: %s", 
                              run_id, node_name, e)
    
    # 记录脱敏后的命令
    sanitized_cmd = _sanitize_cmd_for_log(cmd)
    logger.info("[ffmpeg-diag] run_id=%s node=%s 执行命令: %s", 
               run_id, node_name, sanitized_cmd)


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
    """查找支持中文的字体 - 使用绝对路径定位"""
    from pathlib import Path
    
    # 获取当前文件绝对路径
    current_file = Path(__file__).resolve()
    logger.info("[Node7] 字体查找 - __file__: %s", current_file)
    logger.info("[Node7] 字体查找 - cwd: %s", os.getcwd())
    
    # 从 src/graphs/nodes/final_composition_node.py 向上找到项目根目录
    # 路径: src/graphs/nodes/ -> src/graphs/ -> src/ -> 项目根
    project_root = current_file.parent.parent.parent.parent
    logger.info("[Node7] 字体查找 - project_root (from __file__): %s", project_root)
    
    # 构建字体候选路径列表
    font_candidates = []
    
    # 1. 基于 __file__ 推导的绝对路径
    font_candidates.append(str(project_root / "assets" / "fonts" / "NotoSansSC-Regular.otf"))
    font_candidates.append(str(project_root / "assets" / "Fonts" / "黑体" / "ALIBABA-PUHUITI-BOLD.TTF"))
    
    # 2. 生产部署目录
    production_root = Path("/opt/bytefaas/Creade4070Workflow")
    font_candidates.append(str(production_root / "assets" / "fonts" / "NotoSansSC-Regular.otf"))
    font_candidates.append(str(production_root / "assets" / "Fonts" / "黑体" / "ALIBABA-PUHUITI-BOLD.TTF"))
    
    # 3. 环境变量
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "")
    if workspace_path:
        font_candidates.append(os.path.join(workspace_path, "assets/fonts/NotoSansSC-Regular.otf"))
        font_candidates.append(os.path.join(workspace_path, "assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF"))
    
    logger.info("[Node7] 字体查找 - font_candidates: %s", font_candidates)
    
    # 检查每个候选路径
    for font_path in font_candidates:
        if os.path.exists(font_path) and os.path.isfile(font_path):
            size = os.path.getsize(font_path)
            logger.info("[Node7] 字体查找 - %s: exists=True, is_file=True, size=%d", font_path, size)
            if size > 0:
                logger.info("[Node7] 字体查找 - selected_font_path: %s", font_path)
                return font_path
        else:
            logger.info("[Node7] 字体查找 - %s: exists=%s", font_path, os.path.exists(font_path))
    
    # 扫描系统字体（作为最后手段）
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
                        found_path = os.path.join(root, f)
                        logger.info("[Node7] 字体查找 - 系统字体: %s", found_path)
                        return found_path
    
    # 回退到常见中文字体路径
    fallback_fonts = [
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for f in fallback_fonts:
        if os.path.exists(f):
            logger.info("[Node7] 字体查找 - 回退字体: %s", f)
            return f
    
    logger.error("[Node7] 字体查找 - 未找到中文字体")
    return ""


def _escape_srt_path(srt_path: str) -> str:
    """转义 SRT 路径用于 FFmpeg subtitles 滤镜"""
    # FFmpeg subtitles 滤镜需要转义特殊字符
    escaped = srt_path.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    return escaped


def _parse_srt(srt_path: str) -> List[Dict[str, Any]]:
    """解析 SRT 文件，返回 cue 列表"""
    cues = []
    if not os.path.exists(srt_path):
        return cues
    
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 分割 cue 块
    blocks = re.split(r'\n\n+', content.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        # 解析时间戳
        time_line = lines[1]
        match = re.match(r'(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)', time_line)
        if not match:
            continue
        
        start_h, start_m, start_s, start_ms = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        end_h, end_m, end_s, end_ms = int(match.group(5)), int(match.group(6)), int(match.group(7)), int(match.group(8))
        
        start_time = start_h * 3600 + start_m * 60 + start_s + start_ms / 1000.0
        end_time = end_h * 3600 + end_m * 60 + end_s + end_ms / 1000.0
        
        # 获取文本
        text = '\n'.join(lines[2:])
        
        cues.append({
            "start": start_time,
            "end": end_time,
            "text": text
        })
    
    return cues


def _render_subtitle_png(
    text: str,
    output_path: str,
    font_path: str,
    font_size: int = 38,
    video_width: int = 720,
    video_height: int = 1280,
) -> Dict[str, Any]:
    """使用 Pillow 渲染透明 PNG 字幕图层，返回验证结果"""
    result = {
        "success": False,
        "text_bbox": None,
        "non_transparent_pixel_count": 0,
        "error": None,
    }
    
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # 创建透明背景
        img = Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # 加载字体（必须使用指定字体，不允许回退）
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception as e:
            result["error"] = f"加载字体失败: {e}"
            return result
        
        # 计算文本位置（底部 82% 位置）
        y_position = int(video_height * 0.82)
        
        # 处理多行文本（最多两行）
        lines = text.split('\n')[:2]
        line_height = font_size + 10
        
        # 计算所有行的总高度
        total_text_height = len(lines) * line_height
        start_y = y_position - total_text_height // 2
        
        all_bbox = []
        for i, line in enumerate(lines):
            # 获取文本边界
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            # 居中
            x = (video_width - text_width) // 2
            y = start_y + i * line_height
            
            all_bbox.append((x, y, x + text_width, y + text_height))
            
            # 绘制黑色描边
            outline_width = 3
            for dx in range(-outline_width, outline_width + 1):
                for dy in range(-outline_width, outline_width + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
            
            # 绘制白色文字
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        
        # 保存 PNG
        img.save(output_path, 'PNG')
        
        # 验证文件存在且大小 > 0
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            result["error"] = "PNG 文件不存在或大小为 0"
            return result
        
        # 验证 alpha 通道中有非透明像素
        img_check = Image.open(output_path)
        alpha = img_check.getchannel('A')
        non_transparent = sum(1 for pixel in alpha.getdata() if pixel > 0)
        
        result["text_bbox"] = all_bbox
        result["non_transparent_pixel_count"] = non_transparent
        result["success"] = non_transparent > 0
        
        if non_transparent == 0:
            result["error"] = "PNG 中没有非透明像素"
        
        return result
        
    except Exception as e:
        result["error"] = f"渲染失败: {e}"
        return result


def _burn_subtitles_with_overlay(
    ffmpeg_path: str,
    video_path: str,
    audio_path: str,
    srt_path: str,
    font_path: str,
    output_path: str,
    temp_dir: str,
    video_width: int = 720,
    video_height: int = 1280,
) -> Dict[str, Any]:
    """使用 overlay 方式烧录字幕"""
    result = {
        "subtitle_burned": False,
        "subtitle_strategy": "pillow_overlay",
        "cue_count": 0,
        "png_files": [],
        "filter_complex": "",
        "ffmpeg_returncode": -1,
        "ffmpeg_stderr_tail": "",
    }
    
    # 解析 SRT
    cues = _parse_srt(srt_path)
    if not cues:
        result["error"] = "No cues in SRT"
        return result
    
    result["cue_count"] = len(cues)
    
    # 渲染每个 cue 的 PNG，并验证
    png_files = []
    for i, cue in enumerate(cues):
        png_path = os.path.join(temp_dir, f"subtitle_{i:03d}.png")
        render_result = _render_subtitle_png(
            cue["text"],
            png_path,
            font_path,
            font_size=38,
            video_width=video_width,
            video_height=video_height,
        )
        
        if not render_result["success"]:
            error_msg = render_result.get("error", "Unknown error")
            result["error"] = f"渲染第 {i+1} 条字幕 PNG 失败: {error_msg}"
            return result
        
        png_info = {
            "path": png_path,
            "start": cue["start"],
            "end": cue["end"],
            "text": cue["text"],
            "size": os.path.getsize(png_path),
            "text_bbox": render_result["text_bbox"],
            "non_transparent_pixel_count": render_result["non_transparent_pixel_count"],
        }
        png_files.append(png_info)
    
    result["png_files"] = png_files
    
    if not png_files:
        result["error"] = "Failed to render any subtitle PNGs"
        return result
    
    # 构建 filter_complex
    # 输入: 0=video, 1=audio, 2..N=subtitle PNGs
    filter_parts = []
    current_video = "[0:v]"
    
    for i, png_info in enumerate(png_files):
        input_idx = i + 2
        start = png_info["start"]
        end = png_info["end"]
        output_label = f"[v{i}]"
        
        # 使用 overlay + enable 控制显示时间
        filter_parts.append(
            f"{current_video}[{input_idx}:v]overlay=0:0:enable='between(t,{start},{end})'{output_label}"
        )
        current_video = output_label
    
    filter_complex = ";".join(filter_parts)
    result["filter_complex"] = filter_complex
    
    # 构建 FFmpeg 命令
    cmd = [
        ffmpeg_path, "-y",
        "-threads", "2",
        "-i", video_path,
        "-i", audio_path,
    ]
    
    # 添加字幕 PNG 输入
    for png_info in png_files:
        cmd.extend(["-loop", "1", "-i", png_info["path"]])
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", current_video,
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-threads", "2",
        "-shortest",
        output_path,
    ])
    
    # 诊断日志：字幕烧录命令
    logger.info(
        "[Node7] 字幕烧录开始: cue_count=%d, video=%s, threads=2, "
        "video_resolution=%dx%d",
        len(png_files), video_path, video_width, video_height,
    )
    logger.info("[Node7] 字幕烧录命令: %s", _sanitize_cmd_for_log(cmd))
    
    # 执行 FFmpeg
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        result["ffmpeg_returncode"] = proc.returncode
        result["ffmpeg_stderr_tail"] = proc.stderr[-8000:] if proc.stderr else ""
        
        if proc.returncode != 0:
            # 特殊处理 SIGKILL (-9) = OOM kill
            if proc.returncode == -9:
                result["error"] = (
                    f"FFmpeg 被 SIGKILL 终止 (code=-9)，可能原因：内存超限 (OOM)。"
                    f"字幕数={len(png_files)}, 视频分辨率={video_width}x{video_height}, "
                    f"stderr 末尾: {proc.stderr[-2000:] if proc.stderr else '(empty)'}"
                )
                logger.error("[Node7] 字幕烧录 OOM: cue_count=%d, resolution=%dx%d",
                    len(png_files), video_width, video_height)
            else:
                result["error"] = f"FFmpeg failed with code {proc.returncode}"
            return result
        
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            result["error"] = "输出文件不存在或大小为 0"
            return result
        
        # 验证字幕可见性：在每条 cue 中点抽帧
        verification_results = []
        for i, png_info in enumerate(png_files):
            midpoint = (png_info["start"] + png_info["end"]) / 2
            frame_path = os.path.join(temp_dir, f"verify_frame_{i:03d}.png")
            
            # 抽帧
            extract_cmd = [
                ffmpeg_path, "-y",
                "-threads", "2",
                "-ss", str(midpoint),
                "-i", output_path,
                "-vframes", "1",
                "-threads", "2",
                frame_path,
            ]
            extract_proc = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=30)
            
            if extract_proc.returncode != 0 or not os.path.exists(frame_path):
                verification_results.append({
                    "cue_index": i,
                    "verified": False,
                    "error": "抽帧失败",
                })
                continue
            
            # 检查帧文件是否有效
            verification_results.append({
                "cue_index": i,
                "verified": True,
                "frame_path": frame_path,
                "frame_size": os.path.getsize(frame_path),
                "midpoint": midpoint,
            })
        
        result["verification_results"] = verification_results
        
        # 只有所有验证都通过才标记成功
        all_verified = all(v.get("verified", False) for v in verification_results)
        if all_verified:
            result["subtitle_burned"] = True
        else:
            result["error"] = "部分字幕验证失败"
            
    except Exception as e:
        result["error"] = str(e)
    
    return result


def _burn_subtitles_batched(
    ffmpeg_path: str,
    video_path: str,
    audio_path: str,
    srt_path: str,
    font_path: str,
    output_path: str,
    temp_dir: str,
    video_width: int = 720,
    video_height: int = 1280,
    max_cues_per_batch: int = 3,
) -> Dict[str, Any]:
    """
    分批烧录字幕，避免同时加载所有 PNG 导致 OOM。
    
    策略：
    1. 将 cues 分成多批，每批最多 max_cues_per_batch 个
    2. 每批独立运行 ffmpeg，输出作为下一批的输入
    3. 中间文件放入 temp_dir，完成后清理
    4. 统一使用 -threads 1 限制资源
    """
    result = {
        "subtitle_burned": False,
        "subtitle_strategy": "batched_overlay",
        "cue_count": 0,
        "batch_count": 0,
        "batches": [],
        "ffmpeg_returncode": -1,
        "ffmpeg_stderr_tail": "",
    }
    
    # 解析 SRT
    cues = _parse_srt(srt_path)
    if not cues:
        result["error"] = "No cues in SRT"
        return result
    
    result["cue_count"] = len(cues)
    
    # 分批
    batches = []
    for i in range(0, len(cues), max_cues_per_batch):
        batch_cues = cues[i:i + max_cues_per_batch]
        batches.append({
            "batch_index": len(batches),
            "cue_start": i,
            "cue_end": i + len(batch_cues),
            "cues": batch_cues,
        })
    
    result["batch_count"] = len(batches)
    logger.info(
        "[Node7] 分批字幕烧录: cue_count=%d, batch_count=%d, max_cues_per_batch=%d, "
        "resolution=%dx%d",
        len(cues), len(batches), max_cues_per_batch, video_width, video_height,
    )
    
    # 当前输入视频路径（第一批使用原始视频，后续批次使用上一批的输出）
    current_input_video = video_path
    intermediate_files = []
    
    try:
        for batch_info in batches:
            batch_idx = batch_info["batch_index"]
            batch_cues = batch_info["cues"]
            cue_start = batch_info["cue_start"]
            cue_end = batch_info["cue_end"]
            
            logger.info(
                "[Node7] 烧录批次 %d/%d: cues[%d:%d] (%d 条)",
                batch_idx + 1, len(batches), cue_start, cue_end, len(batch_cues),
            )
            
            # 渲染该批次的 PNG
            batch_png_files = []
            for i, cue in enumerate(batch_cues):
                png_path = os.path.join(temp_dir, f"batch{batch_idx}_subtitle_{i:03d}.png")
                render_result = _render_subtitle_png(
                    cue["text"],
                    png_path,
                    font_path,
                    font_size=38,
                    video_width=video_width,
                    video_height=video_height,
                )
                
                if not render_result["success"]:
                    error_msg = render_result.get("error", "Unknown error")
                    result["error"] = f"批次 {batch_idx + 1} 渲染第 {i+1} 条字幕 PNG 失败: {error_msg}"
                    return result
                
                batch_png_files.append({
                    "path": png_path,
                    "start": cue["start"],
                    "end": cue["end"],
                    "text": cue["text"],
                })
                intermediate_files.append(png_path)
            
            # 构建该批次的 filter_complex
            filter_parts = []
            current_video_label = "[0:v]"
            
            for i, png_info in enumerate(batch_png_files):
                input_idx = i + 2  # 0=video, 1=audio, 2..=PNGs
                start = png_info["start"]
                end = png_info["end"]
                output_label = f"[v{i}]"
                
                filter_parts.append(
                    f"{current_video_label}[{input_idx}:v]overlay=0:0:enable='between(t,{start},{end})'{output_label}"
                )
                current_video_label = output_label
            
            filter_complex = ";".join(filter_parts)
            
            # 确定该批次的输出路径
            if batch_idx == len(batches) - 1:
                # 最后一批输出到最终路径
                batch_output = output_path
            else:
                # 中间批次输出到临时文件
                batch_output = os.path.join(temp_dir, f"batch{batch_idx}_output.mp4")
                intermediate_files.append(batch_output)
            
            # 构建 ffmpeg 命令
            cmd = [
                ffmpeg_path, "-y",
                "-threads", "1",
                "-i", current_input_video,
                "-i", audio_path,
            ]
            
            for png_info in batch_png_files:
                cmd.extend(["-loop", "1", "-i", png_info["path"]])
            
            cmd.extend([
                "-filter_complex", filter_complex,
                "-map", current_video_label,
                "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-threads", "1",
                "-shortest",
                batch_output,
            ])
            
            logger.info("[Node7] 批次 %d 命令: %s", batch_idx + 1, _sanitize_cmd_for_log(cmd))
            
            # 执行 ffmpeg
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            
            batch_result = {
                "batch_index": batch_idx,
                "cue_range": f"[{cue_start}:{cue_end}]",
                "cue_count": len(batch_cues),
                "returncode": proc.returncode,
                "stderr_tail": proc.stderr[-2000:] if proc.stderr else "",
            }
            result["batches"].append(batch_result)
            result["ffmpeg_returncode"] = proc.returncode
            result["ffmpeg_stderr_tail"] = proc.stderr[-8000:] if proc.stderr else ""
            
            if proc.returncode != 0:
                if proc.returncode == -9:
                    result["error"] = (
                        f"批次 {batch_idx + 1} FFmpeg 被 SIGKILL 终止 (code=-9)，内存超限。"
                        f"cue_range=[{cue_start}:{cue_end}], resolution={video_width}x{video_height}, "
                        f"stderr: {proc.stderr[-2000:] if proc.stderr else '(empty)'}"
                    )
                    logger.error(
                        "[Node7] 批次 %d OOM: cue_range=[%d:%d], resolution=%dx%d",
                        batch_idx + 1, cue_start, cue_end, video_width, video_height,
                    )
                else:
                    result["error"] = f"批次 {batch_idx + 1} FFmpeg failed with code {proc.returncode}"
                return result
            
            if not os.path.exists(batch_output) or os.path.getsize(batch_output) == 0:
                result["error"] = f"批次 {batch_idx + 1} 输出文件不存在或大小为 0"
                return result
            
            # 下一批次的输入是当前批次的输出
            current_input_video = batch_output
        
        # 所有批次成功
        result["subtitle_burned"] = True
        logger.info("[Node7] 分批字幕烧录完成: %d 批次全部成功", len(batches))
        
    except Exception as e:
        result["error"] = str(e)
    finally:
        # 清理中间文件（保留最终输出）
        for f in intermediate_files:
            if f != output_path and os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
    
    return result


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
        # 同时验证每个clip文件的有效性
        clip_files = []
        invalid_clips = []
        for s in timeline:
            clip_path = s.get("clip_path", "")
            is_visual_continuation = s.get("visual_continuation", False)
            # 只包含有clip_path且不是visual continuation的条目
            if clip_path and not is_visual_continuation:
                if not os.path.exists(clip_path):
                    logger.warning("[Node7] clip文件不存在: %s", clip_path)
                    invalid_clips.append({"path": clip_path, "error": "file_not_found"})
                    continue
                
                # 使用ffprobe验证clip文件有效性
                clip_validation = validate_video_file(clip_path, min_duration=0.1)
                if not clip_validation["valid"]:
                    error_msg = f"clip文件无效: {clip_validation['error']}"
                    logger.error("[Node7] %s: %s", clip_path, error_msg)
                    invalid_clips.append({"path": clip_path, "error": error_msg})
                    continue
                
                clip_files.append(clip_path)

        if not clip_files:
            error_detail = f"无效clip数: {len(invalid_clips)}, 详情: {invalid_clips[:3]}"
            raise RuntimeError(f"无可用clip文件 ({error_detail})")
        
        if invalid_clips:
            logger.warning("[Node7] 跳过 %d 个无效clip: %s", len(invalid_clips), invalid_clips[:3])
        
        logger.info("[Node7] 活跃clip数: %d/%d (跳过 %d 个无效clip)", len(clip_files), len(timeline), len(invalid_clips))

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
            _log_ffmpeg_diagnostics(cmd, run_dir, "Node7-concat-single")
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
            _log_ffmpeg_diagnostics(cmd, run_dir, "Node7-concat-multi")
            run_ffmpeg(cmd, timeout=300)

        concat_duration = get_media_duration(concat_path)
        logger.info("[Node7] 拼接完成: %.2fs, TTS时长: %.2fs", concat_duration, tts_duration)

        # 1.4 精确Trim: 确保主体视频时长与TTS时长一致
        trim_tolerance = 0.05
        if concat_duration > tts_duration + trim_tolerance:
            trimmed_path = os.path.join(temp_dir, "trimmed.mp4")
            logger.info("[Node7] 精确Trim: %.2fs -> %.2fs", concat_duration, tts_duration)
            trim_cmd = [
                ffmpeg_path, "-y", "-i", concat_path,
                "-t", str(tts_duration),
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                trimmed_path
            ]
            _log_ffmpeg_diagnostics(trim_cmd, run_dir, "Node7-trim")
            run_ffmpeg(trim_cmd, timeout=120)
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
            tpad_cmd = [
                ffmpeg_path, "-y", "-i", concat_path,
                "-vf", f"tpad=stop_mode=clone:stop_duration={end_hold_sec}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                tpad_path
            ]
            _log_ffmpeg_diagnostics(tpad_cmd, run_dir, "Node7-tpad")
            run_ffmpeg(tpad_cmd, timeout=120)
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
        
        # 强制使用 Pillow PNG overlay 方式烧录字幕
        if not font_path:
            error_msg = "无法烧录字幕: 未找到中文字体"
            logger.error("[Node7] %s", error_msg)
            raise RuntimeError(error_msg)
        
        if not os.path.exists(actual_srt_path):
            error_msg = f"无法烧录字幕: SRT文件不存在 {actual_srt_path}"
            logger.error("[Node7] %s", error_msg)
            raise RuntimeError(error_msg)
        
        try:
            logger.info("[Node7] 使用分批 Pillow PNG overlay 方式烧录字幕")
            overlay_result = _burn_subtitles_batched(
                ffmpeg_path=ffmpeg_path,
                video_path=concat_path,
                audio_path=tts_wav_path,
                srt_path=actual_srt_path,
                font_path=font_path,
                output_path=subbed_path,
                temp_dir=temp_dir,
                video_width=1080,
                video_height=1920,
                max_cues_per_batch=3,
            )
            
            if overlay_result.get("subtitle_burned"):
                subtitle_burned = True
                subtitle_filter_used = "batched_pillow_png_overlay"
                logger.info(
                    "[Node7] 分批字幕烧录成功: cue_count=%d, batch_count=%d",
                    overlay_result.get("cue_count", 0),
                    overlay_result.get("batch_count", 0),
                )
            else:
                error_msg = overlay_result.get("error", "Unknown error")
                logger.error("[Node7] 分批字幕烧录失败: %s", error_msg)
                raise RuntimeError(f"字幕烧录失败: {error_msg}")
                
        except Exception as e:
            logger.error("[Node7] 分批字幕烧录异常: %s", e)
            raise RuntimeError(f"字幕烧录失败: {e}")
        
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
        bgm_warnings = []
        
        # 使用基于项目文件位置的绝对路径解析 BGM 目录
        if not bgm_url:
            # 尝试多种方式解析 BGM 目录
            bgm_dir = None
            for candidate in [
                os.path.join(os.path.dirname(__file__), "../../../assets/bgm"),
                os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "assets/bgm"),
            ]:
                candidate = os.path.abspath(candidate)
                if os.path.exists(candidate):
                    bgm_dir = candidate
                    break
            
            if bgm_dir:
                bgm_files = sorted([f for f in os.listdir(bgm_dir) if f.endswith(".mp3")])
                # 验证文件有效性
                valid_bgm_files = []
                for f in bgm_files:
                    fpath = os.path.join(bgm_dir, f)
                    try:
                        if os.path.getsize(fpath) > 0:
                            valid_bgm_files.append(fpath)
                    except Exception:
                        pass
                
                if valid_bgm_files:
                    import hashlib
                    digest = hashlib.sha256(run_dir.encode("utf-8")).digest()
                    bgm_index = int.from_bytes(digest[:8], "big") % len(valid_bgm_files)
                    bgm_url = valid_bgm_files[bgm_index]
                    logger.info("[Node7] 未指定BGM，稳定选择: %s", os.path.basename(bgm_url))
                else:
                    bgm_warnings.append("BGM 目录中没有有效的 MP3 文件，将仅使用 TTS 音频")
                    logger.warning("[Node7] BGM 目录中没有有效的 MP3 文件")
            else:
                bgm_warnings.append("BGM 目录不存在，将仅使用 TTS 音频")
                logger.warning("[Node7] BGM 目录不存在")
        
        if bgm_url:
            try:
                local_bgm = _download_bgm(bgm_url, temp_dir)
                bgm_duration = get_media_duration(local_bgm)
                logger.info("[Node7] BGM时长=%.2fs", bgm_duration)
                
                # 混合 TTS + BGM
                # 使用配置值或默认值 0.15
                bgm_volume = float(os.getenv("BGM_VOLUME", "0.15"))
                bgm_mix_cmd = [
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
                ]
                _log_ffmpeg_diagnostics(bgm_mix_cmd, run_dir, "Node7-bgm-mix")
                run_ffmpeg(bgm_mix_cmd, timeout=180)
                bgm_used = True
                logger.info("[Node7] TTS+BGM 混音完成")
                
            except Exception as e:
                error_msg = str(e)
                logger.error("[Node7] BGM混合失败: %s，仅使用TTS", error_msg)
                bgm_used = False
                # 添加安全、可理解的错误信息，不暴露内部路径或堆栈
                bgm_warnings.append("BGM 混音失败，视频已生成但仅包含 TTS 音频")
        
        if not bgm_used:
            # 仅使用 TTS
            tts_only_cmd = [
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
            ]
            _log_ffmpeg_diagnostics(tts_only_cmd, run_dir, "Node7-tts-only")
            run_ffmpeg(tts_only_cmd, timeout=120)
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

        result = {
            "final_video_path": final_mp4,
            "contact_sheet_path": contact_sheet_path,
            "video_duration": video_duration,
            "end_hold_sec": end_hold_sec if end_hold_sec > 0 else 0.0,
            "final_video_duration": video_duration,
            "final_audio_duration": video_duration,
            "mixed_audio_path": mixed_path,
            "bgm_used": bgm_used,
            "node_trace": ["final_composition"],
        }
        
        # 如果有 BGM 警告，合并到 warnings 中
        if bgm_warnings:
            existing_warnings = list(state.get("warnings") or [])
            existing_warnings.extend(bgm_warnings)
            result["warnings"] = existing_warnings
        
        return result

    except Exception as e:
        logger.error("[Node7] 合成失败: %s", e)
        write_trace_error(run_dir, "final_composition", "CompositionError", str(e))
        raise RuntimeError(f"最终合成失败: {e}")
