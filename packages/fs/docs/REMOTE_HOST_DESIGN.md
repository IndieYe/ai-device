> **已退役：历史 SSH 设计，不再是当前实现契约。** 内置远程后端与工具 `host` 参数已删除。请以 README 和当前工具 Schema 为准；MCP 网络传输及监听地址仍保留。

# ai-device-fs 远程 Host 支持需求设计文档

## 1. 背景

当前 `ai-device-fs` 仅支持对本地文件系统执行 5 个 MCP 工具：

- `read_file`
- `write_file`
- `edit_file`
- `insert_file`
- `apply_patch`

目标是在**不破坏现有本地行为**的前提下，为这 5 个工具统一新增一个可选参数 `host`，用于访问远程 Linux 服务器上的文件系统。

语义约定：

- `host` 不传：继续操作本地文件系统
- `host` 传入：操作配置文件中定义的远程 Linux 主机文件系统

本设计文档用于后续在全新会话中直接实施开发。

---

## 2. 目标与非目标

### 2.1 目标

1. 为 5 个工具统一新增可选参数 `host`
2. `host` 来自服务端受控配置，而不是自由输入 SSH 连接串
3. 远程机器保证存在 `python3`
4. 远程执行语义尽量与本地保持一致：
   - 路径展开
   - UTF-8 文本约束
   - 原子写入
   - 权限保留
   - `dry_run`
   - `include_diff`
   - `apply_patch` 的回滚语义
5. 本地模式逻辑尽量少改，避免回归
6. 为远程模式补齐测试

### 2.2 非目标

本次不做以下能力：

1. 不支持 Windows 远程主机
2. 不支持 SFTP/FTP/SMB 等非 SSH 协议
3. 不支持用户直接传 `user@host:port` 之类自由格式
4. 不支持一次调用跨多个 host
5. 不支持二进制文件编辑能力扩展
6. 不支持为远程模式单独引入长期驻留 agent
7. 不支持 patch 跨 host 操作

---

## 3. 核心设计结论

### 3.1 总体方案

采用**统一 backend + SSH 远程 Python 脚本执行**方案。

- 本地模式：继续调用当前本地实现
- 远程模式：通过 SSH 调用远程 `python3` 执行脚本
- 远程脚本负责真正的文件操作与结果计算
- 本地 MCP 服务负责：
  - 解析 `host`
  - 加载 host 配置
  - 调用 SSH
  - 传输参数
  - 接收 JSON 结果
  - 将结果封装为现有 `ToolResult`

### 3.2 为什么不采用“本地算完再远程写回”

不推荐原因：

1. `edit_file` / `insert_file` / `apply_patch` 需要多轮远程交互，延迟高
2. `apply_patch` 的一致性与回滚语义更难保证
3. 大文件和大 diff 会导致本地与远程之间传输成本变高
4. 容易出现本地/远程语义漂移

### 3.3 为什么采用“远程 Python 脚本执行”

优势：

1. 远程 Linux 已保证有 `python3`
2. 当前项目本身就是 Python，逻辑复用空间大
3. 远程可直接完成：
   - 路径处理
   - 文件读写
   - diff 生成
   - patch 应用
   - 回滚
4. 比拼接复杂 shell 更稳、更可测、更少兼容性问题

---

## 4. 用户可见行为变更

### 4.1 所有工具新增参数

5 个工具全部新增一个可选参数：

- `host: str | None = None`

语义：

- `None`：本地执行
- 非空字符串：按 host 名称查配置并远程执行

### 4.2 工具签名目标

#### `read_file`

```python
read_file(
    path: str,
    type: Literal["bytes", "chars", "lines"] = "chars",
    offset: int = 0,
    limit: Optional[int] = None,
    direction: Literal["forward", "backward"] = "forward",
    max_chars: int = DEFAULT_MAX_CHARS,
    host: Optional[str] = None,
)
```

#### `write_file`

```python
write_file(path: str, content: str, host: Optional[str] = None)
```

#### `edit_file`

```python
edit_file(
    path: str,
    edits: List[Dict[str, str]],
    dry_run: bool = False,
    include_diff: bool = False,
    host: Optional[str] = None,
)
```

#### `insert_file`

