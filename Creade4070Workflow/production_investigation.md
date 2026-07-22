# 生产问题排查报告

## 一、环境确认

当前代码版本：0b51637（已确认）

## 二、BGM 缺失问题分析

### 2.1 BGM 流程追踪

**节点0：script_source_router_node.py (L94-99)**
```python
bgm_url = state.get("bgm_url", "") or ""
if not bgm_url:
    bgm_url = _select_bgm_stable(script_id)
```

**问题1**：如果 `script_id` 为空，`_select_bgm_stable` 会使用空字符串的 MD5，导致每次都选择同一个 BGM（index 0）

**节点7：final_composition_node.py (L788, L1045-1053)**
```python
bgm_url = state.get("bgm_url", "")

if not bgm_url:
    bgm_dir = os.path.join(os.getenv("COZE_WORKSPACE_PATH", ""), "assets/bgm")
    if os.path.exists(bgm_dir):
        bgm_files = sorted([f for f in os.listdir(bgm_dir) if f.endswith(".mp3")])
        if bgm_files:
            hash_val = int(hashlib.md5(run_dir.encode()).hexdigest(), 16)
            bgm_index = hash_val % len(bgm_files)
            bgm_url = os.path.join(bgm_dir, bgm_files[bgm_index])
```

**问题2**：这里又做了一次 BGM 选择，但使用的是 `run_dir` 的 MD5，与节点0的选择逻辑不一致

**节点7：final_composition_node.py (L1055-1085)**
```python
if bgm_url:
    try:
        local_bgm = _download_bgm(bgm_url, temp_dir)
        # ... 混音逻辑
        bgm_used = True
    except Exception as e:
        logger.error("[Node7] BGM混合失败: %s，仅使用TTS", e)
        bgm_used = False

if not bgm_used:
    # 仅使用 TTS
    tts_only_cmd = [...]
```

**问题3**：BGM 失败后静默降级，没有在最终输出中标记 warning

### 2.2 BGM 根因假设

1. **假设A**：`bgm_url` 在传递过程中丢失
   - 检查点：state 中 `bgm_url` 字段是否正确传递
   
2. **假设B**：BGM 文件下载失败
   - 检查点：`_download_bgm` 是否抛出异常
   
3. **假设C**：FFmpeg 混音失败
   - 检查点：混音命令是否执行成功

4. **假设D**：BGM 音量为 0 或过低
   - 当前设置：`bgm_volume = 0.40`（L1062），应该可听

## 三、语义匹配问题分析

### 3.1 语义匹配流程

**节点4：material_matching_node.py**

1. **关键词到标签映射** (L64-150)：
   - "出差" → ["旅行场景"]
   - "折叠" → ["折叠动作"]
   - "小巧" → ["手持大小对比"]
   - 等等

2. **视觉分组** (L484-708)：
   - 将短句合并到视觉组
   - 每个组有一个 `primary_tag`

3. **素材匹配** (L733-850)：
   - 阶段1：精确标签匹配
   - 阶段2：同义标签匹配
   - 阶段3：语义回落
   - 阶段4：兜底选择

### 3.2 潜在问题

**问题4**：视觉分组可能合并了不同语义的短句
- 例如："出差旅行"（旅行场景）+ "折叠带走"（折叠动作）可能被合并
- 合并后只保留一个 `primary_tag`，导致另一个语义丢失

**问题5**：素材匹配后可能被后续节点覆盖
- 需要检查 `timeline_assembly_node.py` 是否修改了 `selected_material_id`

## 四、需要验证的证据

由于无法访问生产环境，需要用户协助提供：

1. **BGM 相关**：
   - 任务 trace 中 `final_composition` 节点的 `bgm_used` 字段
   - 日志中是否有 "[Node7] BGM混合失败" 或 "[Node7] TTS+BGM 混音完成"
   - 最终视频的音频流信息（ffprobe 输出）

2. **语义匹配相关**：
   - `material_matching` 节点输出的 `selected_assets.json`
   - `visual_grouping_report.json` 中的分组情况
   - 每个 visual_group 的 `primary_tag` 和实际使用的素材标签

## 五、代码修复方案

### 5.1 BGM 修复

1. **统一 BGM 选择逻辑**：在节点0选择后，后续节点不再重复选择
2. **增强错误报告**：BGM 失败时在输出中添加 warning
3. **验证 BGM 存在性**：在质量检测节点增加 BGM 检查

### 5.2 语义匹配修复

1. **保护匹配结果**：确保 timeline_assembly 不覆盖已选素材
2. **增强日志**：记录每个视觉组的匹配过程和最终选择
3. **fallback 标记**：在输出中明确标记哪些片段使用了 fallback
