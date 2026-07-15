import os
import json
import csv
import logging
import random
import time
from typing import Dict, List, Any, Optional, Set, Tuple
from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import MaterialMatchInput, MaterialMatchOutput

logger = logging.getLogger(__name__)

# 初始化随机种子（使用时间戳确保每次运行不同）
random.seed(time.time())

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

# 关键词到标签的映射（用于句子标签映射生成）
# 注意：所有标签必须来自 asset_manifest_v2_clean.csv 中已存在的 primary_scene_tag
# 扩充版关键词映射，覆盖促销/CTA/口语化种草/痛点表达
_KEYWORD_TO_TAG: Dict[str, List[str]] = {
    # ==================== 旅行场景 ====================
    "出差": ["旅行场景"], "旅行": ["旅行场景"], "出行": ["旅行场景"],
    "酒店": ["旅行场景", "痛点共鸣"], "客房": ["旅行场景"],
    "旅游": ["旅行场景"], "必备": ["旅行场景"],
    "差旅": ["旅行场景"], "出门": ["旅行场景", "吹发动作"],
    "随行": ["旅行场景"], "随身": ["旅行场景", "放进包包"],
    "没负担": ["旅行场景", "放进包包"],
    
    # ==================== 放进行李箱 / 放进包包 ====================
    "行李箱": ["放进行李箱", "旅行场景"], "旅行箱": ["放进行李箱"],
    "箱子": ["放进行李箱", "放进包包"],
    "包包": ["放进包包"], "背包": ["放进包包"],
    "放包里": ["放进包包"], "放进包": ["放进包包"],
    "随便一塞": ["放进包包"], "边角": ["放进包包"], "缝隙": ["放进包包"],
    "不占空间": ["放进包包"], "不占地": ["放进包包"],
    
    # ==================== 痛点共鸣 ====================
    "怕": ["痛点共鸣"], "愁": ["痛点共鸣"], "烦": ["痛点共鸣"],
    "毛躁": ["痛点共鸣", "护发效果"], "打结": ["痛点共鸣"],
    "吹不干": ["痛点共鸣", "吹发动作"], 
    "稻草": ["痛点共鸣", "护发效果"], "旧": ["痛点共鸣"],
    "发愁": ["痛点共鸣"], "风小": ["痛点共鸣"],
    "手累": ["痛点共鸣"], "烫头皮": ["痛点共鸣"],
    "真的会谢": ["痛点共鸣"], "会谢": ["痛点共鸣"],
    "告别": ["痛点共鸣"],
    
    # ==================== 手持大小对比 ====================
    "巴掌大": ["手持大小对比"], "巴掌大小": ["手持大小对比"],
    "小钢炮": ["手持大小对比"], "小巧": ["手持大小对比"],
    "小巧便捷": ["手持大小对比"], "超mini": ["手持大小对比"],
    "minni": ["手持大小对比"], "迷你": ["手持大小对比"],
    "比手机大一点": ["手持大小对比"], "手机大小": ["手持大小对比"],
    "还没一瓶水重": ["手持大小对比"], "一瓶水": ["手持大小对比"],
    "轻巧": ["手持大小对比"], "超轻": ["手持大小对比"], "超薄": ["手持大小对比"],
    "巴掌": ["手持大小对比"],
    
    # ==================== 手持展示 - 只用于明确出现手持产品的句子 ====================
    "拿在": ["手持展示"], "握在": ["手持展示"], "手里": ["手持展示"],
    "手持": ["手持展示"], "单手": ["手持展示"],
    
    # ==================== 折叠动作 ====================
    "折叠": ["折叠动作"], "折起来": ["折叠动作"], "能折叠": ["折叠动作"],
    "折叠随身携带": ["折叠动作"],
    "折": ["折叠动作"], "收纳": ["折叠动作", "放进包包"],
    "带走": ["折叠动作", "放进包包"],
    
    # ==================== 风力展示 ====================
    "风力": ["风力展示"], "大风": ["风力展示"], "强劲": ["风力展示"],
    "万转": ["风力展示"], "转速": ["风力展示"], "每分钟": ["风力展示"],
    "风速": ["风力展示"], "29米每秒": ["风力展示"],
    "速干": ["风力展示"], "拉满": ["风力展示"],
    "无刷": ["风力展示"], "无刷马达": ["风力展示"], "马达": ["风力展示"],
    "高速": ["风力展示"], "超高速": ["风力展示"],
    "一首歌的时间": ["风力展示", "吹发动作"],
    "转": ["风力展示"],
    
    # ==================== 护发效果 ====================
    "护发": ["护发效果"], "柔顺": ["护发效果"], "顺滑": ["护发效果"],
    "光泽": ["护发效果"], "负离子": ["护发效果"], 
    "五亿级负离子": ["护发效果"],
    "不伤发": ["护发效果"], "不伤头发": ["护发效果"], "不伤": ["护发效果"],
    "强韧": ["护发效果"], "水润": ["护发效果"],
    "沙龙养护": ["护发效果"], "枯草变瀑布": ["护发效果"], "养护": ["护发效果"],
    
    # ==================== 屏显调温 ====================
    "屏显": ["屏显调温"], "调温": ["屏显调温"], "温控": ["屏显调温"],
    "温度": ["屏显调温"], "显温": ["屏显调温"], "控温": ["屏显调温"],
    "智能温控": ["屏显调温"], "智能恒温": ["屏显调温"],
    "恒温": ["屏显调温"], "温档": ["屏显调温"],
    "调节": ["屏显调温"], "5种风温": ["屏显调温"], "10种选择": ["屏显调温"],
    "清清楚楚": ["屏显调温"],
    
    # ==================== 吹发动作 ====================
    "吹": ["吹发动作"], "吹干": ["吹发动作"], "吹发": ["吹发动作"],
    "干发": ["吹发动作"], "长头发": ["吹发动作"], "短头发": ["吹发动作"],
    "头发": ["吹发动作", "护发效果"],
    "快速干发": ["风力展示", "吹发动作"],
    "快": ["风力展示", "吹发动作"],
    
    # ==================== CTA促单 ====================
    "下单": ["CTA促单", "价格促销"], 
    "赶紧": ["CTA促单"], "赶紧来": ["CTA促单"], "赶紧入手": ["CTA促单"],
    "入手": ["CTA促单"], "闭眼入": ["CTA促单"], "闭眼冲": ["CTA促单"],
    "必入": ["CTA促单"], "必买": ["CTA促单"], 
    "试一试": ["CTA促单"], "一定要试": ["CTA促单"],
    "想要的": ["CTA促单"], "就这一波": ["CTA促单"],
    "冲": ["CTA促单", "价格促销"],
    "安排": ["CTA促单"], "必须": ["CTA促单"],
    "姐妹": ["CTA促单"],
    "闭眼": ["CTA促单"], "这波": ["CTA促单"],
    "来吧": ["CTA促单"],
    
    # ==================== 价格促销 ====================
    "买": ["价格促销", "CTA促单"],
    "价": ["价格促销"], "优惠": ["价格促销"], "促销": ["价格促销"],
    "性价比": ["价格促销", "CTA促单"],
    "福利": ["价格促销"], "活动": ["价格促销"],
    "直降": ["价格促销"], "到手": ["价格促销"], "到手价": ["价格促销"],
    "价格": ["价格促销"], "打到地板": ["价格促销"],
    "再不买": ["价格促销", "CTA促单"], "真没了": ["价格促销", "CTA促单"],
    "到手还是": ["价格促销"], "到手还是这么多": ["价格促销"],
    
    # ==================== 赠品展示 ====================
    "送": ["赠品展示", "价格促销"], "赠品": ["赠品展示"], "套装": ["赠品展示"],
    "赠送": ["赠品展示", "价格促销"], "买就送": ["赠品展示", "价格促销"],
    "现在买": ["赠品展示", "价格促销"],
    
    # ==================== 风嘴配件 ====================
    "风嘴": ["风嘴配件"], "造型风嘴": ["风嘴配件"], "磁吸风嘴": ["风嘴配件"],
    "磁吸": ["风嘴配件"], "喷嘴": ["风嘴配件"], "配备": ["风嘴配件"],
    "造型": ["风嘴配件"],
    
    # ==================== 口语化/种草表达（新增） ====================
    "是不是": ["痛点共鸣"], "是不是都在为": ["痛点共鸣"],
    "挖到": ["旅行场景", "产品展示"], "出差神器": ["旅行场景"],
    "神器": ["旅行场景", "产品展示"], "颜值高": ["产品展示"],
    "强悍": ["风力展示", "产品展示"], "小东西": ["手持大小对比", "产品展示"],
    "焊在": ["放进行李箱", "旅行场景"], "焊在你的行李箱里": ["放进行李箱"],
    "也就一个科瑞德的事": ["产品展示", "CTA促单"],
}

