# FS MCP 图片读取与 AI Assistant 多模态工具结果端到端工作文档

> 状态：待实施
> 创建时间：2026-08-06
> 涉及仓库：
>
> - FS MCP Server：`/Users/kongkongye/data/project/ai-device-fs`
> - AI Assistant：`/Users/kongkongye/data/project/ai-assistant`
>
> 本文档用于在全新会话中直接接续实施。实施时不要只完成 FS MCP 返回图片；最终目标是让 OpenAI Responses 模型真正收到并理解工具返回的图片，同时保证会话保存、重载和前端展示闭环。

## 1. 最终目标

新增独立 MCP 工具：

```text
read_image(path, host=None, max_bytes=5242880)
```

完整链路：

```text
模型调用 fs__read_image
→ AI Assistant 调用本地 FS MCP Server
→ FS MCP 读取本地或远程图片
→ FS MCP 返回标准 MCP TextContent + ImageContent
→ AI Assistant 保留并持久化多模态工具结果
→ OpenAI Responses API 使用多模态 function_call_output
→ 视觉模型可以描述、分析图片
→ 前端可以在工具结果中展示图片
→ 会话重载后图片结果和后续模型上下文仍然正确
```

## 2. 范围与非目标

### 2.1 本次范围

1. 在 `ai-device-fs` 新增独立 `read_image` 工具。
2. 支持本地图片读取。
3. 支持现有 SSH Host 远程图片读取。
4. 返回标准 MCP `ImageContent`，不把图片降级为乱码文本或普通 Base64 JSON。
5. 收口 `read_file` 为 UTF-8 文本工具，移除误导性的 `bytes` 模式。
6. 在 `ai-assistant` 中保留 MCP 多内容块工具结果。
7. 将图片工具结果转换为 OpenAI Responses API 支持的多模态 `function_call_output.output`。
8. 持久化并恢复图片工具结果。
9. 前端工具结果中显示图片。
10. 补齐单元、集成和真实端到端测试。

### 2.2 明确非目标

1. 不扩展 `read_file` 使其根据文件类型返回不同结构。
2. 不支持 PDF、音频、视频；未来使用独立工具。
3. 不支持 SVG。
4. 不支持 GIF、APNG、动画 WebP 等动画图片。
5. 不做图片修改、压缩、裁剪、OCR 或图片生成。
6. 不在本阶段实现 OpenAI 托管 Remote MCP 的图片返回；本项目采用“OpenAI function tool → AI Assistant 本地调用 MCP → function_call_output”链路。
7. 不承诺旧版 Chat Completions 或其他模型 Provider 能接收图片工具结果；本阶段必须完整支持 OpenAI Responses。其他 Provider 只能做明确降级或后续独立实现，不能伪装为已支持。

## 3. 当前基线事实

### 3.1 FS MCP 当前只能可靠读取文本

文件：`mcp_server_fs/tools/read_file.py`

当前 `type="bytes"` 虽然用 `rb` 读取，但随后执行：

```python
content = content_bytes.decode("utf-8", errors="replace")
```

因此：

- PNG/JPEG 等二进制数据会被破坏；
- 返回值仍是普通 JSON 文本；
- 没有 `ImageContent`、MIME、Base64 图片块；
- 不能让视觉模型看到图片。

远程实现位于 `mcp_server_fs/remote/remote_script.py`，复制了相同行为。

### 3.2 FastMCP/MCP 已支持图片内容

当前环境：

- `fastmcp>=3.1.0`
- `mcp.types.ImageContent`
- `fastmcp.tools.tool.ToolResult`

标准图片块：

```python
ImageContent(
    type="image",
    data=base64_data,
    mimeType="image/png",
)
```

`ToolResult.content` 可以是多个 MCP 内容块。

### 3.3 AI Assistant 当前会丢失多模态语义

关键位置：

- `backend/src/integrations/server_pool_core.py`
- `backend/src/models.py`
- `backend/src/assistant/agent_result_mixin.py`
- `backend/src/llm/openai_responses_model.py`

当前行为：

