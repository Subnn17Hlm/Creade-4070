## 项目概述
- **名称**: Creade吹风机短视频自动生成工作流
- **功能**: 输入文案 + 素材标签映射文件，输出一条音频、字幕、画面严格同步，素材语义匹配的成品视频

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 下游节点 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| script_source_router | `nodes/script_source_router_node.py` | task | 根据script_source路由到不同分支，创建run_dir | →generated→generate_script / →manual→manual_script | - |
| generate_script | `nodes/generate_script_node.py` | agent | Mode A：基于产品信息+素材标签用LLM生成文案，保存generated_script.txt+original_script.txt | input_normalization | `config/script_generate_llm_cfg.json` |
| manual_script | `nodes/manual_script_node.py` | task | Mode B：直接使用用户文案，不重写不扩写，保存manual_script.txt+original_script.txt | input_normalization | - |
| input_normalization | `nodes/input_normalization_node.py` | task | 清洗文案，生成cleaned_script.txt/tts_input.txt/input_meta.json（含script_source、product_name等） | tts_generation | - |
| tts_generation | `nodes/tts_generation_node.py` | task | 生成TTS语音，保存tts.wav/tts_meta.json | subtitle_timing | - |
| subtitle_timing | `nodes/subtitle_timing_node.py` | task | 拆句、计算时间轴、生成subtitles.srt/timing_debug.json | material_source_audit | - |
| material_source_audit | `nodes/material_source_audit_node.py` | task | 预检素材源URL，检查烧录文字/尺寸异常 | →素材通过→material_matching / →素材不合格→material_fail | - |
| material_fail | `graph.py` (内联) | task | 素材源检测失败时终止流程 | END | - |
| material_matching | `nodes/material_matching_node.py` | task | 读取material_csv，语义匹配素材，生成selected_assets.json/semantic_match_report.json | clip_extraction | - |
| clip_extraction | `nodes/clip_extraction_node.py` | task | 按timeline截取素材片段，保存clip到run_dir/temp | timeline_assembly | - |
| timeline_assembly | `nodes/timeline_assembly_node.py` | task | 合并timeline+clip_paths，输出timeline.json | final_composition | - |
| final_composition | `nodes/final_composition_node.py` | task | 拼接+字幕渲染+混音，输出final.mp4/contact_sheet.jpg | quality_check | - |
| quality_check | `nodes/quality_check_node.py` | task | 质量验收，含字幕视觉校验，输出quality_report.json | END | - |

**类型说明**: task(task节点) / agent(大模型) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

### 条件分支
| 条件函数 | 源节点 | 分支 | 目标节点 |
|---------|-------|------|---------|
| route_script_source | script_source_router | 生成文案 (generated) | generate_script |
| | | 手动文案 (manual) | manual_script |
| material_source_ok_router | material_source_audit | 素材通过 | material_matching |
| | | 素材不合格 | material_fail |

### 全局状态 (GlobalState)
- `script_id`, `script_source`(generated|manual), `script_text`, `cleaned_script`, `platform`, `bgm_url`, `material_csv`
- `product_name`, `core_selling_points`, `target_audience`, `video_style`（生成模式参数）
- `run_dir`: 独立运行目录 (runs/script_{id}/)
- 各中间产物路径: `original_script_path`, `cleaned_script_path`, `input_meta_path`, `tts_wav_path`, `tts_input_path`, `tts_meta_path`, `srt_path`, `timing_debug_path`, `selected_assets_path`, `match_report_path`, `clipped_assets_path`, `clip_report_path`, `final_timeline_path`, `final_video_path`, `contact_sheet_path`
- 素材源预检: `material_audit_path`, `audited_materials`, `material_source_ok`
- `timing`: List[Dict] 每句时间轴
- `timeline_shots`: List[Dict] 每句素材匹配结果
- `selected_assets`: List[Dict] 素材选择详情
- `clip_paths`: List[str] 截取片段路径
- `quality_report`: Dict 质量报告
- `status`: 最终状态

### 数据流向
```
GraphInput → script_source_router
  ├── (generated) → generate_script ─┐
  └── (manual) → manual_script ─────┤
                                     ↓
                              input_normalization → tts_generation → subtitle_timing
  → material_source_audit → (素材通过→material_matching / 素材不合格→material_fail→END)
  → material_matching → clip_extraction → timeline_assembly
  → final_composition → quality_check → GraphOutput
```

