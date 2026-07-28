# 素材质量审计报告 (MATERIAL_QUALITY_AUDIT_REPORT)

## 审计元数据

| 项目 | 值 |
|------|-----|
| 审计版本 | 1.0 |
| 审计时间 | 2026-07-17T12:01:00 |
| OCR 引擎 | rapidocr_onnxruntime |
| OCR 可用 | ✅ 是 |
| 审计素材总数 | 37 |
| 数据来源 | runs/visual_opt_fix2_01~05/clip_records.json |

## 审计结果汇总

### 文字审计

| 状态 | 数量 | 占比 |
|------|------|------|
| passed | 36 | 97.3% |
| passed_with_exception | 1 | 2.7% |
| rejected | 0 | 0% |
| unverified | 0 | 0% |

### 产品一致性审计

| 状态 | 数量 |
|------|------|
| passed | 37 |
| rejected | 0 |
| unverified | 0 |

### 场景标签覆盖

| 场景标签 | 总数 | 安全数 | 拒绝数 | 状态 |
|----------|------|--------|--------|------|
| CTA促单 | 2 | 2 | 0 | ✅ 有安全候选 |
| 产品展示 | 2 | 2 | 0 | ✅ 有安全候选 |
| 价格促销 | 3 | 3 | 0 | ✅ 有安全候选 |
| 吹发动作 | 2 | 2 | 0 | ✅ 有安全候选 |
| 屏显调温 | 4 | 4 | 0 | ✅ 有安全候选 |
| 手持大小对比 | 4 | 4 | 0 | ✅ 有安全候选 |
| 折叠动作 | 2 | 2 | 0 | ✅ 有安全候选 |
| 护发效果 | 4 | 4 | 0 | ✅ 有安全候选 |
| 放进包包 | 1 | 1 | 0 | ✅ 有安全候选 |
| 放进行李箱 | 1 | 1 | 0 | ✅ 有安全候选 |
| 旅行场景 | 4 | 4 | 0 | ✅ 有安全候选 |
| 痛点共鸣 | 3 | 3 | 0 | ✅ 有安全候选 |
| 风力展示 | 4 | 4 | 0 | ✅ 有安全候选 |
| 风嘴配件 | 1 | 1 | 0 | ✅ 有安全候选 |

**结论：所有 14 个场景标签均有安全候选素材。**

## 唯一白名单素材

| 字段 | 值 |
|------|-----|
| asset_id | 屏显调温_003 |
| canonical_name | 屏显调温_003_温度模式_3s |
| file_name | 屏显调温_003_温度模式_3s.mp4 |
| URL | https://coze-video-assets-hlm.tos-s3-cn-beijing.volces.com/materials_v2/屏显调温_003_温度模式_3s.mp4 |
| duration_sec | 3.03 |
| native_text_type | screen_mode_split |
| has_burned_in_text | true |
| native_text_allowed | true |
| text_audit_status | passed_with_exception |
| suppress_generated_subtitle | true |
| manually_confirmed | true |
| whitelist_reason | 用户确认该分屏素材原生字幕内容正确 |

### 白名单素材 OCR 检测结果

| 采样时间 | 检测文字 | 置信度 | 位置 | 面积比 | 类型 |
|----------|----------|--------|------|--------|------|
| 0.0s | REC | 0.998 | top | 0.0011 | 平台装饰 |
| 0.0s | 4K 60FPS | 0.931 | upper | 0.0021 | 平台装饰 |
| 0.0s | HD | 0.996 | upper | 0.0008 | 平台装饰 |
| 0.0s | 冷风 | 0.999 | upper | 0.0052 | 设备屏幕 |
| 1.5s | 温风 | 0.997 | center | 0.0050 | 设备屏幕 |
| 1.5s | 热风 | 0.998 | bottom | 0.0047 | 设备屏幕 |
| 2.7s | POWE(R) | 0.974 | upper | 0.0044 | 设备屏幕 |

**说明**：白名单素材上的文字均为设备屏幕显示内容（冷风/温风/热风/POWER）和摄像机录制标记（REC/4K/HD），属于设备真实显示内容，非营销文字。

### 白名单数量确认

**白名单中只有 1 个素材**：`屏显调温_003`。无其他素材通过前缀匹配或模糊匹配加入白名单。

## 逐素材审计明细

### 安全素材清单 (36 passed + 1 passed_with_exception)

