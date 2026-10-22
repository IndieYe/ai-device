# Bash MCP 多执行工具需求设计

> 状态：历史设计，四工具已实现；其中 SSH 远程部分已退役，当前契约以 README 与源码为准。
> 适用项目：`ai-device-bash`，并涉及 `ai-assistant` 的工具说明、调用元数据和回归测试
> 目标读者：在全新会话中接手设计、开发、测试和审核的 AI/开发者

## 1. 背景

当前 Bash MCP 只提供：

```text
bash(command, ...)
get(pid, ...)
```

`bash` 本地通过 `/bin/bash -c <command>` 执行，远程通过系统 SSH 和远程 `bash -lc` 执行。

该模式适合管道、重定向、循环和环境变量展开，但当 AI 把 Markdown、邮件正文、JSON、SQL 或代码片段拼进命令字符串时，容易出现：

- 引号未闭合；
- 反引号、`$()` 或 `$VAR` 被意外展开；
- 正文被 Shell 修改但命令仍返回成功；
- JSON、Bash、SSH 和 CLI 多层转义导致失败；
- 本地与远程转义行为难以稳定预测。

本次改造采用多个职责单一的工具，而不是向现有 `bash` 增加互斥参数：

```text
bash    现有 Shell 命令，保持兼容
run     结构化 argv 命令，可接收 stdin
script  复杂 Bash 脚本，可接收 stdin
get     查询三个执行工具产生的后台任务
```

外部接口按用途拆分，内部仍共享同一套执行、输出、超时、后台和清理内核。

---

## 2. 目标与非目标

### 2.1 核心目标

- 现有 `bash` 接口和行为不变。
- 普通程序调用优先使用 `run`，参数不经过用户 Shell 解析。
- 管道、重定向、循环和变量展开等 Bash 逻辑使用 `script`。
- `run`、`script` 通过 `stdin` 接收长文本和程序输入。
- `bash`、`run`、`script` 均支持本地和 SSH 远程执行。
- 三个工具共享 cwd、环境、输出、超时、后台、`get` 和进程组终止能力。
- 保留现有 `meta.shell_env` 环境变量注入契约。
- 输入、环境和临时资源边界清晰、可测试、可清理。

### 2.2 质量目标

- 三个工具只负责输入校验、摘要和执行请求构造。
- 不创建三套或六套互相独立的进程生命周期实现。
- 本地与远程语义尽量一致；不能支持时必须显式失败，禁止静默降级。
- stdin、完整脚本、完整 argv 和环境变量值不得进入 Bash MCP 服务日志或工具摘要。
- 临时资源正常结束后立即清理，异常残留通过 TTL 回收。

### 2.3 非目标

本任务不包含：

- 删除或废弃现有 `bash`；
- 给现有 `bash` 增加 `stdin`；
- 自动把旧 `command` 转换为 `run`；
- 在 `run.argv` 中实现 `$VAR`、通配符、管道或重定向展开；
- 新增普通工具参数 `env`；
- 引入新的凭证管理系统；
- 修改 `aimail` CLI；
- 拆分 stdout/stderr 的现有聚合输出格式；
- 改变 SSH alias、`~/.ssh/config`、后台 PID 查询和本地输出文件的基本语义；
- 保证 `script`、`stdin` 不进入 AI Assistant 的工具参数持久化；这是独立的上游存储安全议题。

---

## 3. 当前实现与必须保留的契约

### 3.1 主要代码

```text
src/mcp_server_bash/server.py
src/mcp_server_bash/shell.py
src/mcp_server_bash/models.py
src/mcp_server_bash/tracking.py
src/mcp_server_bash/output_capture.py
src/mcp_server_bash/backends/local.py
src/mcp_server_bash/backends/ssh.py
```

AI Assistant 集成主要涉及：

```text
backend/src/integrations/server_pool_core.py
backend/tests/test_server_pool.py
~/.ai-assistant/skills/bash-usage-guide/SKILL.md
```

