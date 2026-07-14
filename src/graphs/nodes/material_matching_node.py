import os
import json
import csv
import logging
from typing import Dict, List, Any, Optional, Set
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import MaterialMatchInput, MaterialMatchOutput

logger = logging.getLogger(__name__)

# 标签同义映射（用于同义匹配）
_TAG_SYNONYMS: Dict[str, List[str]] = {
    "旅行场景": ["旅行", "出差", "出行", "酒店", "旅差"],
    "痛点共鸣": ["痛点", "困扰", "烦恼", "发愁", "共鸣"],
    "手持展示": ["手持", "产品展示", "展示"],
    "手持大小对比": ["大小对比", "对比", "尺寸对比", "便携"],
    "折叠动作": ["折叠", "收纳", "折叠收纳"],
    "放进包包": ["放包", "收纳携带", "包包", "随身"],
    "放进行李箱": ["行李箱", "收纳箱", "旅行箱"],
    "风力展示": ["风力", "大风力", "速干", "吹风"],
    "护发效果": ["护发", "护发", "柔顺", "顺滑"],
    "屏显调温": ["屏显", "调温", "温控", "温度调节", "温度"],
    "CTA促单": ["促单", "CTA", "行动号召", "购买", "种草"],
    "吹发动作": ["吹发", "吹干", "吹头发", "干发"],
    "包装展示": ["包装", "开箱", "产品"],
    "赠品展示": ["赠品", "配件", "附件"],
    "价格促销": ["价格", "促销", "优惠"],
    "风嘴配件": ["风嘴", "配件", "喷嘴"],
}

# 标签语义回落（用于无任何标签匹配时的兜底）
_TAG_SEMANTIC_FALLBACK: Dict[str, List[str]] = {
    "旅行场景": ["放进行李箱", "放进包包", "折叠动作"],
    "痛点共鸣": ["吹发动作", "放进行李箱"],
    "手持展示": ["手持大小对比", "包装展示"],
    "手持大小对比": ["手持展示", "折叠动作"],
    "折叠动作": ["手持展示", "放进包包"],
    "放进包包": ["放进行李箱", "旅行场景"],
    "放进行李箱": ["旅行场景", "放进包包"],
    "风力展示": ["吹发动作", "护发效果"],
    "护发效果": ["吹发动作", "风力展示"],
    "屏显调温": [],
    "CTA促单": ["旅行场景", "放进行李箱"],
    "吹发动作": ["护发效果", "风力展示"],
    "包装展示": ["手持展示"],
    "赠品展示": ["包装展示"],
    "价格促销": ["CTA促单"],
    "风嘴配件": ["包装展示"],
}


def _load_material_manifest(csv_path: str) -> List[Dict[str, Any]]:
    """加载素材清单CSV，返回素材列表"""
    materials = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            deprecated = row.get('deprecated', '').strip().lower()
            if deprecated == 'true':
                continue
            mat = {
                "asset_id": row.get("asset_id", "").strip(),
                "file_name": row.get("file_name", "").strip(),
                "primary_scene_tag": row.get("primary_scene_tag", "").strip(),
                "bucket": row.get("bucket", "").strip(),
                "object_key": row.get("object_key", "").strip(),
                "s3_url": row.get("s3_url", "").strip(),
                "needs_clip": row.get("needs_clip", "").strip().lower() == 'true',
                "notes": row.get("notes", "").strip(),
                "batch": row.get("batch", "").strip(),
            }
            if mat["s3_url"]:
                materials.append(mat)
    return materials


