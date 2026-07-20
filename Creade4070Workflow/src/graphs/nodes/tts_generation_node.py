"""
Node2: TTS生成
职责：将cleaned_script送入TTS，生成tts.wav，获取audio_duration
"""
import os
import json
import wave
import logging
import traceback

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import TTSClient

from utils.ffmpeg_utils import get_ffmpeg_path, run_ffmpeg, get_ffmpeg_info

logger = logging.getLogger(__name__)


def _get_wav_duration(wav_path: str) -> float:
    """使用 wave 标准库读取 WAV 文件时长（不依赖 ffprobe）"""
    try:
        with wave.open(wav_path, 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return frames / float(rate)
    except Exception as e:
        logger.warning("[Node2] wave 模块读取时长失败: %s", e)
    return 0.0


def _write_trace(run_dir: str, entry: dict) -> None:
    """写入节点追踪文件"""
    trace_path = os.path.join(run_dir, "node_trace.jsonl")
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def tts_generation_node(
    state: dict,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> dict:
    """
    title: TTS生成
    desc: 将清洗后文案送入TTS，生成tts.wav并获取audio_duration
    integrations: 音频
    """
    ctx = runtime.context
    run_dir = state.get("run_dir", "")
    
    # 获取 ffmpeg 信息
    ffmpeg_info = get_ffmpeg_info()
    ffmpeg_path = ffmpeg_info.get("ffmpeg_resolved_path")
    
    # 获取文案内容
    cleaned_script = state.get("cleaned_script", "") or ""
    original_script_path = state.get("original_script_path", "") or ""
    
    # 防御性回退：如果 cleaned_script 为空，尝试从 original_script.txt 读取
    if not cleaned_script and original_script_path and os.path.exists(original_script_path):
        with open(original_script_path, "r", encoding="utf-8") as f:
            cleaned_script = f.read().strip()
        logger.warning("[Node2] cleaned_script为空，从original_script.txt回退读取 (%d chars)", len(cleaned_script))

    tts_wav_path = os.path.join(run_dir, "tts.wav")
    tts_input_path = os.path.join(run_dir, "tts_input.txt")
    tts_meta_path = os.path.join(run_dir, "tts_meta.json")
    
    # Phase: entered
    _write_trace(run_dir, {
        "node": "tts_generation",
        "phase": "entered",
        "provider": "coze_coding_dev_sdk.TTSClient",
        "input_path": tts_input_path,
        "input_chars": len(cleaned_script),
        "requested_output_path": tts_wav_path,
        "ffmpeg_path": ffmpeg_path,
        "ffmpeg_source": ffmpeg_info.get("ffmpeg_source"),
    })
    
    logger.info("[Node2] TTS合成开始... cleaned_script_chars=%d, ffmpeg=%s", 
                len(cleaned_script), ffmpeg_path)

    # 文案为空时抛出异常，不静默返回
    if not cleaned_script:
        error_msg = f"文案为空 (cleaned_script=0, original_script_path={original_script_path})"
        logger.error("[Node2] %s", error_msg)
        _write_trace(run_dir, {
            "node": "tts_generation",
            "phase": "error",
            "error_type": "EmptyScriptError",
            "error_message": error_msg,
        })
        raise RuntimeError(f"TTS失败: {error_msg}")

    # 保存TTS输入文本
    with open(tts_input_path, "w", encoding="utf-8") as f:
        f.write(cleaned_script)

    tts_mp3_path = os.path.join(run_dir, "tts.mp3")
    provider = "coze_coding_dev_sdk.TTSClient"
    audio_url = ""
    audio_size = 0
    response_status = ""
    
    try:
        # 调用 TTS 服务
        tts_client = TTSClient(ctx=ctx)
        audio_url, audio_size = tts_client.synthesize(
            uid="video_workflow",
            text=cleaned_script,
            speaker="zh_female_xiaohe_uranus_bigtts",
            audio_format="mp3",
            sample_rate=24000,
            speech_rate=1.0,
        )
        response_status = f"url_received,size={audio_size}"
        
        if not audio_url:
            raise RuntimeError("TTS返回空URL")

        # 下载 MP3
        import requests
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        response_status = f"downloaded,status={resp.status_code},size={len(resp.content)}"
        
        with open(tts_mp3_path, "wb") as f:
            f.write(resp.content)
        
        source_audio_path = tts_mp3_path
        source_audio_size = os.path.getsize(tts_mp3_path)
        
        # 转码为 WAV (PCM signed 16-bit little-endian, mono, 24000 Hz)
        conversion_command = f"{ffmpeg_path} -y -i {tts_mp3_path} -acodec pcm_s16le -ar 24000 -ac 1 {tts_wav_path}"
        run_ffmpeg([ffmpeg_path, "-y", "-i", tts_mp3_path,
                     "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", tts_wav_path])
        
        # 删除临时 MP3
        if os.path.exists(tts_mp3_path):
            os.remove(tts_mp3_path)

        # 验证输出文件
        if not os.path.exists(tts_wav_path):
            raise RuntimeError(f"TTS输出文件不存在: {tts_wav_path}")
        
        wav_size = os.path.getsize(tts_wav_path)
        if wav_size == 0:
            raise RuntimeError(f"TTS输出文件大小为0: {tts_wav_path}")
        
        # 获取时长：使用 wave 标准库（不依赖 ffprobe）
        wav_duration = _get_wav_duration(tts_wav_path)
        
        if wav_duration <= 0:
            raise RuntimeError(f"TTS输出文件时长为0: {tts_wav_path}")
        
        logger.info("[Node2] TTS完成: duration=%.2fs, size=%d bytes", wav_duration, wav_size)

        # 保存TTS元数据
        tts_meta = {
            "tts_duration": wav_duration,
            "speaker": "zh_female_xiaohe_uranus_bigtts",
            "sample_rate": 24000,
            "provider": provider,
            "audio_url": audio_url[:100] if audio_url else "",
            "audio_size": audio_size,
            "ffmpeg_path": ffmpeg_path,
        }
        with open(tts_meta_path, "w", encoding="utf-8") as f:
            json.dump(tts_meta, f, ensure_ascii=False, indent=2)

        # Phase: completed
        _write_trace(run_dir, {
            "node": "tts_generation",
            "phase": "completed",
            "provider": provider,
            "input_path": tts_input_path,
            "input_chars": len(cleaned_script),
            "requested_output_path": tts_wav_path,
            "response_status": response_status,
            "source_audio_path": source_audio_path,
            "source_audio_size": source_audio_size,
            "conversion_command": conversion_command,
            "wav_path": tts_wav_path,
            "wav_size": wav_size,
            "wav_duration": wav_duration,
            "output_file_exists": True,
            "output_file_size": wav_size,
            "measured_duration": wav_duration,
        })

        # 返回 dict，确保 LangGraph 正确合并到 State
        return {
            "tts_wav_path": tts_wav_path,
            "tts_input_path": tts_input_path,
            "tts_meta_path": tts_meta_path,
            "tts_duration": wav_duration,
            "audio_duration": wav_duration,  # 兼容字段
            # 保留脚本文本防止被后续节点覆盖
            "cleaned_script": cleaned_script,
            "raw_script": state.get("raw_script", "") or "",
            "script_text": state.get("script_text", "") or "",
            "node_trace": ["tts_generation"],
        }

    except Exception as e:
        error_type = type(e).__name__
        error_message = str(e)
        error_traceback = traceback.format_exc()
        
        logger.error("[Node2] TTS失败: %s: %s", error_type, error_message)
        logger.error("[Node2] 堆栈:\n%s", error_traceback)
        
        # Phase: error
        _write_trace(run_dir, {
            "node": "tts_generation",
            "phase": "error",
            "error_type": error_type,
            "error_message": error_message,
            "provider": provider,
            "response_status": response_status,
        })
        
        # 抛出异常，不静默返回
        raise RuntimeError(f"TTS失败 [{error_type}]: {error_message}")