### 3.2 现有工具接口

```python
bash(
    command: str,
    cwd: str | None = None,
    timeout: float | None = 60,
    bytes: int | None = None,
    timeout_behavior: Literal["background", "kill"] = "background",
    host: str | None = None,
)

get(
    pid: int,
    timeout: float | None = 0,
    bytes: int = 200,
    host: str | None = None,
)
```

这两个接口的已有参数不得删除、改名或改变含义。

### 3.3 环境变量

每次调用可通过 MCP request metadata 传入：

```json
{
  "shell_env": {
    "ENV_NAME": "value"
  }
}
```

本地最终环境：

```text
Bash MCP 服务进程 os.environ
+ meta.shell_env 覆盖
```

远程最终环境：

```text
远程 SSH 登录环境
+ 远程 bash -lc 初始化环境
+ meta.shell_env 覆盖
```

三个执行工具必须使用同一环境来源。不得把环境值复制到普通工具 arguments。

### 3.4 cwd

- 本地未传 `cwd`：使用 `DEFAULT_CWD`，否则使用服务当前目录。
- 本地目录不存在：`CWD_NOT_FOUND`。
- 远程未传 `cwd`：保持 SSH 登录后的默认目录。
- 远程目录不存在：`REMOTE_CWD_NOT_FOUND`。

### 3.5 超时、后台与输出

- `timeout_behavior="background"`：超时后继续运行，返回 `BACKGROUND` 和 `background_pid`。
- `timeout_behavior="kill"`：超时后终止整个本地或远程进程组，返回 `TIMEOUT`。
- 后台任务统一由 `get(pid, host)` 查询。
- 远程任务必须使用创建时相同的 host 查询。
- `output_path` 始终是 Bash MCP 服务机器上的本地文件。
- 命令以非零退出码结束时返回 `success=false + code=COMMAND_ERROR`。
- `output` 只包含命令真实输出；无输出时返回空字符串，展示占位文本由调用方负责。

现有结果字段必须保留：

```text
success
code
message
exit_code
output
output_path
truncated
returned_output_bytes
total_bytes
duration_ms
background_pid
```

---

## 4. 最终工具接口

### 4.1 `bash`

保持现有签名，不增加新参数：

```python
bash(
    command: str,
    cwd: str | None = None,
    timeout: float | None = 60,
    bytes: int | None = None,
    timeout_behavior: Literal["background", "kill"] = "background",
    host: str | None = None,
)
```

用途：

- 兼容现有调用；
- 简短 Shell 命令；
- 需要管道、重定向、通配符或 Shell 展开的场景。

不建议把长正文或复杂脚本拼入 `command`。

### 4.2 `run`

```python
run(
    argv: list[str],
    stdin: str | None = None,
    cwd: str | None = None,
    timeout: float | None = 60,
    bytes: int | None = None,
    timeout_behavior: Literal["background", "kill"] = "background",
    host: str | None = None,
)
```

用途：普通程序和 CLI 的默认工具。

语义：

- `argv[0]` 是程序；
- 每个元素是独立参数；
- 不进行 Shell 展开；
- `$HOME`、反引号、`$()`、`;`、`|`、`>` 等均作为字面参数；
- 自由文本通过 `stdin` 传入。

### 4.3 `script`

```python
script(
    script: str,
    stdin: str | None = None,
    cwd: str | None = None,
    timeout: float | None = 60,
    bytes: int | None = None,
    timeout_behavior: Literal["background", "kill"] = "background",
    host: str | None = None,
)
```

用途：复杂 Bash 逻辑。

语义：

- 脚本写入权限受限的临时文件；
- 正式执行前使用目标 Bash 运行 `bash -n`；
- 语法检查失败不得正式执行；
- 程序输入与脚本正文分离，用户 stdin 只交给正式脚本。

### 4.4 `get`

继续共享现有接口。`bash`、`run`、`script` 返回的后台 PID 都由同一个 `get` 查询。

