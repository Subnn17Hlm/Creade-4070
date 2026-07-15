"""
Node3: 字幕分句与时间轴
职责：拆句，生成subtitles.srt和timing_debug.json，禁止平均切句
"""
import os
import json
import re
import logging
from typing import List, Dict, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import SubtitleTimingInput, SubtitleTimingOutput

logger = logging.getLogger(__name__)

# 每行字符数限制
CHARS_PER_LINE_MIN = 10
CHARS_PER_LINE_MAX = 14
MAX_LINES_PER_CUE = 2


def _split_sentences(text: str) -> List[str]:
    """按标点符号和空格拆分语义句段"""
    parts = re.split(r'[。！？，、；：\n\r]+', text)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        sub_parts = re.split(r'\s+', part)
        for sub in sub_parts:
            sub = sub.strip()
            if len(sub) > 2:
                result.append(sub)
    return result or [text.strip()]


def _calculate_sentence_timing(
    sentences: List[str], total_duration: float
) -> List[Dict[str, Any]]:
    """
    基于字符数占比 + 标点停顿权重计算每句时长。
    禁止平均分配。
    """
    punctuation_weights = {
        '。': 1.30, '！': 1.30, '？': 1.30,
        '，': 1.15, '、': 1.10, '；': 1.15, '：': 1.15,
        '…': 1.25,
    }

    weights = []
    for sent in sentences:
        base = float(len(sent))
        pw = 1.0
        for ch in sent:
            if ch in punctuation_weights:
                pw = max(pw, punctuation_weights[ch])
        weights.append(base * pw)

    total_weight = sum(weights) if sum(weights) > 0 else float(len(sentences))

    durations = []
    for w in weights:
        ratio = w / total_weight
        durations.append(total_duration * ratio)

    # 不再统一缩放，直接按照字数比例分配时长
    # 允许某些短句低于1.0秒，但限制在0.9秒以上
    # 这样可以避免为了匹配TTS时长而压缩所有片段
    MIN_DURATION = 0.9
    durations = [max(d, MIN_DURATION) for d in durations]

    # 如果总时长超过TTS时长，只压缩超过的部分，而不是统一缩放
    actual_total = sum(durations)
    if actual_total > total_duration + 0.01:
        # 找出超过的部分，按比例压缩
        excess = actual_total - total_duration
        # 只对超过MIN_DURATION的片段进行压缩
        adjustable = [(i, d - MIN_DURATION) for i, d in enumerate(durations) if d > MIN_DURATION]
        total_adjustable = sum(adj[1] for adj in adjustable)
        if total_adjustable > 0:
            scale = (total_adjustable - excess) / total_adjustable
            for i, adj_d in adjustable:
                durations[i] = MIN_DURATION + adj_d * scale

    result = []
    current = 0.0
    for i, sent in enumerate(sentences):
        dur = durations[i]
        result.append({
            "text": sent,
            "start_time": round(current, 3),
            "end_time": round(current + dur, 3),
            "duration": round(dur, 3),
        })
        current += dur

    if result and abs(result[-1]["end_time"] - total_duration) > 0.01:
        result[-1]["end_time"] = round(total_duration, 3)
        result[-1]["duration"] = round(
            result[-1]["end_time"] - result[-1]["start_time"], 3
        )

    return result


