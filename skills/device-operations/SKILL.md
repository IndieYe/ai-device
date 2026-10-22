---
name: device-operations
description: Device 设备操作指引，MCP 与操作 CLI 均可用。用户提到 Device、device-ops、查询在线设备、在已配对设备执行命令、读写文件或图片、启动和查询远程后台任务、处理操作回执或 unknown 状态时使用。统一设备选择、稳定操作 ID、异步结果、路径归属和授权边界；不用于普通中心本机 Bash/FS 操作，也不把加载技能视为远程执行授权。
---

# Device 操作

## 两种入口都可用

Device 是独立设备操作服务；当前接入不要求它是 AI Assistant 插件。**MCP 与 CLI 都支持同一组操作，可按任务和当前会话工具可用性选择，不强制只用其中一种。**

- **MCP**：适合直接调用结构化参数和接收图片内容。先核实 `device` server 及当前公开工具契约，按宿主支持的工具发现/加载流程使用；技能本身不加载 MCP 工具、不授予 capability 或凭证。工具未加载不等于设备离线。
- **CLI**：适合 Bash、脚本和结构化 JSON。通用入口是 `device-runtime ops`；某些环境另有私有 profile 快捷入口 `device-ops`。如需使用快捷入口，先核实 `command -v device-ops`、`device-ops --help`，再查看子命令帮助；不假定它已经安装。
- 无快捷入口时，核实准确 Device 包已安装 `[ops]`（或包含 httpx 的 `[mcp]`），使用 `device-runtime ops`。不要假定其他机器也有本机快捷入口；环境未就绪时说明缺口，不自行安装或升级真实服务。
- 不用自建 curl/Python 请求替代已经可用的上述入口；确需诊断 HTTP 协议时再查正式文档。

本机 `device-ops` 已安全加载公网中心私有配置和管理员凭证，连接目标固定；不能覆盖 `--server-url`/`--token-env`。通用 `device-runtime ops` 的连接选项位于子命令之前：`--server-url URL`、`--token-env ENV_NAME`；默认环境变量为 `DEVICE_RUNTIME_API_URL`、`DEVICE_RUNTIME_ADMIN_TOKEN`。只指定凭证环境变量名称，不把 Token 值放进命令、URL、输出或技能。远程 HTTPS，loopback 可用 HTTP；不跟随重定向。快捷入口的 `profile_connection_is_pinned` 表示试图覆盖固定连接，`private_profile_unavailable` 表示私有配置或权限不可用；两者都不是设备离线，不应因此换入口绕过或读取 Token 排障。

## 版本识别

Device 0.2.1 起，设备列表的 `runtime_version` 返回当前连接客户端的软件版本和源码构建指纹；
null 表示未上报或离线，不猜测版本。MCP `get_version` 查询 MCP 与中心，CLI `device-ops version`
查询中心；旧中心不支持时报告缺口，不以本地版本替代。`build_id` 是源码指纹，不是制品 Hash。
仍按 capabilities 决定能否执行，不按各端版本是否一致准入；不自动升级或重启。

## 操作前与执行闭环

1. 核实用户授权的设备、操作、目录与副作用。先列设备，检查目标 `device_id`、在线状态和声明的能力，不使用历史设备列表代替实时查询。
2. 设备 ID 未明确且有多个候选时先澄清；设备名称不证明具体宿主身份，必要时在授权范围内只读确认系统。WSL 的命令和路径属于 Linux 环境，不能当成 Windows 宿主原生路径。
3. 除设备列表外，显式提供 `device_id` 和稳定的 `operation_id`。新请求使用唯一 ID（如 `op-<uuid>`），在首次提交前确定并保留，不能每次查询重新生成。
4. 读取回执的 `state`；受理成功不等于执行完成。未完成时以原 ID 查询 `get-operation`，有界等待，必要时报告仍在执行。新版执行工具默认等待到期转后台，使用 get 查询 execution_id；网络超时不等于命令终止。
5. 终态检查 `result.location`、动作结果、命令退出码和截断信息；只报告已验证的结果。查询成功不能扩大为任意命令、全部设备或所有文件能力均已验证。