### 4.5 新增结果字段

所有执行结果及其后台 `get` 结果新增：

```json
{
  "tool_mode": "run"
}
```

取值：

```text
bash | run | script
```

输入尚未进入执行内核时，也应根据工具入口返回确定的 `tool_mode`。

内部现有 `execution_mode=local|ssh` 命名存在冲突，应重命名为：

```text
target_mode: local | ssh
tool_mode: bash | run | script
```

---

## 5. 输入校验

### 5.1 `bash.command`

- 必须是非空字符串；
- 保持当前兼容行为；
- 不执行自动语法检查；
- 不新增可能破坏旧调用的严格长度限制。

### 5.2 `run.argv`

- 必须是非空字符串数组；
- `argv[0]` 必须是非空字符串；
- 其余参数允许空字符串；
- 每个元素禁止包含空字节；
- 不得把数组普通拼接后交给本地 Shell；
- 增加可配置参数数量和 UTF-8 总字节数上限；
- 超限返回 `ARGV_TOO_LARGE`，不得截断后执行。

### 5.3 `script.script`

- 必须是非空字符串；
- 禁止空字节；
- 使用 UTF-8；
- 增加可配置大小上限，建议默认 1 MiB；
- 超限返回 `SCRIPT_TOO_LARGE`；
- 必须在正式执行前完成语法检查。

### 5.4 `stdin`

`stdin` 仅属于 `run` 和 `script`：

- `None`：子进程 stdin 使用 `DEVNULL`；
- 空字符串：提供空文件，目标立即读取到 EOF；
- 非空字符串：UTF-8 原样写入受管临时文件；
- 不进行模板替换、变量展开或换行转换；
- 增加可配置大小上限，建议默认至少 4 MiB；
- 超限返回 `STDIN_TOO_LARGE`，不得部分发送；
- 内容不得进入 Bash MCP 日志、摘要或错误消息。

为避免大 stdin 填满 pipe、阻塞后台返回或产生无人管理的写入协程，不采用“启动进程后异步写 pipe”的主方案。

---

## 6. 统一内部模型

外部工具拆分，内部使用统一请求：

```python
@dataclass(frozen=True)
class ExecutionRequest:
    tool_mode: Literal["bash", "run", "script"]
    command: str | None
    argv: tuple[str, ...] | None
    script_path: str | None
    stdin_path: str | None
    cwd: str
    env_overrides: dict[str, str]
    target: ExecutionTarget
```

构造函数必须保证：

- `bash` 只有 `command`；
- `run` 只有 `argv`；
- `script` 只有 `script_path`；
- `stdin_path` 只可能出现在 `run`、`script`；
- 实例创建后状态内部一致。

后端继续返回 `ProcessExecutionInfo`。启动后统一复用：

- stdout/stderr 读取；
- 输出文件写入；
- 内联输出截断；
- timeout；
- background；
- `get`；
- 本地/远程进程组终止；
- 完成记录保留与清理；
- artifact 清理。

整体结构：

```text
bash / run / script 薄工具入口
            ↓
输入校验、摘要、ExecutionRequest
            ↓
本地后端 / SSH 后端
            ↓
ProcessExecutionInfo
            ↓
统一输出、超时、后台、终止与资源清理
```

---

## 7. 本地执行

### 7.1 `bash`

保持现有方式：

```python
create_subprocess_exec(
    shell,
    "-c",
    command,
    cwd=cwd,
    env=effective_env,
    stdin=DEVNULL,
    ...,
)
```

### 7.2 `run`

必须直接调用：

```python
create_subprocess_exec(
    *argv,
    cwd=cwd,
    env=effective_env,
    stdin=stdin_file_or_devnull,
    ...,
)
```

禁止：

```text
" ".join(argv)
shlex.join(argv) 后交给本地 bash -c
```

空格、引号、换行、Unicode、Shell 符号和空字符串参数必须保持原始 argv 边界。