def _load_sentence_tag_mapping(mapping_path: str) -> List[Dict[str, Any]]:
    """加载句子标签映射JSON"""
    if not os.path.exists(mapping_path):
        logger.warning(f"句子标签映射文件不存在: {mapping_path}")
        return []
    with open(mapping_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("sentences", data.get("mapping", []))
    return []


def _match_exact_tag(required_tags: List[str], material_tags: Set[str]) -> Optional[str]:
    """精确标签匹配：返回第一个匹配到的标签"""
    for tag in required_tags:
        if tag in material_tags:
            return tag
    return None


def _match_synonym_tag(required_tags: List[str], material_tags: Set[str]) -> Optional[str]:
    """同义标签匹配：检查required_tags的同义词是否命中material_tags"""
    for req_tag in required_tags:
        synonyms = _TAG_SYNONYMS.get(req_tag, [])
        for syn in synonyms:
            for mat_tag in material_tags:
                if syn in mat_tag or mat_tag in syn:
                    return req_tag
    return None


def _match_semantic_fallback(required_tags: List[str], material_tags: Set[str]) -> Optional[str]:
    """语义回落匹配：从required_tags的回落标签中查找"""
    for req_tag in required_tags:
        fallback_tags = _TAG_SEMANTIC_FALLBACK.get(req_tag, [])
        for fb_tag in fallback_tags:
            if fb_tag in material_tags:
                return fb_tag
    return None


def material_matching_node(
    state: MaterialMatchInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> MaterialMatchOutput:
    """
    title: 素材标签匹配
    desc: 根据句子标签映射和素材清单的primary_scene_tag进行精确/同义/语义回落匹配
    integrations: 
    """
    ctx = runtime.context
    run_dir = state.run_dir

    # 1. 确定使用的素材清单文件
    csv_path = state.material_csv
    if not csv_path or not os.path.exists(csv_path):
        # 尝试默认路径
        default_csv = os.path.join(os.getenv("COZE_WORKSPACE_PATH", ""), "assets", "asset_manifest_new_no_chuifa.csv")
        if os.path.exists(default_csv):
            csv_path = default_csv
        else:
            raise FileNotFoundError(f"未找到素材标签表: {csv_path}")

    manifest_file_used = os.path.basename(csv_path)

    # 2. 加载素材清单
    all_materials = _load_material_manifest(csv_path)
    logger.info(f"素材清单 {manifest_file_used}: {len(all_materials)} 个可用素材")

    # 3. 构建标签→素材索引
    tag_to_materials: Dict[str, List[Dict]] = {}
    for mat in all_materials:
        tag = mat["primary_scene_tag"]
        if tag not in tag_to_materials:
            tag_to_materials[tag] = []
        tag_to_materials[tag].append(mat)

    available_tags = set(tag_to_materials.keys())
    logger.info(f"可用标签: {sorted(available_tags)}")

    # 4. 加载句子标签映射
    mapping_path = os.path.join(run_dir, "sentence_tag_mapping.json")
    if not os.path.exists(mapping_path):
        # 尝试从项目根目录读取
        mapping_path = os.path.join(
            os.getenv("COZE_WORKSPACE_PATH", ""),
            "assets",
            "sentence_tag_mapping_script_02.json"
        )

    sentence_mappings = _load_sentence_tag_mapping(mapping_path)
    mapping_file_used = os.path.basename(mapping_path) if os.path.exists(mapping_path) else ""
    logger.info(f"句子标签映射: {len(sentence_mappings)} 条")

    # 5. 执行匹配
    selected_assets: List[Dict] = []
    used_material_ids: Set[str] = set()
    total_sentences = len(sentence_mappings)
    exact_count = 0
    synonym_count = 0
    fallback_count = 0
    unmatched_ids: List[int] = []
    high_conf = 0
    medium_conf = 0
    low_conf = 0
    mismatch_ids: List[int] = []

    for idx, mapping in enumerate(sentence_mappings):
        sentence_id = mapping.get("sentence_id", idx + 1)
        sentence_text = mapping.get("sentence_text", "")
        required_tags = mapping.get("required_tags", [])
        target_duration = mapping.get("duration", 3.0)

        # 过滤候选素材
        candidates: List[Dict] = []
        seen_material_ids = set()

        # 4个匹配阶段
        tag_match_type = "fallback"
        matched_tag = None
        semantic_fallback_used = False

        # 阶段1: 精确标签匹配
        for req_tag in required_tags:
            if req_tag in tag_to_materials:
                for mat in tag_to_materials[req_tag]:
                    mid = mat["asset_id"]
                    if mid not in seen_material_ids:
                        candidates.append(mat)
                        seen_material_ids.add(mid)

        if candidates:
            tag_match_type = "exact"
            exact_count += 1
            matched_tag = required_tags[0]
        else:
            # 阶段2: 同义标签匹配
            for req_tag in required_tags:
                synonyms = _TAG_SYNONYMS.get(req_tag, [])
                for syn in synonyms:
                    for tag, mats in tag_to_materials.items():
                        if syn in tag or tag in syn:
                            for mat in mats:
                                mid = mat["asset_id"]
                                if mid not in seen_material_ids:
                                    candidates.append(mat)
                                    seen_material_ids.add(mid)

            if candidates:
                tag_match_type = "synonym"
                synonym_count += 1
            else:
                # 阶段3: 语义回落
                for req_tag in required_tags:
                    fallback_tags = _TAG_SEMANTIC_FALLBACK.get(req_tag, [])
                    for fb_tag in fallback_tags:
                        if fb_tag in tag_to_materials:
                            for mat in tag_to_materials[fb_tag]:
                                mid = mat["asset_id"]
                                if mid not in seen_material_ids:
                                    candidates.append(mat)
                                    seen_material_ids.add(mid)

                if candidates:
                    tag_match_type = "fallback"
                    fallback_count += 1
                    semantic_fallback_used = True
                else:
                    # 阶段4: 完全无匹配，使用任何可用素材
                    for mat in all_materials:
                        mid = mat["asset_id"]
                        if mid not in seen_material_ids:
                            candidates.append(mat)
                            seen_material_ids.add(mid)
                    tag_match_type = "fallback"
                    fallback_count += 1
                    semantic_fallback_used = True
                    mismatch_ids.append(sentence_id)

        # 计算匹配分数
        tag_overlap = 0
        synonym_overlap = 0
        for req_tag in required_tags:
            if req_tag == any(m["primary_scene_tag"] for m in candidates):
                tag_overlap += 1
            else:
                synonyms = _TAG_SYNONYMS.get(req_tag, [])
                for syn in synonyms:
                    if any(syn in m["primary_scene_tag"] or m["primary_scene_tag"] in syn for m in candidates):
                        synonym_overlap += 1
                        break

        # 选择最佳素材
        # 优先选择未使用过的素材
        unused_candidates = [c for c in candidates if c["asset_id"] not in used_material_ids]
        if unused_candidates:
            selected = unused_candidates[0]
            repeated_reason = ""
        else:
            selected = candidates[0] if candidates else all_materials[0]
            repeated_reason = f"素材已用完，复用{selected['asset_id']}"

        used_material_ids.add(selected["asset_id"])

        # 计算置信度
        if tag_match_type == "exact":
            match_confidence = "high"
            match_score = 1.0
            high_conf += 1
        elif tag_match_type == "synonym":
            match_confidence = "medium"
            match_score = 0.7
            medium_conf += 1
        else:
            if semantic_fallback_used and not mismatch_ids:
                match_confidence = "medium"
                match_score = 0.5
                medium_conf += 1
            else:
                match_confidence = "low"
                match_score = 0.3
                low_conf += 1

        # 构建匹配理由
        if tag_match_type == "exact":
            match_reason = f"精确标签匹配: {required_tags} → {selected['primary_scene_tag']}"
        elif tag_match_type == "synonym":
            match_reason = f"同义标签匹配: {required_tags} → {selected['primary_scene_tag']}"
        else:
            match_reason = f"语义回落: {required_tags} → {selected['primary_scene_tag']}"

        # 候选素材列表（前5个）
        alt_candidates = [
            {
                "material_id": c["asset_id"],
                "file_name": c["file_name"],
                "primary_scene_tag": c["primary_scene_tag"],
            }
            for c in candidates[:5]
        ]

        entry = {
            "sentence_id": sentence_id,
            "sentence_text": sentence_text,
            "required_tags": required_tags,
            "candidate_materials": [
                {
                    "material_id": c["asset_id"],
                    "file_name": c["file_name"],
                    "primary_scene_tag": c["primary_scene_tag"],
                }
                for c in candidates[:10]
            ],
            "selected_material_id": selected["asset_id"],
            "selected_file_name": selected["file_name"],
            "selected_primary_scene_tag": selected["primary_scene_tag"],
            "selected_url": selected["s3_url"],
            "tag_match_type": tag_match_type,
            "tag_overlap": tag_overlap,
            "synonym_overlap": synonym_overlap,
            "semantic_fallback_used": semantic_fallback_used,
            "match_score": match_score,
            "match_confidence": match_confidence,
            "match_reason": match_reason,
            "alternative_candidates": alt_candidates,
            "repeated_material_reason": repeated_reason,
        }
        selected_assets.append(entry)

    if not sentence_mappings:
        # 如果没有映射文件，使用状态中的timing
        logger.warning("未找到句子标签映射，使用timing中的sentence_id")
        for idx, shot in enumerate(state.timing):
            sentence_id = shot.get("sentence_id", idx + 1)
            sentence_text = shot.get("text", "")
            required_tags = shot.get("semantic_tags", [])
            target_duration = shot.get("duration", 3.0)

            candidates = []
            for req_tag in required_tags:
                if req_tag in tag_to_materials:
                    for mat in tag_to_materials[req_tag]:
                        candidates.append(mat)

            if not candidates:
                candidates = all_materials[:3]

            selected = candidates[0] if candidates else all_materials[0]
            used_material_ids.add(selected["asset_id"])

            entry = {
                "sentence_id": sentence_id,
                "sentence_text": sentence_text,
                "required_tags": required_tags,
                "candidate_materials": [
                    {"material_id": c["asset_id"], "file_name": c["file_name"],
                     "primary_scene_tag": c["primary_scene_tag"]}
                    for c in candidates[:10]
                ],
                "selected_material_id": selected["asset_id"],
                "selected_file_name": selected["file_name"],
                "selected_primary_scene_tag": selected["primary_scene_tag"],
                "selected_url": selected["s3_url"],
                "tag_match_type": "fallback",
                "tag_overlap": 0,
                "synonym_overlap": 0,
                "semantic_fallback_used": True,
                "match_score": 0.5,
                "match_confidence": "medium",
                "match_reason": f"无映射文件，基于timing标签匹配: {selected['primary_scene_tag']}",
                "alternative_candidates": [],
                "repeated_material_reason": "",
            }
            selected_assets.append(entry)

    # 6. 构建mapping_coverage
    covered_ids = set(e["sentence_id"] for e in selected_assets if e["tag_match_type"] in ("exact", "synonym"))
    total_sentences = max(total_sentences, len(selected_assets))
    mapping_coverage = round(len(covered_ids) / max(total_sentences, 1) * 100, 1)

    # 7. 保存selected_assets.json
    selected_assets_path = os.path.join(run_dir, "selected_assets.json")
    with open(selected_assets_path, 'w', encoding='utf-8') as f:
        json.dump(selected_assets, f, ensure_ascii=False, indent=2)

    # 8. 构建semantic_match_report
    tag_distribution: Dict[str, int] = {}
    for entry in selected_assets:
        tag = entry["selected_primary_scene_tag"]
        tag_distribution[tag] = tag_distribution.get(tag, 0) + 1

    match_report = {
        "manifest_file_used": manifest_file_used,
        "total_manifest_assets": len(all_materials),
        "available_assets_after_filter": len([m for m in all_materials
                                               if m.get("deprecated", "").strip().lower() != "true"]),
        "sentence_mapping_file": mapping_file_used,
        "exact_tag_match_count": exact_count,
        "synonym_match_count": synonym_count,
        "semantic_fallback_count": fallback_count,
        "unmatched_sentence_ids": unmatched_ids,
        "high_confidence": high_conf,
        "medium_confidence": medium_conf,
        "low_confidence": low_conf,
        "repeated_material_count": sum(1 for e in selected_assets if e.get("repeated_material_reason", "")),
        "tag_distribution_used": tag_distribution,
    }

    match_report_path = os.path.join(run_dir, "semantic_match_report.json")
    with open(match_report_path, 'w', encoding='utf-8') as f:
        json.dump(match_report, f, ensure_ascii=False, indent=2)

    logger.info(
        f"匹配完成: exact={exact_count}, synonym={synonym_count}, "
        f"fallback={fallback_count}, high={high_conf}, medium={medium_conf}, low={low_conf}"
    )

    # 9. 构建timeline_shots（合并timing与素材匹配结果）
    # 由于timing拆句结果可能与sentence_tag_mapping不完全一致，
    # 使用文本匹配方式：对每个timing segment，找到最匹配的selected_assets条目
    timeline_shots = []
    for idx, shot in enumerate(state.timing):
        shot_text = shot.get("text", "").strip()
        
        # 尝试按文本精确匹配
        matched = None
        for entry in selected_assets:
            entry_text = entry.get("sentence_text", "").strip()
            if entry_text and (entry_text in shot_text or shot_text in entry_text):
                matched = entry
                break
        
        # 如果文本匹配失败，尝试按索引匹配（如果数量一致时）
        if not matched and idx < len(selected_assets):
            matched = selected_assets[idx]
        
        # 如果仍然没有匹配，使用第一个条目
        if not matched and selected_assets:
            matched = selected_assets[0]
        
        matched = matched or {}
        shot["sentence_id"] = matched.get("sentence_id", idx + 1)
        shot["selected_material_id"] = matched.get("selected_material_id", "")
        shot["selected_file_name"] = matched.get("selected_file_name", "")
        shot["selected_primary_scene_tag"] = matched.get("selected_primary_scene_tag", "")
        shot["selected_url"] = matched.get("selected_url", "")
        shot["tag_match_type"] = matched.get("tag_match_type", "fallback")
        shot["match_confidence"] = matched.get("match_confidence", "low")
        shot["match_score"] = matched.get("match_score", 0.0)
        shot["match_reason"] = matched.get("match_reason", "")
        shot["semantic_fallback_used"] = matched.get("semantic_fallback_used", False)
        shot["repeated_material_reason"] = matched.get("repeated_material_reason", "")
        shot["selected_in_candidates"] = True
        timeline_shots.append(shot)

    if not timeline_shots:
        # 兜底：没有timing数据时从selected_assets构建
        for entry in selected_assets:
            timeline_shots.append({
                "sentence_id": entry["sentence_id"],
                "text": entry["sentence_text"],
                "selected_material_id": entry["selected_material_id"],
                "selected_url": entry["selected_url"],
                "match_confidence": entry["match_confidence"],
                "match_reason": entry["match_reason"],
            })

    return MaterialMatchOutput(
        materials=all_materials,
        timeline_shots=timeline_shots,
        selected_assets=selected_assets,
        selected_assets_path=selected_assets_path,
        match_report_path=match_report_path,
        low_confidence_segments=low_conf,
        unique_material_count=len(used_material_ids),
        used_manifest_file=manifest_file_used,
        mapping_file_used=mapping_file_used,
        mapping_coverage=mapping_coverage,
        exact_tag_match_count=exact_count,
        synonym_match_count=synonym_count,
        semantic_fallback_count=fallback_count,
        unmatched_sentence_ids=unmatched_ids,
        high_confidence_segments=high_conf,
        medium_confidence_segments=medium_conf,
        semantic_mismatch_segments=mismatch_ids,
    )