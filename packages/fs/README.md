# Filesystem MCP Server

本包已迁入 ai-device 单仓库；distribution 已改名，Python import 和命令保持不变。当前仅本地构建，未发布；MCP 仍为可选 extra。

基于 FastMCP 3.1.0 的文件系统 MCP 服务器，提供安全的文件系统访问能力。

## 功能特性

- UTF-8 文本读写（支持分段读取、原子写入、diff 预览）
- PNG、JPEG、静态 WebP 图片读取（标准 MCP `ImageContent`）
- 大文件处理（自动保存到临时文件）
- 双传输模式（stdio / HTTP SSE）

## 环境要求

Python >= 3.10。默认安装只包含本地执行核心及 Pillow；运行 MCP 服务需启用 `mcp` extra（FastMCP >= 3.1.0）。

## 快速开始

### 安装运行

```bash
# stdio 模式（适合 Claude Desktop/Kiro）
uv run --extra mcp python -m mcp_server_fs

# SSE 模式（适合 Web 应用）
uv run --extra mcp python -m mcp_server_fs --transport sse --port 8000
```

### 配置文件

复制 `.env.example` 为 `.env` 并修改：

```bash
MCP_TRANSPORT=stdio    # stdio 或 sse
MCP_HOST=127.0.0.1     # SSE 模式主机
MCP_PORT=8000          # SSE 模式端口
MCP_TEMP_DIR=~/.ai-device-fs  # 临时文件目录
```

配置优先级：命令行参数 > 环境变量 > 默认值

兼容入口说明：

- 推荐入口：`python -m mcp_server_fs`
- 脚本入口：`ai-device-fs`
- 根目录 `main.py` 仍可运行，但仅作为兼容入口保留；新增代码不要再依赖它导出的内部 helper 或公共工具函数

## 客户端集成

### Claude Desktop / Kiro（stdio）

在 `mcp.json` 中添加：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "uv",
      "args": ["run", "--extra", "mcp", "--directory", "/path/to/ai-device-fs", "python", "-m", "mcp_server_fs"]
    }
  }
}
```

### Web 应用（SSE）

```bash
# 启动服务器
uv run --extra mcp python -m mcp_server_fs --transport sse --port 8000
```

```javascript
// JavaScript 客户端
const eventSource = new EventSource('http://localhost:8000/sse');

async function callTool(toolName, args) {
  const response = await fetch('http://localhost:8000/messages/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      method: 'tools/call',
      params: { name: toolName, arguments: args }
    })
  });
  return await response.json();
}

// 示例：读取文件
callTool('read_file', { path: '/path/to/file.txt' });
```

## 可用工具

文本编辑工具返回结构化 JSON。`read_image` 成功时返回简短文本、标准 MCP `ImageContent` 和不含 Base64 的图片元数据；失败时返回稳定的 `error.code + error.message + error.details` 结构。

所有工具固定操作服务进程所在机器的文件系统。工具的 SSH `host` 参数已删除；携带该参数的旧请求（包括 null 或空字符串）会在文件操作开始前被拒绝，不会改为本地执行。

`edit_file` 和 `insert_file` 默认不会返回 diff。只有显式传入 `include_diff=true` 时才会附带 diff；非必要情况下请保持关闭，以避免增加不必要的 token 消耗。

权限语义说明：对已有文件执行 `write_file` / `edit_file` / `insert_file` / `apply_patch(Update/Move)` 时，会保留原文件的权限位（例如 executable bit）；新建文件时仍遵循系统默认创建权限（受 `umask` 影响）。

### 核心工具

- `read_file(path, type="chars", offset=0, limit=null, direction="forward", max_chars=10000)` - 读取 UTF-8 文本文件；`type` 可选 `chars` / `lines`，`limit=null` 表示读取剩余范围；`offset` / `limit` 不得为负，`limit=0` 或偏移超出范围返回空内容。反向偏移从末尾计算，返回内容保持原顺序；按行返回真实 1-based `line_range`，空范围为 `null`
- `read_image(path, max_bytes=5242880)` - 读取服务所在机器上的图片并返回标准 MCP 图片内容；支持 PNG、JPEG、静态 WebP，按真实文件内容识别格式；单图默认及硬上限均为 5 MB，最多 4000 万像素，不支持动画图片
- `write_file(path, content)` - 创建/覆盖文件（原子写入）；适合新建文件或整文件重写，返回精简元数据
- `edit_file(path, edits, dry_run=false, include_diff=false)` - 基于文本精确匹配的编辑；默认直接写入，仅在 `include_diff=true` 时返回 diff，建议只在预览或调试时开启
- `insert_file(path, text, line=null, before=null, after=null, at_end=false, dry_run=false, include_diff=false)` - 按行号、锚点或文件末尾插入文本；`line` / `before` / `after` / `at_end` 必须且只能提供一种定位方式，仅在 `include_diff=true` 时返回 diff，建议只在预览或调试时开启。注意：`after` 是“插在匹配字符串之后”，不会自动跳过该行原有换行符；如果目标是“插到下一行”，请把换行包含进 `after` 锚点，或改用 `line` / `before`
- `apply_patch(patch, dry_run=false)` - 应用自定义 patch DSL，支持 `Add File` / `Delete File` / `Update File` / `Move to`，一次执行多文件文本变更并在失败时回滚；不限制工作区根目录，但只支持 UTF-8 文本文件

详细参数说明请参考工具的 docstring。

## 本地核心复用

直接依赖 `ai-device-fs` 可复用本地操作，不需要安装 FastMCP、启动服务器或提供 MCP 请求上下文。要运行 MCP 服务则安装 `ai-device-fs[mcp]`；源码开发使用 `uv sync --extra mcp`。

```python
from mcp_server_fs import core

