# 设备执行 Runtime 与中心 Device MCP

> 三包迁移说明：本文保留来源项目的历史交付与验收记录，不代表新仓本次已完成相同平台验收。新包名称和当前支持范围以 [阶段C验收](stage-c-acceptance.md) 为准；AI 助手专属配置留在宿主仓库。

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


## 当前执行接口（统一 Bash 模型）

公开 MCP 执行工具为 bash/run/script/get，替代 run_argv/start_task/get_task/read_task_output。run 是结构化 argv，bash 是 Shell 字符串，script 先做 bash -n。三者默认 timeout=60（前台等待）、timeout_behavior=background；到期原执行继续，返回稳定 execution_id=operation_id。kill 仅终止本次命令。get 使用 device_id+execution_id，timeout 默认0，可有界等待并返回输出尾部，不接收PID或调用者查询ID。状态以 result.data.state 和 exit_code 为准，输出文件属于设备。

中心通过短 HTTP 请求组成等待，底层接收超时不等于命令终止；不确定提交先以原ID对账。设备操作ID、历史回执、客户端重启unknown、任务容量和输出保留规则仍有效。设备为查询预留独立有界并发。CLI 同步提供 bash/run/script/get；协议仅保留 bash.execute/bash.get，不提供旧动作、别名或转换层。历史数据不自动删除，旧实例原地升级不在兼容承诺内。中心与设备必须配套升级；开发实现不自动切换在线环境。


## MCP 工具命名

MCP 工具使用 `run`、`get_operation`、`read_file` 等名称；宿主将 Server 注册为 `device` 时，完整工具名为 `device__run` 等。工具名不再重复设备前缀，命名空间由宿主的 MCP Server 注册名提供；不注册旧名称别名。升级 MCP 入口时，同步更新按完整工具名保存的工具选择、加载配置和自动化调用；已有会话需重新发现并加载新名称。历史消息不改写，CLI 子命令、参数、操作 ID、任务 ID 和设备执行协议不变。

`packages/device-runtime`提供中心设备操作服务、主动WebSocket回连的macOS/Linux CLI客户端、稳定operation_id与操作journal，以及中心可选stdio Device MCP。AI Assistant宿主提供默认关闭的设备服务挂载；**本次增强尚未升级既有本机试用；其他设备部署、Windows与桌面安装器不在本次验收范围。**

- AI运行在中心，客户端无AI/MCP；Device MCP只访问中心操作API，不导入/执行本机Bash或FS，不给原有bash/fs添加远程参数。
- 除设备目录外，Device MCP每个工具强制device_id与稳定operation_id；所有路径/结果归属设备。output_path不能交给中心本机FS读取。
- 中心拒绝离线新操作，不排队、不回退。发送前与执行前落盘；unknown/传输错误不代表未执行，不允许盲目换ID重试副作用。执行工具返回受理回执，需用 `get_operation` 查询同一 ID 得到终态。
- 客户端断线保持已受理操作，恢复只上报/对账，重启遗留非终态unknown；设备journal独立于PostgreSQL AI会话存储，不增加Session文件存储或DB迁移。
- 宿主环境显式安装server extra，通过DEVICE_RUNTIME_HOST_ENABLED=1、绝对DEVICE_RUNTIME_HOST_STATE_DIR和独立操作/设备Token启用；路由位于/device-runtime，自行校验Token，不接受AI登录凭证替代配对。默认关闭不会导入设备包或创建目录。
- 中心MCP从环境DEVICE_RUNTIME_API_URL及DEVICE_RUNTIME_ADMIN_TOKEN读取目标与凭证。客户端server-url支持独立根URL或专用/device-runtime前缀，不支持任意路径。远程HTTP/WS须HTTPS/WSS，凭证不放入URL、参数或提交的JSON。

安装、环境重建、升级与备份恢复见 [Mac 版本化交付](device-delivery.md)。CLI、配对配置与 API 的源码说明位于仓库 `packages/device-runtime/README.md`，该路径不属于文档站。MCP 配置复用现有 stdio ServerPool，当前工具与参数见源码包说明，不自动注册正式 MCP。

隔离验收采用FakeModel驱动真实Agent→ServerPool→stdio Device MCP→HTTP中心→WebSocket设备客户端→真实Bash/FS核心，不调用付费模型。此证据不等于Linux实机、异机TLS或真实部署完成；本地开发与提交不会自动修改正式配置或重启真实服务。

