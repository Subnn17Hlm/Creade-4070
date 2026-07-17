"""
Node1: 输入规范化
====================
接收统一字段（script_source, raw_script, run_dir, product_name, material_csv），
保存original_script.txt，生成cleaned_script.txt和增强版input_meta.json。
"""
import os
import json
import re
import logging
import hashlib
from typing import List, Optional, Dict, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import InputNormInput, InputNormOutput
from graphs.shared_utils import ensure_dir

logger = logging.getLogger(__name__)


def _check_no_ellipsis(text: str) -> bool:
    """检测文案是否包含截断标记"""
    return "..." not in text


def _clean_script(text: str) -> str:
    """清洗文案：只做空白、换行、标点规范化，不改写内容"""
    text = text.replace("\n", " ").replace("\r", " ")
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[　]+", " ", text)
    return text


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


def input_normalization_node(
    state: InputNormInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> InputNormOutput:
    """
    title: 输入规范化
    desc: 接收统一字段，保存original_script.txt，生成cleaned_script.txt和增强版input_meta.json
    """
    ctx = runtime.context
    script_source = state.script_source
    raw_script = state.raw_script
    run_dir = state.run_dir
    product_name = state.product_name or ""
    material_csv = state.material_csv or ""

    logger.info("[Node1] script_source=%s, run_dir=%s", script_source, run_dir)

    # 1. 校验：原始脚本不能为空
    if not raw_script or not raw_script.strip():
        logger.error("[Node1] raw_script为空")
        return InputNormOutput(
            cleaned_script="",
            run_dir=run_dir,
            original_script_path="",
            cleaned_script_path="",
            input_meta_path="",
            original_chars=0,
            script_ok=False,
        )

    # 2. 保存原始文案（original_script.txt 必须保持原始字节内容，禁止strip或换行替换）
    script_path = os.path.join(run_dir, "original_script.txt")
    # 写入原始字节内容，不做任何修改
    with open(script_path, "wb") as f:
        f.write(raw_script.encode("utf-8"))
    
    # 计算原始文案SHA256
    original_script_sha256 = hashlib.sha256(raw_script.encode("utf-8")).hexdigest()

    # 3. 截断检测
    if not _check_no_ellipsis(raw_script):
        logger.error("[Node1] 文案含截断标记(...)")
        return InputNormOutput(
            cleaned_script="",
            run_dir=run_dir,
            original_script_path=script_path,
            cleaned_script_path="",
            input_meta_path="",
            original_chars=0,
            script_ok=False,
        )

    # 4. 清洗文案（仅用于TTS和字幕，不影响original_script.txt）
    cleaned = _clean_script(raw_script)
    
    # 计算清洗后文案SHA256
    tts_input_sha256 = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

    # 计算是否发生了改写
    raw_chars = len(''.join(raw_script.split()))
    cleaned_chars = len(''.join(cleaned.split()))
    script_changed = (raw_script != cleaned)

    cleaned_path = os.path.join(run_dir, "cleaned_script.txt")
    with open(cleaned_path, "w", encoding="utf-8") as f:
        f.write(cleaned)

    # 5. 保存TTS输入文本（与cleaned_script一致）
    tts_input_path = os.path.join(run_dir, "tts_input.txt")
    with open(tts_input_path, "w", encoding="utf-8") as f:
        f.write(cleaned)

    # 6. 保存增强版输入元数据
    input_meta = {
        "script_source": script_source,
        "product_name": product_name,
        "raw_script_chars": raw_chars,
        "cleaned_script_chars": cleaned_chars,
        "script_changed": script_changed,
        "change_reason": "whitespace_and_punctuation_normalization" if script_changed else "none",
        "material_csv": material_csv,
        "sentences": _split_sentences(cleaned),
        "sentence_count": len(_split_sentences(cleaned)),
        "run_dir": run_dir,
        "original_script_sha256": original_script_sha256,
        "tts_input_sha256": tts_input_sha256,
    }
    meta_path = os.path.join(run_dir, "input_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(input_meta, f, ensure_ascii=False, indent=2)

    logger.info("[Node1] 完成: raw_chars=%d, cleaned_chars=%d, changed=%s",
                raw_chars, cleaned_chars, script_changed)

    return InputNormOutput(
        cleaned_script=cleaned,
        run_dir=run_dir,
        original_script_path=script_path,
        cleaned_script_path=cleaned_path,
        input_meta_path=meta_path,
        original_chars=raw_chars,
        script_ok=True,
    )