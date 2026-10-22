> **已退役：历史 SSH 设计，不再是当前实现契约。** 内置远程后端与工具 `host` 参数已删除。请以 README 和当前工具 Schema 为准；MCP 网络传输及监听地址仍保留。

# ai-device-bash 远程 Host 执行能力需求设计文档

## 1. 背景与目标

当前 `ai-device-bash` 仅支持在本机执行命令，并提供两个 MCP 工具：

- `bash`：执行命令
- `get`：查询后台进程状态与输出尾部

现计划为这两个工具增加 `host` 参数，以支持：

1. **默认不传 `host` 时，继续使用本机环境执行**。
2. **传入 `host` 时，基于配置中的远程主机信息，通过 SSH 在远程服务器执行**。
3. **无论本地还是远程，命令输出文件都保存在本机**，即 `output_path` 始终返回本机可读路径。
4. **远程执行时，命令输出必须保持纯净**，即不能为了传递 PID、状态等元信息污染 stdout/stderr。
5. **第一版即支持显式远程 kill**，不能只依赖断开 SSH 会话的隐式终止行为。

本设计文档用于后续在新会话中直接按文档实施开发。

---

## 2. 本次改造范围

### 2.1 涉及工具

#### `bash`
新增参数：

- `host: Optional[str] = None`

语义：

- `host` 不传或为空：使用本机执行
- `host` 传值：按配置别名查找远程主机，通过 SSH 执行

#### `get`
新增参数：

- `host: Optional[str] = None`

语义：

- `host` 不传或为空：查询本机后台进程
- `host` 传值：查询该远程 host 对应任务的后台状态

### 2.2 本次不做

以下内容不属于第一版范围：

1. 服务重启后的后台任务恢复
2. 远程日志文件持久化并重新挂接
3. 远程任务列表查询 / 手动清理工具
4. 支持除 SSH 之外的远程连接方式
5. 任务 ID 抽象替代 PID（第一版继续兼容 pid 模型）

---

## 3. 用户可见行为定义

### 3.1 `host` 参数规则

#### 规则 1：默认值
- `bash.host` 不传：表示本机
- `get.host` 不传：表示本机

#### 规则 2：成对使用
- 如果某次 `bash` 调用传了 `host="xxx"`，则后续对应的 `get` 调用也**必须传相同的 `host`**。
- 如果 `get` 使用了不同的 `host`，应返回明确错误，而不是模糊地视为未运行。

#### 规则 3：host 只允许配置别名
- 工具参数中的 `host` 只允许是配置文件中的主机别名
- 不允许用户直接传任意 IP / 域名 / SSH DSN

这样可以避免工具被用作任意 SSH 跳板。

---

## 4. 输出文件与 `output_path` 语义

### 4.1 总体原则
无论本地还是远程：

- `output_path` 都返回**本机路径**
- 该路径始终指向本机保存的命令完整输出文件
- `get` 读取尾部输出时，也只读取本机文件

### 4.2 设计原因
采用“远程执行、本地落日志”的方案，原因如下：

1. 对客户端最一致：`output_path` 永远是本机可读路径
2. 复用现有本地 `output_path`、`HeadBuffer`、`tail` 读取逻辑
3. 避免第一版就引入“远程日志文件读取”复杂度
4. 让远程模式下的 `get` 仍能通过读取本地输出文件尾部返回最新结果

### 4.3 远程模式下的输出流
远程模式中，实际链路为：

1. 本机启动 `ssh` 子进程
2. SSH 在远程机器上执行目标命令
3. 远程命令的 stdout/stderr 通过 SSH 流式回传到本机
4. 本机像当前本地模式一样，实时写入本机 `output_path`

因此：

- 输出不是先全部收集再写文件
- 而是**边读边写**
- 与当前本地模式保持一致

---

## 5. 远程输出必须保持纯净

### 5.1 约束
远程执行时，返回给客户端的命令输出必须与“用户直接 SSH 到远程 shell 中手动执行该命令”看到的输出保持一致。

因此：

- 不能往 stdout 插入 PID 标记
- 不能往 stderr 插入 PID 标记
- 不能在命令前后拼接任何会污染用户输出的额外文案

### 5.2 对 PID 获取的影响
由于输出必须纯净，不能通过：

```bash
echo $$
```

这类方式直接从 stdout/stderr 传回 PID。

因此必须采用**带外元数据通道**。

---

## 6. 远程执行模型设计

### 6.1 本地与远程统一抽象
建议将执行能力抽象为两类 backend：

- `LocalExecutionBackend`
- `SshExecutionBackend`