1. `server_pool_core.py` 将 MCP `result.content` 放入内部 `ToolResult.result`。
2. `_format_tool_result()` 会把内容块递归转成 JSON 字符串。
3. 工具消息只设置 `Message.content`，没有设置图片 `content_parts`。
4. OpenAI Responses 转换工具消息时固定发送：

```python
{
    "type": "function_call_output",
    "call_id": call_id,
    "output": project_message_content(message),
}
```

也就是只发送字符串。

结果是 MCP 图片即使成功返回，也会变成带 Base64 的巨型文本，而不是视觉输入。

### 3.4 AI Assistant 已有用户图片基础能力

当前已有：

- `Message.content_parts`
- `image_url` Data URL 持久化
- OpenAI Responses `input_image` 转换
- 前端图片展示
- Pillow 图片校验和压缩

现有图片限制位于：

`backend/src/web/routes/image_processing.py`

```python
MAX_IMAGE_PARTS = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024
```

本方案的 `read_image` 单图上限统一为 **5 MB**，避免 FS MCP 和 AI Assistant 契约冲突。

## 4. 冻结设计决策

以下决策已经确定，实施时不要重新改成其他方向，除非发现真实技术阻塞并先向用户报告。

### 4.1 使用独立 `read_image`

职责：

```text
read_file  → UTF-8 文本
read_image → 图片视觉输入
```

原因：

- 返回类型不同；
- 图片不适用 `lines/offset/direction/max_chars`；
- 独立工具更容易被模型正确选择；
- 后续图片校验、尺寸、动画限制不会污染文本工具。

### 4.2 支持格式

首期只支持：

| 文件内容格式 | MIME |
|---|---|
| PNG | `image/png` |
| JPEG | `image/jpeg` |
| WebP（静态） | `image/webp` |

规则：

1. 以文件真实内容为准，不信任扩展名。
2. 扩展名与内容不一致但内容有效时允许读取，并返回真实 MIME。
3. 动画图片统一拒绝，包括 APNG 和动画 WebP。
4. SVG、GIF、BMP、TIFF、HEIC 等统一返回不支持错误。

### 4.3 大小与像素限制

常量：

```python
DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024
HARD_MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
```

规则：

- `max_bytes` 允许调用方降低限制，不允许超过硬上限。
- 空文件拒绝。
- 在完整读取前先通过 `stat()` 拒绝超限文件。
- 图片解码后检查 `width * height <= MAX_IMAGE_PIXELS`，避免小文件触发超大解码内存。
- 不对图片做隐式压缩或格式转换；模型看到的是原始受支持图片。

### 4.4 返回内容

成功结果必须同时包含：

1. 一个简短 `TextContent`，用于模型和 UI 了解文件信息；
2. 一个标准 `ImageContent`，用于视觉输入；
3. `structured_content`，只保存图片元数据，不重复保存 Base64；
4. `_meta`/`meta`，供工具展示和诊断。

示例：

```python
ToolResult(
    content=[
        TextContent(
            type="text",
            text="Image example.png: image/png, 1280×720, 245760 bytes",
        ),
        ImageContent(
            type="image",
            data=base64_data,
            mimeType="image/png",
        ),
    ],
    structured_content={
        "path": "/abs/example.png",
        "mime_type": "image/png",
        "width": 1280,
        "height": 720,
        "size": 245760,
        "animated": False,
    },
    meta={
        "summary": "read image example.png",
        "path": "/abs/example.png",
        "mime_type": "image/png",
        "width": 1280,
        "height": 720,
        "size": 245760,
        "execution_mode": "local",
    },
)
```

要求：

- `ImageContent.data` 只放纯 Base64，不带 Data URL 前缀。
- `mimeType` 使用 MCP 字段名。
- 不在文本、`structured_content` 或 `meta` 中复制 Base64。

### 4.5 错误语义

错误返回为单个文本内容块中的结构化 JSON，并设置 `is_error=True`。

统一错误结构：

```json
{
  "success": false,
  "error": {
    "code": "IMAGE_TOO_LARGE",
    "message": "Image exceeds the allowed size",
    "details": {
      "path": "...",
      "size": 6000000,
      "max_bytes": 5242880
    }
  }
}
```

