# ai-device-bash

本包已迁入 ai-device 单仓库；distribution 已改名，Python import 和命令保持不变。当前仅本地构建，未发布；MCP 仍为可选 extra。

可独立使用的本地执行核心，以及可选的 FastMCP 命令执行服务器。当前支持 Linux/macOS，只在当前进程所在机器执行命令；不包含设备身份、网络协议或 Windows 实现。

服务提供四个工具入口，并共享统一的执行、后台追踪、超时终止和输出捕获内核：

- `bash`：执行 Shell 命令，可提供简短业务目的。
- `run`：结构化 argv，不经过 Shell，可接收 stdin，可提供简短业务目的。
- `script`：执行复杂 Bash 脚本，正式执行前先运行 `bash -n`，可提供简短业务目的。
- `get`：查询以上三个执行工具产生的后台任务。

## 安装与启动

要求 Python >= 3.13，使用 `uv` 管理环境。默认安装只有本地核心依赖（`psutil`），不安装 FastMCP、MCP 或 Pydantic：

```bash
# 仓库内：仅核心
uv sync --locked
uv run --locked python -c 'from mcp_server_bash.core import ExecutionRuntime; print(ExecutionRuntime)'

# 仓库内：启用 MCP 适配器并启动
uv sync --locked --extra mcp
uv run --locked --extra mcp mcp-server-bash
# 也可使用 uv run --locked --extra mcp python -m mcp_server_bash
```

安装到其他环境时使用 `uv pip install /path/to/ai-device-bash`（核心），或
`uv pip install '/path/to/ai-device-bash[mcp]'`（MCP）。仅核心安装调用 MCP CLI 会提示安装 `mcp` extra。

## 独立本地核心：`mcp_server_bash.core`

```python
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_server_bash.core import ExecutionRuntime, create_stdin_artifact

async def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        async with ExecutionRuntime(
            output_dir=str(root / "outputs"),
            artifact_dir=str(root / "artifacts"),
        ) as runtime:
            stdin_path, artifacts = create_stdin_artifact(
                b"hello core", runtime.artifact_dir,
            )
            result = await runtime.run_argv(
                (sys.executable, "-c", "import sys; print(sys.stdin.read())"),
                cwd=str(root), stdin_path=stdin_path, artifacts=artifacts,
                timeout=60, output_bytes=4096, max_output_file_bytes=104857600,
            )
            print(result["code"], result["output"])
            if result["background_pid"] is not None:
                print(await runtime.get_background_result(
                    result["background_pid"], timeout=5, output_bytes=4096,
                ))

asyncio.run(main())
```

核心提供程序化执行 API，而不是 MCP 的业务摘要或请求上下文：

| 核心入口 | 输入与用途 |
| --- | --- |
| `runtime.run_command(command, cwd, ...)` | Shell 命令、管道和重定向 |
| `runtime.run_argv(argv, cwd, ..., stdin_path=None, artifacts=None)` | 保留 argv 边界，不经过 Shell |
| `runtime.run_script(script_path, cwd, ..., stdin_path=None, artifacts=None)` | 从文件执行 Bash，先运行 `bash -n` |
| `runtime.run_execution(request, ...)` | 直接使用 `ExecutionRequest` / `ExecutionTarget` |
| `runtime.get_background_result(pid, timeout=0, output_bytes=200)` | 仅查询当前实例的后台结果 |

- 执行选项保留已有底层语义：`timeout=None` 默认等待完成；正超时配合 `timeout_behavior="background"` 或 `"kill"`；`output_bytes` 对应 MCP 的 `bytes`，受 `max_output_bytes=8192` 限制。核心 `max_output_file_bytes=None` 默认不限落盘量；调用方应显式设置适当上限。MCP 的默认超时和 100 MiB 落盘上限不变。
- `create_script_artifacts(script_bytes, stdin_bytes_or_none, artifact_dir)` 返回 `(script_path, stdin_path, artifacts)`；`create_stdin_artifact` 返回 `(stdin_path, artifacts)`。执行接受的受管文件在前台结束、启动失败、取消或后台结束时清理。传入的非受管文件不会被删除；不要把 stdin 拼入脚本源代码。
- 每个实例拥有独立后台索引和结果保留定时器；完成结果默认保留 3600 秒，可通过 `completed_process_retention_seconds` 调整。`get` 可重复读取，跨实例 PID 返回 `NOT_RUNNING`。PID 索引不持久化，重建实例不能接管旧任务。
- 一个实例只在一个事件循环内使用，支持该循环内并发调用；不是跨线程共享对象。使用 `async with`，或在同一事件循环内显式 `await runtime.aclose()`。关闭不可逆、幂等，会取消并回收该实例拥有的前台执行和后台进程，清理受管文件与保留定时器；关闭后执行或查询抛出 `RuntimeError`。取消关闭操作仍会完成清理后再传播取消。关闭仅等待运行实例拥有的内部执行 Task，不等待调用者整条 Task，因此调用者在 `__aexit__`/`finally` 中再次关闭不会形成循环等待。
- 输出文件不在关闭时删除，由调用方管理目录/保留策略。核心不读取 `.env`、配置日志或获取 MCP 上下文；MCP 适配器继续负责输入限额、purpose、可信调用会话环境和启动清理，并在 FastMCP lifespan 退出时关闭自己的运行实例。