```python
insert_file(
    path: str,
    text: str,
    line: Optional[int] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    at_end: bool = False,
    dry_run: bool = False,
    include_diff: bool = False,
    host: Optional[str] = None,
)
```

#### `apply_patch`

需先确认现有签名；从 README 看是：

```python
apply_patch(patch: str, dry_run: bool = False, host: Optional[str] = None)
```

开发时需以源码真实签名为准同步调整 docstring。

### 4.3 返回结构

原则：**尽量不改现有 JSON 返回结构**。

允许新增少量非破坏性字段，建议统一加在：

- `summary`
- `operations[*]`
- `error.details`

中。

推荐新增：

- `host`: `null` 或 host 名
- `execution_mode`: `local` / `remote`

但注意：
- 若现有测试严格比对 JSON，需谨慎处理
- 如果新增字段会导致过多测试改动，可只在 `ToolResult.meta` 中新增，不放入 `content`

**实施建议**：
第一版先保持 `content` 结构基本不变，只在 `ToolResult.meta` 中补：

```python
meta={
  "summary": ...,
  "path": ...,
  "host": host,
  "execution_mode": "remote" if host else "local",
}
```

如果测试和客户端兼容性允许，再考虑把 `host` 放进 JSON payload。

---

## 5. Host 配置设计

### 5.1 基本要求

`host` 不能直接作为 SSH 目标拼接使用，必须从配置中查找。

例如用户调用：

```json
{"path":"/etc/hosts","host":"prod"}
```

服务端读取配置，将 `prod` 映射为：

- hostname
- user
- port
- identity_file
- 可选额外 ssh 参数

### 5.2 推荐配置来源

建议沿用当前项目的 `.env` 风格，并新增一个 JSON 配置项。

推荐环境变量：

- `MCP_REMOTE_HOSTS_JSON`

值示例：

```json
{
  "prod": {
    "hostname": "10.0.0.12",
    "user": "deploy",
    "port": 22,
    "identity_file": "~/.ssh/id_rsa"
  },
  "staging": {
    "hostname": "staging.example.com",
    "user": "ubuntu",
    "port": 22,
    "identity_file": "~/.ssh/staging_key"
  }
}
```

### 5.3 为什么优先选 JSON 环境变量

原因：

1. 当前项目已有 `.env` 习惯
2. 实现成本低
3. 部署简单
4. 不需要额外引入 YAML 解析

### 5.4 可选替代方案

如果你更希望配置独立文件，也可用：

- `MCP_REMOTE_HOSTS_FILE=~/.ai-device-fs-hosts.json`

但**第一版建议只做 `MCP_REMOTE_HOSTS_JSON`**，减少分支。

### 5.5 Host 配置数据结构

建议新增数据模型：

```python
from dataclasses import dataclass
from typing import Optional, Sequence

@dataclass(frozen=True)
class RemoteHostConfig:
    name: str
    hostname: str
    user: Optional[str] = None
    port: int = 22
    identity_file: Optional[str] = None
    ssh_options: tuple[str, ...] = ()
```

### 5.6 Host 配置校验规则

加载配置时需校验：

1. host 名不能为空
2. `hostname` 必填
3. `port` 必须是正整数
4. `identity_file` 如存在，先本地展开 `~`
5. `ssh_options` 如存在，必须是字符串列表

### 5.7 错误语义

建议新增错误码：

- `REMOTE_HOST_NOT_CONFIGURED`
- `REMOTE_HOST_CONFIG_INVALID`

---

## 6. 目录与模块设计

建议新增以下模块：

```text
mcp_server_fs/
  remote/
    __init__.py
    config.py
    models.py
    ssh_runner.py
    remote_backend.py
    remote_script.py
```

### 6.1 模块职责

#### `remote/models.py`
放远程相关 dataclass：

- `RemoteHostConfig`
- `RemoteExecutionResult`

#### `remote/config.py`
负责：

- 加载 `MCP_REMOTE_HOSTS_JSON`
- 解析 host 配置
- 按名字返回 `RemoteHostConfig`

#### `remote/ssh_runner.py`
负责：

- 组装 `ssh` 命令
- 通过 subprocess 调用 SSH
- 将脚本 + JSON 参数发送到远程
- 接收 stdout/stderr/exit code
- 超时控制