### 7.3 `script`

流程：

1. 在 Bash MCP 受管临时目录创建随机执行目录；
2. 原子写入脚本文件；
3. 权限设为 `0600`；
4. 使用正式执行相同的 Bash 路径、cwd 和关键环境运行 `bash -n`；
5. 失败返回 `SCRIPT_SYNTAX_ERROR`，不得执行正式脚本；
6. 成功后执行 `bash <script_path>`；
7. stdin 使用受管临时文件或 `DEVNULL`；
8. 前台结束、kill 或后台真正结束后清理执行目录。

语法错误输出需要限长，但不得在错误结果中复制完整脚本。

---

## 8. 远程执行

### 8.1 通用原则

- 继续使用系统 `ssh` 和 `~/.ssh/config`；
- 不引入项目内 host 配置；
- 固定远程启动器允许使用 Shell；
- 用户 argv、stdin 和脚本内容必须始终作为数据处理；
- 保留 connect timeout、单次连接尝试、远程 PID/PGID、本地 transport PID、远程进程组终止和本地输出文件。

### 8.2 远程 `bash`

保持现有行为，不在本任务中大幅重写。只接入统一请求、结果字段和 artifact 生命周期。

### 8.3 远程 `run`

SSH 协议最终仍会形成远程命令字符串，因此不能假设把本地 argv 直接追加给 `ssh` 就能天然保持远程 argv 边界。

首版采用：

- 服务端生成固定 bootstrap；
- 对每个 argv 元素独立使用严格 Shell quoting（例如 Python `shlex.quote`）；
- 生成固定 `exec <quoted argv...>`；
- 不使用 `$*`、`eval` 或普通空格拼接；
- 不允许用户内容进入未 quote 的 Shell 位置。

必须通过真实 SSH 集成测试证明以下参数可往返：

- 空字符串；
- 空格、单引号、双引号；
- `$HOME`、`${VAR}`、`$(...)`；
- 反引号、分号、管道和重定向符；
- Unicode；
- 内嵌换行。

如果严格 quoting 无法稳定满足全部边界，再升级为结构化参数文件协议；首版不预先引入该复杂度。

### 8.4 远程 `script`

不能把完整脚本塞入 `ssh host 'bash -lc "..."'`。

采用两阶段协议：

1. 准备连接：通过 stdin 将脚本上传到唯一远程临时目录，权限 `0600`；
2. 在远程运行 `bash -n`；
3. 语法检查通过后，正式连接执行远程脚本；
4. 正式连接的 stdin 只用于目标脚本的用户输入；
5. 正式进程继续产生现有 PID/PGID 元数据；
6. 结束后尽力清理远程执行目录。

准备、语法检查或正式启动失败时，不得遗留未受管资源，也不得把脚本内容写入错误信息。

### 8.5 远程 stdin

`run` 和 `script` 的 stdin 先写入本地权限受限的临时文件，再将该文件作为正式 SSH 进程 stdin：

- `None`：SSH stdin 为 `DEVNULL`；
- 非 `None`：SSH stdin 从临时文件读取；
- SSH 原样转发给远程目标程序；
- 固定 bootstrap 和元数据逻辑不得消费目标 stdin；
- 元数据探测继续使用独立 SSH 连接。

这样可以保证调用超时转后台后，SSH 进程仍能继续从稳定文件描述符读取，而不依赖工具调用中的写入协程。

---

## 9. 临时资源管理

引入统一 artifact 模型：

```python
@dataclass
class ExecutionArtifacts:
    local_paths: tuple[str, ...] = ()
    remote_paths: tuple[str, ...] = ()
```

资源可能包括：

- 本地脚本文件；
- 本地 stdin 文件；
- 远程脚本目录；
- 未来必要的参数数据文件。

要求：