## 工具选择

| 场景 | 工具 |
| --- | --- |
| 简单 Shell 命令、管道或重定向 | `bash` |
| 精确保留参数边界、不需要 Shell 展开 | `run` |
| 循环、函数、分支、多行复杂 Bash | `script` |
| 查询或等待后台任务 | `get` |

## 执行位置与升级

- 四个工具固定操作服务所在机器；`cwd`、后台 PID、输出和临时文件均属于该机器。
- 工具的 SSH `host` 参数及内置 SSH 后端已删除。旧调用只要仍携带 `host`（包括 null 或空字符串）就会在执行前报错；请更新调用方并刷新工具 Schema，不会自动转为本地执行。
- stdio、SSE 和 Streamable HTTP 传输仍保留；`SSE_HOST` 是监听地址，与被删除的工具参数不同。
- 显式运行 `ssh` 命令仍然允许，但不提供原内置远程任务追踪与远程终止保证。
- 部署升级应先妥善处理当前实例的后台任务；任务索引仍保存在实例内存中。此变更不提供旧任务的迁移或恢复。

## Tool: `bash`

```python
bash(command, purpose=None, cwd=None, timeout=60, bytes=None,
     timeout_behavior="background")
```

执行 Shell 命令，保留原有接口与行为。需要精确参数边界时优先使用 `run`；复杂多行逻辑
优先使用 `script`。

```json
{"purpose":"定位工具摘要生成链路","command":"rg -n summary_key backend/src web/src"}
```

## Tool: `run`

```python
run(argv, purpose=None, stdin=None, cwd=None, timeout=60, bytes=None,
    timeout_behavior="background")
```

- `argv`：非空字符串数组，首元素为程序名，每个元素作为一个独立参数传递。
- 不解释 `$VAR`、通配符、管道、重定向、命令替换或其他 Shell 语法。
- `stdin=None` 表示不提供输入；空字符串表示立即 EOF；其他字符串按 UTF-8 原样输入。

```json
{"purpose":"检查后端定向测试是否通过","argv":["uv","run","pytest","tests/test_tool_call_summary.py","-q"]}
```

## Tool: `script`

```python
script(script, purpose=None, stdin=None, cwd=None, timeout=60, bytes=None,
       timeout_behavior="background")
```

- 脚本写入权限受限的受管临时文件。
- 正式执行前使用相同 Bash、cwd 和环境运行 `bash -n`。
- 语法检查失败返回 `SCRIPT_SYNTAX_ERROR`，不会正式执行脚本。
- `stdin` 与脚本正文使用独立通道。

```json
{"purpose":"生成受影响测试文件清单","script":"for file in tests/*.py; do printf '%s\\n' \"$file\"; done"}
```

## Tool: `get`

```python
get(pid, timeout=0, bytes=200)
```

- 查询 `bash`、`run` 或 `script` 返回的后台 PID。
- `timeout=0` 立即返回；正数表示最多等待对应秒数。
- `bytes` 限制本次头尾预览的字节数；额度小到容不下分隔标记时，`get` 保留最新尾部。`output_path` 始终是服务所在机器上的本机路径。
- 只能查询当前服务实例追踪的本地后台 PID。
- `get` 是原后台执行的状态查询，不重复接收 `purpose`。

## 通用执行参数