错误码：

| 错误码 | 场景 |
|---|---|
| `IMAGE_INVALID_ARGUMENT` | `max_bytes` 等参数非法 |
| `IMAGE_NOT_FOUND` | 文件不存在 |
| `IMAGE_NOT_A_FILE` | 路径不是普通文件 |
| `IMAGE_EMPTY` | 空文件 |
| `IMAGE_TOO_LARGE` | 超过大小限制 |
| `IMAGE_TOO_MANY_PIXELS` | 解码尺寸超过像素限制 |
| `IMAGE_FORMAT_UNSUPPORTED` | 格式不支持 |
| `IMAGE_ANIMATED_UNSUPPORTED` | 动画图片 |
| `IMAGE_INVALID` | 文件损坏或不能解码 |
| `IMAGE_READ_ERROR` | 本地读取失败 |
| `REMOTE_IMAGE_READ_ERROR` | 远程图片读取失败 |
| `REMOTE_PROTOCOL_ERROR` | 远程返回结构非法 |

远程 SSH/配置错误继续沿用现有错误码，如 `REMOTE_SSH_ERROR`、`REMOTE_TIMEOUT`。

## 5. 工具接口

```python
def read_image(
    path: str,
    host: str | None = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> ToolResult:
    """读取本地或远程图片，并作为标准 MCP 图片内容返回。"""
```

参数说明：

| 参数 | 说明 |
|---|---|
| `path` | 本地路径；传 `host` 时表示远程主机路径 |
| `host` | 系统 `~/.ssh/config` 中的 SSH alias |
| `max_bytes` | 调用级大小限制，默认和硬上限均为 5 MB，只允许降低 |

不提供：

- `offset`
- `limit`
- `direction`
- `max_chars`
- 图片压缩参数

## 6. FS MCP Server 实施方案

仓库：`/Users/kongkongye/data/project/ai-device-fs`

### 6.1 预计新增文件

```text
mcp_server_fs/image_utils.py
mcp_server_fs/tools/read_image.py
tests/core/test_read_image.py
```

可按现有测试目录风格调整测试文件名，但不要把所有图片测试继续堆入 `test_tool_behaviors.py`。

### 6.2 `image_utils.py` 职责

集中实现：

1. 图片大小常量。
2. 支持格式和 MIME 映射。
3. `ImageReadError`（包含 `code/message/details`）。
4. 参数校验。
5. 图片字节验证。
6. Pillow 格式识别。
7. 静态/动画识别。
8. 像素限制。
9. Base64 编码。
10. 成功 `ToolResult` 和错误 `ToolResult` 构造辅助。

建议内部数据模型：

```python
@dataclass(frozen=True)
class ImageInfo:
    data: bytes
    mime_type: str
    format: str
    width: int
    height: int
    size: int
    animated: bool
```

验证流程：

```text
校验 max_bytes
→ validate_path
→ exists/is_file/stat
→ 读取原始 bytes
→ Pillow Image.open
→ 读取 format/size/n_frames/is_animated
→ 校验支持格式
→ 校验非动画
→ 校验像素数
→ verify() 检查完整性
→ Base64
→ ToolResult
```

注意：

- `verify()` 后如仍需读取属性，应重新 `Image.open(BytesIO(data))`，不要复用已 verify 的对象。
- 捕获 Pillow 解码异常并映射到稳定错误码，不向用户泄漏无意义堆栈。
- MIME 由 Pillow 识别格式映射，不用 `mimetypes.guess_type(path)` 作权威判断。

### 6.3 Pillow 依赖

当前 FS MCP 没有 Pillow。使用项目规定的 `uv` 添加：

```bash
cd /Users/kongkongye/data/project/ai-device-fs
uv add pillow
```

同步检查：

- `pyproject.toml`
- `uv.lock`

不要使用 `pip`。

### 6.4 注册工具

修改：

```text
mcp_server_fs/tools/__init__.py
```

加入：

```python
"read_image": register_read_image_tool(mcp)
```

最终对客户端暴露完整名：

