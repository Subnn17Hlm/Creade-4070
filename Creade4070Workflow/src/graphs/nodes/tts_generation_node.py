"""
Node2: TTS生成
职责：将cleaned_script送入TTS，生成tts.wav，用ffprobe获取audio_duration
"""
import os
import json
import logging

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import TTSClient

from graphs.state import TTSGenInput, TTSGenOutput
from graphs.shared_utils import ensure_dir, get_media_duration, run_ffmpeg

logger = logging.getLogger(__name__)


def tts_generation_node(
    state: TTSGenInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> TTSGenOutput:
    """
    title: TTS生成
    desc: 将清洗后文案送入TTS，生成tts.wav并获取audio_duration
    integrations: 音频
    """
    ctx = runtime.context
    cleaned_script = state.cleaned_script
    run_dir = state.run_dir

    logger.info("[Node2] TTS合成开始... cleaned_script_chars=%d", len(cleaned_script))

    # 防御性回退：如果 cleaned_script 为空，尝试从 original_script.txt 读取
    if not cleaned_script:
        original_script_path = getattr(state, 'original_script_path', '') or ''
        if original_script_path and os.path.exists(original_script_path):
            with open(original_script_path, "r", encoding="utf-8") as f:
                cleaned_script = f.read().strip()
            logger.warning("[Node2] cleaned_script为空，从original_script.txt回退读取 (%d chars)", len(cleaned_script))

    if not cleaned_script:
        logger.error("[Node2] 文案为空 (cleaned_script=0, original_script_path=%s)", 
                     getattr(state, 'original_script_path', ''))
        return TTSGenOutput(
            tts_wav_path="", tts_input_path="", tts_meta_path="",
            tts_duration=0.0,
        )

    # 保存TTS输入文本
    tts_input_path = os.path.join(run_dir, "tts_input.txt")
    with open(tts_input_path, "w", encoding="utf-8") as f:
        f.write(cleaned_script)

    tts_wav = os.path.join(run_dir, "tts.wav")
    tts_mp3 = os.path.join(run_dir, "tts.mp3")

    try:
        tts_client = TTSClient(ctx=ctx)
        audio_url, audio_size = tts_client.synthesize(
            uid="video_workflow",
            text=cleaned_script,
            speaker="zh_female_xiaohe_uranus_bigtts",
            audio_format="mp3",
            sample_rate=24000,
            speech_rate=1.0,
        )
        if not audio_url:
            raise RuntimeError("TTS返回空URL")

        # 下载并转码
        resp = __import__("requests").get(audio_url, timeout=60)
        resp.raise_for_status()
        with open(tts_mp3, "wb") as f:
            f.write(resp.content)
        run_ffmpeg(["ffmpeg", "-y", "-i", tts_mp3,
                     "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", tts_wav])
        os.remove(tts_mp3)

        tts_duration = get_media_duration(tts_wav)
        logger.info("[Node2] TTS完成: %.2fs", tts_duration)

        # 保存TTS元数据
        tts_meta = {
            "tts_duration": tts_duration,
            "speaker": "zh_female_xiaohe_uranus_bigtts",
            "sample_rate": 24000,
        }
        tts_meta_path = os.path.join(run_dir, "tts_meta.json")
        with open(tts_meta_path, "w", encoding="utf-8") as f:
            json.dump(tts_meta, f, ensure_ascii=False, indent=2)

        return TTSGenOutput(
            tts_wav_path=tts_wav,
            tts_input_path=tts_input_path,
            tts_meta_path=tts_meta_path,
            tts_duration=tts_duration,
            # 保留脚本文本防止被后续节点覆盖
            cleaned_script=cleaned_script,
            raw_script=getattr(state, 'raw_script', '') or '',
            script_text=getattr(state, 'script_text', '') or '',
            node_trace=["tts_generation"],
        )

    except Exception as e:
        logger.error("[Node2] TTS失败: %s", e)
        return TTSGenOutput(
            tts_wav_path="", tts_input_path=tts_input_path,
            tts_meta_path="", tts_duration=0.0,
            # 即使失败也要保留脚本文本
            cleaned_script=cleaned_script,
            raw_script=getattr(state, 'raw_script', '') or '',
            script_text=getattr(state, 'script_text', '') or '',
            node_trace=["tts_generation"],
        )