两者提供统一接口，例如：

- `run_command(...)`
- `get_background_result(...)`
- `terminate_background(...)`（内部能力）

MCP 工具层只负责：

1. 解析 `host`
2. 选择 backend
3. 组装参数
4. 返回统一响应结构

### 6.2 第一版远程 backend 实现原则
第一版不引入远程 agent，只通过系统 SSH 能力执行。

远程执行本质上仍是：

- 在本机启动一个 `ssh` 子进程
- 通过 SSH 驱动远程 shell 执行目标命令

### 6.3 远程后台任务追踪原则
虽然命令运行在远程机器上，但第一版中本机仍需记录该任务，以便：

- `get(host, pid)` 查询状态
- 超时时显式远程 kill
- 读取本地 `output_path`

因此后台任务记录不能只保留本地 PID，必须扩展为“带 host 的任务记录”。

---

## 7. 远程 PID / PGID 获取与纯净输出方案

### 7.1 总体思路
需要同时满足：

1. 输出纯净
2. 能拿到远程 pid / pgid
3. 第一版支持显式远程 kill

推荐方案：

- 在远程侧为每次命令执行创建**唯一的元数据目录 / 文件**
- 启动远程包装 shell
- 将目标进程的 PID / PGID 写入远程元数据文件
- 目标命令的 stdout/stderr 不被污染，仍然只通过 SSH 回传
- 本机通过带外方式读取远程元数据文件

### 7.2 为什么需要 PGID
为了实现更可靠的显式远程 kill，建议记录：

- `remote_pid`
- `remote_pgid`

优先按远程进程组 kill，而不是只 kill 单个 pid。

理由：

- 用户命令可能衍生子进程
- kill 单个 pid 不能确保整棵命令树结束
- kill 进程组更接近当前本地 `start_new_session=True + kill(-pid)` 的语义

### 7.3 推荐的远程元数据目录
每次远程执行创建唯一目录，例如：

```text
/tmp/ai-device-bash/meta/<uuid>/
```

其中至少包含：

- `pid`
- `pgid`
- 可选：`started_at`
- 可选：`cwd`
- 可选：`command.sha256` / `command.txt`

第一版最低要求只需：

- `pid`
- `pgid`

### 7.4 元数据文件唯一性
必须保证每次执行使用唯一目录或唯一文件名，不能写固定路径，例如不能用：

```text
/tmp/demo.pid
```

否则并发执行时必然冲突。

正确方式：

```text
/tmp/ai-device-bash/meta/<uuid>/pid
/tmp/ai-device-bash/meta/<uuid>/pgid
```

---

## 8. 第一版远程执行建议链路

### 8.1 目标
在不污染输出的前提下，实现：

- 启动远程命令
- 获取远程 pid/pgid
- 将远程 stdout/stderr 实时写到本机 output_path
- 超时时支持 background / kill
- `get` 可查询任务状态

### 8.2 推荐高层流程
对于一次 `bash(host="prod-1", command=...)` 调用：

1. 本机生成唯一执行 ID，例如 `exec_id`
2. 本机生成本地 `output_path`
3. 本机构造远程元数据目录，例如：
   - `/tmp/ai-device-bash/meta/<exec_id>/`
4. 本机启动 SSH 子进程执行远程包装 shell
5. 远程包装 shell：
   - 创建元数据目录
   - 写入 pid / pgid
   - `exec` 替换为目标命令
6. 本机读取 SSH 子进程 stdout/stderr
7. 流式写入本地 `output_path`
8. 本机通过带外方式拿到远程 `pid/pgid`
9. 若超时：
   - `background`：转入后台追踪
   - `kill`：显式 SSH 执行远程 kill PGID

---

## 9. 远程 kill 设计

### 9.1 需求
第一版必须支持显式远程 kill，不能仅依赖中断 SSH 会话。

### 9.2 Kill 目标
建议优先 kill 远程 PGID：

```bash
kill -TERM -<pgid>
```

等待短时间后若仍存在，再执行：

```bash
kill -KILL -<pgid>
```

### 9.3 Kill 策略
建议：

1. 先 `SIGTERM`
2. 等待短暂宽限期（例如 1~2 秒）
3. 若仍存活，则 `SIGKILL`

### 9.4 适用场景
显式远程 kill 至少在以下场景触发：

- `bash(..., timeout_behavior="kill")` 且超时
- 后续如果新增停止接口，也可复用

### 9.5 本地 SSH 进程处理
远程 kill 完成后，应同步结束 / 清理本地 SSH 子进程，避免本地侧 reader/writer 悬挂。

---

