# Device Runtime（独立设备服务与中心 Device MCP）

独立的中心设备操作服务、可选的中心 Device MCP，以及 **无 AI、无 MCP** 的 macOS/Linux Python CLI 客户端。AI Assistant可显式启用设备服务挂载，默认关闭；没有Windows/桌面安装器。SQLite journal是设备操作与结果台账，不是AI会话存储。开发验收不代表真实部署已完成。

## 运行版本查询（0.2.1 起）

Device 中心、客户端、操作 CLI 与 MCP 使用同一个组件版本来源
`src/device_runtime/__init__.py::__version__`；wheel 元数据由 Hatch 从该定义读取。
Bash/FS 是独立组件，不因 Device 发版机械递增。

```bash
# 当前本地程序，无需凭证，不连接中心
 device-runtime --version
 device-mcp --version
# 正在运行的中心，沿用管理员连接配置；私有 profile 可用 device-ops version
 device-runtime ops version
# 设备列表包含每个当前在线客户端的 runtime_version
 device-runtime ops list-devices --limit 40
```

运行身份字段为 `version` 与 `build_id`。`build_id` 是 `sha256:` 前缀的 Device
Python 源码指纹（包含相对文件名与精确源码字节），不是 wheel/bundle 文件 Hash、Git
提交号或安全完整性证明；无法读取源码时为 null。版本与指纹在版本模块首次加载时固定，
查询不会读取实例 active 指针，也不会在每次请求中扫描文件。运行期间不得改写安装源码；
editable 开发目录的修改须重新启动相应进程才能得到新快照，本地 `--version` 是新进程信息，
不能用它证明既有后台服务已升级。

- 中心 `GET /v1/version` 使用现有管理员认证，返回 `role=center`、`version`、`build_id`。
- MCP `get_version` 返回当前 MCP 与中心身份；MCP initialize 的 serverInfo.version 同步软件版本。
  中心不支持查询或查询失败时明确报错，不以本地版本伪造中心版本。
- 客户端在现有 WebSocket 连接请求头 `X-Device-Runtime-Version` 中上报同一身份 JSON，
  不改 v1 hello/welcome 或操作协议。旧中心忽略附加头；新中心接受未上报的旧客户端。
  该头不含认证信息，诊断头缺失、超长（>512 字符）或格式错误时按未上报处理，不中断连接。
- 设备列表新增 `runtime_version: {version, build_id} | null`，仅属于当前连接；旧端未上报、
  无效上报或离线时为 null，表示未知，不代表版本过旧。不会持久化或沿用离线设备的旧版本。
- 版本识别不替代 capabilities：执行准入仍按已声明能力，不要求各端版本相同才能连接。

### 发布规则

发布不同 Device 代码时递增唯一的 `__version__`（PEP 440，例如 `0.2.1`），构建新 wheel；
不以同名同版本文件覆盖旧发布制品。交付 `release_version` 可独立标识整套包，
`bundle_sha256` 标识准确制品，二者均不冒充某个进程的组件运行版本。
新增 Device 版本须验证持久状态契约，并按验证结果维护 `STATE_COMPATIBILITY`；
0.2.1 未更改持久格式，沿用格式 1。版本上报不表示回退或数据恢复自动安全。

本功能只识别和查询版本，不探测发布源、不自动判断“最新”、不自动下载、部署、停止或重启。

## 安装与边界

Python >=3.13，依赖/测试只用 `uv`。当前受控组合为 Device / Bash / FS **0.2.0**，Device 的 `client` extra 显式声明两份核心的精确版本。独立中心使用 `server`，独立 stdio MCP 使用 `mcp`；这两个角色不安装 Bash/FS。客户端默认包不含 MCP 服务器，FS 的图片核心依赖 Pillow。

正式交付必须使用 bundle 中带来源与 Hash 的准确三份 wheel；不要仅凭同名 PyPI 版本判断 core API 可用。三包尚未远程发布，bundle 提供准确制品，第三方依赖允许联网。

Mac 用户按 [交付文档的顺序流程](../../docs/device-delivery.md) 操作：受控获取/校验准确 bundle → 无源码三角色安装 → 环境配对 → 稳定 run/stdio 配置 → 诊断 → 同 bundle 重建 → 软件切换/回退 → 停机备份与全新同身份恢复。该页是命令与失败出口的统一入口，不以迁移当前试用为验收前提。

