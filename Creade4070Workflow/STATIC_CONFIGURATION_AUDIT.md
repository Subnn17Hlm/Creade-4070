# 静态配置核查报告

## 基本信息

| 项目 | 值 |
|------|-----|
| 项目实际根目录 | `/workspace/projects/Creade4070Workflow/` |
| 工作区根目录 | `/workspace/projects/` |
| 检查时间 | 2026-07-17 17:30 CST |
| 检查范围 | 只读静态检查，未安装依赖、未运行工作流、未生成视频、未部署 |

---

## 1. 已确认存在的关键目录与文件

### 目录

| 路径 | 状态 |
|------|------|
| `pyproject.toml` | OK |
| `src/` | OK |
| `src/main.py` | OK |
| `src/graphs/` | OK |
| `src/graphs/graph.py` | OK |
| `src/graphs/nodes/` (11 个节点文件) | OK |
| `src/agents/` | OK |
| `src/pipeline/` | OK |
| `src/storage/` | OK |
| `src/tools/` | OK |
| `src/utils/` | OK |
| `scripts/` | OK |
| `assets/` | OK |
| `config/` | OK |

### 资源文件

| 路径 | 状态 | 说明 |
|------|------|------|
| `assets/bgm/bgm_01.mp3` ~ `bgm_12.mp3` | OK | 12 首 BGM |
| `assets/Fonts/` (含 ALIBABA-PUHUITI-BOLD.TTF 等) | OK | 字幕字体 |
| `assets/native_text_whitelist.json` | OK | 原生文字白名单 |
| `assets/product_4070_safe_whitelist.json` | OK | 产品安全白名单 |
| `assets/sentence_tag_mapping_script_02.json` | OK | 句子标签映射 |
| `config/script_generate_llm_cfg.json` | OK | 文案生成 LLM 配置 |
| `config/script_parse_llm_cfg.json` | OK | 文案解析 LLM 配置 |
| `config/l1_tagging_llm_cfg.json` | OK | L1 标注 LLM 配置 |
| `config/l2_tagging_llm_cfg.json` | OK | L2 标注 LLM 配置 |
| `config/l3_intent_llm_cfg.json` | OK | L3 意图 LLM 配置 |

### Python 语法编译

全部 40 个 `.py` 文件通过 `py_compile` 编译检查，无语法错误。

---

## 2. 缺失项分类

### 必须补齐（阻塞运行）

| # | 缺失项 | 说明 | 影响范围 |
|---|--------|------|----------|
| 1 | `assets/asset_manifest_new_no_chuifa.csv` | 素材清单 CSV（73 个无字幕原始素材），代码默认路径。当前不存在 | 素材匹配、素材源预检、clip 截取等核心节点全部无法执行 |
| 2 | ffmpeg 未安装 | `setup.sh` 不安装 ffmpeg，当前环境也未安装 | TTS 转码、素材截取、视频拼接、字幕渲染、质量检查全部依赖 ffmpeg |
| 3 | 素材视频 URL 可访问性 | CSV 中 `source_url`/`s3_url` 指向的视频必须可被工作流环境访问 | clip_extraction_node 直接从 URL 截取片段 |

### 可自动修复（不阻塞但影响功能完整性）

| # | 问题 | 文件 | 行号 | 修复方式 |
|---|------|------|------|----------|
| 1 | 白名单路径引用错误 | `src/graphs/nodes/timeline_assembly_node.py` | 224 | 将 `"素材质量优化"` 改为 `"assets"` |
| 2 | 审计目录路径引用错误 | `scripts/validate_delivery_artifacts.py` | 177 | 将 `"素材质量优化"` 改为 `"assets"` |
| 3 | 输出路径引用错误 | `scripts/validate_delivery_artifacts.py` | 258 | 将 `"素材质量优化"` 改为 `"assets"` |
| 4 | 审计目录路径引用错误 | `scripts/material_quality_audit.py` | 47 | 将 `"素材质量优化"` 改为 `"assets"` |
| 5 | 证据图片路径全部失效 | `assets/product_4070_safe_whitelist.json` | 9~453 | 需重建 `素材质量优化/material_audit_evidence/` 目录或更新 JSON 中的路径 |
| 6 | setup.sh 缺少 ffmpeg 安装 | `scripts/setup.sh` | - | 增加 `apt-get install -y ffmpeg` 或等效系统依赖安装 |