```text
fs__read_image
```

实际前缀由 AI Assistant 中 MCP Server 名称 `fs` 决定；MCP Server 内工具名保持 `read_image`。

### 6.5 收口 `read_file`

修改本地与远程接口：

```python
type: Literal["chars", "lines"] = "chars"
```

移除：

- `bytes` 分支；
- `bytes` 类型文档；
- “媒体文件可通过 read_file 读取”的描述。

`read_file` 明确只处理 UTF-8 文本。

对二进制文件使用 `chars/lines` 时继续返回明确的 `UnicodeDecodeError` 文本错误；不要自动转给 `read_image`，避免工具返回类型突然变化。

### 6.6 README 和技能文档

修改 FS 仓库：

```text
README.md
```

至少更新：

- 功能特性改为“UTF-8 文本读写 + 图片读取”；
- `read_file` 参数不再包含 `bytes`；
- 新增 `read_image` 参数、格式和限制；
- 说明图片使用标准 MCP `ImageContent`；
- 说明本地/远程行为一致。

AI Assistant 全局技能也需要在集成阶段同步：

```text
~/.ai-assistant/skills/fs-usage-guide/SKILL.md
```

但该目录属于用户全局配置，不要在没有检查变更归属时盲改。若实施会话确认应更新，则将 `read_image` 用法和 `read_file` 新参数同步进去。

## 7. 远程 Host 实施方案

### 7.1 原则

远程主机只要求：

- Linux；
- SSH 可达；
- 安装 `python3`。

不要求远程安装 Pillow。远程端负责：

1. 路径校验；
2. 普通文件校验；
3. `stat` 大小限制；
4. 读取原始 bytes；
5. Base64 编码；
6. 返回给本地 FS MCP。

本地 FS MCP 解码 Base64 后使用 Pillow完成真实格式、动画、尺寸、像素和完整性校验。

### 7.2 远程协议改造

当前协议只支持：

```json
{
  "success": true,
  "tool_result_content": "text",
  "meta": {}
}
```

需要改成内部类型化结果：

```json
{
  "success": true,
  "tool_result": {
    "kind": "text",
    "content": "..."
  },
  "meta": {}
}
```

图片：

```json
{
  "success": true,
  "tool_result": {
    "kind": "image",
    "data": "<base64>",
    "size": 245760
  },
  "meta": {
    "path": "/remote/example.png"
  }
}
```

要求：

- `kind` 只能为 `text` 或 `image`；未知类型 fail closed。
- 文本工具统一转换到 `kind=text`，不要同时保留两套长期协议。
- `read_image` 远程端不自行声称 MIME，以本地 Pillow 验证结果为准。
- 本地校验 Base64 必须使用严格模式，例如 `base64.b64decode(data, validate=True)`。
- 解码后的字节长度必须与远程 `size` 一致，否则 `REMOTE_PROTOCOL_ERROR`。
- 远程端在读取前检查 5 MB 上限，防止超大 stdout。

### 7.3 预计修改文件

```text
mcp_server_fs/remote/remote_script.py
mcp_server_fs/remote/remote_backend.py
mcp_server_fs/remote/ssh_runner.py（如需要协议/输出保护）
tests/remote/test_host.py
tests/remote/test_infra.py
tests/remote/test_integration.py
```

新增：

```python
remote_read_image(...)
```

本地与远程必须复用同一 `image_utils` 验证和 ToolResult 构造逻辑，避免格式规则漂移。

## 8. AI Assistant 端到端集成方案

仓库：`/Users/kongkongye/data/project/ai-assistant`

开始修改前必须遵守该仓库：

- `.ai-assistant/rules/operational-safety.md`
- `PROJECT_GUIDE.md`
- `rule/CODING_STANDARDS.md`
- 后端改动前阅读 `backend/README.md`
- 前端改动前阅读 `web/README.md`

### 8.1 内部内容模型

目标：MCP 内容块不能在进入 Agent 后立即变成字符串。

推荐复用 `Message.content_parts` 作为持久化和 UI 的通用多模态内容字段，不另建重复字段。

MCP 到内部格式映射：