#### `remote/remote_script.py`
负责：

- 持有远程执行脚本文本模板
- 或脚本生成函数

#### `remote/remote_backend.py`
负责：

- 提供统一方法：
  - `remote_read_file(...)`
  - `remote_write_file(...)`
  - `remote_edit_file(...)`
  - `remote_insert_file(...)`
  - `remote_apply_patch(...)`
- 将参数打包为 JSON
- 调 `ssh_runner`
- 解析远程返回

---

## 7. 远程执行协议设计

### 7.1 总体协议

远程执行统一采用：

- 本地通过 `ssh ... python3 -` 启动远程 Python
- 将 Python 脚本通过 stdin 发送
- 远程脚本内部再从环境变量或 argv 获取 operation
- 参数通过 stdin 第二段或命令行/base64 传输

### 7.2 推荐落地方式

为避免 shell quoting 复杂度，推荐：

#### 方案：脚本走 stdin，参数走 base64 argv

本地执行类似：

```bash
ssh <target> python3 -c '<bootstrap>' <operation> <payload_base64>
```

但这种方式对 bootstrap quoting 也不优。

#### 更推荐的最终方案：单次 stdin 传 JSON envelope

本地命令：

```bash
ssh <target> python3 -
```

stdin 内容为完整 Python 脚本。

脚本运行后，从环境变量 `MCP_REMOTE_REQUEST_B64` 中读取请求。

本地通过 SSH 命令前加环境变量：

```bash
MCP_REMOTE_REQUEST_B64=... python3 -
```

但远程 shell 上环境变量也有 quoting 问题和长度问题。

#### 最稳方案

本设计最终推荐：

**通过 ssh 执行一个很小的 bootstrap，bootstrap 从 stdin 读取一个 JSON 包：前半段是脚本，后半段是请求。**

但实现复杂。

### 7.3 为了实现简单，第一版明确采用以下方案

**方案 C：ssh 调远程 `python3 -c`，代码极小；真正业务请求为 base64 JSON，内嵌在 argv。**

不过考虑到 Python 脚本本身会较大，最佳平衡点是：

### 7.4 最终确定方案

**本地 SSH 命令直接执行一个内联 bootstrap；bootstrap 中嵌入远程业务脚本源码字符串并执行。**

更简单的工程实现如下：

1. 在本地代码中维护一个远程脚本字符串 `REMOTE_SCRIPT`
2. SSH 命令为：

```bash
ssh <target> python3 -c '<bootstrap>' '<payload_b64>'
```

3. bootstrap 做：
   - base64 解码 `REMOTE_SCRIPT`
   - `exec()` 脚本
   - 传入 `payload_b64`

但这样会有双层 quoting。

### 7.5 进一步收敛后的实施建议

**最易实现、最稳、最适合当前项目的方式：**

- `subprocess.run([...], input=REMOTE_SCRIPT_SOURCE, text=True, ...)`
- SSH 命令固定为：

```bash
ssh <target> python3 - --payload-b64 <payload_b64>
```

- `python3 -` 表示从 stdin 读取脚本源码
- 远程脚本通过 `argparse` 读取 `--payload-b64`

这样优点是：

1. 不需要 shell heredoc
2. 远程脚本无需预先落盘
3. payload 与脚本分离
4. quoting 风险最低
5. 当前 Python `subprocess.run` 很容易实现

**后续实施必须按这一方案做。**

### 7.6 远程请求 JSON 结构

统一请求格式：

```json
{
  "operation": "read_file",
  "arguments": {
    "path": "/tmp/a.txt",
    "type": "chars",
    "offset": 0,
    "limit": null,
    "direction": "forward",
    "max_chars": 10000
  }
}
```

### 7.7 远程响应 JSON 结构

统一响应格式：

```json
{
  "success": true,
  "result": {
    "kind": "text",
    "content": "{...原工具返回 JSON 字符串...}"
  },
  "meta": {
    "path": "/tmp/a.txt"
  }
}
```

图片结果使用同一类型化入口：

```json
{
  "success": true,
  "result": {
    "kind": "image",
    "data": "<纯 Base64>",
    "size": 12345,
    "path": "/tmp/a.png"
  },
  "meta": {
    "path": "/tmp/a.png"
  }
}
```