- 不保存脚本或 stdin 正文到后台对象；
- 本地路径只能位于 Bash MCP 固定受管根目录；
- 远程路径只能位于固定远程根目录和随机 execution ID 下；
- 文件和目录权限最小化；
- 正常完成、命令失败、启动失败、kill、后台完成和取消均尽力清理；
- 后台返回时不能提前删除仍被任务使用的资源；
- 清理幂等，且不能影响其他任务；
- 清理失败只记录安全警告，不覆盖真实执行结果。

### 9.1 崩溃恢复

进程被强制终止时，内存回调无法保证执行，因此必须增加 TTL 回收：

- 服务启动时扫描受管本地临时根目录；
- 可选定时扫描长时间残留资源；
- 只清理满足固定路径、合法 execution ID 和超过 TTL 的资源；
- 远程过期目录可在该 host 下一次执行时顺带清理；
- 不扫描或删除受管根目录之外的文件。

---

## 10. 后台任务与 `get`

`BackgroundProcess` 不再要求保存完整命令输入：

```python
@dataclass
class BackgroundProcess:
    ...
    target_mode: Literal["local", "ssh"]
    tool_mode: Literal["bash", "run", "script"]
    display_command: str
    artifacts: ExecutionArtifacts | None
```

要求：

- `get` 保留现有字段和 host/pid 归属规则；
- 增加原始 `tool_mode`；
- 不返回 stdin、脚本、环境映射或完整敏感 argv；
- artifact 在后台任务真正结束时清理；
- 已完成结果继续按现有时间保留并允许重复查询。

---

## 11. 摘要、日志与隐私边界

### 11.1 工具摘要

```text
bash:   run git status --short
run:    run git (+2 args)
script: run bash script
```

远程继续增加 host 前缀：

```text
[prod-1] run git (+2 args)
[prod-1] run bash script
```

要求：

- stdin 永不进入摘要；
- script 正文永不进入摘要；
- `run` 默认只展示程序 basename 和参数数量；
- 不根据参数名猜测并打印密钥；
- 摘要长度有上限。

### 11.2 服务日志

允许记录：

- invocation ID；
- host alias；
- tool mode；
- timeout 和 timeout behavior；
- cwd；
- argv 参数数量；
- script/stdin 字节数；
- 环境变量键名和数量；
- exit code、background PID、duration。

禁止记录：

- stdin 内容；
- script 正文；
- 完整 argv；
- 环境变量值；
- 远程环境导出脚本；
- 可能包含用户正文的完整异常命令。

### 11.3 明确的隐私边界

- 本设计保证 Bash MCP 自身不新增敏感日志扩散。
- AI Assistant 当前会持久化工具 arguments，因此 `run.argv`、`script.script` 和 `stdin` 可能进入会话存储及工具详情。
- 该能力解决转义、参数边界和误执行问题，不等同于秘密传输。
- 凭证仍应只通过 `meta.shell_env` 等受控机制传入，不应写入 argv、script 或 stdin。
- 如果未来要求正文也不持久化，需要单独设计敏感参数引用、外部 blob 或脱敏存储协议。

### 11.4 远程环境风险

如果现有远程 `meta.shell_env` 实现将环境值编码在本地 SSH 进程参数中，值可能短暂出现在本机进程列表中。

本任务必须：

1. 核实当前实现；
2. 用假值测试日志和错误输出不会泄露；
3. 在文档中如实记录剩余风险；
4. 不得宣称环境值已经通过完全独立的数据通道传输。

若要消除此风险，应另行采用权限受限的环境数据文件或等价协议，不与本次工具拆分强行捆绑。

---

## 12. 错误码

保留现有错误码，并新增或明确：