- `purpose`：`bash`、`run`、`script` 可选的一句简短业务目的，最多 200 个字符。大多数调用
  推荐填写，用于说明“为什么执行”，不要重复 command、argv 或 script；该字段用户可见且可能
  持久化，不得包含凭证或完整敏感载荷。`purpose` 不改变执行行为，也不是安全授权。
- `cwd`：目标工作目录。
- `timeout`：执行或等待秒数。
- `bytes`：内联输出字节数；不指定时上限由 `BASH_MAX_OUTPUT_BYTES` 决定（默认 8192 字节）。完整输出保存在 `output_path`。
- `timeout_behavior="background"`：超时后继续运行并返回后台 PID。
- `timeout_behavior="kill"`：超时后终止目标进程组。
- 命令已启动后、后台 PID 返回前取消调用（包括超时转后台的移交窗口），会终止并回收该调用的本地进程、输出任务及临时资源；重复取消不会打断这段清理。后台 PID 已成功返回的任务仍由当前实例追踪。

调用元数据可提供 `meta.shell_env` 作为仅本次调用的环境变量覆盖，也可提供
`meta.invocation_id` 作为调用链日志 ID。

### 受控调用会话上下文

当 Bash MCP 以 HTTP/SSE 运行且本机 `.env` 配置 `BASH_CALLER_CONTEXT_TOKEN` 后，只有携带
相同 `X-AI-Assistant-Caller-Context` 请求头的受控主服务调用，才可将其调用 metadata 中的
`assistant.session_id` 投影为子进程环境变量 `AI_ASSISTANT_CALLER_SESSION_ID`。该变量用于
后续建立会话谱系的创建意图，**不是授权凭证**。

- 裸 MCP `_meta` 是客户端可写数据；没有匹配的服务身份头时，即使其中包含
  `assistant.session_id` 也会被忽略。
- `meta.shell_env` 不能覆盖该保留变量。
- 每次调用都显式重建该变量；无有效上下文时置为空，避免服务进程环境或前次调用泄漏。
- 主服务侧应将相同令牌仅配置为 Bash server 的固定 HTTP header，不能写入 tool 参数、metadata、
  日志或仓库文件。

## 返回契约

- `success=true` 表示命令成功完成，或任务仍在后台正常运行。
- 命令以非零退出码结束时返回 `success=false`、`code=COMMAND_ERROR`，并保留真实 `exit_code`。
- 未超限时，`output` 是完整命令输出；超限时返回开头、`...[output omitted]...` 标记和结尾（预算极小时只返回开头）。无输出时为空字符串，且 `returned_output_bytes=0`。
- `truncated=true` 表示预览省略了内容；需要中间部分时按 `output_path` 读取落盘文件，不重新执行命令。
- `total_bytes` 表示完整原始输出文件的字节数，不包含任何展示占位文本。
- 落盘输出超过 `BASH_MAX_OUTPUT_FILE_BYTES` 时返回 `success=false`、`code=OUTPUT_LIMIT_EXCEEDED`、
  `exit_code=-1`：命令被终止，`output_path` 指向的文件被清空，因此 `total_bytes=0`。**此时
  `output_path` 不再包含完整输出**——上面两条关于完整输出的承诺只在该上限未触发时成立。
- `bash`、`run`、`script` 提供 `purpose` 时，`meta.summary` 等于去除首尾空白后的值；省略时
  分别使用稳定的 `bash`、`run command`、`script` 摘要。执行成功、失败或转入后台时保持一致。
  显式提供的 purpose 非法时返回 `INVALID_PURPOSE`，且不会启动命令。

## 配置

配置来源有两个，优先级从高到低：

1. 进程环境变量；
2. 项目根目录 `.env`（源码检出时的 `packages/bash/.env`）。

以 `pip install ai-device-bash` 安装后不存在项目根目录，`.env` 不参与配置，必须使用环境变量。只有下节列出的键会从环境变量读取，其他变量不会变成配置。

读取配置不会把值写回进程环境：执行后端为每条命令继承 `os.environ`，因此 `BASH_CALLER_CONTEXT_TOKEN` 等值不会泄漏到用户 shell。

新增执行模式相关配置：

```env
BASH_MAX_ARGV_COUNT=4096
BASH_MAX_ARGV_BYTES=262144
BASH_MAX_SCRIPT_BYTES=1048576
BASH_MAX_STDIN_BYTES=4194304
BASH_ARTIFACT_DIR=/tmp/mcp-server-bash/artifacts
BASH_ARTIFACT_TTL_SECONDS=86400
BASH_MAX_OUTPUT_FILE_BYTES=104857600
BASH_OUTPUT_TTL_SECONDS=604800
```