第一期补充可选命令stdin、文本按行/分段/反向读取、编辑预演与diff，以及`insert_file`。完整接口透传既有Bash/FS核心；预演不写入，实际修改需另一个稳定ID，新增默认参数保留旧基础请求指纹。第一期需要配套升级中心/MCP/客户端及读取修复版FS核心，不自动替换既有运行安装；第二期加入`read_image`，读取设备PNG/JPEG/WebP（最多5 MiB），操作查询返回标准MCP图片块及设备元数据，文本不重复编码载荷。图片结果通道上限8 MiB，操作请求上限仍512 KiB。第三期加入显式后台任务；apply_patch仍排除。

统一执行默认活动上限8，可通过设备客户端 `max_active_tasks` 调整。前台、后台使用同一容量计数，监控故障且未确认退出的执行仍占用额度。默认每执行16 MiB输出预算，超限终止当前运行时自有命令并限制输出文件。`get` 返回状态与输出尾部，不提供字节游标或无损Base64协议；完整输出应通过目标设备文件读取。断线不自动重放，客户端重启不按历史PID接管；unknown不代表未执行。正常关闭仅清理当前运行时自有进程，不删除历史journal或输出。

MCP `run_argv` 参数与严格设备协议保持一致：`timeout` 为整数1–300（默认60），`output_bytes` 为整数1–131072（默认8192）；错误类型或范围在HTTP提交前失败。宿主CLI启动报告在Uvicorn实际readiness之后，lifespan启动/关闭失败转为worker异常退出并保留finally清理。相关失败验证仅使用隔离临时进程/目录，不代表真实部署已验证。

## 执行和查询等待

`bash/run/script` 使用统一前台等待参数 `timeout` 和 `timeout_behavior`。达到前台等待时间可转后台，不取消原执行；通过 `get` 查询或等待同一执行。中心的 `POST /v1/operations?wait_timeout_ms=...` 是内部有界回执等待能力，不再单独暴露旧 CLI 等待参数。中心接收超时不证明命令未执行，始终以原 ID 对账。

## 中心操作 CLI

准确 Device 包的 `ops` extra 提供 `device-runtime ops`，直接访问中心 HTTP API，覆盖当前 12 个 MCP 工具对应的操作，不依赖 MCP SDK、不导入设备执行器。现有 `server/client/bundle/instance` 命令不变，部署实例角色与稳定启动入口不变；操作 CLI 可在已安装 MCP 依赖的环境直接使用，或单独安装准确 wheel 的 `[ops]`。

通过环境 `DEVICE_RUNTIME_API_URL` / `DEVICE_RUNTIME_ADMIN_TOKEN` 连接；`--server-url` / `--token-env` 位于操作子命令之前，后者只指定环境变量名称，不传 Token 值。远程使用 HTTPS，loopback 可用 HTTP。操作列表、参数、JSON 契约、退出码及可复制示例见[包说明](../packages/device-runtime/README.md#中心操作-cli可选)。

CLI stdout 默认单 JSON 对象 `ok/code/data`，所有动作保持稳定操作 ID 与设备归属，未完成返回退出码 5，结果未知返回 6，不自动重试或换 ID。执行结果以 `result.data.state` 与 `exit_code` 为准。图片默认保留 Base64，也可显式 `--save-image` 保存到不存在的本地目标；设备路径与本地输入/输出路径明确区分。CLI 调用中断或本地图片保存失败不会取消或重放远程操作。

### get 等待截止与查询回执

`get` 的 `timeout` 控制等待命令完成的预算，不是取消命令或取消首次 HTTP 受理的期限。首次快照立即提交（中心等待为 0），使用正常 HTTP 传输超时，因此网络慢时实际调用可超过很短的 `timeout`。取得回执后，刷新等待到期返回最近已知回执，可能仍为运行中或 `sent/result=null`；后者按回执的查询 ID 使用 `get_operation` 回收。真正的传输故障仍返回错误并保留查询 ID 与原 execution ID，不掩盖为成功。查询 ID 在本地生成，不单独证明中心已受理；404 也不能证明原命令未执行，禁止换 ID 重放原执行请求。
