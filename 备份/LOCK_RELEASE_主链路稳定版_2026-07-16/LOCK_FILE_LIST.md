# LOCK_FILE_LIST — 主链路稳定版关键文件清单

**备份日期**: 2026-07-16  
**备份名称**: LOCK_RELEASE_主链路稳定版_2026-07-16

---

## 核心代码（已复制到 code_snapshot/）

| 文件 | 路径 | 说明 |
|------|------|------|
| material_matching_node.py | `src/graphs/nodes/material_matching_node.py` | 素材语义匹配，关键词→标签→素材 |
| clip_extraction_node.py | `src/graphs/nodes/clip_extraction_node.py` | 素材裁剪，按timeline截取片段 |
| timeline_assembly_node.py | `src/graphs/nodes/timeline_assembly_node.py` | 画面timeline组装，跨句视觉延续 |
| final_composition_node.py | `src/graphs/nodes/final_composition_node.py` | 最终合成，拼接+字幕+混音 |
| script_source_router_node.py | `src/graphs/nodes/script_source_router_node.py` | 文案来源路由+BGM稳定选择 |
| quality_check_node.py | `src/graphs/nodes/quality_check_node.py` | 质量验收，含字幕视觉校验 |
| graph.py | `src/graphs/graph.py` | 主图编排，12节点流水线 |
| state.py | `src/graphs/state.py` | 全局状态+节点出入参定义 |

## 其他核心代码（未复制，但依赖）

| 文件 | 路径 | 说明 |
|------|------|------|
| input_normalization_node.py | `src/graphs/nodes/input_normalization_node.py` | 文案清洗 |
| tts_generation_node.py | `src/graphs/nodes/tts_generation_node.py` | TTS语音合成 |
| subtitle_timing_node.py | `src/graphs/nodes/subtitle_timing_node.py` | 字幕时间轴 |
| material_source_audit_node.py | `src/graphs/nodes/material_source_audit_node.py` | 素材源预检 |
| generate_script_node.py | `src/graphs/nodes/generate_script_node.py` | LLM文案生成 |
| manual_script_node.py | `src/graphs/nodes/manual_script_node.py` | 手动文案处理 |
| shared_utils.py | `src/graphs/shared_utils.py` | 共享工具函数 |

## 素材与资源

| 文件/目录 | 路径 | 说明 |
|-----------|------|------|
| 素材清单CSV | `assets/asset_manifest_v2_clean.csv` | 126个无字幕原始素材（已复制到 asset_manifest_snapshot/） |
| 字体目录 | `assets/Fonts/` | 字幕渲染字体 |
| BGM目录 | `assets/bgm/` | 背景音乐文件 |

## 配置文件（已复制到 config_snapshot/）

| 文件 | 路径 | 说明 |
|------|------|------|
| script_generate_llm_cfg.json | `config/script_generate_llm_cfg.json` | 文案生成模型配置 |
| script_parse_llm_cfg.json | `config/script_parse_llm_cfg.json` | 文案解析模型配置 |
| l1_tagging_llm_cfg.json | `config/l1_tagging_llm_cfg.json` | L1标签模型配置 |
| l2_tagging_llm_cfg.json | `config/l2_tagging_llm_cfg.json` | L2标签模型配置 |
| l3_intent_llm_cfg.json | `config/l3_intent_llm_cfg.json` | L3意图模型配置 |

## 验证产物

| 目录 | 说明 |
|------|------|
| `runs/batch_fix2_01/` | 验证批次1（✅ low_conf=2, diff=0.0s） |
| `runs/batch_fix2_02/` | 验证批次2（✅ low_conf=2, diff=0.0s） |
| `runs/batch_fix2_03/` | 验证批次3（✅ low_conf=0, diff=0.0s） |
| `runs/batch_fix2_04/` | 验证批次4（✅ low_conf=2, diff=0.0s） |
| `runs/batch_fix2_05/` | 验证批次5（✅ low_conf=0, diff=0.0s） |

## 验证报告（已复制到 validation_report/）

| 文件 | 路径 | 说明 |
|------|------|------|
| BATCH_VALIDATION_REPORT.md | `5条小批量验证修复版2/BATCH_VALIDATION_REPORT.md` | 5条小批量验证报告 |
