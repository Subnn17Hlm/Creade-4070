# Creade 吹风机短视频工作流 — 迁移说明

## 迁移概述

本文档用于将 Creade 吹风机短视频自动生成工作流从当前个人 Coze 空间迁移到公司账号。

---

## 当前保留目录结构

```
├── src/                          # 工作流源代码（11节点 DAG）
├── config/                       # LLM 配置文件
├── scripts/                      # 运行/部署脚本
├── docs/                         # 文档
├── assets/
│   ├── asset_manifest_new_no_chuifa.csv  # 【最终】素材标签表（73个素材）
│   ├── sentence_tag_mapping_script_02.json  # 语义映射文件
│   ├── Fonts/                     # 字体文件
│   └── bgm/                       # BGM 文件
├── runs/script_02/               # 最新运行结果（Script 2 验收通过）
├── pyproject.toml                # 项目配置
├── README.md
├── AGENTS.md                     # 工作流节点索引
└── MIGRATION_README.md           # 本文档
```

## 当前归档目录

```
_archive_legacy/
├── assets/                       # 旧素材CSV、旧成片视频、旧调试报告等
├── scripts/                      # L1/L2/L3 旧脚本
└── runs/                         # 旧运行结果
```

> 归档文件仅供历史参考，工作流不会读取 `_archive_legacy/` 中的任何文件。

---

## 关键路径

| 项目 | 路径 |
|-----|------|
| 最新运行目录 | `runs/script_02/` |
| 最终素材标签 CSV | `assets/asset_manifest_new_no_chuifa.csv` |
| 语义映射文件 | `assets/sentence_tag_mapping_script_02.json` |
| 最终成品视频 | `runs/script_02/final.mp4` |
| 质量报告 | `runs/script_02/quality_report.json` |
| 语义匹配报告 | `runs/script_02/semantic_match_report.json` |

---

## Script 2 当前验收状态

| 检查项 | 状态 |
|-------|------|
| 文案来源选择 | ✅ 已验证（双模式：generated/manual） |
| 文案生成 | ✅ 已验证 |
| 手动文案保留 | ✅ 已验证（不改写、不扩写） |
| 输入规范化 | ✅ 已验证 |
| TTS 生成 | ❌ 当前个人账号欠费（需迁移后验证） |
| 字幕分句与时间轴 | ✅ 已验证 |
| 素材源预检 | ✅ 已验证（73个素材全部通过） |
| 语义素材匹配 | ✅ 已验证（19句全部高置信度匹配） |
| 剪辑提取 | ✅ 已验证 |
| 时间轴组装 | ✅ 已验证 |
| 最终合成（字幕烧录） | ✅ 已验证（drawtext 渲染可见） |
| 质量验收 | ✅ 已验证（字幕可见，烧录文字已规避） |

---

## 公司账号导入后第一步操作

### 步骤 1：导入项目

1. 在公司 Coze 空间创建项目
2. 将本迁移包解压到项目工作目录
3. 运行 `uv sync` 安装依赖

### 步骤 2：验证环境

```bash
# 检查依赖
uv pip list 2>/dev/null | grep -E "coze|langgraph|langchain"

# 检查 TTS 可用性（公司账号自动注入环境变量）
python3 -c "
from coze_coding_dev_sdk import TTSClient
client = TTSClient()
audio_url, size = client.synthesize(uid='migration_test', text='测试语音')
print(f'TTS可用: {audio_url}')
"
```

### 步骤 3：只跑 Script 2，不要直接批量

```python
# 使用工作流接口（推荐）
# 入参：
#   script_source: "manual"
#   script_text: "每次出差旅行..."
#   product_name: "Creade吹风机"
#   platform: "抖音"
#   material_csv: "assets/asset_manifest_new_no_chuifa.csv"
```

**重要：** 公司账号导入后，**必须先跑 Script 2 单条验证**，确认所有节点通过后再考虑批量运行。

---

## TTS 说明

- 当前 TTS 使用 **Coze 平台内置 TTS 服务**（`coze-coding-dev-sdk` 封装）
- 底层引擎：火山引擎（ByteDance Voice）Uranus BigTTS
- 当前个人账号 402 欠费 **属于个人账号/空间的额度问题，不是代码或配置问题**
- 迁移包**不包含** TTS API key（Key 由 Coze 平台自动注入环境变量）
- 公司账号迁移后，应使用公司账号的 Coze 平台内置 TTS 额度
- 公司账号导入后，需重新跑 Script 2 验证 TTS 是否可用

---

## 迁移后检查清单

### 阻塞项（必须修复后才能迁移）

- [ ] 无（当前 `migration_ready = true`）

### 迁移后检查（post_migration）

- [ ] 公司账号 Coze 空间 TTS 是否可用（需充值额度）
- [ ] 重新跑 Script 2 单条，验证全流程通过
- [ ] 检查 `final.mp4` 字幕是否可见
- [ ] 检查 `contact_sheet.jpg` 是否显示字幕
- [ ] 检查 `quality_report.json` 中 subtitle_visible 是否为 true
- [ ] 验证后再考虑批量运行

---

## 注意事项

1. **禁止直接批量**：必须先跑 Script 2 单条验证全流程
2. **禁止修改素材标签 CSV**：`asset_manifest_new_no_chuifa.csv` 是最终版
3. **禁止修改 final.mp4**：当前验收通过的成品视频
4. **不要删除 `_archive_legacy/`**：保留历史参考
5. **TTS 欠费不是代码问题**：公司账号 TTS 需要重新充值/开通