- `BASH_MAX_ARGV_COUNT`：`run.argv` 最大元素数量。
- `BASH_MAX_ARGV_BYTES`：`run.argv` UTF-8 总字节数上限。
- `BASH_MAX_SCRIPT_BYTES`：`script.script` UTF-8 字节数上限。
- `BASH_MAX_STDIN_BYTES`：`run/script` stdin UTF-8 字节数上限。
- `BASH_MAX_OUTPUT_BYTES`：内联输出最大字节数，默认 8192；显式 `bytes` 也不能超过此上限。
- `BASH_ARTIFACT_DIR`：stdin 和脚本受管临时资源根目录。
- `BASH_ARTIFACT_TTL_SECONDS`：异常残留临时资源的回收阈值。
- `BASH_MAX_OUTPUT_FILE_BYTES`：单个输出文件落盘字节上限，默认 104857600（100 MiB）。超出即清空该文件、终止命令并返回 `OUTPUT_LIMIT_EXCEEDED`。
- `BASH_OUTPUT_TTL_SECONDS`：输出文件保留时长，默认 604800（7 天）；服务启动时清理更早的文件。
  默认输出目录在 `/tmp` 下由系统回收，一旦把 `BASH_OUTPUT_DIR` 指向持久路径，就依赖该策略控制增长。

完整示例见 `.env.example`。

## 隐私边界

- Bash MCP 服务日志不记录完整 argv、script、stdin 或环境变量值。
- 上节列出的配置项不会进入 `bash`、`run`、`script` 启动的子进程环境。宿主通过 `config.json` 的 `env` 或 `inherit_env` 供给的配置值同样被排除，包括 `BASH_CALLER_CONTEXT_TOKEN` 这类服务间密钥；命令需要某个值时，调用方应显式通过 `meta.shell_env` 传入。
- 其他环境变量照常继承，模型与工具不应据此假定子进程只能看到白名单。
- AI Assistant 会持久化工具调用参数，因此敏感凭证不要写入 purpose、command、argv、script
  或 stdin；应通过调用元数据 `meta.shell_env` 传递。

## 开发与测试

所有测试只启动测试自有的本地进程，不连接正式 MCP。建议把缓存、临时文件和测试输出限制在当前工作区的 `.tmp/`（已忽略）：

```bash
mkdir -p .tmp
export TMPDIR="$PWD/.tmp" UV_CACHE_DIR="$PWD/.tmp/uv-cache"

# 不启用 mcp extra，独立运行核心测试
uv run --locked --with pytest --with pytest-asyncio python -m pytest tests/core -q \
  --basetemp=.tmp/pytest-core

# 完整核心 + MCP 回归
uv run --locked --extra mcp --with pytest --with pytest-asyncio python -m pytest -q \
  --basetemp=.tmp/pytest-full

# 正确性静态检查；E402 排除既有测试为安装 MCP stub 而延后的导入
uv run --locked --extra mcp --with ruff ruff check --isolated \
  --select E4,E7,E9,F --ignore E402 src tests
```

MCP 模块导入默认配置服务日志。在专用测试 checkout 内可设置被 Git 忽略的 `.env`：
`SERVER_LOG_ENABLED=false`，并把 `BASH_OUTPUT_DIR`、`BASH_ARTIFACT_DIR`、`SERVER_LOG_DIR`
指定到 checkout 内 `.tmp/` 的绝对路径。不要覆盖已有部署配置。

`tests/core/` 直接测试公共核心的真实 argv/Shell/script/stdin、输出限制、后台结果、实例隔离、保留时长、取消和异常清理。`test_no_mcp_install.py` 用 `uv sync --locked` 创建全新仅核心环境，再通过隔离解释器 `python -I` 实际导入并执行三个模式和后台查询，确认未安装 FastMCP/MCP，而非仅静态检查 imports。

`tests/integration/test_local_only_protocol.py` 在独立 Python 进程内建立真实的内存 MCP 会话，验证工具发现、旧 `host` 拒绝、本地执行与后台查询；不启动监听端口，也不连接正式服务或远程主机。

当前契约以本 README 和源码为准；SSH 设计及原多执行模式设计文档仅保留为历史资料。