# ==================== 强语义短句模式（禁止合并） ====================
# 即使字数很短，只要有明确卖点，也要允许独立匹配
_STRONG_SEMANTIC_SHORT_PATTERNS: List[str] = [
    # 风力相关
    "十一万转", "11万转", "万转", "高风速", "大风力",
    # 大小相关
    "巴掌大", "巴掌大小", "超mini", "超迷你", "小巧",
    # 护发相关
    "不伤发", "护发", "柔顺", "水润",
    # 折叠相关
    "折叠带走", "折叠", "折起来",
    # 屏显相关
    "实时显温", "显温", "屏显", "温度看得见",
    # CTA相关
    "闭眼冲", "闭眼入", "买它", "赶紧", "冲",
    # 赠品相关
    "送礼", "赠品", "收纳包", "收纳袋",
    # 价格相关
    "直降", "到手", "到手价",
    # 负离子
    "负离子",
]

# ==================== 短句合并条件关键词（弱语义） ====================
# 这些关键词表示句子偏承接、铺垫、语气词、转折句，适合合并
_WEAK_SEMANTIC_PATTERNS: List[str] = [
    "就是", "就是这款", "就是这款",
    "现在", "现在上新", "现在福利",
    "但", "但它的", "但是",
    "还", "还能", "还好",
    "看", "别看",
    "真的", "真的会",
    "超", "超爱",
    "想要", "想要的",
    "也就", "也就是",
    "不挑", "不占",
    "自带",
    "买就",
    "趁着",
    "到手还是",
]