### 暂不影响静态检查

| # | 项目 | 说明 |
|---|------|------|
| 1 | `素材质量优化/` 目录不存在 | 仅影响审计溯源和字幕关闭逻辑，工作流有容错处理 |
| 2 | `PGDATABASE_URL` 未配置 | 仅异步任务/checkpoint 持久化模式需要，同步工作流不依赖 |
| 3 | `COZE_BUCKET_ENDPOINT_URL` / `COZE_BUCKET_NAME` 未配置 | 仅产物上传到 S3 时需要，本地运行不依赖 |

---

## 3. asset_manifest_new_no_chuifa.csv 引用清单

### 引用位置

| 文件 | 行号 | 用途 |
|------|------|------|
| `src/graphs/state.py` | 117 | `GraphInput.material_csv` 字段默认值 |
| `src/graphs/nodes/material_matching_node.py` | 590 | 未传入 CSV 时的回退默认路径 |
| `src/graphs/nodes/material_source_audit_node.py` | 85~87 | 素材源预检，读取 CSV 逐行 ffprobe 检测 |
| `src/pipeline/single_run.py` | 8 | 本地调试示例的默认路径 |
| `scripts/material_quality_audit.py` | 81 | 素材质量审计脚本的输入文件 |

### CSV 必须包含的字段

| 字段 | 必需 | 说明 |
|------|------|------|
| `asset_id` | 是 | 素材唯一标识 |
| `file_name` | 是 | 文件名 |
| `primary_scene_tag` | 是 | 场景标签，用于语义匹配核心字段 |
| `source_url` 或 `s3_url` | 是 | 素材视频可访问 URL（至少一个非空） |
| `duration_sec` | 否 | 素材时长秒数，默认 3 |
| `deprecated` | 否 | 值为 `true` 则跳过该素材 |
| `enabled` | 否 | 值为 `false` 则跳过该素材 |
| `bucket` | 否 | S3 bucket 名称 |
| `object_key` | 否 | S3 object key |
| `description` | 否 | 素材描述 |
| `needs_clip` | 否 | 是否需要截取（`true`/`false`） |
| `notes` | 否 | 备注 |
| `batch` | 否 | 批次标识 |
| `tags` | 否 | 逗号分隔标签，素材源预检使用 |

---

## 4. native_text_whitelist.json 路径问题

### 实际路径

```
/workspace/projects/Creade4070Workflow/assets/native_text_whitelist.json
```

### 错误引用位置

| 文件 | 行号 | 错误引用路径 | 影响 |
|------|------|-------------|------|
| `src/graphs/nodes/timeline_assembly_node.py` | 224 | `${COZE_WORKSPACE_PATH}/素材质量优化/native_text_whitelist.json` | 字幕关闭逻辑失效（有容错，不崩溃） |
| `scripts/validate_delivery_artifacts.py` | 177 | `${project_root}/素材质量优化` | 交付验证找不到审计目录 |
| `scripts/validate_delivery_artifacts.py` | 258 | `${project_root}/素材质量优化/delivery_validation_report.json` | 报告输出路径不存在 |
| `scripts/material_quality_audit.py` | 47 | `${WORKSPACE}/素材质量优化` | 审计脚本输出目录不存在 |

### 建议修复路径

将上述引用中的 `"素材质量优化"` 统一改为 `"assets"`，使路径指向实际存在的 `assets/native_text_whitelist.json`。

---

## 5. LLM / TTS 环境变量

### LLM

LLM 通过 `coze_coding_dev_sdk.LLMClient` 调用，由平台运行时上下文 (`Context`) 自动注入认证信息，**不需要手动设置 API Key 环境变量**。