失败时：

```json
{
  "success": false,
  "error": {
    "code": "REMOTE_EXECUTION_ERROR",
    "message": "...",
    "details": {...}
  }
}
```

### 7.8 为什么远程响应使用类型化 `result`

远程文本工具和图片工具的返回类型不同，必须由协议显式区分，避免把图片 Base64 降级为普通文本。

这样做的好处是：

1. 远程层和工具层边界清楚
2. 文本工具继续复用现有 JSON 字符串 payload
3. 图片字节可在本地完成 Base64、大小、格式、完整性和像素限制校验后再转成标准 MCP `ImageContent`

---

## 8. 本地与远程共用逻辑的重构建议

### 8.1 不要在工具函数里直接塞大量远程分支

避免这种写法：

```python
def write_file(..., host=None):
    if host:
        ...很多远程逻辑...
    else:
        ...很多本地逻辑...
```

会导致 5 个工具都变得臃肿。

### 8.2 推荐抽出“核心业务函数”

建议把现有工具的核心逻辑从 `ToolResult` 封装中拆出来。

例如：

- `execute_read_file(args) -> tuple[payload_str, meta_dict]`
- `execute_write_file(args) -> tuple[payload_str, meta_dict]`
- `execute_edit_file(args) -> tuple[payload_str, meta_dict]`
- `execute_insert_file(args) -> tuple[payload_str, meta_dict]`
- `execute_apply_patch(args) -> tuple[payload_str, meta_dict]`

然后：

- 本地工具：直接调用核心函数
- 远程脚本：也调用相同或等价核心函数

### 8.3 现实约束

因为远程执行发生在目标主机 Python 进程内，最理想是远程脚本能直接 import 项目内模块。

但当前项目代码**不在远程机器上**，因此不能依赖远程 import 本仓库模块。

所以这里要做一个取舍：

### 8.4 最终建议

第一版不要强行把远程逻辑设计成远程 import 本地项目模块，而是：

- 本地保留现有工具实现
- 远程脚本内复制一份必要的核心逻辑

虽然有少量重复，但：

1. 实现最直接
2. 不依赖远程部署项目代码
3. 能最快落地

### 8.5 如何控制重复成本

建议把远程脚本写成一个独立生成模块，例如：

- `mcp_server_fs/remote/remote_script.py`

其内容应尽量按“与本地 common/tool 逻辑一致”的方式组织，并在注释中明确：

- 当本地工具语义变化时，需要同步更新远程脚本逻辑

---

## 9. SSH Runner 详细设计

### 9.1 依赖

仅依赖本地系统存在：

- `ssh`

远程存在：

- `python3`

### 9.2 命令构造

建议用 `subprocess.run([...])` 传数组，不使用 `shell=True`。

SSH 参数构造规则：

- 基础命令：`ssh`
- `-p <port>` 若端口非默认或统一显式传也可以
- `-i <identity_file>` 若配置存在
- `-o BatchMode=yes`
- `-o StrictHostKeyChecking=yes`（推荐默认）
- `-o ConnectTimeout=10`（建议）
- 额外 `ssh_options` 可附加
- 目标：`user@hostname` 或 `hostname`
- 远程命令：`python3 - --payload-b64 <...>`

### 9.3 超时

建议为远程执行新增统一超时常量，例如：

- `REMOTE_SSH_TIMEOUT_SECONDS = 60`

第一版可先写死在代码中，后续再配置化。

### 9.4 stdout/stderr 约定

- 远程脚本的 stdout：只输出一段 JSON
- 远程脚本的 stderr：保留调试/错误信息

本地解析时：

1. 若 SSH 退出码非 0，优先返回 `REMOTE_SSH_ERROR`
2. 若 stdout 不是合法 JSON，返回 `REMOTE_PROTOCOL_ERROR`
3. 若远程 JSON 中 `success=false`，映射为对应工具错误

### 9.5 建议新增错误码

- `REMOTE_SSH_ERROR`
- `REMOTE_PROTOCOL_ERROR`
- `REMOTE_TIMEOUT`
- `REMOTE_PYTHON_UNAVAILABLE`
- `REMOTE_EXECUTION_ERROR`

---

## 10. 路径语义

### 10.1 本地模式