同一个 ID、相同请求去重，返回既有状态；同 ID 不同请求冲突。所有操作 ID 均应唯一，不能以为换设备就能重复使用同 ID。`unknown`、提交超时/断连/5xx 可能已产生副作用，**先查询原 ID 对账，不自动重试 POST，不换 ID 盲目重发**。`failed` 也不保证没有部分修改；404 同样不能独自证明先前未执行。修正请求前先确认原操作与设备实际状态。

## 能力对应

若 MCP server 注册名为 `device`，完整工具名通常为 `device__run`；以当前工具发现结果为准，不根据示例猜测可用名称或参数。下表 MCP 列列出 server 内工具名；CLI 列均接在 `device-ops`（或 `device-runtime ops`）之后。

| MCP 工具 | CLI 子命令 | 重点 |
| --- | --- | --- |
| `get_version` | `version` | 只读返回运行中的 MCP/中心版本；CLI 查询中心，本地 `device-runtime --version` 不证明后台服务版本 |
| `list_devices` | `list-devices` | offset/limit，最多 100 项，按 next_offset 续页 |
| `get_operation` | `get-operation` | 查询同一个操作，不重新执行 |
| `bash` | `bash` | command，Shell 解析 |
| `run` | `run` | argv，不经过 Shell |
| `script` | `script` | 先 bash -n，脚本与 stdin 分离 |
| `get` | `get` | execution_id，当前状态、等待及输出尾部 |
| `read_file` | `read-file` | chars/lines、offset/limit、direction、max_chars |
| `write_file` | `write-file` | 写入可能覆盖，先核实路径与授权 |
| `edit_file` | `edit-file` | 精确 oldText/newText，dry_run/include_diff |
| `insert_file` | `insert-file` | line/before/after/at_end 恰好选一项 |
| `read_image` | `read-image` | PNG/JPEG/WebP，最大 5 MiB |

MCP 使用下划线参数（如 `timeout_behavior`），CLI 使用连字符（`--timeout-behavior`）。数值边界与结构校验以当前工具 Schema、CLI 帮助和协议为准；不把终端字符串规则套到 MCP 的严格整数类型。

## CLI 使用

以下设备名和 ID 均为占位示例；替换成已核实目标和新操作 ID 后执行，不原样复用。

```bash
device-ops list-devices --offset 0 --limit 40

device-ops run --device-id DEVICE_ID --operation-id NEW_OPERATION_ID \
  --cwd / --timeout 1.5 -- /bin/pwd

device-ops get-operation --device-id DEVICE_ID --operation-id SAME_OPERATION_ID
```

- 命令放在 `--` 后面，argv 原样传递，无隐式 shell；不要用拼接字符串悄悄引入 shell。需要 shell 时明确 argv 与副作用，并核实授权。
- `--stdin TEXT` 与 `--stdin-file LOCAL_PATH` 互斥；文件选项支持 `-` 从标准输入读取，UTF-8 且有大小上限。
- 写文件用 `--content` 或 `--content-file`；插入用 `--text` 或 `--text-file`；精确编辑用 `--edits-json` 或 `--edits-file`，JSON 为 `[{"oldText":"...","newText":"..."}]`。
- 编辑/插入可用 `--dry-run --include-diff`。预演与实际修改是不同请求，使用不同 ID；这不是给失败或未知操作换 ID 重放的理由。

stdout（help 除外）为一个 JSON 对象 `{"ok":bool,"code":string,"data":...}`。回执在 `data` 下；默认成功或错误不会回显原请求。Bash 返回非零退出码不意味着输出为空，应读取对应 JSON，不用 `&&` 丢弃未完成回执。

| 退出码 | 含义与下一步 |
| --- | --- |
| 0 | 目录查询或操作成功，仍检查具体业务结果 |
| 2 | 参数、配置、依赖或图片数据/保存条件错误；若请求可能已提交，保留原 ID |
| 3 | HTTP、响应归属或本地 I/O 错误；检查 code 和原 ID，不盲目重发 |
| 4 | 操作 failed，可能已有部分副作用 |
| 5 | dispatching/sent/running；原 ID 查询，尚未完成 |
| 6 | unknown 或提交结果不确定；原 ID 对账 |
| 130 | 调用方中断；不承诺取消远程执行，原 ID 查询 |

## 统一执行与后台查询