使用的模型配置（`config/` 目录）：

| 配置文件 | 模型 | 用途 |
|----------|------|------|
| `script_generate_llm_cfg.json` | doubao-seed-2-0-pro-260215 | 文案生成（generated 模式） |
| `script_parse_llm_cfg.json` | doubao-seed-2-0-lite-260215 | 文案解析 |
| `l1_tagging_llm_cfg.json` | doubao-seed-1-8-251228 | L1 素材标注 |
| `l2_tagging_llm_cfg.json` | doubao-seed-1-8-251228 | L2 精细化标注 |
| `l3_intent_llm_cfg.json` | doubao-seed-1-8-251228 | L3 意图标注 |

### TTS

TTS 通过 `coze_coding_dev_sdk.TTSClient` 调用，同样由 Context 自动注入认证，**不需要手动设置 API Key 环境变量**。

- Speaker: `zh_female_xiaohe_uranus_bigtts`
- 音频格式: mp3 -> 转码为 wav (pcm_s16le, 44100Hz, mono)

### 程序读取的环境变量清单

| 变量名 | 用途 | 必需条件 |
|--------|------|----------|
| `COZE_WORKSPACE_PATH` | 项目根目录，所有资源路径的基准 | 始终必需 |
| `PGDATABASE_URL` | PostgreSQL 连接串（checkpoint 持久化） | 异步任务模式 |
| `COZE_BUCKET_ENDPOINT_URL` | S3 对象存储 endpoint | 上传产物时 |
| `COZE_BUCKET_NAME` | S3 bucket 名称 | 上传产物时 |
| `COZE_PROJECT_ENV` | 环境标识（`DEV`=开发环境） | 可选 |
| `PIP_TARGET` | 部署模式 pip 安装目标路径 | 部署模式 |
| `DEPLOY_RUN_PORT` | 部署运行端口 | 可选（默认 5000） |

> 注：以上仅列出变量名称和用途，不包含任何密钥值。

---

## 6. 素材视频存放与匹配规则

### 存放方式

素材视频**不需要本地存放**。CSV 中的 `source_url` 或 `s3_url` 字段指向可访问的 URL：

- 优先使用 `source_url`，其次使用 `s3_url`
- 支持 S3 预签名 URL 或 HTTP/HTTPS URL
- `clip_extraction_node` 直接用 ffmpeg 从 URL 截取片段（`ffmpeg -i <url>`）

### 文件命名与匹配规则

- 素材通过 `asset_id` 唯一标识
- 通过 `primary_scene_tag` 与文案句子标签进行语义匹配
- 匹配策略三级回落：exact（精确标签匹配） -> synonym（同义标签） -> fallback（安全标签兜底）
- 兜底标签按句子类型选择：CTA促单 / 价格促销 / 痛点共鸣 / 旅行场景 / 放进包包 / 产品展示
- 禁止使用"手持展示"作为兜底标签
- 使用 `used_material_ids` 跟踪已使用素材，避免重复选择

### 素材格式要求

- 竖屏 9:16（素材源预检会检测宽高比）
- 无烧录文字（通过文件名白名单和帧检测判断）
- 无字幕原始素材

---

## 7. ffmpeg 安装情况

### setup.sh 是否安装 ffmpeg

**否**。`scripts/setup.sh` 仅执行 Python 依赖安装（`uv sync`），不包含任何系统级依赖安装。

### 当前环境状态

ffmpeg **未安装**。

### 部署环境要求

ffmpeg 在工作流中被以下节点直接调用：

| 节点 | 用途 |
|------|------|
| `tts_generation_node.py` | mp3 转 wav（pcm_s16le, 44100Hz） |
| `clip_extraction_node.py` | 从 URL 截取素材片段 |
| `final_composition_node.py` | 视频拼接、字幕渲染（drawtext）、混音（TTS+BGM）、结尾停留（tpad） |
| `quality_check_node.py` | 暗场检测、抽帧、ffprobe 媒体信息、字幕视觉校验 |
| `material_source_audit_node.py` | ffprobe 检测素材分辨率和时长 |