## 版本化交付包与独立实例（Mac）

`bundle build/inspect` 构建、校验格式 `2` 的三包制品与角色契约；`instance install/rebuild` 执行角色安装与同版本环境重建。不会自动迁移现有实例或启动服务。格式 `1` 的早期构建验证包须由原制品重新构建，不能作为本安装器输入。

```bash
# 没有源码 checkout 也能用 bundle 内 Device wheel 引导安装
BUNDLE=/absolute/bundle
ROOT=/absolute/new-instance
DEVICE_WHEEL="$BUNDLE/wheels/ai_device-0.2.0-py3-none-any.whl"
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance install --bundle "$BUNDLE" --instance-id isolated-mac --root "$ROOT" \
  --role center --role mcp --role client --server-url http://127.0.0.1:28765 --port 28765

# 单角色可独立安装；只有中心不需要 --server-url。
# 使用稳定入口启动；凭证由当前环境提供，不能放进命令参数。
"$ROOT/run" center
"$ROOT/run" client
"$ROOT/run" mcp

# 用户先停止该实例，确认持久数据完整。缺失 venv 时仍由准确 wheel 引导：
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance rebuild --root "$ROOT" --bundle "$BUNDLE"
```

`--root` 默认 `~/.device-runtime/instances/<instance-id>`，自定义路径可含空格。安装拒绝所有已存在目标（包括空目录）；失败候选保留而不激活，不自动删除。实例的 `config/roles.json`、`instance.json`、`state/<role>/`（journal/任务/输出）与 `releases/` 分离。启动配置存 URL/端口/设备 ID 及可选的客户端并发上限，凭证仍由环境提供。稳定 `run` 读取 `active.json`，保持运行租约并转发终止信号；重建会拒绝经该入口启动的运行实例，但不能发现用户绕过入口启动的进程。

重建只接受完全相同的 bundle，准备新目录后原子切换指针；不覆盖任何旧环境或持久数据、不重放操作、不接管旧进程。rebuild 没有 `--stopped` 参数，但仍须用户确认全部角色及直接启动进程停机。缺失持久目录必须恢复备份，不能用重建制造空数据代替。

稳定 run 的 shebang 是 `#!/usr/bin/env python3`，调用方 PATH 须有可用的 python3 标准库解释器；角色 venv 要求 Python >=3.13，uv 准备角色环境不等于设置了宿主 PATH。AI command 固定为绝对 `<root>/run`、args 单独为 `["mcp"]`，根路径可含空格，不拼 shell 字符串、不依赖 release 内 Python。软件切换、回退、同版本重建不改 AI 路径；恢复到新 root 才更新根路径。首期不承诺无 Python、launchd 或开机自启。

AI Assistant 的顶层配置是 `mcp-servers`，含新增字段的 Core 可声明 `inherit_env: ["DEVICE_RUNTIME_ADMIN_TOKEN"]`，仅在 stdio 建连时从实际宿主进程环境供给；名单不存值、缺/空变量在 spawn 前拒绝，不与字面量 env 同 key 并存。旧 Core 不能仅靠写该字段获得支持，也不展开 `${VAR}`。完整可用 JSON 与标准 MCP SDK 内存参数示例见 [交付文档](../../docs/device-delivery.md)；不将真凭证写入 roles/config、日志或聊天，不更改真实主服务来完成开发验收。

### 诊断、停机切换与备份恢复

以下使用包含生命周期功能的准确 Device wheel 安装出的 `device-runtime`；活动环境损坏或没有源码时，使用交付文档的 `uv run --no-project --with <准确wheel>` 引导方式。

```bash
device-runtime instance inspect --root "$ROOT"
# 可选认证只读中心GET，总超时10秒；token仅从环境读取，不提交operation。
device-runtime instance inspect --root "$ROOT" --health-url http://127.0.0.1:28765

# 用户先停止该实例的全部进程（包括绕过run的直接启动）；程序本身不停止进程。
device-runtime instance prepare --root "$ROOT" --bundle /absolute/new-bundle --stopped
# 使用prepare返回的release ID；准备本身不切换。
device-runtime instance activate --root "$ROOT" --release '<candidate-release-id>' --stopped
device-runtime instance rollback --root "$ROOT" --release '<previous-release-id>' --stopped

device-runtime instance backup --root "$ROOT" --destination /absolute/new-backup --stopped
device-runtime instance restore --backup /absolute/new-backup --destination /absolute/new-restored-instance
```