## 10. `get` 工具在远程模式下的语义

### 10.1 调用要求
若后台任务由 `bash(host="xxx")` 创建，则调用 `get` 时必须：

```json
{
  "pid": 12345,
  "host": "xxx"
}
```

若 `host` 不一致，应报错。

### 10.2 为什么 `get` 仍要传 host
虽然第一版中可能仍会追踪本地 SSH 子进程，但接口层必须坚持 host 显式传入，原因：

1. 语义清晰：pid 属于哪个执行环境必须明确
2. 避免未来内部实现变更时接口语义漂移
3. 避免不同 host 上出现 pid 误判

### 10.3 查询状态时的信息来源
远程模式下，`get(host, pid)` 建议至少依赖以下信息：

1. 本机内存中的后台任务记录
2. 本机 `output_path`
3. 远程 host 信息
4. 远程 pid / pgid 元数据

### 10.4 `get` 返回的输出来源
无论本地还是远程，`get` 的 `output` 都应来自：

- 本机 `output_path` 的尾部读取结果

这样与当前逻辑保持一致。

---

## 11. 后台任务记录结构设计

### 11.1 当前问题
当前 `BackgroundProcess` 仅适用于本地模式，字段不足以支持远程：

- 只记录本地 pid
- 不区分 host
- 不记录远程 pid/pgid
- 不记录远程元数据路径

### 11.2 推荐扩展结构
建议引入统一的后台任务记录结构，至少包含：

```python
@dataclass
class BackgroundProcess:
    pid: int                     # 对外暴露、用于 get() 的 pid
    host: str                    # host 别名；本机可固定为 "local"
    command: str
    output_path: str
    start_time: float
    task: asyncio.Task[int]
    execution_mode: Literal["local", "ssh"]
    remote_pid: Optional[int] = None
    remote_pgid: Optional[int] = None
    remote_meta_dir: Optional[str] = None
    local_transport_pid: Optional[int] = None  # 例如本地 ssh 进程 pid
```

### 11.3 `pid` 的对外语义建议
第一版建议：

- 本地模式：继续返回本地命令 pid
- 远程模式：**返回远程 pid** 作为 `background_pid`

原因：

1. 用户在远程模式下传 `host + pid` 更符合直觉
2. `get(host, pid)` 更自然地表示“查询该远程主机上的该任务”
3. 为显式远程 kill 服务

### 11.4 内部索引建议
后台任务存储结构建议从：

```python
BACKGROUND_PROCESSES: Dict[int, BackgroundProcess]
```

扩展为：

```python
BACKGROUND_PROCESSES: Dict[tuple[str, int], BackgroundProcess]
```

其中 key 为：

- `(host_alias, exposed_pid)`

本机可统一使用 `host_alias = "local"`。

---

## 12. Host 配置设计

### 12.1 不建议继续放在 `.env` 中
由于 host 配置是多条结构化记录，不适合继续使用简单 key-value 的 `.env` 承载。

建议新增结构化配置文件，例如：

- `hosts.json`
- 或 `hosts.yaml`

第一版推荐使用 **JSON**，实现成本更低，无需新增 YAML 依赖。

### 12.2 推荐配置文件路径
建议默认路径：

```text
<project_root>/hosts.json
```

同时可考虑通过 `.env` 增加一个可选配置项：

- `HOSTS_CONFIG_PATH`

当未配置时，默认读取项目根目录 `hosts.json`。

### 12.3 推荐配置结构
建议：

```json
{
  "hosts": {
    "prod-1": {
      "type": "ssh",
      "hostname": "1.2.3.4",
      "port": 22,
      "user": "root",
      "identity_file": "~/.ssh/id_rsa",
      "default_cwd": "/root",
      "bash_path": "/bin/bash"
    },
    "dev-1": {
      "type": "ssh",
      "hostname": "dev.example.com",
      "port": 22,
      "user": "ubuntu",
      "identity_file": "~/.ssh/id_ed25519",
      "default_cwd": "/home/ubuntu",
      "bash_path": "/bin/bash"
    }
  }
}
```

### 12.4 字段说明
每个 host 至少支持：

- `type`：第一版固定为 `ssh`
- `hostname`：远程主机地址
- `port`：SSH 端口
- `user`：用户名
- `identity_file`：私钥路径
- `default_cwd`：远程默认工作目录
- `bash_path`：远程 bash 路径

### 12.5 host 校验规则
- `host` 工具参数必须是配置中的 key
- 不在配置中的值直接报错 `UNKNOWN_HOST`
- 不允许动态连接任意目标地址

---

## 13. `cwd` 与环境变量语义