```text
MCP TextContent
→ {"type": "text", "text": "..."}

MCP ImageContent
→ {
     "type": "image_url",
     "image_url": {
       "url": "data:image/png;base64,...",
       "detail": "auto",
       "original_filename": "example.png",
       "original_mime_type": "image/png",
       "original_bytes": 245760,
       "source": "tool_result"
     }
   }
```

关键规则：

1. `Message.content` 只保存文本块拼接后的可读文本，不放 Base64。
2. `Message.content_parts` 保存文本和图片内容。
3. 工具消息 `role="tool"` 允许拥有 `content_parts`。
4. 工具失败结果不产生图片部分。
5. MCP `ImageContent.data` 必须严格 Base64 校验。
6. MIME 只允许 `image/png`、`image/jpeg`、`image/webp`。
7. 解码后单图不得超过 5 MB。
8. 一个工具结果首期只允许一张图片；若未来支持多图，再统一套用总数/总大小限制。

### 8.2 `ToolResult` 调整

当前：

```python
@dataclass
class ToolResult:
    result: Any
    structured_content: Optional[Dict[str, Any]]
```

建议新增 provider-neutral 字段：

```python
content_parts: Optional[List[Dict[str, Any]]] = None
```

职责：

- `result`：保留文本/结构化运行结果兼容用途；
- `content_parts`：标准化后的文本、图片多模态内容；
- `structured_content`：MCP structuredContent；
- 不要求 Agent 和 LLM 层理解 `mcp.types.ImageContent` 对象。

不要把 MCP SDK 类型直接扩散到核心模型和持久化层。

### 8.3 Server Pool 标准化

修改：

```text
backend/src/integrations/server_pool_core.py
```

在 MCP `CallToolResult` 返回后：

1. 遍历 `result.content`；
2. 识别 TextContent/ImageContent；
3. 转换为内部 `content_parts`；
4. 文本块组成 `ToolResult.result` 的文本投影；
5. 图片 Base64 不进入日志；
6. 未知内容类型明确拒绝或转成有限文本描述，不能静默序列化任意大对象；
7. `structuredContent` 保持现有读取逻辑。

建议新增独立标准化模块，避免 `server_pool_core.py` 继续膨胀，例如：

```text
backend/src/integrations/mcp_content.py
```

至少包含：

```python
normalize_mcp_tool_content(...)
validate_tool_image_part(...)
```

### 8.4 构造工具消息

修改：

```text
backend/src/assistant/agent_result_mixin.py
```

`_build_tool_result_message()` 应设置：

```python
Message(
    role="tool",
    content=text_projection,
    content_parts=tool_result.content_parts,
    ...
)
```

持久化要求：

- 完整策略下，图片 Data URL 进入 `content_parts`，从而支持会话重载和后续模型继续推理；
- `persisted_content` 只投影文本，不复制 Base64；
- `summary_only` 工具不得持久化原图，必须清除图片 `content_parts` 或只保存安全摘要；
- `read_image` 默认沿用外部 MCP 工具的 `full` 持久化策略。

必须检查：

```text
backend/src/tool_persistence.py
backend/src/runtime/branches/message_codec.py
backend/src/runtime/session_store_postgres_state_normalized_decompose.py
backend/src/runtime/session_store_postgres_state_normalized_load.py
backend/src/web/routes/session_read_routes.py
```

现有 `Message.content_parts` 已进入消息 codec，但需验证 tool 角色不会被过滤或丢失。

### 8.5 OpenAI Responses 转换

修改：

```text
backend/src/llm/openai_responses_model.py
```

当前工具输出固定是字符串。新增专用转换方法：

```python
_convert_tool_output(message: Message) -> str | list[dict[str, Any]]
```

无图片时保持字符串：

```json
{
  "type": "function_call_output",
  "call_id": "call-1",
  "output": "tool text"
}
```

有图片时：

```json
{
  "type": "function_call_output",
  "call_id": "call-1",
  "output": [
    {
      "type": "input_text",
      "text": "Image example.png: image/png, 1280×720, 245760 bytes"
    },
    {
      "type": "input_image",
      "image_url": "data:image/png;base64,...",
      "detail": "auto"
    }
  ]
}
```