保持不变：

- `~` 按本地用户展开
- 相对路径相对当前 MCP 进程工作目录

### 10.2 远程模式

必须重新定义：

- `~` 按**远程用户**展开
- 相对路径相对**远程进程启动目录**

由于 SSH 默认远程 cwd 通常是远程用户 home，第一版可接受：

- 相对路径相对远程用户 home / 登录默认目录

但需在文档中明确，避免歧义。

### 10.3 实施要求

远程脚本中不要依赖本地 `validate_path()`，要在远程用 Python 自己实现等价逻辑：

```python
path = Path(path_str).expanduser()
if not path.is_absolute():
    path = Path.cwd() / path
path = path.resolve()
```

### 10.4 symlink 语义

第一版保持与当前本地逻辑一致，即 `resolve()` 后操作真实路径。

---

## 11. 各工具详细设计

## 11.1 `read_file`

### 目标

远程读取 UTF-8 文本文件，支持：

- `bytes`
- `chars`
- `lines`
- `offset`
- `limit`
- `direction`
- `max_chars`

### 本地逻辑现状

当前 `read_file.py`：

1. `validate_path(path)`
2. 判断存在与文件类型
3. 按读取模式读取
4. 超过 `max_chars` 时直接截断返回，不落临时文件
5. 返回 `ToolResult`

### 远程推荐实现

远程脚本内完整实现同等逻辑。

### 注意点

1. `type="bytes"` 时：
   - 先按字节切片
   - 再 `decode('utf-8', errors='replace')`
2. `type="chars"` / `lines`：
   - 读取整文件到内存
3. 保持与本地返回字段一致
4. 保持错误类型尽量一致：
   - 文件不存在
   - 不是文件
   - 解码失败/读取异常

### 性能说明

- 小文件正常
- 大文件仍要跨 SSH 回传内容
- 第一版接受此成本

---

## 11.2 `write_file`

### 目标

远程创建或覆盖文本文件，并保持：

- UTF-8 写入
- 原子写入
- 若覆盖已有文件则保留权限位

### 本地逻辑现状

依赖 `atomic_write_text()`：

1. 创建父目录
2. 若目标存在则读取原权限
3. `mkstemp`
4. 写入临时文件
5. `chmod`
6. `os.replace`

### 远程推荐实现

远程脚本中实现等价的 `atomic_write_text`。

### 注意点

1. 新文件权限遵循远程系统默认 `umask`
2. 覆盖已有文件时保留原权限位
3. 错误结构尽量与本地一致

### 风险

- 远程目录不可写
- 父路径不是目录
- 磁盘满
- 权限不足

---

## 11.3 `edit_file`

### 目标

远程执行精确替换：

- `edits` 按顺序执行
- 每条只替换一次
- `dry_run`
- `include_diff`
- 原子写回

### 本地逻辑现状

1. 读取整文件
2. 对每条 edit 做 `replace(..., 1)`
3. 任一 `oldText` 不存在则报错
4. 用 `difflib.unified_diff` 生成 diff
5. 非 `dry_run` 时原子写回

### 远程推荐实现

远程脚本中完整实现同样逻辑。

### 注意点

1. 需保持 edit 顺序
2. 每条 edit 仅替换一次
3. `include_diff=false` 时仍可内部生成 diff 或按需生成

### 实施建议

为了减少无意义开销：

- 当 `include_diff=false` 且 `dry_run=false` 时，可不生成 diff
- 但这会与本地行为略有实现差异，不影响返回结构

**为减少回归，第一版建议仍统一生成 diff。**

### 大 diff 处理

本地 `build_success_response(... include_diff=True)` 会调用 `truncate_with_file_fallback`，把超长 diff 保存到**本地 MCP 临时目录**。

远程模式也应保持这个语义：

- 远程脚本只返回完整 diff 文本
- 本地 MCP 进程再决定是否保存到本地 `MCP_TEMP_DIR`

所以远程脚本不应自己创建 diff 临时文件。

这一点很重要。

---

## 11.4 `insert_file`

### 目标

远程执行文本插入，支持：

- `line`
- `before`
- `after`
- `at_end`
- `dry_run`
- `include_diff`
- 原子写回

### 本地逻辑现状