### 13.1 `cwd`
- 本地模式：表示本机目录
- 远程模式：表示远程机器上的目录
- 若未传 `cwd`：
  - 本地模式使用 `DEFAULT_CWD`
  - 远程模式使用 host 配置中的 `default_cwd`

### 13.2 `cwd` 检查
- 本地模式沿用现有目录存在性检查
- 远程模式需要在远程执行前或远程包装 shell 中校验目录存在
- 若目录不存在，返回等价错误

建议返回：

- `CWD_NOT_FOUND`：本地模式
- `REMOTE_CWD_NOT_FOUND`：远程模式

### 13.3 `meta.shell_env`
保持现有能力，但扩展语义：

- 本地模式：注入本机执行环境
- 远程模式：注入远程 shell 执行环境

要求：

- 仍沿用合法环境变量名校验
- 值统一转为字符串
- 远程模式构造命令时必须保证 shell 转义安全

---

## 14. 返回结构与兼容性

### 14.1 `bash` 返回结构
保持现有字段不变，只扩展语义：

- 本地模式 `background_pid`：本地 pid
- 远程模式 `background_pid`：远程 pid
- `output_path`：始终为本机路径

### 14.2 `get` 返回结构
保持现有字段不变。

### 14.3 不新增用户可见字段（第一版）
为了控制接口变更范围，第一版不强制新增：

- `resolved_host`
- `remote_pid`
- `remote_pgid`
- `execution_mode`

这些信息可以仅保存在服务端内部记录中。

如需增强可观测性，可在后续版本再考虑增加返回字段。

---

## 15. 错误码设计

建议新增或细分以下错误码：

- `UNKNOWN_HOST`：host 不存在于配置中
- `INVALID_HOST`：host 参数格式非法
- `REMOTE_CONNECT_ERROR`：SSH 连接失败
- `REMOTE_EXECUTION_ERROR`：远程命令启动失败
- `REMOTE_CWD_NOT_FOUND`：远程工作目录不存在
- `REMOTE_KILL_ERROR`：显式远程 kill 失败
- `HOST_PID_MISMATCH`：`get(host, pid)` 的 host 与任务归属不一致
- `REMOTE_METADATA_ERROR`：无法获取远程 pid/pgid 元数据

现有错误码保留：

- `INVALID_BYTES`
- `INVALID_TIMEOUT_BEHAVIOR`
- `TIMEOUT`
- `BACKGROUND`
- `RUNNING`
- `NOT_RUNNING`
- `COMMAND_ERROR`
- `EXECUTION_ERROR`

---

## 16. 服务重启行为定义

第一版明确约束：

- **不支持服务重启后的后台任务恢复**
- 后台任务注册信息仅保存在服务内存中
- 服务重启后：
  - 旧任务即使仍在远程运行
  - 本服务也视为无法继续追踪
  - `get` 返回 `NOT_RUNNING` 或等价错误

这样可以避免第一版引入任务元数据持久化复杂度。

---

## 17. 并发要求

### 17.1 远程元数据文件唯一化
所有远程元数据文件 / 目录必须基于唯一执行 ID 创建，禁止使用固定文件名。

### 17.2 本地输出文件唯一化
继续沿用现有本地 `create_output_path()` 逻辑，保证每次执行对应唯一 `output_path`。

### 17.3 多 host 并发
应支持：

- 多个不同 host 并发执行
- 同一 host 上多个命令并发执行

因此内部索引必须至少以 `(host, pid)` 为 key，不能仅使用 `pid`。

---

## 18. 安全要求

### 18.1 Host 白名单
仅允许连接配置中的 host 别名，不允许任意地址。

### 18.2 SSH 凭据来源
SSH 连接凭据仅允许来自服务端配置，不允许由工具调用参数动态传入。

### 18.3 命令构造安全
远程模式需要安全处理以下内容：

- `command`
- `cwd`
- `meta.shell_env`
- 远程元数据目录路径

要求：

- 不能简单字符串拼接
- 必须采用严谨的 shell quoting / escaping

### 18.4 输出纯净性
任何远程包装逻辑都不得污染目标命令的 stdout/stderr。

---

## 19. 代码改造建议

### 19.1 `main.py`
需要改动：

1. `bash()` 工具签名增加 `host`
2. `get()` 工具签名增加 `host`
3. 启动时加载 host 配置
4. 根据 `host` 选择本地或远程 backend
5. `summary` 可按需补充 host 信息（例如内部 summary），但不要影响工具返回

### 19.2 `shell.py`
建议重构方向：