**结论**: 部署镜像或 setup.sh 中必须安装 ffmpeg，否则工作流在 TTS 节点即会失败。

---

## 8. 工作流启动入口

| 方式 | 命令 | 说明 |
|------|------|------|
| HTTP 服务 | `python src/main.py -m http -p 5000` | 启动 FastAPI 服务，监听 5000 端口 |
| 本地工作流 | `bash scripts/local_run.sh -m flow` | 本地执行完整工作流 |
| 本地单节点 | `bash scripts/local_run.sh -m node -n <node_name>` | 本地执行单个节点 |
| 部署启动 | `bash scripts/http_run.sh -p 5000` | 部署环境启动（含 venv 激活） |

### HTTP 端口

固定为 **5000**。

### HTTP API 入口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/run` | POST | 同步运行工作流 |
| `/stream_run` | POST | 流式运行工作流（SSE） |
| `/node_run` | POST | 运行单个节点 |
| `/v1/chat/completions` | POST | OpenAI 兼容接口 |

### GraphInput 参数

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `script_id` | str | 必填 | 脚本 ID，如 `script_02` |
| `script_source` | str | `manual` | `generated` 或 `manual` |
| `script_text` | str | `""` | 原始文案（manual 模式） |
| `product_name` | str | `""` | 产品名（generated 模式） |
| `core_selling_points` | List[str] | `[]` | 核心卖点（generated 模式） |
| `target_audience` | str | `""` | 目标人群（generated 模式） |
| `video_style` | str | `""` | 视频风格（generated 模式） |
| `platform` | str | `抖音` | 目标平台 |
| `bgm_url` | str | `""` | BGM 链接（空则自动选择） |
| `material_csv` | str | `assets/asset_manifest_new_no_chuifa.csv` | 素材标签 CSV 路径 |

---

## 9. 下一步操作清单

严格按以下顺序执行：

1. **安装 ffmpeg** — 在 setup.sh 中增加系统依赖安装，或确认部署镜像已包含 ffmpeg
2. **上传 `assets/asset_manifest_new_no_chuifa.csv`** — 包含 73 个素材的完整清单，字段参见第 3 节
3. **确认素材视频 URL 可访问** — 验证 CSV 中所有 `source_url`/`s3_url` 在工作流环境中可被 ffmpeg 访问
4. **修复白名单路径引用** — 将 4 处 `"素材质量优化"` 引用改为 `"assets"`（参见第 4 节）
5. **配置环境变量** — 确认 `COZE_WORKSPACE_PATH` 正确指向项目根目录；如需产物上传，配置 `COZE_BUCKET_ENDPOINT_URL` 和 `COZE_BUCKET_NAME`
6. **安装 Python 依赖** — 执行 `bash scripts/setup.sh`
7. **启动 HTTP 服务** — 执行 `bash scripts/http_run.sh -p 5000`
8. **验证工作流** — 使用 manual 模式发送测试请求，确认 TTS、素材匹配、视频合成全链路通过
9. **（可选）修复 product_4070_safe_whitelist.json 中的证据路径** — 重建审计证据目录或更新路径引用

---

## 附录：待确认事项

| # | 事项 | 说明 |
|---|------|------|
| 1 | `asset_manifest_new_no_chuifa.csv` 的完整字段格式 | 待确认是否有示例文件或模板 |
| 2 | 素材视频 URL 的访问方式 | 待确认是 S3 预签名 URL 还是其他形式，以及 URL 有效期 |
| 3 | LLM/TTS 的 Context 注入机制 | 待确认在非平台环境（如本地开发）下如何提供认证信息 |
| 4 | `素材质量优化/` 目录的预期内容 | 待确认该目录是否需要保留，或完全迁移到 `assets/` |
| 5 | 部署镜像是否预装 ffmpeg | 待确认目标部署环境的基础镜像 |