def _is_strong_semantic_short(text: str) -> bool:
    """判断短句是否有强语义（不应被合并）"""
    text_lower = text.lower().strip()
    for pattern in _STRONG_SEMANTIC_SHORT_PATTERNS:
        if pattern in text_lower:
            return True
    # 检查是否包含明确卖点关键词
    for keyword in _KEYWORD_TO_TAG:
        if keyword in text_lower and len(keyword) >= 2:
            # 如果匹配到的标签不是弱标签（如产品展示），则认为有强语义
            tags = _KEYWORD_TO_TAG[keyword]
            strong_tags = {"风力展示", "护发效果", "屏显调温", "折叠动作", 
                          "手持大小对比", "赠品展示", "风嘴配件", "CTA促单",
                          "价格促销", "放进行李箱", "放进包包"}
            if any(t in strong_tags for t in tags):
                return True
    return False


def _is_weak_semantic(text: str) -> bool:
    """判断句子是否为弱语义（承接、铺垫、语气词、转折）"""
    text_lower = text.lower().strip()
    for pattern in _WEAK_SEMANTIC_PATTERNS:
        if pattern in text_lower:
            return True
    # 纯语气词/感叹词
    if text_lower in ["真的", "真的吗", "真的会", "超", "还", "但"]:
        return True
    return False