注意：

- 不复用当前 `_convert_message_content()` 的“图片只允许 user role”限制；工具输出应走独立方法。
- 保持 function call 的 `call_id` 配对、去重和闭合校验。
- Base64 只能出现在发给 Provider 的请求体和必要的持久化字段，不进入普通日志或错误信息。

### 8.6 其他 Provider 的行为

本阶段最低要求：

- OpenAI Responses：完整支持。
- OpenAI Chat Completions：如果接口不支持图片 tool message，必须明确降级为文本并记录可诊断信息，不能把 Base64 当普通工具文本发送。
- Anthropic：不在本阶段强制实现；若现有协议天然支持且改动很小，可另立阶段，不能阻塞 OpenAI Responses 主链路。

如果当前模型配置不是支持图片工具结果的 Provider，系统应给模型可读文本：

```text
The tool returned an image, but the active model adapter does not support image tool outputs.
```

同时 UI 仍可显示图片；不要丢失用户可见结果。

### 8.7 前端展示

重点检查：

```text
web/src/types.ts
web/src/components/chat/MessageContent.tsx
web/src/components/chat/MessageItemParts.tsx
web/src/components/chat/toolCallPresentation.ts
```

要求：

1. 工具消息的 `content_parts` 能显示图片缩略图。
2. 点击可查看原图。
3. 工具结果卡片仍显示工具名、摘要和成功状态。
4. 不在页面上渲染 Base64 文本。
5. 会话重载后展示一致。
6. 移动端不产生超宽布局。

尽量复用现有 `MessageContent` 图片组件，不新造第二套图片预览体系。

## 9. 分阶段实施与门禁

必须按阶段推进，每阶段完成测试和限定审核后再进入下一阶段。

### 阶段 0：实施前盘点

目标：确认两个仓库当前状态和变更归属。

操作：

1. 检查两个仓库 `git status --short --branch`。
2. 区分其他会话或用户的现有变更，不覆盖、不暂存、不提交无关内容。
3. 阅读两个仓库相关 README 和项目规则。
4. 重新核对 FastMCP `ToolResult/ImageContent` 当前版本接口。
5. 核对 OpenAI SDK/OpenAPI 当前 `function_call_output.output` 图片数组契约。

门禁：

- 变更边界清楚；
- 没有需要用户先决策的架构冲突；
- 输出阶段计划并只进入阶段 1。

### 阶段 1：FS MCP 本地 `read_image`

范围：

- Pillow 依赖；
- `image_utils.py`；
- `read_image.py`；
- 工具注册；
- 本地测试；
- `read_file` 移除 `bytes`；
- README 更新。

验收：

- PNG/JPEG/静态 WebP 返回 TextContent + ImageContent；
- MIME、尺寸、大小、Base64 正确；
- 假扩展名按内容识别；
- 动画、损坏、不支持格式、空文件、目录、不存在、超限、超像素均稳定失败；
- `read_file` 只暴露 `chars/lines`；
- FS MCP 全量本地测试通过。

建议测试：

```bash
cd /Users/kongkongye/data/project/ai-device-fs
uv run pytest tests/core -q
uv run pytest -q
```

阶段结束：限定审核 FS 本地图片工具和文本工具收口，不进入远程实现。

### 阶段 2：FS MCP 远程图片

范围：

- 类型化远程返回协议；
- 远程 `read_image`；
- 本地严格解析和验证；
- 远程测试；
- 文档补充。

验收：

- mock SSH 成功返回图片；
- Base64、size、kind、缺字段、未知类型均有协议测试；
- 超限在远程读取前失败；
- 本地/远程图片元数据和错误语义一致；
- 现有远程文本工具不回归；
- 有 `test` SSH alias 时真实远程集成通过，否则测试明确 skip。

建议测试：

```bash
cd /Users/kongkongye/data/project/ai-device-fs
uv run pytest tests/remote/test_host.py tests/remote/test_infra.py -q
uv run pytest tests/remote/test_integration.py -q
uv run pytest -q
```