1. 提取统一执行抽象
2. 保留现有本地执行逻辑
3. 增加 SSH 远程执行逻辑
4. 扩展后台任务结构与索引方式
5. 增加远程 kill 与远程元数据读取逻辑

### 19.3 新增模块建议
为避免 `shell.py` 继续膨胀，建议考虑拆出：

- `hosts.py`：host 配置加载与校验
- `remote_ssh.py`：SSH 相关执行与 kill 逻辑
- 或至少新增内部辅助函数分区

若希望最小改动，也可暂时不拆文件，但应至少做函数级抽象。

---

## 20. 测试建议

### 20.1 需要保留的现有测试
现有本地行为测试必须继续通过，确保：

- 本地 `bash` 不受影响
- 本地 `get` 不受影响
- `bytes` 行为不受影响
- `output_path` 行为不受影响

### 20.2 新增测试类型
建议补充：

#### Host 参数基础测试
- `bash` 不传 `host` 仍走本地
- `get` 不传 `host` 仍走本地
- 传未知 `host` 返回 `UNKNOWN_HOST`

#### Host 匹配测试
- 远程任务创建后，`get` 用相同 host 成功
- `get` 使用不同 host 返回 `HOST_PID_MISMATCH`

#### 远程输出文件语义测试
- 远程模式下 `output_path` 返回本机路径
- `get` 输出来自本机 `output_path` 尾部

#### 远程 kill 逻辑测试
- `timeout_behavior="kill"` 时触发显式远程 kill
- 先 TERM 后 KILL 的流程得到调用

#### 并发安全测试
- 同一 host 多次执行使用不同远程元数据路径
- 不同 host 并发互不冲突

### 20.3 测试实现建议
第一版单元测试可优先通过 mock / fake SSH 子进程实现，避免测试环境强依赖真实 SSH 服务器。

可以 mock 的对象包括：

- SSH 命令构造
- 元数据读取结果
- 远程 kill 命令执行
- 远程目录存在性校验

---

## 21. 推荐实施顺序

### 第一步：配置与参数层
1. 设计并加载 `hosts.json`
2. 为 `bash` / `get` 增加 `host` 参数
3. 补充 host 校验与错误码

### 第二步：后台任务结构升级
1. 将后台任务索引从 `pid` 升级为 `(host, pid)`
2. 扩展 `BackgroundProcess` 字段
3. 保持本地模式兼容

### 第三步：远程执行主链路
1. 实现 SSH 命令执行
2. 保证输出流式写入本地 `output_path`
3. 实现远程 pid/pgid 元数据读取

### 第四步：远程 timeout / background / kill
1. 实现远程 `background` 模式追踪
2. 实现显式远程 kill
3. 使 `get(host, pid)` 正常工作

### 第五步：测试与文档
1. 补充测试
2. 更新 README 工具说明
3. 增加 host 配置说明和示例

---

## 22. 最终建议摘要（供新会话快速理解）

本次改造的核心决策如下：

1. `bash` 和 `get` 都新增 `host` 参数
2. 不传 `host` 时默认本机
3. 传 `host` 时按配置别名走 SSH 远程执行
4. `bash` 传了某个 `host`，`get` 必须传相同 `host`
5. `output_path` 无论本地还是远程，始终返回本机路径
6. 远程命令输出必须保持纯净，不能插入 PID 标记
7. 远程 pid/pgid 通过带外元数据文件获取
8. 第一版就实现显式远程 kill，优先 kill 远程 PGID
9. 后台任务内存索引升级为 `(host, pid)`
10. 第一版不支持服务重启后的后台任务恢复
11. host 配置使用结构化 `hosts.json`，不允许动态连接任意地址

---

## 23. 实施验收标准

完成后应满足：

1. 本地模式行为与当前版本兼容
2. 远程模式支持通过 `host` 别名执行命令
3. 远程模式下 `output_path` 为本机文件路径
4. 远程模式下命令输出不被 PID/元信息污染
5. 远程命令超时时：
   - `background` 可继续 `get`
   - `kill` 可显式远程终止
6. `get(host, pid)` 可正确区分 host
7. 并发执行多个远程命令不会因元数据文件冲突而互相覆盖
8. README 与配置说明同步更新

---

## 24. 建议的新会话执行说明

在全新会话中，可直接基于本文件执行，建议顺序：

1. 阅读 `main.py` / `shell.py` 当前实现
2. 按本文的配置、接口、后台任务结构和错误码设计实施
3. 优先保证本地模式零回归
4. 再完成远程执行、远程 kill、`get(host, pid)` 逻辑
5. 最后更新 README 和测试