| 错误码 | 含义 |
|---|---|
| `INVALID_ARGV` | argv 类型、首元素或元素格式非法 |
| `ARGV_TOO_LARGE` | argv 数量或总字节数超限 |
| `INVALID_SCRIPT` | script 类型或内容非法 |
| `SCRIPT_TOO_LARGE` | script 超限 |
| `STDIN_TOO_LARGE` | stdin 超限 |
| `ARTIFACT_PREPARE_ERROR` | 本地临时资源准备失败 |
| `SCRIPT_SYNTAX_ERROR` | `bash -n` 失败，正式脚本未执行 |
| `REMOTE_SCRIPT_UPLOAD_ERROR` | 远程脚本上传失败 |
| `REMOTE_ARTIFACT_ERROR` | 远程临时资源准备失败 |

清理失败不建议覆盖主结果 code，应进入 `warnings` 或安全日志。

所有输入错误：

- `success=false`；
- 不启动目标进程；
- `background_pid=null`；
- 返回对应 `tool_mode`；
- 不在错误消息中包含环境值、stdin 或完整脚本。

---

## 13. 配置

建议新增配置项，名称可结合现有项目风格最终确定：

```text
BASH_MAX_ARGV_COUNT
BASH_MAX_ARGV_BYTES
BASH_MAX_SCRIPT_BYTES
BASH_MAX_STDIN_BYTES
BASH_ARTIFACT_DIR
BASH_ARTIFACT_TTL_SECONDS
REMOTE_ARTIFACT_BASE_DIR
```

要求：

- 提供安全且足够日常开发的默认值；
- 配置解析失败时使用明确默认值或启动错误，不能变成无限制；
- 更新 `.env.example` 和 README；
- 远程根目录固定，不允许由模型传入任意路径。

---

## 14. AI Assistant 集成

### 14.1 ServerPool

验证：

- `bash`、`run`、`script` 都从同名 `timeout` 参数计算 MCP transport deadline；
- invocation ID 继续注入；
- `meta.shell_env` 继续注入；
- session/branch metadata 不受影响；
- 工具 Schema 能正确发现四个工具。

### 14.2 工具参数持久化

核对：

- argv 数组、script 和 stdin 可正常 JSON 序列化与恢复；
- 工具详情页面不会因新字段崩溃；
- 大输入上限与消息、数据库和请求体边界协调；
- 不需要数据库 migration，除非实际模型不是自由 JSON 结构。

### 14.3 工具说明

模型选择规则：

```text
普通程序和 CLI：优先 run
长文本或程序输入：通过 run/script 的 stdin
管道、重定向、循环和 $VAR 展开：script
现有或简短 Shell 命令：bash
后台结果：get
```

必须明确：

- `run.argv` 不进行 Shell 展开；
- `run` 仍继承完整环境并接收 `meta.shell_env`；
- 不要把邮件正文、Markdown、JSON 或代码块嵌入 `bash.command`；
- `script` 用于 Shell 逻辑，不是所有命令的默认工具；
- 凭证不应写入普通工具参数。

### 14.4 `bash-usage-guide`

至少包含：

- 本地和远程 `bash`；
- 本地和远程 `run`；
- 本地和远程 `script`；
- `run + stdin`；
- `script + stdin`；
- 环境变量传递与 `$VAR` 展开的区别；
- 后台任务 `get`；
- arguments 会持久化的隐私提醒。

---

## 15. 测试设计

### 15.1 单元测试

覆盖：

- `run.argv` 类型、空数组、空 argv0、空字符串参数、空字节和大小限制；
- `script` 空值、空字节、大小限制和语法错误；
- stdin 的 `None`、空字符串、Unicode、换行和大小限制；
- 工具摘要不包含正文、完整 argv 和环境值；
- 三个工具构造正确的统一请求；
- `target_mode`、`tool_mode` 和后台记录；
- artifact 权限、幂等清理和 TTL 边界；
- 新错误码与旧结果字段兼容。

### 15.2 本地真实进程集成测试

不依赖运行中的真实 MCP 服务，直接调用执行内核：