- 诊断分开报告安装完整性、持久布局、环境可用性、租约占用和未检查/健康/不可用的在线状态；不会修复或创建journal、任务或锁文件。在线探测只证明中心认证GET可用，不证明client/MCP或旧任务健康。
- 准备、切换、回退和备份均要求停机确认与独占租约。准备绑定原活动指针、身份与角色配置，切换时拒绝陈旧候选；失败候选保留，不自动清理。
- 首期仅确认 Device `0.2.0` 的state格式1兼容域；允许不同release/bundle指纹，但未来Device版本需明确验证兼容性后才能加入。无数据schema迁移；软件回退只切指针，保留后来journal/config/任务输出。
- 完成备份包含原身份、全部config/state及SQLite WAL/SHM、稳定入口和准确bundle，不依赖旧venv。正式operations/tasks数据库即使magic损坏或为空也不能跳过检查；普通非SQLite输出仍按字节保留。恢复只允许不存在的新隔离目标，保留持久字节和原URL/端口/device_id，重建角色环境后发布指针，不启动或重放。
- 旧任务journal的绝对output_path不改写：备份清单携带按任务引用生成的受管输出映射，恢复写入私有`task-output-map.json`并将其Hash绑定到active。输出接口只读恢复副本，不fallback至原实例；映射随软件切换/回退/重建保留，后续备份/恢复继续传递。缺失映射或本地输出、外部非受管旧路径均不可用。早期试验备份缺少映射时需重新停机备份。
- 恢复映射是实例的必需运行元数据：目标client环境必须实际声明`device_runtime.tasks.SUPPORTED_OUTPUT_MAP_FORMATS`包含格式`1`。prepare/切换/回退/重建与首次恢复均在发布前探测；缺模块、未声明或不支持均拒绝，保持原active和持久字节。inspect将这类活动环境判为不可用，不只检查map Hash是否保留。支持依据来自目标解释器的实际模块，不从包版本或release标签推断；不带恢复映射的正常实例不受此门禁限制。
- 环境复核按角色server/mcp/client启用extra，并沿依赖边递归传播extra、检查基础marker；不启用无关可选extra。缺失uvicorn等必需依赖时诊断不可用，安装/重建/切换/回退拒绝发布。
- 恢复的是**同身份实例**：用户必须避免与原在线实例同时运行。备份可能包含敏感数据，使用私有权限并妥善保管；损坏/未完成备份不可恢复。

详细说明与无源码命令见 [`doc/docs/device-delivery.md`](../../docs/device-delivery.md)。源码测试通过不等于准确制品验收或整体验收完成；现有试用实例不自动迁移。

## 配对与信任

- 中心从环境 `DEVICE_RUNTIME_ADMIN_TOKEN` 读操作API Token；从 `DEVICE_RUNTIME_DEVICE_TOKENS` 读JSON对象 `{device_id: token}`。管理员Token必须与设备Token不同，各Token至少16字符；勿将实际Token写入仓库、命令参数或日志。
- 客户端从环境 `DEVICE_RUNTIME_TOKEN` 读配对Token。HTTPS/WebSocket请求使用 `Authorization: Bearer ...`，不把Token放URL。
- CLI中心仅允许loopback绑定；外部部署须用 `create_app(state_dir, admin_token=..., device_tokens=...)` 配置TLS反向代理。客户端非loopback URL强制wss，环境代理默认不参与设备连接。
- 这是有意授予配对中心的本机命令/文件权限。仅用于受控、自有设备；不是OS沙箱或不可信多租户隔离。命令与文件操作由显式设备目标授权，不回退到中心本机。
- 服务使用单worker、单实例state-dir；每份journal有进程排他锁。同一客户端state-dir绑定device_id与server URL，不可改身份复用。不要删除journal来解决未知结果。

## 操作 API

管理员Token保护全部HTTP操作接口：

- `GET /v1/devices`：配对设备、在线状态、当前明确能力。
- `POST /v1/operations`：必须提供 `operation_id`、`device_id`、`action`、`arguments`。
- `GET /v1/operations/{operation_id}`：同一操作状态与结果查询。