1. 校验 4 种模式必须且只能选一种
2. 读取文件
3. 计算插入点
4. 拼接新内容
5. 生成 unified diff
6. 非 `dry_run` 时原子写回

### 远程推荐实现

远程脚本中完整实现同等逻辑。

### 注意点

1. `line` 为 1-based
2. `line == total_lines + 1` 表示追加到末尾
3. `before` / `after` 只匹配首次出现
4. 锚点不存在时报错
5. 保持错误文本尽量一致

### diff 处理

与 `edit_file` 相同：

- 远程返回完整 diff 文本
- 本地决定是否截断与落本地临时文件

---

## 11.5 `apply_patch`

### 目标

远程完整应用 patch DSL，并保持：

- Add/Delete/Update/Move
- dry_run
- 多文件校验
- 提交失败回滚
- 仅支持 UTF-8 文本
- 单文件大小限制 2 MiB

### 本地逻辑现状

本地已有：

- `patch/parser.py`
- `patch/executor.py`
- `patch/models.py`
- `patch/errors.py`

执行路径大致为：

1. 解析 patch 文档
2. 校验 operation graph
3. 模拟每个操作
4. `dry_run` 直接返回模拟结果
5. 否则建立快照
6. 逐个提交文件变更
7. 若失败则回滚

### 远程推荐实现

远程脚本内实现一套与当前 `patch/executor.py` 语义一致的 patch 解析与执行逻辑。

### 关键困难

`apply_patch` 是整个远程支持中最复杂的一部分，因为它涉及：

1. 多文件路径解析
2. hunk 精确匹配
3. source/target 路径冲突校验
4. move 语义
5. commit/rollback
6. 文件大小与 UTF-8 校验

### 实施策略

第一版建议：

- 将当前 `patch/parser.py` / `executor.py` 的核心逻辑**移植/内嵌**到远程脚本中
- 保持错误码尽量一致

### 非推荐方案

不要做：

- 本地 parse patch + 远程逐文件提交

因为这样：

1. patch graph 与目标文件真实状态不在同一执行上下文
2. 回滚更麻烦
3. 多轮 SSH 往返性能更差

### 大 patch 处理

patch 文本本身由本地打包到 payload 中传给远程。

如 patch 非常大，可能导致：

- SSH argv 过长

所以这里必须注意：

### 关键要求

`payload_b64` 不能走过长 argv，如果担心极端长度，需让请求也走 stdin。

但在本次第一版中，常规 patch 文本通常仍可接受；不过为了架构更稳：

### 最终建议

本次远程协议不要只支持 argv payload，应支持：

- 脚本从 stdin 读取 JSON 请求内容

也就是：

1. SSH 执行 `python3 -`
2. stdin 传入的是一个 envelope：
   - 固定脚本代码
   - 或脚本内直接再读后续 stdin 请求

不过这样实现会复杂。

### 为保证可实现性，第一版在这里做明确折中

**第一版仍采用 `--payload-b64` argv 方案。**

原因：

- 代码简单
- 多数场景足够用
- 当前 patch 通常不会大到突破命令行长度上限

但在设计与实现中要明确：

- 这是已知限制
- 后续如遇超长 patch，再升级为 stdin 传 request 的协议

### 错误码

尽量保持与现有 patch 错误码一致，例如：

- `PATCH_INVALID_PATH`
- `PATCH_FILE_TOO_LARGE`
- `PATCH_BINARY_FILE_NOT_SUPPORTED`
- `PATCH_CONTEXT_NOT_FOUND`
- `PATCH_CONTEXT_AMBIGUOUS`
- `PATCH_MOVE_TARGET_EXISTS`
- `PATCH_INTERNAL_ERROR`

### 回滚语义

远程模式必须由**远程脚本自己**完成回滚，不能依赖本地补偿。

---

## 12. 远程脚本内部结构建议

建议远程脚本内部组织如下：

1. 通用 helper
   - 路径解析
   - JSON 输出
   - 原子写入
   - truncation helper（如需要）
2. 5 个 operation handler
   - `handle_read_file`
   - `handle_write_file`
   - `handle_edit_file`
   - `handle_insert_file`
   - `handle_apply_patch`