阶段结束：限定审核远程协议和图片传输，不进入 AI Assistant。

### 阶段 3：AI Assistant 后端多模态工具结果

范围：

- 核心 `ToolResult.content_parts`；
- MCP 内容标准化；
- 工具消息构造和持久化；
- OpenAI Responses 转换；
- 后端测试。

验收：

- MCP ImageContent 不再被序列化成普通 Base64 文本；
- 工具消息保存文本投影 + 图片 content_parts；
- OpenAI Responses 请求中图片位于 `function_call_output.output[].type=input_image`；
- 无图工具结果格式完全不回归；
- 会话保存/重载保留工具图片；
- summary-only 不持久化图片；
- 日志、Hook 投影和错误中不出现完整 Base64。

重点测试文件：

```text
backend/tests/test_server_pool.py
backend/tests/test_agent.py
backend/tests/test_openai_responses_model.py
backend/tests/test_session_store_postgres.py
backend/tests/test_tool_execution_complete_hook.py
```

建议测试：

```bash
cd /Users/kongkongye/data/project/ai-assistant/backend
uv run pytest tests/test_server_pool.py -q
uv run pytest tests/test_agent.py -q
uv run pytest tests/test_openai_responses_model.py -q
uv run pytest tests/test_session_store_postgres.py -q
uv run ruff check src tests
```

根据影响面再运行后端全量：

```bash
uv run pytest
```

阶段结束：限定审核多模态数据结构、持久化和 Provider 转换。

### 阶段 4：前端与真实端到端验收

范围：

- 工具结果图片展示；
- 前端测试；
- 实际 MCP/模型调用；
- 重载恢复验收。

前端测试：

```bash
cd /Users/kongkongye/data/project/ai-assistant/web
pnpm test:run
```

真实验收步骤：

1. 准备小于 5 MB 的 PNG/JPEG/WebP fixture。
2. 让模型调用 `fs__read_image`。
3. 检查工具结果 UI 显示图片而非 Base64。
4. 检查 OpenAI Responses 请求构造为多模态 function_call_output。
5. 模型正确描述图片内容。
6. 刷新页面或重载会话，图片仍可显示。
7. 继续发送追问，模型仍可基于历史工具图片回答。
8. 验证错误图片不会污染后续上下文。

## 10. 测试用例清单

### 10.1 FS 本地

- 有效 PNG。
- 有效 JPEG。
- 有效静态 WebP。
- 大小等于 5 MB 边界。
- 大小超过 5 MB。
- 调用方将 `max_bytes` 降低后触发限制。
- `max_bytes <= 0`。
- `max_bytes > HARD_MAX_IMAGE_BYTES`。
- 文件不存在。
- 路径是目录。
- 空文件。
- 损坏 PNG/JPEG/WebP。
- PNG 内容但扩展名为 `.txt`。
- JPEG 内容但扩展名为 `.png`。
- GIF。
- SVG。
- APNG/动画 WebP。
- 超过像素限制。
- Base64 解码后与原始 bytes 一致。
- `ImageContent.mimeType` 正确。
- `structured_content/meta` 不含 Base64。

### 10.2 FS 远程

- 成功图片结果。
- 不存在。
- 目录。
- 超限。
- SSH 错误。
- 超时。
- 无 Python。
- 返回非法 JSON。
- 缺少 `tool_result`。
- `kind` 未知。
- 图片 Base64 非法。
- 图片 `size` 与解码结果不一致。
- 远程返回损坏图片，本地 Pillow 拒绝。
- 原有 read/write/edit/insert/apply_patch 远程回归。

### 10.3 AI Assistant 后端

- TextContent-only 保持字符串工具输出。
- TextContent + ImageContent 标准化。
- ImageContent-only 自动生成最小文本投影或保持可诊断文本。
- 非法 Base64 拒绝。
- 不支持 MIME 拒绝。
- 超过 5 MB 拒绝。
- 工具消息 content_parts 持久化与重载。
- OpenAI Responses 工具图片转换。
- call_id 配对不变。
- 多轮历史重放不重复工具输出。
- summary-only 图片不持久化。
- Hook/SSE/日志不泄漏完整 Base64。

