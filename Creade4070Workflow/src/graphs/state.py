"""
状态定义 - 10节点流水线（含文案来源选择）
================================
每个节点使用独立的 NodeInput/NodeOutput。
GlobalState 包含所有中间字段用于 LangGraph 自动合并。
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ============================================================
# 全局状态（LangGraph 自动合并）
# ============================================================

class GlobalState(BaseModel):
    """全局状态 - 包含所有节点中间产物字段"""
    # 原始输入
    script_id: str = Field(default="", description="脚本ID")
    script_source: str = Field(default="manual", description="脚本来源：generated|manual")
    script_text: str = Field(default="", description="原始文案（手动模式）")
    product_name: str = Field(default="", description="产品名（生成模式）")
    core_selling_points: List[str] = Field(default=[], description="核心卖点（生成模式）")
    target_audience: str = Field(default="", description="目标人群（生成模式）")
    video_style: str = Field(default="", description="视频风格（生成模式）")
    platform: str = Field(default="", description="平台")
    bgm_url: str = Field(default="", description="BGM链接")
    material_csv: str = Field(default="", description="素材标签CSV路径")
    run_dir: str = Field(default="", description="运行目录")

    # Node0 - 文案来源选择
    raw_script: str = Field(default="", description="最终原始脚本")
    generated_script_path: str = Field(default="", description="生成脚本路径（仅生成模式）")

    # Node1 - 输入规范化
    cleaned_script: str = Field(default="", description="清洗后文案")
    original_script_path: str = Field(default="", description="原始文案保存路径")
    cleaned_script_path: str = Field(default="", description="清洗文案保存路径")
    input_meta_path: str = Field(default="", description="输入元数据路径")
    original_chars: int = Field(default=0, description="原始文案字符数")
    script_ok: bool = Field(default=False, description="文案校验通过")

    # Node2 - TTS生成
    tts_wav_path: str = Field(default="", description="TTS音频路径")
    tts_input_path: str = Field(default="", description="TTS输入文本路径")
    tts_meta_path: str = Field(default="", description="TTS元数据路径")
    tts_duration: float = Field(default=0.0, description="TTS时长(秒)")

    # Node3 - 字幕时间轴
    sentences: List[str] = Field(default=[], description="分句列表")
    timing: List[Dict[str, Any]] = Field(default=[], description="时间轴")
    srt_path: str = Field(default="", description="SRT字幕路径")
    timing_debug_path: str = Field(default="", description="时间轴调试JSON路径")
    srt_no_overlap: bool = Field(default=False, description="字幕无重叠")
    srt_coverage: float = Field(default=0.0, description="字幕文案覆盖率")
    final_chars: int = Field(default=0, description="最终字幕字符数")

    # Node4 - 素材匹配
    materials: List[Dict[str, Any]] = Field(default=[], description="素材库")
    timeline_shots: List[Dict[str, Any]] = Field(default=[], description="时间线镜头")
    selected_assets: List[Dict[str, Any]] = Field(default=[], description="选中素材明细")
    selected_assets_path: str = Field(default="", description="选中素材JSON路径")
    match_report_path: str = Field(default="", description="匹配报告路径")
    low_confidence_segments: int = Field(default=0, description="低置信度段落数")
    unique_material_count: int = Field(default=0, description="唯一素材数")
    used_manifest_file: str = Field(default="", description="使用的素材清单文件")

    # Node4b - 素材源预检
    material_audit_path: str = Field(default="", description="素材审计报告路径")
    audited_materials: List[Dict[str, Any]] = Field(default=[], description="审计后素材列表")
    clean_material_count: int = Field(default=0, description="无字素材数量")
    dirty_material_count: int = Field(default=0, description="带字素材数量")
    material_source_ok: bool = Field(default=False, description="素材源全部通过预检")

    # Node5 - 素材截取
    clip_paths: List[str] = Field(default=[], description="截取片段路径列表")
    clipped_assets_path: str = Field(default="", description="截取素材JSON路径")
    clip_report_path: str = Field(default="", description="截取报告路径")

    # Node6 - Timeline组装
    final_timeline_path: str = Field(default="", description="最终timeline路径")

    # Node7 - 最终合成
    final_video_path: str = Field(default="", description="最终视频路径")
    contact_sheet_path: str = Field(default="", description="联系图路径")
    video_duration: float = Field(default=0.0, description="视频时长(秒)")
    video_duration_before_pad: float = Field(default=0.0, description="填充前视频时长")
    end_hold_sec: float = Field(default=0.0, description="结尾停留时长(秒)")

    # Node8 - 质量验收
    quality_report: Dict[str, Any] = Field(default={}, description="质量报告")
    status: str = Field(default="", description="最终状态")
    fail_reason: str = Field(default="", description="失败原因")
    failure_category: str = Field(default="", description="失败分类")
    final_video_url: str = Field(default="", description="最终视频URL")
    total_duration: float = Field(default=0.0, description="总时长")


# ============================================================
# 图出入参
# ============================================================

class GraphInput(BaseModel):
    """工作流输入"""
    script_id: str = Field(..., description="脚本ID，如 script_02")
    script_source: str = Field(default="manual", description="脚本来源：generated|manual")
    # 手动模式
    script_text: str = Field(default="", description="原始文案（手动模式）")
    # 生成模式
    product_name: str = Field(default="", description="产品名（生成模式）")
    core_selling_points: List[str] = Field(default=[], description="核心卖点列表（生成模式）")
    target_audience: str = Field(default="", description="目标人群（生成模式）")
    video_style: str = Field(default="", description="视频风格（生成模式）")
    # 通用
    platform: str = Field(default="抖音", description="平台")
    bgm_url: str = Field(default="", description="BGM链接")
    material_csv: str = Field(default="assets/asset_manifest_v2_bound.csv", description="素材标签CSV路径")


class GraphOutput(BaseModel):
    """工作流输出"""
    final_video_url: str = Field(default="", description="最终视频URL")
    total_duration: float = Field(default=0.0, description="视频总时长")
    status: str = Field(default="", description="状态：success/failed/needs_review")
    fail_reason: str = Field(default="", description="失败原因")
    run_id: str = Field(default="", description="运行ID")
    quality_report: Dict[str, Any] = Field(default={}, description="质量报告JSON")


# ============================================================
# Node0a - 文案来源路由
# ============================================================

class ScriptSourceRouterInput(BaseModel):
    """文案来源路由节点输入"""
    script_id: str = Field(..., description="脚本ID")
    script_source: str = Field(..., description="脚本来源：generated|manual")
    script_text: str = Field(default="", description="原始文案（手动模式）")
    product_name: str = Field(default="", description="产品名（生成模式）")
    core_selling_points: List[str] = Field(default=[], description="核心卖点（生成模式）")
    target_audience: str = Field(default="", description="目标人群（生成模式）")
    video_style: str = Field(default="", description="视频风格（生成模式）")
    material_csv: str = Field(default="", description="素材CSV路径")
    platform: str = Field(default="", description="平台")
    bgm_url: str = Field(default="", description="BGM链接")


class ScriptSourceRouterOutput(BaseModel):
    """文案来源路由节点输出"""
    script_source: str = Field(..., description="脚本来源")
    script_text: str = Field(default="", description="原始文案（手动模式）")
    product_name: str = Field(default="", description="产品名（生成模式）")
    core_selling_points: List[str] = Field(default=[], description="核心卖点（生成模式）")
    target_audience: str = Field(default="", description="目标人群（生成模式）")
    video_style: str = Field(default="", description="视频风格（生成模式）")
    material_csv: str = Field(default="", description="素材CSV路径")
    platform: str = Field(default="", description="平台")
    bgm_url: str = Field(default="", description="BGM链接")
    run_dir: str = Field(..., description="运行目录")


class ScriptSourceRouteCheck(BaseModel):
    """文案来源条件判断的输入"""
    script_source: str = Field(..., description="脚本来源：generated|manual")


# ============================================================
# Node0b - 生成文案（Mode A）
# ============================================================

class GenerateScriptInput(BaseModel):
    """生成文案节点输入"""
    product_name: str = Field(..., description="产品名")
    core_selling_points: List[str] = Field(default=[], description="核心卖点")
    target_audience: str = Field(default="", description="目标人群")
    video_style: str = Field(default="", description="视频风格")
    material_csv: str = Field(default="", description="素材CSV路径")
    run_dir: str = Field(..., description="运行目录")


class GenerateScriptOutput(BaseModel):
    """生成文案节点输出"""
    raw_script: str = Field(..., description="生成的脚本")
    script_source: str = Field(default="generated", description="脚本来源")
    generated_script_path: str = Field(..., description="generated_script.txt路径")
    original_script_path: str = Field(..., description="original_script.txt路径")


# ============================================================
# Node0c - 手动文案（Mode B）
# ============================================================

class ManualScriptInput(BaseModel):
    """手动文案节点输入"""
    script_text: str = Field(..., description="用户提供的文案")
    run_dir: str = Field(..., description="运行目录")


class ManualScriptOutput(BaseModel):
    """手动文案节点输出"""
    raw_script: str = Field(..., description="原始脚本")
    script_source: str = Field(default="manual", description="脚本来源")
    manual_script_path: str = Field(..., description="manual_script.txt路径")
    original_script_path: str = Field(..., description="original_script.txt路径")


# ============================================================
# Node1 - 输入规范化
# ============================================================

class InputNormInput(BaseModel):
    """输入规范化节点输入"""
    script_source: str = Field(..., description="脚本来源")
    raw_script: str = Field(..., description="原始脚本正文")
    run_dir: str = Field(..., description="运行目录")
    product_name: str = Field(default="", description="产品名")
    material_csv: str = Field(default="", description="素材CSV路径")


class InputNormOutput(BaseModel):
    """输入规范化节点输出"""
    cleaned_script: str = Field(..., description="清洗后文案")
    run_dir: str = Field(..., description="运行目录")
    original_script_path: str = Field(..., description="原始文案保存路径")
    cleaned_script_path: str = Field(..., description="清洗文案保存路径")
    input_meta_path: str = Field(..., description="输入元数据路径")
    original_chars: int = Field(..., description="原始文案字符数")
    script_ok: bool = Field(..., description="文案校验通过")


# ============================================================
# Node2 - TTS生成
# ============================================================

class TTSGenInput(BaseModel):
    """TTS生成节点输入"""
    cleaned_script: str = Field(..., description="清洗后文案")
    run_dir: str = Field(..., description="运行目录")


class TTSGenOutput(BaseModel):
    """TTS生成节点输出"""
    tts_wav_path: str = Field(..., description="TTS音频路径")
    tts_input_path: str = Field(..., description="TTS输入文本路径")
    tts_meta_path: str = Field(..., description="TTS元数据路径")
    tts_duration: float = Field(..., description="TTS时长(秒)")


# ============================================================
# Node3 - 字幕时间轴
# ============================================================

class SubtitleTimingInput(BaseModel):
    """字幕时间轴节点输入"""
    cleaned_script: str = Field(..., description="清洗后文案")
    tts_duration: float = Field(..., description="TTS时长")
    run_dir: str = Field(..., description="运行目录")


class SubtitleTimingOutput(BaseModel):
    """字幕时间轴节点输出"""
    sentences: List[str] = Field(..., description="分句列表")
    timing: List[Dict[str, Any]] = Field(..., description="时间轴")
    srt_path: str = Field(..., description="SRT字幕路径")
    timing_debug_path: str = Field(..., description="时间轴调试JSON路径")
    srt_no_overlap: bool = Field(..., description="字幕无重叠")
    srt_coverage: float = Field(..., description="字幕文案覆盖率")
    final_chars: int = Field(..., description="最终字幕字符数")


# ============================================================
# Node4 - 素材匹配
# ============================================================

class MaterialMatchInput(BaseModel):
    """素材匹配节点输入"""
    timing: List[Dict[str, Any]] = Field(..., description="时间轴")
    material_csv: str = Field(..., description="素材CSV路径")
    run_dir: str = Field(..., description="运行目录")
    mapping_file: str = Field(default="", description="句子标签映射JSON路径")
    audited_materials: List[Dict[str, Any]] = Field(default=[], description="预检通过的素材列表")


class MaterialMatchOutput(BaseModel):
    """素材匹配节点输出"""
    materials: List[Dict[str, Any]] = Field(..., description="素材库")
    timeline_shots: List[Dict[str, Any]] = Field(..., description="时间线镜头")
    selected_assets: List[Dict[str, Any]] = Field(..., description="选中素材明细")
    selected_assets_path: str = Field(..., description="选中素材JSON路径")
    match_report_path: str = Field(..., description="匹配报告路径")
    low_confidence_segments: int = Field(..., description="低置信度段落数")
    unique_material_count: int = Field(..., description="唯一素材数")
    used_manifest_file: str = Field(..., description="使用的素材清单文件")
    mapping_file_used: str = Field(default="", description="使用的句子标签映射文件")
    mapping_coverage: float = Field(default=0.0, description="映射覆盖率")
    exact_tag_match_count: int = Field(default=0, description="精确标签匹配数")
    synonym_match_count: int = Field(default=0, description="同义标签匹配数")
    semantic_fallback_count: int = Field(default=0, description="语义回落匹配数")
    unmatched_sentence_ids: List[int] = Field(default=[], description="未匹配句子ID")
    high_confidence_segments: int = Field(default=0, description="高置信度段数")
    medium_confidence_segments: int = Field(default=0, description="中置信度段数")
    semantic_mismatch_segments: List[int] = Field(default=[], description="语义不匹配句子ID")


# ============================================================
# Node4b - 素材源预检
# ============================================================

class MaterialAuditInput(BaseModel):
    """素材源预检节点输入"""
    material_csv: str = Field(..., description="素材CSV路径")
    run_dir: str = Field(..., description="运行目录")
    material_audit_path: str = Field(default="", description="素材审计报告路径（由预检节点输出）")


class MaterialAuditOutput(BaseModel):
    """素材源预检节点输出"""
    material_audit_path: str = Field(..., description="素材审计报告路径")
    audited_materials: List[Dict[str, Any]] = Field(..., description="审计后素材列表（只含source_ok=true的素材）")
    clean_material_count: int = Field(..., description="无字素材数量")
    dirty_material_count: int = Field(..., description="带字素材数量")
    material_source_ok: bool = Field(..., description="有足够无字素材可用")
    # 图输出字段（含失败路径）
    final_video_url: str = Field(default="", description="最终视频URL")
    total_duration: float = Field(default=0.0, description="最终视频时长")
    status: str = Field(default="", description="最终状态")
    fail_reason: str = Field(default="", description="失败原因")
    failure_category: str = Field(default="", description="失败分类")
    run_id: str = Field(default="", description="运行ID")


class MaterialSourceCheck(BaseModel):
    """素材源条件判断的输入"""
    material_source_ok: bool = Field(..., description="素材源是否通过预检")


# ============================================================
# Node5 - 素材截取
# ============================================================

class ClipExtractInput(BaseModel):
    """素材截取节点输入"""
    timeline_shots: List[Dict[str, Any]] = Field(..., description="时间线镜头")
    run_dir: str = Field(..., description="运行目录")


class ClipExtractOutput(BaseModel):
    """素材截取节点输出"""
    clip_paths: List[str] = Field(..., description="截取片段路径列表")
    clipped_assets_path: str = Field(..., description="截取素材JSON路径")
    clip_report_path: str = Field(..., description="截取报告路径")


# ============================================================
# Node6 - Timeline组装
# ============================================================

class TimelineAssemblyInput(BaseModel):
    """Timeline组装节点输入"""
    timeline_shots: List[Dict[str, Any]] = Field(..., description="时间线镜头")
    clip_paths: List[str] = Field(..., description="截取片段路径")
    timing: List[Dict[str, Any]] = Field(..., description="时间轴")
    run_dir: str = Field(..., description="运行目录")


class TimelineAssemblyOutput(BaseModel):
    """Timeline组装节点输出"""
    final_timeline_path: str = Field(..., description="最终timeline路径")


# ============================================================
# Node7 - 最终合成
# ============================================================

class FinalCompositionInput(BaseModel):
    """最终合成节点输入"""
    final_timeline_path: str = Field(..., description="最终timeline路径")
    srt_path: str = Field(..., description="SRT字幕路径")
    tts_wav_path: str = Field(..., description="TTS音频路径")
    bgm_url: str = Field(default="", description="BGM链接")
    run_dir: str = Field(..., description="运行目录")


class FinalCompositionOutput(BaseModel):
    """最终合成节点输出"""
    final_video_path: str = Field(..., description="最终视频路径")
    contact_sheet_path: str = Field(..., description="联系图路径")
    video_duration: float = Field(..., description="视频时长(秒)")
    end_hold_sec: float = Field(default=0.0, description="结尾停留时长(秒)")


# ============================================================
# Node8 - 质量验收
# ============================================================

class QualityCheckInput(BaseModel):
    """质量验收节点输入"""
    original_script_path: str = Field(..., description="原始文案路径")
    tts_input_path: str = Field(..., description="TTS输入路径")
    tts_wav_path: str = Field(..., description="TTS音频路径")
    srt_path: str = Field(..., description="SRT字幕路径")
    timing_debug_path: str = Field(..., description="时间轴调试路径")
    final_video_path: str = Field(..., description="最终视频路径")
    clip_report_path: str = Field(..., description="截取报告路径")
    selected_assets: List[Dict[str, Any]] = Field(..., description="选中素材明细")
    timeline_shots: List[Dict[str, Any]] = Field(..., description="时间线镜头")
    timing: List[Dict[str, Any]] = Field(..., description="时间轴")
    tts_duration: float = Field(..., description="TTS时长")
    low_confidence_segments: int = Field(..., description="低置信度段落数")
    unique_material_count: int = Field(..., description="唯一素材数")
    used_manifest_file: str = Field(..., description="素材清单文件")
    run_dir: str = Field(..., description="运行目录")
    original_chars: int = Field(..., description="原始字符数")
    final_chars: int = Field(..., description="最终字符数")
    srt_coverage: float = Field(..., description="字幕覆盖率")
    script_ok: bool = Field(..., description="文案校验通过")
    srt_no_overlap: bool = Field(..., description="字幕无重叠")
    sentences: List[str] = Field(..., description="分句列表")


class QualityCheckOutput(BaseModel):
    """质量验收节点输出"""
    quality_report: Dict[str, Any] = Field(..., description="质量报告")
    status: str = Field(..., description="最终状态：success/failed/needs_review")
    fail_reason: str = Field(..., description="失败原因")
    failure_category: str = Field(..., description="失败分类")
    final_video_url: str = Field(..., description="最终视频URL")
    total_duration: float = Field(..., description="总时长")