def _build_visual_groups(
    sentence_mappings: List[Dict[str, Any]],
    timing: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    构建视觉组（visual_group）：将短句合并到相邻句子，确保每个视觉片段 >= 1.2秒。
    
    规则：
    1. 短句判定：字数 <= 5 或 TTS时长 < 0.9秒
    2. 短句必须语义不完整（弱语义），且有明确独立卖点标签的短句不合并
    3. 优先合并到下一句；如果在结尾，合并到上一句
    4. 每个视觉组的视觉片段时长 >= 1.2秒
    
    Args:
        sentence_mappings: 句子标签映射列表
        timing: 时间轴数据
    
    Returns:
        visual_groups: 视觉组列表，每组包含 sentence_ids, sentence_texts, total_duration, primary_tag 等
    """
    if not sentence_mappings:
        return []
    
    # 构建 timing 索引（按 sentence_id）
    timing_by_id: Dict[int, Dict] = {}
    for t in timing:
        sid = t.get("sentence_id", 0)
        if sid:
            timing_by_id[sid] = t
    
    n = len(sentence_mappings)
    
    # 第一步：标记哪些句子是"可合并短句"
    mergeable: List[bool] = [False] * n
    for i, mapping in enumerate(sentence_mappings):
        text = mapping.get("sentence_text", "")
        duration = mapping.get("duration", 1.0)
        char_count = len(text.strip())
        
        # 短句判定：字数 <= 5 或 时长 < 0.9秒
        is_short = char_count <= 5 or duration < 0.9
        
        if not is_short:
            mergeable[i] = False
            continue
        
        # 强语义短句不合并
        if _is_strong_semantic_short(text):
            mergeable[i] = False
            continue
        
        # 单独作为视觉片段时长会低于 1.2 秒
        if duration >= 1.2:
            mergeable[i] = False
            continue
        
        mergeable[i] = True
    
    # 第二步：构建视觉组
    visual_groups: List[Dict[str, Any]] = []
    i = 0
    
    while i < n:
        mapping = sentence_mappings[i]
        sentence_id = mapping.get("sentence_id", i + 1)
        text = mapping.get("sentence_text", "")
        duration = mapping.get("duration", 1.0)
        tags = mapping.get("required_tags", [])
        primary_tag = mapping.get("primary_scene_tag", "")
        
        if not mergeable[i]:
            # 不可合并的句子，独立成组
            group = {
                "group_id": len(visual_groups) + 1,
                "sentence_ids": [sentence_id],
                "sentence_texts": [text],
                "original_sentence_durations": [duration],
                "total_duration": duration,
                "merged": False,
                "merge_reason": "",
                "primary_tag": primary_tag,
                "required_tags": tags,
                "is_strong_semantic": True,
                "low_confidence": mapping.get("low_confidence", False),
                "fallback_reason": mapping.get("fallback_reason", ""),
            }
            visual_groups.append(group)
            i += 1
        else:
            # 可合并的短句，尝试合并到下一句
            merged_texts = [text]
            merged_ids = [sentence_id]
            merged_durations = [duration]
            merged_tags = list(tags)
            merge_reason = ""
            
            if i + 1 < n:
                # 合并到下一句
                next_mapping = sentence_mappings[i + 1]
                next_id = next_mapping.get("sentence_id", i + 2)
                next_text = next_mapping.get("sentence_text", "")
                next_duration = next_mapping.get("duration", 1.0)
                next_tags = next_mapping.get("required_tags", [])
                next_primary_tag = next_mapping.get("primary_scene_tag", "")
                
                merged_texts.append(next_text)
                merged_ids.append(next_id)
                merged_durations.append(next_duration)
                
                # 组的标签：优先取下一句（强语义句）的标签
                if next_tags:
                    merged_tags = next_tags
                    primary_tag = next_primary_tag
                
                total_dur = duration + next_duration
                merge_reason = f"短句'{text}'(时长{duration:.2f}s)合并到下一句'{next_text[:15]}...'"
                
                # 如果合并后仍然 < 1.2秒，且后面还有句子，继续合并
                j = i + 2
                while total_dur < 1.2 and j < n:
                    more_mapping = sentence_mappings[j]
                    more_id = more_mapping.get("sentence_id", j + 1)
                    more_text = more_mapping.get("sentence_text", "")
                    more_duration = more_mapping.get("duration", 1.0)
                    more_tags = more_mapping.get("required_tags", [])
                    
                    merged_texts.append(more_text)
                    merged_ids.append(more_id)
                    merged_durations.append(more_duration)
                    total_dur += more_duration
                    
                    # 如果新加入的句子有更强的标签，更新
                    if more_tags and not merged_tags:
                        merged_tags = more_tags
                        primary_tag = more_mapping.get("primary_scene_tag", "")
                    
                    j += 1
                
                group = {
                    "group_id": len(visual_groups) + 1,
                    "sentence_ids": merged_ids,
                    "sentence_texts": merged_texts,
                    "original_sentence_durations": merged_durations,
                    "total_duration": sum(merged_durations),
                    "merged": True,
                    "merge_reason": merge_reason,
                    "primary_tag": primary_tag,
                    "required_tags": merged_tags,
                    "is_strong_semantic": False,
                    "low_confidence": mapping.get("low_confidence", False) and not any(
                        not sentence_mappings[k].get("low_confidence", False) 
                        for k in range(i, min(j, n))
                    ),
                    "fallback_reason": mapping.get("fallback_reason", ""),
                }
                visual_groups.append(group)
                i = j  # 跳过已合并的句子
            else:
                # 短句在结尾，合并到上一句
                if visual_groups:
                    prev_group = visual_groups[-1]
                    prev_group["sentence_ids"].append(sentence_id)
                    prev_group["sentence_texts"].append(text)
                    prev_group["original_sentence_durations"].append(duration)
                    prev_group["total_duration"] += duration
                    prev_group["merged"] = True
                    prev_group["merge_reason"] += f"; 结尾短句'{text}'(时长{duration:.2f}s)合并到上一句"
                    i += 1
                else:
                    # 没有上一句，独立成组
                    group = {
                        "group_id": len(visual_groups) + 1,
                        "sentence_ids": [sentence_id],
                        "sentence_texts": [text],
                        "original_sentence_durations": [duration],
                        "total_duration": duration,
                        "merged": False,
                        "merge_reason": "首句且为短句，无法合并",
                        "primary_tag": primary_tag,
                        "required_tags": tags,
                        "is_strong_semantic": False,
                        "low_confidence": mapping.get("low_confidence", False),
                        "fallback_reason": mapping.get("fallback_reason", ""),
                    }
                    visual_groups.append(group)
                    i += 1
    
    return visual_groups


def _generate_sentence_tag_mapping(
    timing: List[Dict[str, Any]], 
    available_tags: Set[str]
) -> List[Dict[str, Any]]:
    """
    为每条新文案生成句子标签映射。
    只能从 available_tags（CSV中已存在的 primary_scene_tag 集合）中选择标签。
    
    Args:
        timing: 分句结果，包含每句的text、duration等信息
        available_tags: CSV中已存在的 primary_scene_tag 集合
    
    Returns:
        句子标签映射列表，每条记录包含 sentence_id, sentence_text, primary_scene_tag, reason
    """
    mappings = []
    
    for idx, shot in enumerate(timing):
        sentence_id = shot.get("sentence_id", idx + 1)
        sentence_text = shot.get("text", "")
        duration = shot.get("duration", 3.0)
        
        # 基于关键词匹配标签
        matched_tags = []
        text_lower = sentence_text.lower()
        
        for keyword, keyword_tags in _KEYWORD_TO_TAG.items():
            if keyword in text_lower:
                for tag in keyword_tags:
                    # 只保留 available_tags 中存在的标签
                    if tag in available_tags and tag not in matched_tags:
                        matched_tags.append(tag)
        
        # 如果没有匹配到任何标签，标记为 low_confidence
        # 不允许把"手持展示"作为默认兜底标签
        # "手持展示"只能用于明确出现：手持、拿着、握持、单手、手里展示、拿在手里 等语义
        is_low_confidence = False
        fallback_reason = ""
        candidate_tags = []
        
        if not matched_tags:
            is_low_confidence = True
            
            # 智能兜底：根据句子特征选择更安全的兜底标签
            text_lower = sentence_text.lower()
            
            # 定义各类关键词
            cta_keywords = ["赶紧", "来吧", "入手", "闭眼", "必入", "必买", "试一试", 
                           "一定要", "想要的", "就这一波", "冲", "安排", "必须", "姐妹",
                           "再不买", "真没了"]
            price_keywords = ["福利", "活动", "直降", "到手", "价格", "优惠", "买就送",
                             "现在买", "赠送", "送", "再不买", "真没了", "打到地板",
                             "到手还是", "到手还是这么多"]
            pain_keywords = ["发愁", "风小", "伤发", "手累", "烫头皮", "真的会谢", 
                            "会谢", "告别", "怕", "愁", "烦", "是不是", "是不是都在为"]
            travel_keywords = ["出差", "旅行", "旅游", "差旅", "出门", "随行", "随身",
                              "行李箱", "箱子", "包包", "放包里", "放进包", "随便一塞",
                              "边角", "缝隙", "不占空间", "不占地", "没负担",
                              "挖到", "出差神器", "神器", "焊在"]
            product_keywords = ["颜值高", "强悍", "小东西", "科瑞德"]
            
            # 1. CTA/促单类句子：包含号召性用语
            if any(kw in text_lower for kw in cta_keywords):
                fallback_tags = ["CTA促单", "价格促销"]
            
            # 2. 促销/价格类句子
            elif any(kw in text_lower for kw in price_keywords):
                fallback_tags = ["价格促销", "CTA促单"]
            
            # 3. 痛点/口语化句子
            elif any(kw in text_lower for kw in pain_keywords):
                fallback_tags = ["痛点共鸣", "护发效果"]
            
            # 4. 旅行/出门语境
            elif any(kw in text_lower for kw in travel_keywords):
                fallback_tags = ["旅行场景", "放进包包", "放进行李箱"]
            
            # 5. 产品描述/种草
            elif any(kw in text_lower for kw in product_keywords):
                fallback_tags = ["产品展示", "手持大小对比"]
            
            # 6. 产品介绍/泛化描述
            else:
                # 兜底标签优先级：产品展示 > 手持大小对比 > 折叠动作 > 放进包包
                # 注意：不包含"手持展示"，避免滥用
                fallback_tags = ["产品展示", "手持大小对比", "折叠动作", "放进包包",
                                "价格促销", "CTA促单"]
            
            for tag in fallback_tags:
                if tag in available_tags:
                    matched_tags = [tag]
                    break
            
            # 如果还是没有，选择 available_tags 中的第一个（排除手持展示）
            if not matched_tags and available_tags:
                safe_tags = [t for t in available_tags if t != "手持展示"]
                if safe_tags:
                    matched_tags = [safe_tags[0]]
                elif available_tags:
                    matched_tags = [list(available_tags)[0]]
            
            fallback_reason = f"无关键词匹配，使用兜底标签: {matched_tags[0] if matched_tags else '无'}"
            candidate_tags = [t for t in fallback_tags if t in available_tags]
        
        # 选择第一个匹配的标签作为 primary_scene_tag
        primary_tag = matched_tags[0] if matched_tags else ""
        
        # 构建匹配理由
        if is_low_confidence:
            reason = f"[LOW_CONFIDENCE] {fallback_reason}"
        elif primary_tag:
            reason = f"关键词匹配: '{sentence_text[:20]}...' → {primary_tag}"
        else:
            reason = f"默认标签: '{sentence_text[:20]}...' → {primary_tag}"
        
        mapping = {
            "sentence_id": sentence_id,
            "sentence_text": sentence_text,
            "primary_scene_tag": primary_tag,
            "required_tags": matched_tags,  # 保留多个标签用于匹配
            "duration": duration,
            "reason": reason,
            "low_confidence": is_low_confidence,
            "fallback_reason": fallback_reason,
            "candidate_tags": candidate_tags
        }
        mappings.append(mapping)
    
    return mappings


def _load_material_manifest(csv_path: str) -> List[Dict[str, Any]]:
    """加载素材清单CSV，返回素材列表"""
    materials = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            deprecated = row.get('deprecated', '').strip().lower()
            if deprecated == 'true':
                continue
            enabled = row.get('enabled', '').strip().lower()
            if enabled == 'false':
                continue
            # 优先使用source_url，其次使用s3_url
            url = row.get("source_url", "").strip() or row.get("s3_url", "").strip()
            # 解析duration_sec
            duration_str = row.get("duration_sec", "3").strip()
            try:
                duration_sec = float(duration_str)
            except ValueError:
                duration_sec = 3.0
            mat = {
                "asset_id": row.get("asset_id", "").strip(),
                "file_name": row.get("file_name", "").strip(),
                "primary_scene_tag": row.get("primary_scene_tag", "").strip(),
                "bucket": row.get("bucket", "").strip(),
                "object_key": row.get("object_key", "").strip(),
                "s3_url": url,
                "source_url": url,
                "duration_sec": duration_sec,
                "description": row.get("description", "").strip(),
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

    # 4. 加载或生成句子标签映射
    mapping_path = os.path.join(run_dir, "sentence_tag_mapping.json")
    
    if os.path.exists(mapping_path):
        # 如果已存在，直接加载
        sentence_mappings = _load_sentence_tag_mapping(mapping_path)
        mapping_file_used = os.path.basename(mapping_path)
        logger.info(f"句子标签映射: {len(sentence_mappings)} 条 (已存在)")
    else:
        # 如果不存在，基于 timing 自动生成
        # 禁止回退到 assets/sentence_tag_mapping_script_02.json
        logger.info("句子标签映射不存在，基于 timing 自动生成")
        sentence_mappings = _generate_sentence_tag_mapping(state.timing, available_tags)
        mapping_file_used = "auto_generated"
        
        # 保存生成的映射到 run_dir
        with open(mapping_path, 'w', encoding='utf-8') as f:
            json.dump(sentence_mappings, f, ensure_ascii=False, indent=2)
        logger.info(f"已生成并保存句子标签映射: {mapping_path} ({len(sentence_mappings)} 条)")
    
    logger.info(f"句子标签映射: {len(sentence_mappings)} 条")

    # 4b. 构建视觉组（visual_grouping）：将短句合并到相邻句子
    visual_groups = _build_visual_groups(sentence_mappings, state.timing)
    merged_count = sum(1 for g in visual_groups if g.get("merged"))
    merged_sentence_count = sum(
        len(g["sentence_ids"]) - 1 for g in visual_groups if g.get("merged")
    )
    logger.info(
        f"视觉组构建完成: {len(visual_groups)} 个组, "
        f"{merged_count} 个组发生合并, {merged_sentence_count} 个短句被合并"
    )
    
    # 保存视觉组报告
    visual_grouping_report_path = os.path.join(run_dir, "visual_grouping_report.json")
    with open(visual_grouping_report_path, 'w', encoding='utf-8') as f:
        json.dump(visual_groups, f, ensure_ascii=False, indent=2)

    # 5. 执行匹配（基于视觉组）
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

    # 匹配循环：基于视觉组（每个视觉组匹配一个素材）
    for group_idx, group in enumerate(visual_groups):
        group_id = group.get("group_id", group_idx + 1)
        sentence_ids = group.get("sentence_ids", [])
        sentence_texts = group.get("sentence_texts", [])
        total_duration = group.get("total_duration", 1.0)
        required_tags = group.get("required_tags", [])
        primary_tag = group.get("primary_tag", "")
        group_low_confidence = group.get("low_confidence", False)
        group_fallback_reason = group.get("fallback_reason", "")
        
        # 使用组的第一个句子作为代表文本
        sentence_text = " ".join(sentence_texts) if sentence_texts else ""
        sentence_id = sentence_ids[0] if sentence_ids else group_idx + 1
        target_duration = total_duration  # 视觉组的总时长作为目标时长

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
                    # 阶段4: 完全无匹配，报告缺素材，不随便选择
                    # 禁止"无匹配就随便选择未使用素材"
                    logger.warning(f"句子 {sentence_id} '{sentence_text[:20]}...' 无匹配素材")
                    unmatched_ids.append(sentence_id)
                    # 不添加任何候选素材，跳过此句子
                    continue

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
        
        # 短句优先短素材逻辑
        # 当句子时长较短（< 1.5秒）时，优先选择duration_sec较短的素材
        is_short_sentence = target_duration < 1.5
        
        # 辅助函数：计算素材与句子的匹配分数
        def _calculate_material_score(mat: Dict, sentence_text: str, is_short: bool) -> float:
            """
            计算素材与句子的匹配分数
            - description 辅助匹配：description 中包含句子关键词则加分
            - duration_sec 辅助匹配：短句优先短素材，长句优先长素材
            """
            score = 0.0
            mat_desc = mat.get("description", "").lower()
            mat_duration = mat.get("duration_sec", 3.0)
            
            # description 辅助匹配
            # 检查 description 中是否包含句子中的关键词
            for keyword in _KEYWORD_TO_TAG.keys():
                if keyword in sentence_text.lower() and keyword in mat_desc:
                    score += 0.3
            
            # duration_sec 辅助匹配
            if is_short:
                # 短句：优先选择时长较短的素材（1-3秒）
                if mat_duration <= 3.0:
                    score += 0.2
                elif mat_duration <= 5.0:
                    score += 0.1
            else:
                # 长句：优先选择时长较长的素材（5秒以上）
                if mat_duration >= 5.0:
                    score += 0.2
                elif mat_duration >= 3.0:
                    score += 0.1
            
            return score
        
        if unused_candidates:
            if is_short_sentence:
                # 短句：优先选择时长较短的素材（1-3秒）
                short_candidates = [c for c in unused_candidates if c.get("duration_sec", 3) <= 3]
                if short_candidates:
                    # 从短素材中选择分数最高的
                    short_candidates_with_score = [
                        (c, _calculate_material_score(c, sentence_text, True))
                        for c in short_candidates
                    ]
                    short_candidates_with_score.sort(key=lambda x: x[1], reverse=True)
                    selected = short_candidates_with_score[0][0]
                else:
                    # 没有短素材，从未使用的候选中选择分数最高的
                    unused_with_score = [
                        (c, _calculate_material_score(c, sentence_text, True))
                        for c in unused_candidates
                    ]
                    unused_with_score.sort(key=lambda x: x[1], reverse=True)
                    selected = unused_with_score[0][0]
            else:
                # 长句：从所有未使用的候选中选择分数最高的
                unused_with_score = [
                    (c, _calculate_material_score(c, sentence_text, False))
                    for c in unused_candidates
                ]
                unused_with_score.sort(key=lambda x: x[1], reverse=True)
                selected = unused_with_score[0][0]
            repeated_reason = ""
        else:
            # 素材已用完，需要复用或扩展到相邻标签
            # 首先尝试扩展到相邻安全标签
            expanded_candidates = []
            seen_expanded_ids = set(seen_material_ids)
            
            # 根据当前标签确定可扩展的相邻标签
            current_tag = candidates[0]["primary_scene_tag"] if candidates else ""
            expansion_tags = []
            
            if current_tag == "产品展示":
                # 产品展示用完时，可扩展到：手持大小对比、折叠动作、放进包包、价格促销、CTA促单
                expansion_tags = ["手持大小对比", "折叠动作", "放进包包", "价格促销", "CTA促单"]
            elif current_tag in ["价格促销", "CTA促单"]:
                # 促销/CTA用完时，可扩展到：产品展示
                expansion_tags = ["产品展示"]
            elif current_tag == "痛点共鸣":
                # 痛点共鸣用完时，可扩展到：护发效果、旅行场景
                expansion_tags = ["护发效果", "旅行场景"]
            elif current_tag == "旅行场景":
                # 旅行场景用完时，可扩展到：放进包包、放进行李箱
                expansion_tags = ["放进包包", "放进行李箱"]
            
            # 从相邻标签收集中未使用的素材
            for exp_tag in expansion_tags:
                if exp_tag in tag_to_materials:
                    for mat in tag_to_materials[exp_tag]:
                        mid = mat["asset_id"]
                        if mid not in seen_expanded_ids and mid not in used_material_ids:
                            expanded_candidates.append(mat)
                            seen_expanded_ids.add(mid)
            
            if expanded_candidates:
                # 有未使用的相邻标签素材，从中选择
                if is_short_sentence:
                    short_expanded = [c for c in expanded_candidates if c.get("duration_sec", 3) <= 3]
                    if short_expanded:
                        short_expanded_with_score = [
                            (c, _calculate_material_score(c, sentence_text, True))
                            for c in short_expanded
                        ]
                        short_expanded_with_score.sort(key=lambda x: x[1], reverse=True)
                        selected = short_expanded_with_score[0][0]
                    else:
                        expanded_with_score = [
                            (c, _calculate_material_score(c, sentence_text, True))
                            for c in expanded_candidates
                        ]
                        expanded_with_score.sort(key=lambda x: x[1], reverse=True)
                        selected = expanded_with_score[0][0]
                else:
                    expanded_with_score = [
                        (c, _calculate_material_score(c, sentence_text, False))
                        for c in expanded_candidates
                    ]
                    expanded_with_score.sort(key=lambda x: x[1], reverse=True)
                    selected = expanded_with_score[0][0]
                repeated_reason = f"扩展到相邻标签: {current_tag} → {selected['primary_scene_tag']}"
            else:
                # 相邻标签也没有未使用的素材，只能复用
                if is_short_sentence:
                    # 短句：优先复用时长较短的素材
                    short_candidates = [c for c in candidates if c.get("duration_sec", 3) <= 3]
                    if short_candidates:
                        short_candidates_with_score = [
                            (c, _calculate_material_score(c, sentence_text, True))
                            for c in short_candidates
                        ]
                        short_candidates_with_score.sort(key=lambda x: x[1], reverse=True)
                        selected = short_candidates_with_score[0][0]
                    else:
                        candidates_with_score = [
                            (c, _calculate_material_score(c, sentence_text, True))
                            for c in candidates
                        ] if candidates else [(all_materials[0], 0.0)]
                        candidates_with_score.sort(key=lambda x: x[1], reverse=True)
                        selected = candidates_with_score[0][0]
                else:
                    candidates_with_score = [
                        (c, _calculate_material_score(c, sentence_text, False))
                        for c in candidates
                    ] if candidates else [(all_materials[0], 0.0)]
                    candidates_with_score.sort(key=lambda x: x[1], reverse=True)
                    selected = candidates_with_score[0][0]
                repeated_reason = f"素材已用完，复用{selected['asset_id']}"

        used_material_ids.add(selected["asset_id"])

        # 获取 visual_group 中的 low_confidence 标记
        mapping_low_confidence = group_low_confidence
        mapping_fallback_reason = group_fallback_reason
        mapping_candidate_tags = required_tags  # 使用组的标签

        # 计算置信度
        # 如果 group 标记为 low_confidence（无关键词匹配触发兜底），则最终置信度为 low
        if mapping_low_confidence:
            match_confidence = "low"
            match_score = 0.3
            low_conf += 1
        elif tag_match_type == "exact":
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
        if mapping_low_confidence:
            match_reason = f"[LOW_CONFIDENCE] {mapping_fallback_reason}; 素材: {required_tags} → {selected['primary_scene_tag']}"
        elif tag_match_type == "exact":
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
            "low_confidence": mapping_low_confidence,
            "fallback_reason": mapping_fallback_reason,
            "candidate_tags": mapping_candidate_tags,
            # visual_group 字段
            "visual_group_id": group.get("group_id", group_idx + 1),
            "visual_group_sentence_ids": group.get("sentence_ids", [sentence_id]),
            "visual_group_sentence_texts": group.get("sentence_texts", [sentence_text]),
            "visual_group_merged": group.get("merged", False),
            "visual_group_merge_reason": group.get("merge_reason", ""),
            "visual_group_total_duration": group.get("total_duration", target_duration),
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

    # 9. 构建timeline_shots（基于视觉组）
    # 每个视觉组对应一个timeline_shot，时长为组的总时长
    timeline_shots = []
    
    # 构建 timing 索引（按 sentence_id）
    timing_by_sid: Dict[int, Dict] = {}
    for t in state.timing:
        sid = t.get("sentence_id", 0)
        if sid:
            timing_by_sid[sid] = t
    
    for entry in selected_assets:
        group_sentence_ids = entry.get("visual_group_sentence_ids", [entry.get("sentence_id", 1)])
        group_total_duration = entry.get("visual_group_total_duration", 0)
        
        # 计算视觉组的起始时间（基于组内第一个句子的timing）
        first_sid = group_sentence_ids[0] if group_sentence_ids else entry.get("sentence_id", 1)
        first_timing = timing_by_sid.get(first_sid, {})
        group_start = first_timing.get("start", 0)
        
        # 构建组的文本（合并所有句子文本）
        group_texts = entry.get("visual_group_sentence_texts", [entry.get("sentence_text", "")])
        group_text = " ".join(group_texts)
        
        # 构建字幕时间列表（组内每个句子的起止时间）
        subtitle_start_end_list = []
        cumulative_start = group_start
        for sid in group_sentence_ids:
            t = timing_by_sid.get(sid, {})
            s_start = t.get("start", cumulative_start)
            s_dur = t.get("duration", 1.0)
            s_end = s_start + s_dur
            subtitle_start_end_list.append({
                "sentence_id": sid,
                "start": s_start,
                "end": s_end,
                "duration": s_dur,
            })
            cumulative_start = s_end
        
        shot = {
            "sentence_id": entry.get("sentence_id", 1),
            "text": group_text,
            "start": group_start,
            "duration": group_total_duration if group_total_duration > 0 else entry.get("duration", 1.0),
            "selected_material_id": entry.get("selected_material_id", ""),
            "selected_file_name": entry.get("selected_file_name", ""),
            "selected_primary_scene_tag": entry.get("selected_primary_scene_tag", ""),
            "selected_url": entry.get("selected_url", ""),
            "tag_match_type": entry.get("tag_match_type", "fallback"),
            "match_confidence": entry.get("match_confidence", "low"),
            "match_score": entry.get("match_score", 0.0),
            "match_reason": entry.get("match_reason", ""),
            "semantic_fallback_used": entry.get("semantic_fallback_used", False),
            "repeated_material_reason": entry.get("repeated_material_reason", ""),
            "selected_in_candidates": True,
            # visual_group 字段
            "visual_group_id": entry.get("visual_group_id", 1),
            "visual_group_sentence_ids": group_sentence_ids,
            "visual_group_merged": entry.get("visual_group_merged", False),
            "subtitle_start_end_list": subtitle_start_end_list,
        }
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
                "visual_group_id": entry.get("visual_group_id", 1),
                "visual_group_sentence_ids": entry.get("visual_group_sentence_ids", []),
                "visual_group_merged": entry.get("visual_group_merged", False),
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