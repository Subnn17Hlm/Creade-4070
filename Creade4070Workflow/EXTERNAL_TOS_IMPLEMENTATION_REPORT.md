# 外部 TOS 接入实现报告

## 基本信息

| 项目 | 值 |
|------|-----|
| 项目名称 | Creade4070Workflow |
| 项目根目录 | `/workspace/projects/Creade4070Workflow` |
| 报告生成时间 | 2026-03-29 |
| 报告类型 | 代码接入实现（第一阶段） |
| 执行范围 | 仅代码接入，不在终端执行真实 TOS 连接测试 |

---

## 1. 环境变量配置

### 1.1 必需环境变量

| 变量名 | 用途 | 终端检查结果 |
|--------|------|-------------|
| `TOS_ACCESS_KEY` | TOS 访问密钥 | 缺失（不注入终端） |
| `TOS_SECRET_KEY` | TOS 秘密密钥 | 缺失（不注入终端） |
| `TOS_ENDPOINT` | TOS 服务端点 | 缺失（不注入终端） |
| `TOS_REGION` | TOS 区域 | 缺失（不注入终端） |
| `TOS_BUCKET` | 默认桶名 | 缺失（不注入终端） |

**说明**: 用户已确认环境变量在"项目开发环境变量"中配置，但不注入编程助手终端。代码在运行时从 `os.environ` 读取。

### 1.2 配置缺失处理

- 缺少任何必需变量时，`get_tos_config()` 抛出 `TosConfigError`
- 错误消息仅包含缺失的变量名，不包含任何敏感值
- `get_client()` 在环境变量未配置时返回 `None`，不抛出异常
- 健康检查接口返回 `env_configured: false` 和具体缺失项

---

## 2. TOS SDK 集成

### 2.1 依赖添加

**文件**: `pyproject.toml`

```toml
"tos>=2.8,<3",
```

火山引擎 TOS Python SDK 官方包。

### 2.2 客户端模块

**文件**: `src/storage/tos/tos_client.py`

| 组件 | 说明 |
|------|------|
| `TosConfig` | 配置数据类，不包含敏感值 |
| `TosConfigError` | 配置缺失异常 |
| `TosClientError` | 客户端操作异常 |
| `TosMaterialClient` | 素材存储客户端 |
| `get_client()` | 全局客户端实例（延迟初始化） |
| `resolve_material_url()` | 模块级 URL 解析函数 |
| `check_env_configured()` | 环境变量存在性检查 |
| `validate_object_key()` | 对象键前缀验证 |

### 2.3 安全约束

- 认证参数仅从环境变量读取
- 禁止将 Access Key / Secret Key 写入代码、日志、报告、CSV、Git
- 预签名 URL 仅在运行时生成，不写回 CSV
- 日志中仅记录域名和 HTTP 状态，不记录完整 URL

---

## 3. URL 解析逻辑

### 3.1 统一解析优先级

```
source_url > s3_url > TOS 预签名 URL > local_path
```

### 3.2 统一调用入口

所有节点通过 `src.storage.tos.tos_client.resolve_material_url()` 统一解析：

```python
from src.storage.tos.tos_client import resolve_material_url

url, url_type = resolve_material_url(
    source_url=row.get("source_url", ""),
    s3_url=row.get("s3_url", ""),
    bucket=row.get("bucket", ""),
    object_key=row.get("object_key", ""),
    local_path=row.get("local_path", ""),
)
```

### 3.3 已统一接入的节点

| 节点 | 文件 | 接入方式 |
|------|------|----------|
| 素材匹配 (Node4) | `material_matching_node.py` | `_resolve_material_url()` 调用 `resolve_material_url()` |
| 素材源审计 (Node3) | `material_source_audit_node.py` | 直接调用 `resolve_material_url()` |
| clip 截取 (Node5) | `clip_extraction_node.py` | 通过 `selected_url` 从 Node4 传递 |

### 3.4 前缀安全校验

`object_key` 必须以 `materials_v2/` 开头，否则跳过 TOS 预签名步骤。

---

## 4. 健康检查接口

### 4.1 接口定义

| 属性 | 值 |
|------|-----|
| 路径 | `GET /internal/tos-health` |
| 认证 | 无（内部接口） |
| 超时 | 10 秒 |