请求示例（Token经HTTP header提供，不在示例载荷中）：

```json
{"operation_id":"demo-001","device_id":"laptop-one","action":"bash.execute","arguments":{"mode":"run","argv":["/bin/echo","hello device"],"cwd":"/tmp","timeout":60.0,"bytes":8192}}
```

当前执行协议只有 `bash.execute` 和 `bash.get`；MCP 与 CLI 提供 `bash/run/script/get`。文件能力包括 `fs.read_file`、`fs.write_file`、`fs.edit_file`、`fs.insert_file`、`fs.read_image`。设备路径必须是绝对 POSIX 路径。

所有结果包含 `location.namespace=device` 和 `location.device_id`。`data.output_path` 等仍是**设备本地路径**，不能交给服务器本机FS打开。读取文本请提交目标设备的 `fs.read_file`。命令输出、读取长度、请求载荷、并发受理有明确上限。

## 可靠性与恢复

1. 中心发送前持久化dispatching，成功发送后sent；客户端执行前持久化running；完成后持久化succeeded/failed。
2. 同ID同请求返回既有状态，绝不重新执行；同ID不同请求返回409。离线新操作409，不排队，不自动改目标。
3. 发送结果不确定、中心断线/重启或客户端重启留下非终态时标unknown。**unknown不代表未执行，不得直接换ID重发有副作用操作。** 先检查设备实际状态及已知结果。
4. 客户端连接断开不取消已受理操作；恢复连接只上报running/终态/unknown，不重放命令。终态结果落盘后重连重复上报，中心ack后标确认；旧running的ack不会确认后来的终态。
5. 客户端停止先关闭受理门禁，等待初始化/受理路径收口、取消执行协程并关闭核心。不可取消的FS线程由执行器跟踪并等待完成或失败，之后才释放journal lease；关闭可能等待底层文件I/O，不做超时后的假清理。取消或崩溃可能已产生部分副作用，只能unknown；failed也不保证没有部分副作用。不能宣称exactly-once。
6. journal使用SQLite事务/FULL同步/WAL；完整SQLite与FS同步工作在线程执行，不阻塞共享事件循环。连接异常不吞掉持久化失败。
7. 结果过大时保持已知执行状态并显式省略结果详情。台账持久保留，首版没有自动清理/过期重试功能；设备state-dir和磁盘容量由操作员管理。

## 开发验证

```bash
# 在 ai-device 仓库根目录执行
uv sync --locked --all-packages --all-extras --group dev
(cd packages/device-runtime && ../../.venv/bin/python -m pytest -q)
uv build --all-packages
uv run --no-sync python scripts/verify_artifacts.py
```

仓库根 `uv.lock` 使用 workspace 源码；正式安装使用准确 wheel，不依赖旧仓或 `local-wheels/`。测试服务只使用随机 loopback 端口和临时目录，不读取真实 AI 配置/凭证、不启停真实服务。

## 设备端并发配置

每个客户端实例分别限制请求受理和 Bash 活动任务数，多个会话调用同一实例时共享额度：

| CLI 参数 | `config/roles.json` 中的 client 字段 | 默认值 | 范围 |
| --- | --- | ---: | --- |
| `--max-concurrent-requests` | `max_concurrent_requests` | 8 | 除 `bash.get` 外的在途操作（包括文件操作） |
| `--max-concurrent-queries` | `max_concurrent_queries` | 8 | `bash.get` 在途查询，独立于普通请求额度 |
| `--max-active-tasks` | `max_active_tasks` | 8 | `bash.execute` 的前台及后台活动执行 |

三个值都必须是正整数；不支持 0、负数或无限制模式。未配置时保持原有默认值。请求受理超限返回 `client_capacity_exceeded`，任务容量超限返回 `TASK_CAPACITY_EXCEEDED`，均不排队。后台任务仍占用活动任务额度，直到实际退出。请求额度与任务额度不是同一个限制；提高任务上限时也应考虑普通请求额度。

直接启动示例（其余连接、身份和凭证参数保持原约定）：

```bash
device-runtime client --server-url wss://device.example/device-runtime \
  --device-id my-device --state-dir /absolute/device-state \
  --max-concurrent-requests 16 --max-concurrent-queries 8 --max-active-tasks 16
```