3. patch parser/executor helper
4. `main()`：
   - parse `--payload-b64`
   - dispatch operation
   - print JSON to stdout
   - exception -> print structured error JSON

### 12.1 stdout 纪律

远程脚本 stdout 必须只输出 JSON，一次且仅一次。

### 12.2 stderr 纪律

stderr 可输出调试信息，但默认尽量保持空。

---

## 13. 本地工具层改造要求

每个工具函数统一改造模式：

1. 增加 `host: Optional[str] = None`
2. 若 `host is None`：走现有本地逻辑
3. 若 `host` 非空：
   - 加载 host 配置
   - 调 remote backend
   - 将结果转回 `ToolResult`

### 13.1 推荐封装辅助函数

建议新增一个统一 helper，例如：

```python
def build_tool_result_from_remote_response(...):
    ...
```

负责：

- remote JSON -> `ToolResult`
- 统一补 meta
- 统一错误回退

### 13.2 meta 设计

建议远程模式 meta 至少包含：

```python
{
  "summary": "read hosts",
  "path": "/etc/hosts",
  "host": "prod",
  "execution_mode": "remote"
}
```

本地模式可同步补：

```python
{
  "summary": ...,
  "path": ...,
  "host": None,
  "execution_mode": "local"
}
```

如担心测试影响，可只在远程模式加新字段。

---

## 14. 错误处理设计

### 14.1 错误分层

错误分 4 层：

1. 工具参数/业务错误
2. host 配置错误
3. SSH 连接/执行错误
4. 远程脚本内部错误

### 14.2 推荐错误映射

#### host 配置相关
- `REMOTE_HOST_NOT_CONFIGURED`
- `REMOTE_HOST_CONFIG_INVALID`

#### SSH 执行相关
- `REMOTE_SSH_ERROR`
- `REMOTE_TIMEOUT`
- `REMOTE_PYTHON_UNAVAILABLE`

#### 协议相关
- `REMOTE_PROTOCOL_ERROR`

#### 远程执行兜底
- `REMOTE_EXECUTION_ERROR`

### 14.3 工具层返回格式

不要直接抛异常到 FastMCP。

应继续返回与现有工具一致风格的 JSON 字符串：

- 成功：原有 success payload
- 失败：原有 error payload

并在 `details` 中加入：

- `host`
- `execution_mode="remote"`
- `ssh_exit_code`（如有）
- `stderr`（必要时）

---

## 15. 性能与限制说明

### 15.1 可接受的性能损耗

第一版接受以下额外成本：

1. 每次远程工具调用都新建一次 SSH 会话
2. 远程脚本每次通过 stdin 下发
3. 大文件内容经 SSH 传输回本地
4. `edit/insert/apply_patch` 在远程整文件读入内存

### 15.2 已知性能问题

1. 高频小文件操作会比本地明显慢
2. 大文件读取会占用网络带宽
3. 大 patch / 大 diff 会增加命令与输出体积
4. 没有连接复用

### 15.3 第一版不做优化

以下优化先不做：

1. SSH ControlMaster 连接复用
2. 远程常驻 agent
3. 流式分块协议
4. patch/request stdin 双通道传输
5. 压缩传输

这些都留待后续。

---

## 16. 测试设计

## 16.1 测试分层

建议新增三层测试：

### A. 单元测试：host 配置与 SSH runner

覆盖：

1. 成功加载 host 配置
2. host 不存在
3. 配置字段非法
4. SSH 命令参数构造正确

### B. 单元测试：远程响应解析

可通过 mock `subprocess.run` 覆盖：

1. SSH 成功，stdout 为合法 JSON
2. SSH 返回非 0
3. stdout 非法 JSON
4. timeout
5. stderr 带错误信息

### C. 集成测试：远程模式工具行为

第一版不强依赖真实远程主机。

推荐方式：

- mock `ssh_runner.run_remote_python` 返回预构造结果
- 测试工具层对 `host` 分支的接线正确

### D. 可选真实集成测试

如环境允许，可后续补：

- 使用本机 `localhost` + SSH 服务做真实远程测试

但第一版不是必须。

## 16.2 各工具最少新增测试点

### `read_file`

1. 传 `host` 时走 remote backend
2. remote 成功结果正确包装为 `ToolResult`
3. remote 文件不存在错误正确透传