### 核心约束
- **禁止处理素材画面**: 不裁切、不缩放、不遮挡、不模糊、不去字幕、不补黑边
- **字幕只来自 subtitles.srt**: 白色字体(38px)、黑色描边(3px)、底部位置(y=0.82)
- **字幕渲染方式**: ffmpeg drawtext filter链（替代subtitles/ass filter，避免libass兼容性问题）
- **match_confidence 诚实**: 仅基于CSV真实标签计算，expansion仅辅助召回
- **selected_material_id 必须来自 candidate_materials**
- **素材源预检**: 所有素材URL必须通过source_audit，带字素材禁止进入剪辑池
- **素材匹配策略**: 三级匹配（exact→synonym→fallback），基于文本匹配合并timing与selected_assets
- **BGM选择逻辑**: 使用MD5(script_id)稳定选择，同一script_id始终选择相同BGM（非随机）
- **兜底素材策略**: 按句子类型选择安全标签（CTA促单/价格促销/痛点共鸣/旅行场景/放进包包/产品展示），禁止使用"手持展示"作为兜底标签
- **兜底素材轮换**: 使用used_material_ids跟踪已使用素材，避免重复选择
- **跨句视觉延续安全**: full_play_required素材跨句延续时，若clip时长不足以覆盖所有相邻句，回退只覆盖clip能实际覆盖的句子，确保视觉总时长=TTS总时长
- **关键词字典**: ~160个关键词映射到标签，覆盖旅行/便携/痛点/小巧/手持/折叠/风力/护发/屏显/吹发/CTA/促销/赠品/风嘴/口语化种草表达
- **资源文件**: `assets/asset_manifest_new_no_chuifa.csv`（73个无字幕原始素材，primary_scene_tag标签体系）
- **标签映射文件**: `assets/sentence_tag_mapping_script_02.json`（19句文案到required_tags的精确映射）

### 质量验收标准
1. 文案覆盖率 >= 95%
2. 音视频偏差 <= 1.5s
3. 字幕无重叠、样式正确
4. 素材未修改画面
5. 无黑边/黑底
6. 所有 selected_material_id 来自 candidate_materials
7. low_confidence_segments < 3 才 success（阈值从 >0 调整为 >=3，允许少量低置信度）
8. 关键卖点句低置信度必须 failed
9. 最终画面文字只能来自 subtitles.srt（`final_visual_text_only_from_srt`）
10. 无暗场/黑屏（`dark_frame_ratio` 检测）
11. 素材烧录文字检测（`material_label_text_burned_in`）
12. **字幕视觉校验**：从最终视频抽帧检测字幕是否实际渲染到画面
    - `subtitle_burned_into_final`: true/false — 字幕是否烧录到像素层
    - `subtitle_visible_in_final_video`: 至少4/5抽样帧检测到字幕文字
    - `sampled_subtitle_frame_paths`: 5张抽帧图片路径
    - `expected_srt_text_per_frame`: 每帧期望的SRT文本
    - `sampled_subtitle_frames`: 每帧详细检测结果
    - `subtitle_ocr_matches_srt`: 检测到的文字是否匹配SRT
    - `manual_visual_check_required`: 是否需要人工复核
    - 检测方式：基于text_blocks（连续白色像素行）检测，废弃bright_pixel_ratio
    - 字幕不可见时强制 `status=failed`
13. **语义匹配验收**：
    - `mapping_file_used`: 使用的标签映射文件
    - `manifest_file_used`: 使用的素材清单文件
    - `mapping_coverage`: 映射覆盖率 >= 95%
    - `exact_tag_match_count`: 精确标签匹配数
    - `synonym_match_count`: 同义标签匹配数
    - `semantic_fallback_count`: 语义回落匹配数
    - `unmatched_sentence_ids`: 未匹配句子ID
    - `high_confidence_segments`: 高置信度段落数
    - `medium_confidence_segments`: 中置信度段落数
    - `low_confidence_segments`: 低置信度段落数
    - `semantic_mismatch_segments`: 语义不匹配段落ID

### 运行目录结构 (runs/script_{id}/)
```
original_script.txt   (统一入口，来自manual_script.txt或generated_script.txt)
generated_script.txt  (仅生成模式)
manual_script.txt     (仅手动模式)
cleaned_script.txt
input_meta.json
tts_input.txt
tts.wav
tts_meta.json
timing_debug.json
subtitles.srt
selected_assets.json
semantic_match_report.json
clipped_assets.json
clip_extract_report.json
timeline.json
final.mp4
contact_sheet.jpg
quality_report.json
material_source_audit.json
temp/  (中间临时文件)
```