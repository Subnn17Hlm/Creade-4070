"""
Node0b: 生成文案（Mode A）
============================
仅当 script_source=generated 时进入此节点。
根据产品信息、核心卖点、目标人群、视频风格和素材标签，使用LLM生成完整短视频口播文案。
输出：
  - generated_script.txt（LLM生成的原稿）
  - original_script.txt（统一入口，等于generated_script.txt）
"""
import os
import json
import logging
from typing import List, Dict, Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from langchain_core.messages import SystemMessage, HumanMessage
from coze_coding_dev_sdk import LLMClient

from graphs.state import GenerateScriptInput, GenerateScriptOutput

logger = logging.getLogger(__name__)

WORKSPACE = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")


def _read_material_tags(material_csv: str) -> List[str]:
    """从素材CSV读取所有可用标签"""
    from pathlib import Path
    # 项目根目录：src/graphs/nodes/ -> 上溯 3 级
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

    if not material_csv:
        material_csv = "assets/asset_manifest_v2_bound.csv"

    csv_path = Path(material_csv)
    if not csv_path.is_absolute():
        csv_path = _PROJECT_ROOT / csv_path

    if not csv_path.is_file():
        logger.warning("[Node0b] 素材CSV不存在: %s", csv_path)
        return []

    tags = set()
    try:
        import csv
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tag_str = row.get("tags", row.get("tag", ""))
                if tag_str:
                    for t in tag_str.split(","):
                        t = t.strip()
                        if t:
                            tags.add(t)
    except Exception as e:
        logger.warning("[Node0b] 读取素材标签失败: %s", e)
    return sorted(tags)


def _generate_script_with_llm(
    product_name: str,
    selling_points: List[str],
    target_audience: str,
    video_style: str,
    material_tags: List[str],
    cfg_path: str,
    runtime: Runtime[Context],
) -> str:
    """使用LLM生成文案"""
    ctx = runtime.context or None

    # 读取LLM配置
    full_cfg_path = cfg_path if os.path.isabs(cfg_path) else os.path.join(WORKSPACE, cfg_path)
    with open(full_cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    llm_config = cfg.get("config", {})
    sp = cfg.get("sp", "")
    up = cfg.get("up", "")

    # 渲染模板
    from jinja2 import Template
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "product_name": product_name,
        "core_selling_points": "、".join(selling_points) if selling_points else "无",
        "target_audience": target_audience or "目标消费者",
        "video_style": video_style or "短视频带货",
        "material_tags": "、".join(material_tags) if material_tags else "无限制",
    })

    # 调用LLM
    client = LLMClient(ctx=ctx)
    messages = [
        SystemMessage(content=sp),
        HumanMessage(content=user_prompt),
    ]

    response = client.invoke(
        messages=messages,
        model=llm_config.get("model", "doubao-seed-2-0-pro-260215"),
        temperature=llm_config.get("temperature", 0.7),
        top_p=llm_config.get("top_p", 0.95),
        max_completion_tokens=llm_config.get("max_completion_tokens", 2048),
    )

    # 处理响应内容
    if isinstance(response.content, str):
        script = response.content.strip()
    elif isinstance(response.content, list):
        parts = []
        for item in response.content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        script = " ".join(parts).strip()
    else:
        script = str(response.content).strip()

    # 清理：移除可能的markdown代码块包装
    if script.startswith("```"):
        lines = script.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        script = "\n".join(lines).strip()

    # 清理：移除可能的引号包装
    script = script.strip('"').strip("'").strip()

    return script


def generate_script_node(
    state: GenerateScriptInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> GenerateScriptOutput:
    """
    title: 生成文案
    desc: 根据产品信息、核心卖点、目标人群、视频风格和素材标签，使用LLM生成完整短视频口播文案。仅当script_source=generated时进入此分支。
    integrations: 大语言模型
    """
    ctx = runtime.context
    product_name = state.product_name or "未知产品"
    selling_points = state.core_selling_points or []
    target_audience = state.target_audience or "目标消费者"
    video_style = state.video_style or "短视频带货"
    material_csv = state.material_csv or ""
    run_dir = state.run_dir

    logger.info("[Node0b] 生成文案: product=%s, run_dir=%s", product_name, run_dir)

    # 读取素材标签
    material_tags = _read_material_tags(material_csv)
    logger.info("[Node0b] 素材标签 (%d): %s", len(material_tags), material_tags[:10])

    # 从metadata读取LLM配置路径
    llm_cfg = config.get("metadata", {}).get("llm_cfg", "config/script_generate_llm_cfg.json")

    # 生成文案
    try:
        raw_script = _generate_script_with_llm(
            product_name=product_name,
            selling_points=selling_points,
            target_audience=target_audience,
            video_style=video_style,
            material_tags=material_tags,
            cfg_path=llm_cfg,
            runtime=runtime,
        )
        logger.info("[Node0b] LLM生成文案完成: %d chars", len(raw_script))
    except Exception as e:
        logger.error("[Node0b] LLM生成文案失败: %s", e)
        raise RuntimeError(f"生成文案失败: {e}")

    # 保存generated_script.txt
    generated_script_path = os.path.join(run_dir, "generated_script.txt")
    with open(generated_script_path, "w", encoding="utf-8") as f:
        f.write(raw_script)
    logger.info("[Node0b] 生成文案已保存: %s (%d chars)", generated_script_path, len(raw_script))

    # 保存original_script.txt（统一入口）
    original_script_path = os.path.join(run_dir, "original_script.txt")
    with open(original_script_path, "w", encoding="utf-8") as f:
        f.write(raw_script)

    logger.info("[Node0b] 完成: source=generated, chars=%d", len(raw_script))

    return GenerateScriptOutput(
        raw_script=raw_script,
        script_source="generated",
        generated_script_path=generated_script_path,
        original_script_path=original_script_path,
    )