### `write_file`

1. 传 `host` 时走 remote backend
2. remote 写入失败错误正确返回

### `edit_file`

1. remote dry_run
2. remote include_diff
3. remote 匹配失败

### `insert_file`

1. remote line 插入
2. remote before/after/at_end
3. remote 锚点不存在

### `apply_patch`

1. remote dry_run
2. remote update/add/delete/move 成功
3. remote patch context not found
4. remote rollback 类错误映射

---

## 17. 文档更新要求

需同步更新：

1. `README.md`
2. 各工具 docstring
3. `.env.example`

### 17.1 README 需新增内容

1. 5 个工具新增 `host` 参数说明
2. 远程 host 配置方式
3. 仅支持远程 Linux + Python 3
4. 远程模式性能说明
5. 已知限制

### 17.2 `.env.example` 需新增示例

例如：

```bash
MCP_REMOTE_HOSTS_JSON={"prod":{"hostname":"10.0.0.12","user":"deploy","port":22,"identity_file":"~/.ssh/id_rsa"}}
```

注意示例中的引号转义和可读性。

---

## 18. 实施步骤建议

建议按以下顺序实施，不要一上来同时改所有文件后再调试。

### 第一步：配置与 SSH 基础设施

1. 新增 `remote/models.py`
2. 新增 `remote/config.py`
3. 新增 `remote/ssh_runner.py`
4. 新增 `remote/remote_script.py`
5. 为这些模块补单测

### 第二步：先接 `read_file` / `write_file`

1. 新增 `host` 参数
2. 接入 remote backend
3. 跑现有测试 + 新增测试

### 第三步：接 `edit_file` / `insert_file`

1. 复用远程脚本机制
2. 验证 diff 与 dry_run 行为
3. 补测试

### 第四步：接 `apply_patch`

1. 实现远程 patch 逻辑
2. 对齐现有 patch 错误码
3. 补测试

### 第五步：文档收尾

1. 更新 README
2. 更新 `.env.example`
3. 更新必要测试快照/断言

---

## 19. 开发时必须遵守的约束

1. 不要破坏 `host=None` 时的现有行为
2. 不要用 `shell=True`
3. 不要让 `host` 直接参与 shell 拼接
4. 不要把 diff 临时文件写到远程
5. 不要要求远程预安装本项目代码
6. 不要把 `apply_patch` 做成本地 parse + 远程零碎提交
7. 远程脚本 stdout 必须保持纯 JSON

---

## 20. 待实现时需要再次确认的小点

以下是实施时可直接按本文默认处理、一般不需再次讨论的点：

1. host 配置首版采用 `MCP_REMOTE_HOSTS_JSON`
2. 远程只支持 Linux + Python3
3. SSH 每次新建连接，不做复用
4. 远程脚本通过 `ssh ... python3 - --payload-b64 <...>` 执行
5. patch 超长 argv 风险作为已知限制，首版接受
6. diff 超长时仍保存到**本地** `MCP_TEMP_DIR`

---

## 21. 建议的验收标准

实现完成后应满足：

1. 5 个工具都支持可选 `host`
2. `host=None` 时现有测试继续通过
3. 远程模式下：
   - `read_file` 可读取远程文件
   - `write_file` 可原子覆盖远程文件并保留权限位
   - `edit_file` 可远程 dry_run / diff / 应用替换
   - `insert_file` 可远程按 4 种模式插入
   - `apply_patch` 可远程 dry_run / add / delete / update / move / rollback
4. host 配置错误、SSH 失败、协议失败都有清晰错误码
5. README 和 `.env.example` 已更新

---

## 22. 给后续执行会话的直接指令

后续在新会话中实施时，请直接按以下原则执行：

1. 先阅读本文件
2. 先实现 `remote/` 基础设施
3. 再依次接 `read_file`、`write_file`、`edit_file`、`insert_file`、`apply_patch`
4. 开发过程中优先保持本地语义不变
5. 每完成一阶段就运行对应测试
6. 最后统一更新 README、`.env.example`、docstring

如果实现过程中发现“远程脚本 argv payload 长度”在实际代码里不可接受，再把请求协议升级为“脚本 stdin + 请求 stdin”双段方案；否则第一版不主动扩大复杂度。