def _split_long_sentence(
    text: str, start_time: float, end_time: float
) -> List[Dict[str, Any]]:
    """
    将长句拆分为多个字幕cue。
    每行10-14字符，最多2行，超过则拆成多个cue。
    """
    duration = end_time - start_time
    if duration <= 0:
        duration = 0.5

    # 如果句子长度 <= 28字符(14*2)，直接作为1个cue(2行)
    if len(text) <= MAX_LINES_PER_CUE * CHARS_PER_LINE_MAX:
        return [{"text": text, "start_time": start_time, "end_time": end_time, "duration": duration}]

    # 按字符数拆分
    chars = list(text)
    cues = []
    chunk_size = CHARS_PER_LINE_MAX * MAX_LINES_PER_CUE  # 28字符
    total_chunks = max(1, (len(chars) + chunk_size - 1) // chunk_size)
    chunk_duration = duration / total_chunks

    for i in range(0, len(chars), chunk_size):
        chunk_text = "".join(chars[i:i + chunk_size])
        cs = start_time + i / len(chars) * duration
        ce = start_time + min((i + chunk_size) / len(chars) * duration, duration)
        if ce <= cs:
            ce = cs + 1.2
        cues.append({
            "text": chunk_text,
            "start_time": round(cs, 3),
            "end_time": round(ce, 3),
            "duration": round(ce - cs, 3),
        })

    return cues


def _build_srt(timing: List[Dict[str, Any]], srt_path: str):
    """生成SRT字幕，处理长句拆分"""
    lines = []
    cue_idx = 1
    for seg in timing:
        text = seg["text"].strip()
        if not text:
            continue
        start = seg["start_time"]
        end = seg["end_time"]

        # 拆分长句
        cues = _split_long_sentence(text, start, end)
        for cue in cues:
            cs = cue["start_time"]
            ce = cue["end_time"]
            fmt = lambda s: f"{int(s//3600):02d}:{int(s%3600//60):02d}:{s%60:06.3f}".replace(".", ",")
            lines.append(f"{cue_idx}")
            lines.append(f"{fmt(cs)} --> {fmt(ce)}")
            lines.append(cue["text"])
            lines.append("")
            cue_idx += 1

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _check_srt_overlap(srt_path: str) -> bool:
    """严格检查SRT字幕时间轴是否有重叠"""
    pat = re.compile(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)")
    prev_end = -1.0
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    for m in pat.finditer(content):
        factors = [3600, 60, 1, 0.001]
        start = sum(int(m.group(i)) * factors[i - 1] for i in range(1, 5))
        end = sum(int(m.group(i)) * factors[i - 5] for i in range(5, 9))
        if start < prev_end - 0.01:
            return False
        prev_end = end
    return True


def subtitle_timing_node(
    state: SubtitleTimingInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> SubtitleTimingOutput:
    """
    title: 字幕分句与时间轴
    desc: 基于文案拆句，使用字符权重+标点停顿分配时长，生成SRT字幕和时间轴调试JSON
    """
    ctx = runtime.context
    cleaned_script = state.cleaned_script
    tts_duration = state.tts_duration
    run_dir = state.run_dir

    logger.info("[Node3] 字幕时间轴分配...")

    if tts_duration <= 0:
        logger.error("[Node3] TTS时长为0，无法分配")
        return SubtitleTimingOutput(
            sentences=[], timing=[], srt_path="",
            timing_debug_path="", srt_no_overlap=False,
            srt_coverage=0.0, final_chars=0,
        )

    # 1. 拆句
    sentences = _split_sentences(cleaned_script)
    logger.info("[Node3] 拆句: %d句", len(sentences))

    # 2. 计算时间轴（字符权重+标点，禁止平均）
    timing = _calculate_sentence_timing(sentences, tts_duration)
    total_timing = sum(t["duration"] for t in timing)
    logger.info("[Node3] 时间轴: 总和=%.3fs, TTS=%.3fs", total_timing, tts_duration)

    # 3. 保存时间轴调试JSON
    timing_debug_path = os.path.join(run_dir, "timing_debug.json")
    with open(timing_debug_path, "w", encoding="utf-8") as f:
        json.dump(timing, f, ensure_ascii=False, indent=2)

    # 4. 生成SRT字幕（含长句拆分）
    srt_path = os.path.join(run_dir, "subtitles.srt")
    _build_srt(timing, srt_path)
    no_overlap = _check_srt_overlap(srt_path)
    logger.info("[Node3] 字幕生成: %s, 无重叠=%s", srt_path, no_overlap)

    # 5. 计算字幕覆盖率
    srt_text = " ".join(t["text"] for t in timing)
    clean_orig = ''.join(cleaned_script.split())
    clean_srt = ''.join(srt_text.split())
    overlap_count = sum(1 for ch in clean_orig if ch in clean_srt)
    coverage = overlap_count / len(clean_orig) if clean_orig else 0
    final_chars = int(len(clean_orig) * coverage)

    logger.info("[Node3] 字幕覆盖率: %.1f%%", coverage * 100)

    return SubtitleTimingOutput(
        sentences=sentences,
        timing=timing,
        srt_path=srt_path,
        timing_debug_path=timing_debug_path,
        srt_no_overlap=no_overlap,
        srt_coverage=coverage,
        final_chars=final_chars,
    )