- `run` 不执行 Shell 展开；
- 空参数、引号、Shell 符号、Unicode 和换行保持 argv 边界；
- stdin 内容与 EOF 完整；
- 目标不读取 stdin 时，后台返回不因 pipe 写入阻塞；
- script 语法错误不会产生正式执行副作用；
- `script + stdin`；
- cwd 和 `meta.shell_env`；
- 前台成功、非零退出、timeout background、timeout kill 和 `get`；
- 输出文件、脚本和 stdin 临时文件清理；
- 并发任务互不污染。

### 15.3 隔离 MCP 协议测试

启动独立临时实例，不停止或重启真实 Bash MCP：

- 优先使用 stdio，不占用真实端口；
- 工具发现包含 `bash`、`run`、`script`、`get`；
- JSON Schema 符合最终签名；
- 真实 MCP 客户端可调用三个执行工具；
- metadata、ToolResult、background 和 `get` 正常；
- 测试结束只关闭临时测试进程。

如需 SSE，使用随机独立端口、临时配置和临时运行目录。

### 15.4 真实 SSH 集成测试

通过 `RUN_REAL_SSH_TESTS=1` 和测试 host alias 显式启用：

- 远程 `bash` 回归；
- 远程 `run` 参数边界；
- 远程 stdin；
- 远程 script 上传、语法检查和执行；
- cwd 和 `meta.shell_env`；
- 后台 PID/PGID、`get` 和 kill；
- 本地及远程 artifact 清理；
- 并发任务隔离；
- 不可达 host 快速失败。

测试失败时也必须尽力清理远程测试目录。

### 15.5 AI Assistant 测试

- 四个工具 Schema 可发现；
- ServerPool timeout 计算覆盖 `run` 和 `script`；
- invocation ID 与 `meta.shell_env` 注入；
- arguments 序列化、持久化和恢复；
- 工具详情页面兼容；
- 技能和工具说明更新。

### 15.6 不重启真实服务时的验证边界

开发完成后无需重启真实 Bash MCP，即可完成：

- 全部单元测试；
- 本地真实进程集成测试；
- 隔离 MCP stdio/SSE 协议测试；
- 真实 SSH 集成测试；
- AI Assistant 静态及隔离集成测试。

唯一无法证明的是：当前运行中的真实实例已经加载新代码和新 Schema。

最终上线仅保留：

1. 用户明确确认本次真实服务重启；
2. 重启作为该轮最后一个工具调用；
3. 服务恢复后的下一轮验证工具发现；
4. 分别执行 `bash`、`run`、`script` 和后台 `get` 最小冒烟测试。

---

## 16. 实施阶段

### 阶段 1：统一模型与本地 `run`

- 新增 `run` 工具；
- 引入 `ExecutionRequest`；
- 重命名内部 `target_mode`；
- 增加 `tool_mode`；
- 本地 argv 直接执行；
- 保持 `bash` 回归通过。

验收：本地参数边界、环境、cwd、输出、timeout、background、kill 和 `get`。

### 阶段 2：artifact 与 stdin

- 引入统一 artifact 管理；
- stdin 使用权限受限临时文件；
- 支持 `run + stdin`；
- 增加 TTL 回收；
- 覆盖后台和异常清理。

验收：大 stdin 不阻塞后台返回，无提前删除和残留泄漏。

### 阶段 3：本地 `script`

- 新增 `script` 工具；
- 本地脚本临时文件；
- `bash -n`；
- `script + stdin`；
- 完整清理和错误映射。

验收：语法错误零副作用，前台、后台和 kill 路径资源正确。

### 阶段 4：远程 `run` 与 stdin

- 固定 bootstrap；
- 严格逐参数 quoting；
- 远程 stdin 文件转发；
- 保留 PID/PGID 和 kill 语义。

验收：真实 SSH 参数往返、环境、cwd、后台和 kill。

### 阶段 5：远程 `script`

- 两阶段上传与执行；
- 远程 `bash -n`；
- 远程 artifact 清理与 TTL；
- 错误路径和并发隔离。

验收：真实 SSH script、stdin、后台、kill 和残留回收。

### 阶段 6：AI Assistant 集成和文档