written = core.write_file("/tmp/example.txt", "hello")
assert written.data["success"]
text = core.read_file("/tmp/example.txt", max_chars=1000)
print(text.data["content"])
preview = core.edit_file(
    "/tmp/example.txt", [{"oldText": "hello", "newText": "world"}],
    dry_run=True, include_diff=True, temp_dir="/tmp/my-client-output",
)
```

- 六个公共操作：`read_file`、`write_file`、`edit_file`、`insert_file`、`apply_patch`、`read_image`。
- 文本操作返回 `FileResult`：`data` 是普通字典（保持既有成功/错误字段），`metadata` 保存路径和摘要。无需解析 JSON 字符串。`read_file` 错误为 `data["error"]`，写入/编辑/插入/patch 错误为 `data["success"] == False`；本阶段保留现有行为，不统一改写错误契约。
- 图片操作返回 `ImageResult`：`info` 包含原始 `bytes`、MIME、尺寸等，`path` 为设备本地路径；失败抛出带稳定错误码的 `ImageReadError`。Base64 与 MCP `ImageContent` 仅由适配层生成。
- 核心导入不加载 `.env`、不创建目录、不初始化 MCP。编辑/插入的长 diff 可用 `temp_dir` 指定保存目录；省略时使用系统临时目录下的 `ai-device-fs/`，仅在确实需要保存完整输出时创建。MCP 适配仍使用 `MCP_TEMP_DIR` 配置。
- 核心是同步本地 I/O。异步客户端应将完整操作放到线程池，不直接阻塞事件循环。设备身份、网络、任务重试与断线对账由设备层负责；返回路径属于执行所在机器。

## 执行位置与升级

- 工具路径及临时文件均属于服务所在机器；升级调用方后应刷新工具 Schema。
- 本次依赖拆分后，MCP 部署必须显式安装 `ai-device-fs[mcp]`，或在源码目录执行 `uv sync --extra mcp`；只安装默认包用于核心复用，不能启动 MCP 服务。现有启动命令需按上方示例启用 extra，不能仅更新代码。
- 调试入口 `bash scripts/inspector.sh` 会在当前仓库启用 `mcp` extra；可追加 `--transport stdio` 等服务参数。该脚本调用 pnpm 下载/运行 Inspector，不属于无网络的核心测试。
- stdio 和 SSE 传输保持不变。CLI `--host` / 环境变量 `MCP_HOST` 是监听地址，不是 SSH 目标，仍然保留。
- 内置 SSH、远程 Python 脚本与远程文件处理后端已经删除；`docs/REMOTE_HOST_DESIGN.md` 仅是退役历史资料。

## 项目结构

```text
mcp_server_fs/
├── cli.py                  # 主 CLI 入口与传输配置
├── config.py               # 配置常量
├── path_utils.py           # 路径处理
├── formatting.py           # 格式化辅助
├── image_utils.py          # 图片校验
├── io_utils.py             # 原子写入与临时文件
├── responses.py            # 结构化响应辅助
├── common.py               # 兼容聚合层
├── core/                   # 六个协议无关本地操作与结果模型
├── defaults.py             # 无导入副作用的核心默认值
├── tools/                  # 六个薄 MCP 适配及结果转换
└── patch/                  # patch 解析与执行

tests/
├── core/                   # 本地行为与真实内存 MCP 契约测试
└── patch/                  # patch DSL 与执行测试
```

## 大文件处理

当响应中的 diff 内容超过 `max_chars` 限制（默认 10000）时：

- `read_file`: 直接截断，使用 `offset` 和 `limit` 参数分段读取
- `read_image`: 不截断、不压缩、不转换格式；超过大小或像素限制时直接失败
- `edit_file` / `insert_file`: 当 `include_diff=true` 时，超长 diff 会保存到 `MCP_TEMP_DIR`，并在响应中返回临时文件路径，可用 `read_file` 分段读取

## 使用建议

- 默认优先使用不返回 diff 的模式，减少 AI 工具调用的 token 开销。
- 只有在“用户明确要看 diff”“需要 dry run 预览”“需要排查定位是否正确”时，再启用 `include_diff=true`。
- 如果目标是整文件覆盖，优先用 `write_file`；通常比返回 diff 的编辑类工具更省 token。
- 图片必须使用 `read_image`，不要用 `read_file` 读取二进制内容。`read_image` 的 Base64 只出现在 MCP `ImageContent.data` 中，不会复制到文本或元数据。
- diff 超长时，完整 diff 保存在服务所在机器的 `MCP_TEMP_DIR`。

## 安全特性

- 原子写入：临时文件 + 重命名确保安全
- 权限保留：编辑或覆盖已有文件时保留原文件权限位，新建文件遵循默认创建权限
- 路径规范化：自动解析相对路径和 `~`

## 开发测试

```bash
uv run --extra mcp pytest -q
uv run --extra mcp pytest --cov=mcp_server_fs --cov-report=term-missing
```

`tests/core/test_local_only_contract.py` 通过真实的内存 MCP 会话检查六工具的 Schema、旧 `host` 拒绝、无文件操作副作用及本地调用成功路径。`tests/core/test_standalone_core.py` 覆盖核心六操作、结构化错误、原子写入失败、显式临时目录和图片原始字节；还会构建 wheel 并在不安装 FastMCP/MCP/Pydantic/dotenv 的隔离环境中实际执行六操作，验证核心导入不加载 `.env` 或创建目录。测试仅使用临时目录，不连接正式服务或远程主机。

## 许可证

MIT
