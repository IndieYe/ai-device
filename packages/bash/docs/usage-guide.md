> 本文件是现有全局使用技能的本地化修订稿，供部署升级时同步；本轮未覆盖实际工作目录的技能。

---
name: bash-usage-guide
description: 当 AI 需要通过 Bash MCP 执行命令、结构化程序参数、复杂 Bash 脚本或追踪本地后台任务时使用；指导 bash、run、script、get 的选择、执行规则与返回契约。
---

# Bash MCP 使用指引

本文介绍可选 MCP 适配器的工具契约，需安装 `mcp` extra：`uv sync --extra mcp`。
默认安装只提供 `mcp_server_bash.core` 本地执行 API，安装、入口示例及实例生命周期见 [README](../README.md)。

Bash MCP 提供四个工具，共享相同的本地执行、输出捕获、超时和后台追踪语义：

- `bash`：Shell 命令字符串，保留现有兼容接口。
- `run`：结构化 argv，不经过 Shell，可接收 stdin。
- `script`：复杂 Bash 脚本，执行前先运行 `bash -n`，可接收 stdin。
- `get`：查询或等待前三个工具产生的后台任务。

## 工具选择

### 使用 `bash`

适合简短 Shell 命令，以及明确需要 Shell 解析的管道、重定向、变量展开或命令连接：

```json
{"purpose":"检查仓库工作区状态","command":"git status --short","cwd":"/repo","bytes":4000}
```

### 使用 `run`

适合直接运行程序并精确保留参数边界。每个 `argv` 元素都是一个独立参数，不解释 Shell
符号、变量或命令替换：

```json
{"purpose":"验证 Python 运行环境","argv":["python3","-c","print('hello world')"],"cwd":"/repo","bytes":4000}
```

自由文本和程序输入应通过 `stdin` 传递，不要为了引用和转义把它拼进 Shell 命令：

```json
{"purpose":"验证标准输入传递","argv":["python3","-c","import sys; print(sys.stdin.read())"],"stdin":"hello\nworld\n"}
```

### 使用 `script`

适合循环、函数、条件分支、here-document、多步错误处理和较长的 Bash 逻辑：

```json
{
  "purpose":"检查 JSON 文件语法",
  "script":"set -euo pipefail\nfor file in *.json; do\n  jq empty \"$file\"\ndone",
  "cwd":"/repo"
}
```

正式执行前会运行 `bash -n`。用户输入应放在 `stdin`，不要混入脚本正文。

### 使用 `get`

`bash`、`run`、`script` 超时转后台后都会返回 `background_pid`，统一通过 `get` 查询：

```json
{"pid":12345,"timeout":30,"bytes":4000}
```

## 通用参数

`bash`、`run`、`script` 共同支持：

- `purpose`：可选的简短业务目的。大多数调用都应填写，说明“为什么执行”，不要复述命令；
  不得包含凭证或完整敏感载荷。省略时工具仍可执行，但摘要只显示稳定的工具级回退。
- `cwd`：服务所在机器上的工作目录。
- `timeout`：前台等待秒数，默认 `60`。
- `bytes`：返回的内联输出字节数；可传 `0`，负数无效。
- `timeout_behavior`：`background` 或 `kill`，默认 `background`。

`run`、`script` 还支持：

- `stdin=None`：不提供输入。
- `stdin=""`：提供空输入并立即 EOF。
- 其他字符串：按 UTF-8 原样传给目标程序。

`get` 参数：

- `pid`：后台任务 PID。
- `timeout`：最多等待秒数，默认 `0`，表示立即查询。
- `bytes`：返回输出尾部字节数，默认 `200`。

## 执行位置

四个工具只操作服务所在机器；路径、输出及后台 PID 均属于该机器。SSH `host` 参数已删除，携带该参数的旧请求会在执行前失败，不能被忽略后转为本地调用。MCP 传输与监听配置不受影响。显式执行 SSH 命令仍然允许，但不具有原内置远程任务管理保证。

## 后台与超时

### `timeout_behavior="background"`

达到 timeout 后目标继续运行，返回：

- `code: BACKGROUND`
- `exit_code: null`
- `background_pid: <pid>`

后续使用 `get` 查询或等待。

### `timeout_behavior="kill"`

达到 timeout 后终止本地进程组，通常返回：

- `code: TIMEOUT`
- `exit_code: -1`
- `background_pid: null`

## 返回契约

- `success=true` 表示命令成功，或任务处于 `BACKGROUND` / `RUNNING`。
- 非零退出返回 `success=false`、`code=COMMAND_ERROR` 和真实 `exit_code`。
- `output` 只包含真实输出；无输出时为 `""`，`returned_output_bytes=0`。

## 输出读取

- `bash`、`run`、`script` 返回输出头部，`get` 返回输出尾部。
- `output_path` 始终是 Bash MCP 服务所在机器上的本机路径。
- 首次使用较小的 `bytes`，不足时逐步增大；需要完整原始输出时读取 `output_path`。

重点返回字段：

- `success`
- `code`
- `exit_code`
- `output`
- `output_path`
- `truncated`
- `returned_output_bytes`
- `total_bytes`
- `background_pid`
- `tool_mode`

## `meta.shell_env` 凭证传递

需要向 Bash MCP 传递凭证时，使用调用元数据 `meta.shell_env`，不要写入 `command`、`argv`、`script` 或 `stdin`。Bash MCP 日志仅记录环境变量名。

## 推荐原则

1. `bash`、`run`、`script` 的大多数调用填写一句简短 `purpose`，让运行中与历史记录直接说明业务目的。
2. `purpose` 写“为什么执行”，不要复制命令、argv 或脚本；机械格式探测、一次性极低价值检查可省略。
3. 不需要 Shell 时优先 `run`，减少引用和注入风险。
4. 复杂 Bash 逻辑使用 `script`，不要压缩成难以维护的一行命令。
5. 简单 Shell 操作和兼容调用继续使用 `bash`。
6. 长任务允许转后台，并用 `get` 主动查询；需要严格截止时使用 `kill`；`get` 不接收 `purpose`。