已安装实例在 `config/roles.json` 的现有 `client` 对象中添加所需字段，例如 `"max_active_tasks": 16`，保留其余字段；稳定 `run client` 会将它们传给客户端。配置在下次启动客户端时生效，不是热重载；修改源码也不会自动更新已部署的客户端或旧版稳定启动脚本。升级交付后应核对启动入口支持这些参数；不要覆盖持久配置或凭证。

## 中心操作 CLI（可选）

安装准确 Device wheel 的 `ops` extra 后使用 `device-runtime ops`；仅增加 httpx，不要求 MCP SDK，也不安装设备端 Bash/FS 核心。已安装 `mcp` extra 的环境也具备该依赖。CLI 直接复用 `DeviceAPI` 与 `Operation` 协议访问中心，不经过 MCP、不执行本地设备操作、不自动重试。源码变更不会自动升级现有安装。

连接参数位于操作子命令之前：`device-runtime ops [--server-url URL] [--token-env ENV_NAME] <command>`。URL 默认读取 `DEVICE_RUNTIME_API_URL`，管理员凭证默认读取 `DEVICE_RUNTIME_ADMIN_TOKEN`；只接受 Token 环境变量名称，不接受明文 Token 参数。远程中心必须 HTTPS；HTTP 仅限 loopback，不跟随重定向或使用环境代理。

```bash
# 环境变量由安全入口预先注入，不把真实 Token 放入命令或日志。
device-runtime ops list-devices --offset 0 --limit 40
device-runtime ops run --device-id laptop-one --operation-id command-001 \
  --cwd /tmp -- /bin/echo 'hello device'
device-runtime ops get-operation --device-id laptop-one --operation-id command-001
device-runtime ops write-file --device-id laptop-one --operation-id write-001 \
  --path /tmp/note.txt --content-file ./note.txt
device-runtime ops read-image --device-id laptop-one --operation-id image-001 --path /tmp/a.png
device-runtime ops get-operation --device-id laptop-one --operation-id image-001 \
  --save-image ./download.png
```

操作命令与当前 MCP 对齐：`version`、`list-devices`、`get-operation`、`bash`、`run`、`script`、`get`、`read-file`、`write-file`、`edit-file`、`insert-file`、`read-image`。不保留旧执行动作或 CLI 别名。

- `run` 使用 `--` 分隔原样 argv，无隐式 shell；`bash` 接收 `--command`，`script` 接收 `--script` 或 `--script-file`。标准输入支持 `--stdin`/`--stdin-file`。执行参数统一为 `--timeout`、`--timeout-behavior`、`--bytes` 和 `--output-limit-bytes`；`get --execution-id ID` 返回状态与输出尾部。
- 写入支持互斥的 `--content`/`--content-file`；插入支持 `--text`/`--text-file`，以及 `--line`/`--before`/`--after`/`--at-end` 四选一；编辑支持 `--edits-json`/`--edits-file`（JSON 数组，元素为 `oldText/newText`）。本地输入文件均为 UTF-8，文件选项支持 `-`，受请求大小预算限制。编辑/插入支持 `--dry-run` 和 `--include-diff`。
- `--path`/`--cwd` 始终是设备绝对 POSIX 路径；只有显式 `*-file` 输入和 `--save-image` 是本地路径。设备返回的 `output_path` 不在 CLI 本机打开。
- 图片默认 JSON 保留 Base64；显式 `--save-image LOCAL_PATH` 在完整图片回执到达时保存原始字节，JSON 改为图片元数据与 `saved_to`。目标存在即拒绝，不创建父目录，不自动覆盖；未完成时不创建文件，继续查询原操作 ID。保存失败不触发远程重放。
- 执行 ID 等于原操作 ID；后台查询使用 `get`，不接收 PID。状态和退出码以 `result.data` 为准。需要完整原始输出时在目标设备显式读取 `output_path`，不能把输出尾部当作无损游标。

操作命令 stdout 固定为单个 JSON 对象 `{"ok":bool,"code":string,"data":...}`（`--help` 除外）。操作回执在 `data` 下保留 `device_id/operation_id/state/result/recovery`，不回显原请求；错误不输出敏感输入或 HTTP 错误正文。`ok=true` 仅表示当前请求得到成功终态/目录。外层 `data.state` 表示本次操作回执状态；执行或查询的命令状态与退出码分别读取 `data.result.data.state` 和 `data.result.data.exit_code`，不存在 `task` 嵌套层。目录等非执行结果不包含这些命令字段。