- ServerPool 测试；
- 工具持久化与 UI 兼容；
- README、`.env.example`、工具 docstring；
- `bash-usage-guide`；
- 隔离 MCP 协议回归。

验收：四工具可发现，模型说明无歧义，不需要重启真实服务即可完成发布前测试。

每阶段必须独立实现、验证和复核；未经用户明确要求不得自动提交代码。

---

## 17. 最终验收标准

### 功能

- 提供 `bash`、`run`、`script`、`get` 四个工具；
- `bash` 保持现有接口和行为；
- `run` 本地和远程均保持 argv 边界；
- `script` 本地和远程均使用临时文件并先做语法检查；
- `run`、`script` 均支持 stdin；
- 三个执行工具共享 cwd、环境、输出、timeout、background、kill 和 `get`。

### 兼容

- 旧 `bash` 调用无需修改；
- 旧结果字段和错误码保持不变；`COMMAND_ERROR` 统一表示 `success=false` 的命令失败；
- host alias、输出文件和 `get(host, pid)` 行为不变；
- 仅新增工具和 `tool_mode` 字段。

### 安全与稳定性

- argv 不被意外解释为 Shell；
- script 和 stdin 不进入 Bash MCP 日志或摘要；
- artifact 权限受限、路径受控、清理幂等；
- 后台任务结束前资源不会被提前删除；
- 崩溃残留可通过 TTL 回收；
- 不可达 SSH 和错误路径不会无限阻塞；
- 清理失败不会覆盖真实命令结果。

### 测试

- 全部单元测试通过；
- 本地真实进程集成测试通过；
- 隔离 MCP 协议测试通过；
- AI Assistant 相关测试通过；
- 有测试 host 时，真实 SSH 集成测试通过；
- 真实服务重启前无需依赖真实运行实例完成开发调试。

---

## 18. 实施前固定决策

如无新的产品要求，开发时直接采用以下决策：

1. 保留 `bash`，新增 `run`、`script`，继续共享 `get`。
2. 不向 `bash` 增加 argv、script 或 stdin 参数。
3. `run` 是普通程序推荐工具，argv 不进行 Shell 展开。
4. `script` 专用于 Bash 逻辑，并在正式执行前运行 `bash -n`。
5. stdin 仅属于 `run`、`script`，采用权限受限临时文件，不使用无人管理的 pipe 写入协程。
6. 环境继续通过继承环境和 `meta.shell_env` 提供，不新增普通 `env` 参数。
7. 本地和远程最终都支持三个执行工具，不接受永久能力缺口。
8. 远程 argv 首版采用固定 bootstrap + 逐参数严格 quoting，并由真实 SSH 测试证明边界。
9. 远程 script 采用上传与正式执行分离的两阶段协议。
10. 临时资源必须同时具备终态清理和 TTL 崩溃恢复。
11. `tool_mode` 表示 `bash|run|script`，`target_mode` 表示 `local|ssh`。
12. AI Assistant arguments 持久化属于明确隐私边界，凭证不得放入普通工具参数。
13. 开发和发布前测试不依赖重启真实 Bash MCP；真实重启仅用于最终加载和冒烟验证。

---

## 19. 新会话执行提示

新会话接手时按以下顺序执行：

1. 阅读本文件、README、现有实现和测试；
2. 检查 Git 状态，保留用户已有改动；
3. 对照实际源码复核文档假设；
4. 按阶段实施，每阶段只做一个可验证的语义切片；
5. 先补测试，再完成本地、隔离 MCP 和真实 SSH 验证；
6. 不停止真实服务；真实重启必须获得用户针对本次操作的明确确认；
7. 未明确要求时不自动提交代码。

一句话原则：

> 外部采用 `bash / run / script / get` 四个清晰工具，内部共享一套执行生命周期；普通命令走 argv，长文本走 stdin，复杂 Shell 走 script，发布前测试不依赖重启真实服务。