bash/run/script 要求稳定 operation_id；timeout 是前台等待秒数（默认60，0<timeout≤300），timeout_behavior 默认 background，达到等待时间原命令继续运行，返回 result.data.code=BACKGROUND。kill 表示等待到期终止本次命令，不增加服务操作授权。bytes 是内联输出预算，允许0。

后台 execution_id 就是原 operation_id，不使用裸PID。get(device_id, execution_id, timeout=0, bytes=200) 返回当前状态和输出尾部；timeout 为0–300秒，仅等待不取消，不需要调用方管理新查询ID。result.data.state/exit_code 才是命令状态，外层 succeeded 只表示查询完成。网络故障或unknown先对账原ID，不盲目重发。get 的不确定回执保留内部查询ID。 首次快照立即提交并使用正常 HTTP 传输超时，因此实际调用可超过很短的 timeout；已有回执后的刷新等待到期返回最近已知回执，可能仍为运行中或 sent/result=null，后者按返回的内部查询ID用 get_operation 回收。查询ID在本地生成，不单独证明中心已受理；404不能证明原命令未执行。

`get_operation` / `get-operation` 对账的是指定操作的回执，不刷新后台执行状态：原执行 ID 可能仍返回最初的 `BACKGROUND/running` 快照，即使命令已完成。实时状态与最终退出码用 `get(execution_id)`；`get(timeout=0)` 即使命令已完成也可能返回 `sent/result=null`，按内部查询 ID 回收，或用正等待时间查询。

需要完整输出时用同设备 read_file 读取 output_path。客户端重启后可能unknown，不接管裸PID。旧低层 CLI/协议仅用于运维恢复，不再公开三个任务 MCP 工具。新中心与设备包需配套升级，不自动部署或重启。

## 路径、文件与图片

- `cwd`、`path`、返回的 `output_path`/`diff.temp_file` 都属于目标设备；绝不交给中心本机 FS 打开。需要更多输出或 diff 时，在同一设备上用 read-file 分段读取。
- CLI 的 `*-file` 输入和 `--save-image` 才是显式本地路径。绝不能把远程返回路径当成本地输入路径。
- 首次读取采用有界预算，再按游标/范围展开；读取结果可能截断，写入后的核验需在同设备同路径读回并比对，不能拿受理回执代替内容验证。
- MCP 图片在 read-image 或原 ID 的 get-operation 完成后提供标准图片内容；CLI 默认 JSON 保留 Base64，也可在这两个命令显式指定 `--save-image LOCAL_PATH`，保存后 JSON 改为元数据与 saved_to。
- 保存图片拒绝覆盖，不自动创建父目录；pending 时不创建文件。保存失败不代表远程读取失败，不重新读取或换 ID；修正本地目标后查询原回执保存。图片原始格式不自动缩放或转换。

## 授权和运行安全

技能可用、设备在线、持有管理员凭证均不等于获准任意操作。遵循项目运行安全原则和用户本次授权；更换入口不增加权限。

- 普通授权范围内先只读核验，写入只在明确设备与目录内进行；不默认覆盖、删除、批量外发或读取无关敏感文件。
- 真实服务停止、重启、部署、远程 Git、不可逆数据修改等继续适用项目独立门禁，不能通过 Device 绕过。
- 不输出凭证、不读取私有凭证文件正文来调试；入口报告凭证故障时仅检查脱敏状态和权限，必要时请用户处理。
- 不把远程执行失败回退为中心本机执行，不自行改设备、目录或中心地址。

## 进一步查证

本项目源码为 `packages/device-runtime/`。需要完整参数、安装和交付说明时，从已核实项目根目录按需查阅：

- `packages/device-runtime/README.md`：操作 CLI 与 MCP、退出码、任务语义。
- `doc/docs/device-runtime.md`：操作协议和可靠性约定。
- `doc/docs/device-delivery.md`：安装与升级，不代表已获得部署授权。

这些是仓库文档路径，不是本技能目录内的配套资源。技能不保存设备列表、Token 或固定历史操作 ID，实际可用性以当次发现和回执为准。

## 执行协议边界

当前协议仅使用 `bash.execute`/`bash.get`；CLI 使用 `bash/run/script/get`，不保留旧执行或任务命令。每设备实例的普通请求、查询、活动任务并发上限分别可配置，默认均8，只接受正整数。旧实例启动脚本不匹配时不承诺原地升级，不自动覆盖脚本或删除历史数据。