| 退出码 | 含义 |
| --- | --- |
| 0 | 目录查询或操作成功；任务快照可能包含失败的任务 |
| 2 | 参数/配置/输入错误、缺少 ops 依赖或图片不可保存 |
| 3 | HTTP/响应归属错误或本地 I/O 错误 |
| 4 | 远程操作回执为 failed，不保证没有部分副作用 |
| 5 | dispatching/sent/running，尚未完成；查询原 ID |
| 6 | unknown 或提交结果不确定；按原 ID 对账，禁止盲目重放 |
| 130 | 调用方中断；不承诺取消远程执行，按原 ID 查询 |

同 ID 同请求去重，不同请求冲突；预演和实际写入使用不同 ID。超时、5xx、unknown 或 failed 均不能作为换 ID 重发的依据。CLI 不自动等待到无限完成，也不自动把前台命令转成后台任务。

## 中心 Device MCP（可选）

中心安装本包`mcp` extra，运行`device-mcp`或`python -m device_runtime.mcp_server`。这只是HTTP适配器，**不导入/执行本机Bash或FS**；不安装到设备客户端。MCP SDK限定宿主当前支持的v1系列。环境`DEVICE_RUNTIME_API_URL`为独立中心根URL或宿主`/device-runtime`URL，`DEVICE_RUNTIME_ADMIN_TOKEN`为操作Token。凭证由操作员/安全环境提供，禁止放入URL、命令参数或提交的MCP JSON。远程API必须HTTPS，不跟随重定向，不使用环境代理。

公开 11 个工具：`list_devices`、`get_operation`、`bash`、`run`、`script`、`get`、`read_file`、`write_file`、`edit_file`、`insert_file`、`read_image`。宿主注册为 device 时使用 `device__run` 等完整名称；不公开 run_argv/start_task/get_task/read_task_output 别名。

执行工具要求 device_id、稳定 operation_id、设备 cwd；run 使用 argv，bash 使用 command，script 使用 script（先 bash -n，可单独传 stdin）。共同支持 timeout（前台等待，默认60秒，0<timeout≤300）、timeout_behavior（默认 background，可选 kill）、bytes（0–131072，默认8192）、purpose。

默认等待到期后原命令继续运行，result.data.code=BACKGROUND，execution_id 等于原 operation_id。使用 get(device_id, execution_id, timeout=0, bytes=200) 查询当前状态与输出尾部；timeout 可为0–300秒，只等待不取消。短 HTTP 请求组成等待，不将网络超时当作执行超时。提交不确定先 get_operation 原ID，禁止换ID重放。get 内部生成查询ID；遇到不确定保留返回的查询ID进行对账。

执行完成看 result.data.state、exit_code，外层 state 是操作回执状态。网络/客户端故障可能 unknown；客户端重启不接管 PID。output_path 属于设备，需要完整输出时通过同设备 read_file 读取。

CLI 新入口为 bash/run/script/get，参数使用连字符（输出预算为 --bytes）。例如：

```bash
device-runtime ops run --device-id DEVICE --operation-id NEW_ID --cwd /tmp --timeout 1 -- /bin/echo hello
device-runtime ops get --device-id DEVICE --execution-id SAME_ID --timeout 10 --bytes 4000
```

CLI 的 bash 使用 --command，script 支持 --script 或 --script-file；--stdin/--stdin-file 互斥。本版本保留旧低层运维 CLI/协议用于历史回执与交付恢复，不再作为 MCP 工具公开。更新中心和设备包后才能使用新能力；旧设备不声明 bash.execute/bash.get 时请求失败，不回退到本机或重新执行。

## 内部后台任务与恢复

统一执行复用 Bash ExecutionRuntime。意图在启动进程前持久化，后台由稳定执行ID跟踪，PID只留在当前运行时。所有执行均经过同一活动任务容量检查，不保留旧执行协议或日志游标入口。

## AI Assistant宿主挂载（默认关闭）

在宿主专属环境显式安装本包server extra；环境设置`DEVICE_RUNTIME_HOST_ENABLED=1`、绝对`DEVICE_RUNTIME_HOST_STATE_DIR`及前述中心Token变量后启动受控宿主。默认0不会导入设备包、创建目录/Journal或注册路由；配置错误fail-closed，Token不进入Options repr。此处不修改运行中的真实配置，部署另需授权。