### 10.4 前端

- 工具结果缩略图显示。
- 点击预览。
- 文本说明显示。
- Base64 不作为文本渲染。
- 失败结果不显示破损图片。
- 会话刷新后图片显示。
- 手机端布局正常。

## 11. 安全与性能要求

1. 不记录完整 Base64 到普通日志。
2. 不把完整 Base64 放入错误 `details`、Hook 文本摘要或工具 summary。
3. 读取前先 `stat` 限制，减少不必要内存。
4. Base64 会增加约 33% 体积；5 MB 原图约形成 6.7 MB 字符串，测试内存和数据库写入路径。
5. 不同时保留多份 Base64 副本；运行时尽量只保留 MCP 原始块和规范化 Data URL 中必要的一份。
6. 图片必须实时随工具消息保存，遵守 AI Assistant 会话实时持久化要求。
7. 不引入针对可信本地插件的过度沙箱；只做格式、大小、路径、协议和资源限制。
8. 不读取或打印敏感凭证。

## 12. 文档和配置联动

完成后检查：

### FS MCP

- `README.md`
- `pyproject.toml`
- `uv.lock`
- `docs/REMOTE_HOST_DESIGN.md`（若远程协议说明受影响）

### AI Assistant

- `backend/README.md`（若有工具多模态结果说明入口）
- `web/README.md`（若前端展示契约需要说明）
- `web/src/types.ts`
- `~/.ai-assistant/skills/fs-usage-guide/SKILL.md`
- 模板中的 `base_tools`：只有需要默认开放 `fs__read_image` 时才改；否则工具可通过发现/激活使用。

默认建议将 `fs__read_image` 与 `fs__read_file` 一样加入开发模板基础工具，因为它是通用只读文件能力。但这是产品级默认工具集合变化，实施时先检查当前模板管理方式和 token/工具数量影响，再做最小联动。

## 13. Git、服务与操作限制

1. 两个仓库分别检查 Git 状态和变更归属。
2. 不使用 `git add .`。
3. 未经用户明确要求，不自动提交。
4. 未经用户本次明确授权，不执行 `git push`。
5. 不停止真实 AI Assistant 服务。
6. 若安装/更新 FS MCP 后必须重启真实 AI Assistant 才能加载新工具：
   - 先完成全部不依赖重启的修改、测试和状态保存；
   - 向用户说明原因、影响和恢复检查；
   - 获得本次明确确认；
   - 将 `ai service restart` 作为该轮最后一个、独立的 Bash 工具调用；
   - 重启后在下一轮优先验证服务、工具列表和真实图片链路。

## 14. 完成定义

只有同时满足以下条件，才能宣布任务完成：

- [ ] `fs__read_image` 已注册并可调用。
- [ ] 本地 PNG/JPEG/WebP 返回标准 MCP ImageContent。
- [ ] 远程 Host 图片读取可用。
- [ ] `read_file` 已收口为 UTF-8 文本工具。
- [ ] FS MCP 全量测试通过。
- [ ] AI Assistant 不再把图片工具结果降级成 Base64 文本。
- [ ] OpenAI Responses 收到多模态 function_call_output。
- [ ] 视觉模型能正确分析工具返回图片。
- [ ] 工具图片结果实时持久化并可重载。
- [ ] 前端能在工具结果中显示图片。
- [ ] 普通文本工具调用没有回归。
- [ ] 日志、Hook 和错误信息未泄漏完整 Base64。
- [ ] 两个仓库的受影响测试、lint 和最终集成验收通过。
- [ ] 文档与实际接口一致。

## 15. 新会话启动指令建议

在全新会话中可直接发送：

```text
请读取并严格按照以下工作文档分阶段实施：
/Users/kongkongye/data/project/ai-device-fs/docs/READ_IMAGE_END_TO_END_WORK_PLAN.md

先完成阶段 0 盘点并汇报，不要跳过阶段门禁，不要自动重启真实服务，不要提交或推送代码，除非我后续明确要求。
```