| asset_id | 场景标签 | 文字审计 | 产品一致性 | OCR检测文字 | 说明 |
|----------|----------|----------|------------|-------------|------|
| CTA促单_001 | CTA促单 | passed | passed | 无 | 无文字 |
| CTA促单_003 | CTA促单 | passed | passed | 无 | 无文字 |
| 产品展示_001 | 产品展示 | passed | passed | Creade(品牌logo) | 品牌标识 |
| 产品展示_002 | 产品展示 | passed | passed | 无 | 无文字 |
| 价格促销_001 | 价格促销 | passed | passed | 无 | 无文字 |
| 价格促销_002 | 价格促销 | passed | passed | 无 | 无文字 |
| 价格促销_003 | 价格促销 | passed | passed | 无 | 无文字 |
| 吹发动作_001 | 吹发动作 | passed | passed | Creade(OCR误读为Creoihie等) | 品牌logo误读 |
| 吹发动作_008 | 吹发动作 | passed | passed | 噪声(单字符) | 压缩伪影 |
| 屏显调温_001 | 屏显调温 | passed | passed | 设备屏幕数字 | 设备真实显示 |
| **屏显调温_003** | **屏显调温** | **passed_with_exception** | passed | REC/4K/HD/冷风/温风/热风 | **唯一白名单** |
| 屏显调温_007 | 屏显调温 | passed | passed | 设备屏幕数字 | 设备真实显示 |
| 屏显调温_008 | 屏显调温 | passed | passed | 设备屏幕数字 | 设备真实显示 |
| 手持大小对比_001 | 手持大小对比 | passed | passed | 科瑞德\|高速折叠吹风机 | 包装文字 |
| 手持大小对比_002 | 手持大小对比 | passed | passed | 无 | 无文字 |
| 手持大小对比_003 | 手持大小对比 | passed | passed | 无 | 无文字 |
| 手持大小对比_006 | 手持大小对比 | passed | passed | 无 | 无文字 |
| 折叠动作_001 | 折叠动作 | passed | passed | 无 | 无文字 |
| 折叠动作_002 | 折叠动作 | passed | passed | 无 | 无文字 |
| 护发效果_003 | 护发效果 | passed | passed | 无 | 无文字 |
| 护发效果_004 | 护发效果 | passed | passed | 无 | 无文字 |
| 护发效果_005 | 护发效果 | passed | passed | 噪声(单字符) | 压缩伪影 |
| 护发效果_006 | 护发效果 | passed | passed | 噪声(单字符) | 压缩伪影 |
| 放进包包_005 | 放进包包 | passed | passed | 噪声(数字串) | 压缩伪影 |
| 放进行李箱_005 | 放进行李箱 | passed | passed | 无 | 无文字 |
| 旅行场景_001 | 旅行场景 | passed | passed | 噪声(数字串) | 压缩伪影 |
| 旅行场景_002 | 旅行场景 | passed | passed | 视频水印(LAND/DOL) | 编辑软件水印 |
| 旅行场景_003 | 旅行场景 | passed | passed | 噪声(单字符) | 压缩伪影 |
| 旅行场景_005 | 旅行场景 | passed | passed | 噪声(数字) | 压缩伪影 |
| 痛点共鸣_001 | 痛点共鸣 | passed | passed | 噪声(单字符) | 压缩伪影 |
| 痛点共鸣_003 | 痛点共鸣 | passed | passed | 无 | 无文字 |
| 痛点共鸣_004 | 痛点共鸣 | passed | passed | 噪声(单字符) | 压缩伪影 |
| 风力展示_001 | 风力展示 | passed | passed | 无 | 无文字 |
| 风力展示_002 | 风力展示 | passed | passed | 无 | 无文字 |
| 风力展示_004 | 风力展示 | passed | passed | 无 | 无文字 |
| 风力展示_005 | 风力展示 | passed | passed | 无 | 无文字 |
| 风嘴配件_007 | 风嘴配件 | passed | passed | 无 | 无文字 |

### 拒绝素材清单

**无拒绝素材。** 所有 37 个素材均通过审计。

## 旧版 has_burned_in_text=false 误判清单

| asset_id | 旧版判定 | 实际检测结果 | 修正判定 |
|----------|----------|-------------|----------|
| 屏显调温_003 | has_burned_in_text=false | 检测到REC/4K/HD/冷风/温风/热风 | has_burned_in_text=true (白名单例外) |
| 屏显调温_001 | has_burned_in_text=false | 检测到设备屏幕数字 | has_burned_in_text=true (设备真实显示) |
| 屏显调温_007 | has_burned_in_text=false | 检测到设备屏幕数字 | has_burned_in_text=true (设备真实显示) |
| 屏显调温_008 | has_burned_in_text=false | 检测到设备屏幕数字 | has_burned_in_text=true (设备真实显示) |
| 手持大小对比_001 | has_burned_in_text=false | 检测到"科瑞德\|高速折叠吹风机" | has_burned_in_text=true (包装文字) |
| 吹发动作_001 | has_burned_in_text=false | 检测到Creade品牌logo(OCR误读) | has_burned_in_text=true (品牌标识) |
| 旅行场景_002 | has_burned_in_text=false | 检测到视频编辑水印 | has_burned_in_text=true (非营销水印) |

**说明**：旧版仅依赖文件名判断 has_burned_in_text，未进行实际帧采样OCR检测，导致多个带文字素材被误判为无文字。

## 非目标产品素材清单

**无非目标产品素材。** 所有素材产品一致性审计均为 passed。

## 证据文件

| 类型 | 路径 |
|------|------|
| 审计详情 | /workspace/projects/素材质量优化/material_audit_detail.json |
| 审计汇总 | /workspace/projects/素材质量优化/material_audit_summary.json |
| 白名单 | /workspace/projects/素材质量优化/native_text_whitelist.json |
| 安全素材 | /workspace/projects/素材质量优化/safe_assets.json |
| 拒绝素材 | /workspace/projects/素材质量优化/rejected_assets.json |
| 证据帧目录 | /workspace/projects/素材质量优化/material_audit_evidence/ |

## 阶段1结论

- ✅ 审计素材总数：37
- ✅ passed：36，passed_with_exception：1，rejected：0，unverified：0
- ✅ 所有 14 个场景标签均有安全候选素材
- ✅ 白名单中只有 1 个素材（屏显调温_003）
- ✅ 无非目标产品素材
- ✅ 无拒绝素材
- ✅ 产品核心标签均有安全素材

**阶段1通过，可进入阶段2 Smoke Test。**