### 4.2 响应格式

```json
{
    "env_configured": true,
    "env_details": {
        "TOS_ACCESS_KEY": true,
        "TOS_SECRET_KEY": true,
        "TOS_ENDPOINT": true,
        "TOS_REGION": true,
        "TOS_BUCKET": true
    },
    "head_ok": true,
    "range_ok": true,
    "error_type": ""
}
```

### 4.3 检查流程

1. 检查 5 个环境变量是否存在
2. 初始化 TOS 客户端
3. 从 CSV 选取第一个有效测试对象
4. HEAD 检查对象是否存在
5. 生成 60 秒预签名 URL
6. HTTP Range 请求读取前 1024 字节

### 4.4 安全约束

- 不返回密钥值
- 不返回完整预签名 URL
- 不返回请求签名
- 仅返回状态摘要

---

## 5. 修改文件清单

### 5.1 新增文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/storage/tos/__init__.py` | 0 | 模块初始化 |
| `src/storage/tos/tos_client.py` | 310 | TOS 客户端模块 |

### 5.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `pyproject.toml` | 添加 `tos>=2.8,<3` 依赖 |
| `src/main.py` | 添加 `GET /internal/tos-health` 接口 |
| `src/graphs/nodes/material_matching_node.py` | `_resolve_material_url()` 和 `_get_presigned_url()` 改用 TOS 客户端 |
| `src/graphs/nodes/material_source_audit_node.py` | URL 解析改用 `resolve_material_url()` |

---

## 6. Python 语法编译结果

| 检查范围 | 通过 | 失败 |
|----------|------|------|
| `src/` + `scripts/` | **42** | **0** |

全部通过。

---

## 7. 待运行时验证项

以下项目需要部署环境中的真实 TOS 凭据才能验证：

| 项目 | 验证方式 | 当前状态 |
|------|----------|----------|
| TOS 客户端初始化 | `get_client()` | 待验证 |
| HEAD 对象检查 | `client.head_object()` | 待验证 |
| 预签名 URL 生成 | `client.generate_presigned_url()` | 待验证 |
| Range 读取测试 | HTTP Range 请求 | 待验证 |
| 126 条素材完整性 | 批量 HEAD 检查 | 待验证 |

### 7.1 验证步骤

1. 部署项目到运行环境
2. 访问 `GET /internal/tos-health` 查看连接状态
3. 如 `head_ok: true` 且 `range_ok: true`，可进入单条冒烟测试
4. 如有失败，根据 `error_type` 排查

---

## 8. 是否具备冒烟测试条件

| 条件 | 状态 |
|------|------|
| TOS SDK 已加入依赖 | 已完成 |
| 客户端代码已实现 | 已完成 |
| URL 解析逻辑已统一 | 已完成 |
| 健康检查接口已添加 | 已完成 |
| 语法编译全部通过 | 已完成 |
| 环境变量已配置（用户确认） | 已确认 |
| TOS 连接实际验证 | **待部署后验证** |

**结论**: 代码接入完成，部署后可通过 `/internal/tos-health` 验证连接状态。如健康检查全部通过，即可进入单条工作流冒烟测试。

---

## 附录 A: TOS 客户端 API 参考

### TosMaterialClient

```python
# 初始化
client = TosMaterialClient()  # 从环境变量读取配置

# HEAD 检查
result = client.head_object(bucket="coze-video-assets-hlm", object_key="materials_v2/001_片头.mp4")
# result = {"exists": True, "content_length": 1234567, "content_type": "video/mp4", "error_type": ""}

# 生成预签名 URL
url = client.generate_presigned_url(bucket="...", object_key="...", expires=1800)

# 统一 URL 解析
url, url_type = client.get_material_url(
    source_url="", s3_url="", bucket="...", object_key="...", local_path=""
)
```

### 模块级函数

```python
from src.storage.tos.tos_client import (
    get_client,              # 获取全局客户端实例
    resolve_material_url,    # 统一 URL 解析
    check_env_configured,    # 检查环境变量
    is_env_configured,       # 是否全部配置
    validate_object_key,     # 验证对象键前缀
)
```

---

*报告结束*