路由：`/device-runtime/v1/devices`、`/device-runtime/v1/operations`、`/device-runtime/v1/operations/{operation_id}`及设备WebSocket。它不使用AI登录Cookie/CLI Token，而校验独立操作/设备Token；父子lifespan显式组合，子启动失败退出父生命周期，关闭收口服务并释放Journal lease。单worker/单实例state-dir限制不变。

客户端server-url支持独立服务根URL及专用`/device-runtime`前缀，如`wss://center.example/device-runtime`；不支持任意路径。state-dir绑定完整URL，不可改目标复用旧journal。

隔离跨阶段验收使用FakeModel驱动真实Agent→ServerPool→stdio Device MCP→HTTP中心→WebSocket客户端→真实Bash/FS，不调用付费模型。动态验证在macOS；Linux实机、远程TLS/异机、Windows/发布另行验收。

## Linux 客户端交付

Linux 首期开放 client 角色，macOS 三角色保持不变。构建 Linux bundle 使用
`bundle build --target-system linux`；默认仍构建 darwin bundle。安装及生命周期
按主机平台和 bundle 角色校验，不能跨平台安装/恢复，也不支持 Linux center/mcp 正式交付。

详细边界、Ubuntu Python 3.13 工具链、凭证与容器验收说明见
[Linux 客户端指南](../../docs/device-linux-client.md)。容器验证不代表公网、systemd、主机重启或其他架构已验收。

## 低层 Bash 提交的短暂等待（非新版执行工具）

`run_argv` 可传 `wait_timeout_ms`（整数，0–5000，默认 0 保持原有立即返回行为）。例如查询主机名时传 1500，可在同一次工具调用内获取中心已保存的执行结果。未完成则返回当前状态和稳定操作 ID，随后使用 `get_operation` 查询原 ID；不得盲目重新执行。

该参数仅限制提交完成后的结果等待，与命令 `timeout` 分开；等待到期或调用方取消不会取消已接受的命令。`unknown` 提前返回，不能视为执行失败或重发依据。实际调用耗时还包括登记、发送、网络和结果读取。设备端执行协议与操作摘要不变，等待时长不属于命令参数。

中心 HTTP 提交接口支持 `POST /v1/operations?wait_timeout_ms=1500`，正文仍是原 `Operation`。结果仍通过数据库持久化后通知等待者；设备离线、能力校验和同 ID 不同请求冲突规则保持不变。该等待能力目前只暴露于 Bash 执行 MCP 工具，其他工具保持原有行为。

### CentOS 7 固定依赖

标准 `bundle build` 与 `instance install/prepare/rebuild` 支持 `--dependency-dir`。完整本地 wheel 清单、v3 快照、无索引安装、解释器前置条件和构建验收限制见 [Device交付说明](../../docs/device-delivery.md#centos-7-的本地依赖交付)。旧 v2 bundle 继续可用；不通过临时 uv 包装器或取消 UV 环境过滤实现。

### get 等待截止与查询回执

`get` 的 `timeout` 控制等待命令完成的预算，不是取消命令或取消首次 HTTP 受理的期限。首次快照立即提交（中心等待为 0），使用正常 HTTP 传输超时，因此网络慢时实际调用可超过很短的 `timeout`。取得回执后，刷新等待到期返回最近已知回执，可能仍为运行中或 `sent/result=null`；后者按回执的查询 ID 使用 `get_operation` 回收。真正的传输故障仍返回错误并保留查询 ID 与原 execution ID，不掩盖为成功。查询 ID 在本地生成，不单独证明中心已受理；404 也不能证明原命令未执行，禁止换 ID 重放原执行请求。

## 非兼容交付边界

旧执行动作及命令已移除，不提供别名、协议转换或旧任务查询兼容。历史 journal 和输出文件不会被删除；可通过操作回执审计和受支持的设备文件读取检查保留数据。修改启动脚本的版本不承诺旧实例原地升级；旧 `run` 与当前模板不一致时标准生命周期入口会拒绝。新安装与当前模板实例的重建/切换仍受现有校验保护，不应手工覆盖真实实例来绕过检查。
