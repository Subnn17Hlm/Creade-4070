"""
字幕预设模块

定义 4 个视觉差异明显的字幕预设，每个预设绑定一种字体。
支持批次内确定性均衡轮换。
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from subtitle_styling.font_pool import (
    FontConfig,
    get_enabled_fonts,
    get_font_by_id,
)
from subtitle_styling.style_pool import (
    SubtitleStyle,
    get_style_by_id,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubtitlePreset:
    """字幕预设配置"""
    preset_id: str
    preset_name: str
    font_id: str
    style_id: str
    description: str


# 4 个预设定义 - 每个预设绑定一种字体，确保视觉差异明显
_PRESETS = [
    SubtitlePreset(
        preset_id="preset_01_source_han",
        preset_name="思源黑体-白字黑描边",
        font_id="source_han_sans",
        style_id="default_white_black_stroke",
        description="思源黑体，白色文字，黑色描边",
    ),
    SubtitlePreset(
        preset_id="preset_02_smiley_sans",
        preset_name="得意黑-黄字黑粗描边",
        font_id="smiley_sans",
        style_id="yellow_black_stroke",
        description="得意黑，黄色文字，黑色粗描边",
    ),
    SubtitlePreset(
        preset_id="preset_03_alibaba_bold",
        preset_name="阿里巴巴普惠体-青字深蓝描边",
        font_id="alibaba_puhuiti",
        style_id="cyan_dark_blue_stroke",
        description="阿里巴巴普惠体，青色文字，深蓝色描边",
    ),
    SubtitlePreset(
        preset_id="preset_04_alibaba_heavy",
        preset_name="阿里巴巴普惠体Heavy-深红字白描边",
        font_id="alibaba_puhuiti_heavy",
        style_id="red_white_stroke",
        description="阿里巴巴普惠体 Heavy，深红色文字，白色描边",
    ),
]


def get_presets() -> List[SubtitlePreset]:
    """获取所有预设列表"""
    return list(_PRESETS)


def get_preset_by_id(preset_id: str) -> Optional[SubtitlePreset]:
    """根据 preset_id 获取预设"""
    for preset in _PRESETS:
        if preset.preset_id == preset_id:
            return preset
    return None


def get_preset_count() -> int:
    """获取预设数量"""
    return len(_PRESETS)


def get_preset_for_task(task_index: int) -> SubtitlePreset:
    """
    根据任务索引获取预设（均衡轮换）
    
    参数:
        task_index: 任务在批次中的索引（从 0 开始）
    
    返回:
        SubtitlePreset: 预设配置
    
    算法:
        使用 task_index % preset_count 实现均衡轮换
        例如：4 个预设，6 个任务 → 0,1,2,3,0,1
    """
    preset_count = len(_PRESETS)
    if preset_count == 0:
        raise RuntimeError("没有可用的字幕预设")
    
    index = task_index % preset_count
    return _PRESETS[index]


def get_preset_for_task_id(task_id: str) -> SubtitlePreset:
    """
    根据 task_id 获取预设（确定性哈希）
    
    参数:
        task_id: 任务 ID（字符串）
    
    返回:
        SubtitlePreset: 预设配置
    
    算法:
        使用 SHA-256(task_id) 的前 8 位计算索引
        保证同一 task_id 总是获得相同预设
    """
    import hashlib
    
    preset_count = len(_PRESETS)
    if preset_count == 0:
        raise RuntimeError("没有可用的字幕预设")
    
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % preset_count
    return _PRESETS[index]


def validate_preset(preset: SubtitlePreset) -> Tuple[bool, str]:
    """
    验证预设配置是否有效
    
    返回:
        (success, error_message)
    """
    # 检查字体是否存在且启用
    font = get_font_by_id(preset.font_id)
    if font is None:
        return False, f"字体 {preset.font_id} 不存在"
    if not font.enabled:
        return False, f"字体 {preset.font_id} 未启用"
    
    # 检查样式是否存在
    style = get_style_by_id(preset.style_id)
    if style is None:
        return False, f"样式 {preset.style_id} 不存在"
    
    # 检查字体是否在样式的允许列表中
    if preset.font_id not in style.allowed_font_ids:
        return False, f"字体 {preset.font_id} 不在样式 {preset.style_id} 的允许列表中"
    
    return True, ""


def get_preset_status_report() -> Dict[str, any]:
    """获取预设状态报告"""
    report = {
        "total_presets": len(_PRESETS),
        "valid_presets": 0,
        "invalid_presets": 0,
        "presets": [],
    }
    
    for preset in _PRESETS:
        valid, error = validate_preset(preset)
        entry = {
            "preset_id": preset.preset_id,
            "preset_name": preset.preset_name,
            "font_id": preset.font_id,
            "style_id": preset.style_id,
            "description": preset.description,
            "valid": valid,
            "error": error if not valid else None,
        }
        report["presets"].append(entry)
        if valid:
            report["valid_presets"] += 1
        else:
            report["invalid_presets"] += 1
    
    return report
