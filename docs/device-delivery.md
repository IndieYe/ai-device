# Device 版本化交付包（Mac 首期）

运行版本查询自 Device 0.2.1 起提供，详见 [Device Runtime](device-runtime.md)。
组件版本由 `__version__` 唯一定义；不同代码发布必须递增版本。交付包 `release_version`、
制品 Hash 与进程运行版本职责不同，安装指针切换不证明既有进程已加载新版本。
版本查询不执行自动升级；真实停机、切换和启动仍遵循下述流程与授权边界。


本页描述交付包构建/校验、角色安装、环境重建、诊断、停机软件切换与一致性备份恢复。生命周期命令必须由包含该功能的准确 wheel 提供；同名同版本不代表相同字节。源码回归不能代替准确制品、Mac整体用户路径和独立审核；现有 Device 试用实例不会自动迁移。

## 交付与边界

交付包是包含 `manifest.json`、`constraints.txt` 与三份明确 wheel 的独立目录。构建或检查包不会安装依赖、创建实例、读取凭证、操作 journal、启停服务或调用设备。

- 三个组件固定为 `ai-device`、`ai-device-bash`、`ai-device-fs`。
- 首期接受三份 `py3-none-any` 纯 Python wheel。Mac 是部署验收目标，不表示已验证其他平台。
- 必须提供每个组件的完整 40 位源码 commit；它是构建者声明的来源，不是 CLI 自动证明的源码/制品对应关系。正式制品仍须经过受控构建与字节验收。
- wheel 的文件名、内部 distribution/version、顶层 dist-info 目录身份和核心文件入口须匹配。版本与第三方 pin 使用显式依赖 `packaging` 按 PEP 440 校验。校验器不执行代码，因此结构检查不能代替 core API 或真实功能验收。
- 文件 Hash 区分同名同版本的不同内容。本交付采用三包 `0.2.0`，客户端 extra 精确依赖 Bash/FS `==0.2.0`；尚未发布 PyPI。安装使用准确 bundle 内 wheel，不以公开同名包为替代。
- 第三方依赖允许联网获取，约束文件仅接受每行 `name==version` 的明确 pin；空行/整行注释允许。禁止把三份已打包组件再次写入第三方约束。
- 构建器校验 pin 格式，但不调用依赖解析器，不证明依赖约束覆盖完整传递闭包或能在目标 Python 版本安装；这些属于安装阶段的门禁。

## Mac 用户流程

这是一条无源码 checkout 的独立实例路径。先由交付方提供**完整准确 bundle**及通过独立可信渠道取得的清单、Device wheel 校验值；没有自动下载、PyPI 发布或仅按版本号选包的入口。保存原交付及校验结果，同名 `0.2.0` 也可能是不同准确制品。

下表引用本页已有命令，按顺序执行；路径、实例 ID 和空闲 loopback 端口由用户选择，示例不指向已有部署。

| 步骤 | 输入与执行入口 | 结果 | 失败或恢复出口 |
| --- | --- | --- | --- |
| 1. 获取、确认与校验 | 完整 bundle、交付方独立提供的 SHA-256；按“无源码安装”设置 `BUNDLE`、`DEVICE_WHEEL`，先用 `shasum -a 256` 比对清单与引导 wheel，再从该 wheel 执行“清单与检查”的 inspect 命令 | 三组件身份、Hash、来源与角色契约可核对 | 来源或校验值不符就停止，重新取得准确交付；不能通过改清单接受异常制品 |
| 2. 无源码三角色安装 | PATH 上的 uv、联网依赖、全新独立且可含空格的 `ROOT`；执行“无源码安装”命令 | 三角色环境、持久布局及 `active.json` 发布 | 目标已存在或依赖检查失败时保留日志/候选，重新选择全新目标；不覆盖已有实例 |
| 3. 环境凭证与配对 | 用户从受控来源向各角色进程环境注入凭证；中心设备映射的 key 与 `config/roles.json` 的 client `device_id` 一致，设备 token 与客户端一致，admin token 与设备 token 不同 | 中心、客户端和 MCP 使用正确的独立身份/凭证 | 认证失败核对环境、ID 和 URL；不改 journal 或在聊天、配置、日志中粘贴凭证。详见 [Device 配对与运行](device-runtime.md) |
| 4. 稳定启动 | “独立持久布局与稳定启动”的三个 run 命令；MCP 由 AI 客户端按下节配置启动，不重复启动同一角色 | run 读取 active，选择当前角色解释器 | 缺环境、配置错误或租约占用则拒绝；用户核对安装与既有角色，不绕过锁启动同身份副本 |
| 5. 只读诊断 | “只读版本与健康诊断”的 inspect；在线检查显式提供中心 URL 和环境 admin token | 准确 bundle 指纹、角色、持久布局/环境/租约及所选中心认证接口状态 | `unavailable` 不是自动修复；区分凭证、依赖、持久数据故障。中心 healthy 不等于完整 MCP/设备链通过 |
| 6. 同 bundle 重建 | 用户确认所有角色及绕过 run 的进程已停机；执行“同版本环境重建” | 新角色环境发布，身份/config/state 字节保留，AI 入口不变 | 指纹不符不是重建，转软件准备流程；数据丢失转备份恢复；失败保留原 active 和候选 |
| 7. 新软件准备与切换 | 按步骤1验证 `NEW_BUNDLE`；全部停机后执行 prepare，从其 JSON 取 release，再 activate；均带 `--stopped` | 新准确环境发布，仍使用同一个 root/run | 陈旧准备重新 prepare；兼容域、依赖或恢复映射支持不足时拒绝，不强行发布 |
| 8. 软件回退 | 全部停机，显式保留的 previous release；执行 rollback `--stopped` | 仅软件指针切回，AI 路径不变 | 目标实际环境或映射支持不符则拒绝；不覆盖后来 journal，不承诺任意版本回退 |
| 9. 一致性备份 | 用户确认全部停机；全新私有目标，执行 backup `--stopped` | 完成标记、完整数据/输出映射与准确活动 bundle | 坏 DB、变化的文件、Hash 或完成标记异常均拒绝；保留失败目录，不当成有效备份 |
| 10. 同身份隔离恢复 | 有效备份、全新不存在且不交叠的目标；用备份准确 wheel 执行 restore | 同身份持久字节保留，重建环境，不自动启动或重放 | 失败候选不能运行；用户确认原实例离线后才允许启动恢复实例，并将 AI command 改为新 root/run，再重复步骤4–5与真实链验收 |

升级准备/切换、回退和备份要求 `--stopped`；重建**没有该 CLI 参数**，但同样需要用户确认全部停机。租约不能证明所有绕过入口的进程都已结束。软件与数据恢复不是同一条路径，unknown 操作不自动重放；同身份原实例与恢复副本不得并行运行。

## 构建

已安装包含本功能的 Device 包后执行；开发环境使用包目录下的 `uv run --locked` 前缀。

```bash
device-runtime bundle build \
  --output /absolute/new-bundle-directory \
  --version 0.2.0 \
  --device-wheel /absolute/wheels/ai_device-0.2.0-py3-none-any.whl \
  --bash-wheel /absolute/wheels/mcp_server_bash-0.2.0-py3-none-any.whl \
  --fs-wheel /absolute/wheels/mcp_server_fs-0.2.0-py3-none-any.whl \
  --constraints /absolute/constraints.txt \
  --sources /absolute/sources.json
```

上述是本交付组合的构建示例，并非远程发布指令。`--version` 是交付包版本，三个 wheel 各自的真实版本另存入清单，不会改写 wheel 内容。

`sources.json`：

```json
{
  "device": "<完整小写40位commit>",
  "bash": "<完整小写40位commit>",
  "fs": "<完整小写40位commit>"
}
```

约束文件示例（不是完整安装锁）：

```text
pydantic==2.13.5
websockets==17.2
```

构建先在输出父目录暂存、完成自校验，再以同文件系统 rename 发布。输出路径必须不存在；已有空目录也会拒绝，失败不会覆盖已有交付物。

## 清单与检查

```bash
device-runtime bundle inspect /absolute/bundle-directory
```

成功在 stdout 输出 JSON，失败返回退出码 `2` 和有限、可定位的错误提示。清单格式版本 `2` 包含：

- `release_version` 与 Mac/Python >=3.13/联网依赖的目标约定。
- `components`：每份 wheel 的 distribution、真实版本、相对路径、SHA-256、源码 commit。
- `constraints`：约束文件相对路径、SHA-256 和解析出的 pin。
- `roles`：center 只使用 Device 的 `server` extra，mcp 只使用 `mcp` extra，client 使用三组件与 `client` extra，不因此安装 MCP 适配器。格式1验证包须重新构建，不隐式兼容旧角色布局。

检查会重新读取制品、验证 Hash、内部身份与核心文件入口，核对约束 pin 和角色契约，并拒绝缺失或未列入清单的文件。清单 Hash 用于制品正确性与来源定位，不是签名或面向恶意本机代码的信任系统。

## 无源码安装

前置条件是 PATH 上有 `uv`、可联网取得受控 Python 3.13 与第三方依赖，并持有已核对来源与引导 wheel 校验值的完整 bundle。不使用系统 pip，也不需要源码仓库。先比较交付方校验值，再用下列相同 uv 引导前缀执行 `bundle inspect "$BUNDLE"`（即替换 `instance install ...` 子命令）；成功后才安装。不能先执行来源未确认的 wheel，再用其自检代替来源确认。

```bash
BUNDLE=/absolute/bundle
ROOT="/absolute/instances/My Mac"
DEVICE_WHEEL="$BUNDLE/wheels/ai_device-0.2.0-py3-none-any.whl"
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance install --bundle "$BUNDLE" --instance-id isolated-mac --root "$ROOT" \
  --role center --role mcp --role client --server-url http://127.0.0.1:28765 --port 28765
```

可重复 `--role` 或仅安装一个角色。`--server-url` 是 HTTP(S) 中心根地址，客户端自动转换为 ws/wss；复用现有 URL 的凭证、TLS 和路径校验，不接受 URL 中的凭证。center 绑定 loopback；mcp/client 可以独立安装，但这不是其他设备或公网部署的验收授权。

- 目标路径必须不存在；默认 `~/.device-runtime/instances/<instance-id>`，`--root` 支持自定义和空格。
- 每次准备独立 `releases/<version>-<unique-id>/<role>/venv`，完整 bundle 同时复制、重校验；不原地修改环境。
- 仅安装 wheel，不执行第三方源码构建；第三方包必须命中固定 pin，安装后核验完整已安装闭包和角色真实导入。启用extra严格对应center=`server`、mcp=`mcp`、client=`client`，同时检查基础marker和依赖边上请求的extra，并递归传播至闭包；不会给所有已安装包启用所有可选extra。center还验证真实启动所需的uvicorn入口，缺失时诊断不可用，安装/重建/切换/回退均拒绝发布。构建器的结构校验不能替代这一步。
- 全部角色准备通过才生成持久布局并原子发布 `active.json`。失败保留候选/日志，不发布活动入口，不自动删除成果。
- 传输安装日志保存在候选的 `<role>/prepare.log`，不能把真实凭证加入命令、日志或 bundle。

## 独立持久布局与稳定启动

```text
<instance-root>/
  instance.json                # 稳定实例身份
  config/roles.json            # URL/设备ID/端口；不存凭证
  state/<center|client|mcp>/   # journal/任务记录/输出，绝不由重建覆盖
  run                         # 只依赖标准库的稳定启动入口
  active.json                 # 活动release与bundle指纹
  releases/<version>-<id>/     # bundle副本、准备记录与角色venv
  .installation.lock          # 安装独占/运行共享租约
  task-output-map.json        # 仅恢复client实例：受管旧任务路径映射，私有且与身份/活动指针绑定
```

```bash
# 三个独立终端/进程；仅在用户已配置相应环境凭证后运行。
"$ROOT/run" center   # DEVICE_RUNTIME_ADMIN_TOKEN、DEVICE_RUNTIME_DEVICE_TOKENS
"$ROOT/run" client   # DEVICE_RUNTIME_TOKEN
"$ROOT/run" mcp      # DEVICE_RUNTIME_ADMIN_TOKEN；API_URL来自roles.json
```

中心 admin token 和各设备 token 必须不同，各 token 至少16字符，具体配对规则沿用 Device Runtime。中心的 `DEVICE_RUNTIME_DEVICE_TOKENS` 是 `{device_id: token}` JSON 对象，key 使用客户端持久配置的设备 ID；token 由用户受控注入，不提供示例假 token。凭证只由进程环境供给，不写入持久 roles/config、命令参数、聊天或日志；安装器不会复制真实配置或生成/输出凭证。`run` 保持共享租约并将 SIGTERM/SIGINT 转发给子进程；重建遇到租约时拒绝。绕过入口直接运行的进程无法由此锁检测，所以操作前必须由用户确认该实例已停止。AI 不得停止真实实例；开发测试只收口明确属于测试的进程。

`run` 当前 shebang 是 `#!/usr/bin/env python3`，调用方（包括 AI 宿主）的 PATH 必须能找到可运行该入口及标准库的 python3；入口不依赖 Device 第三方包，再选择角色 venv 的 Python >=3.13。uv 准备角色环境不保证宿主 PATH 已有 python3，不能承诺无 Python 安装。AI/MCP 配置使用绝对 `<root>/run` 与单独的 `mcp` 参数，而不是某个 release 内的 Python；新软件切换、回退、同版本重建均无需改 AI 路径。首期没有 launchd/开机自启或自动进程恢复。

## stdio AI/MCP 配置

AI Assistant 工作目录的 `config/config.json` 使用顶层 **`mcp-servers`**；不是通用客户端的 `mcpServers` 容器。将下列项按现有配置结构合并，不覆盖其他设置：

```json
{
  "mcp-servers": {
    "device": {
      "command": "/absolute/instances/My Mac/run",
      "args": ["mcp"],
      "inherit_env": ["DEVICE_RUNTIME_ADMIN_TOKEN"]
    }
  }
}
```

替换为实际绝对 root，JSON command 内的空格是路径本身，**不额外嵌 shell 引号、不把 command 和 args 拼成 shell 字符串**。API URL 由 run 从 roles.json 读取；只有声明的 admin token 从宿主环境加入这次 stdio 的内存 env。名单只存名称，不保存解析值、不做 `${VAR}` 展开，不自动继承整个宿主环境；缺失或空值在 spawn 前拒绝，同名键不能同时存在于字面量 env。每次重连重新取值，不能把配置保存当成凭证已供给。

此字段要求包含 `inherit_env` 实现的 Core；旧 Core 可能忽略未知字段，**仅写 JSON 不能越过宿主升级和连接验证门禁**。用户负责让实际启动 Core 的进程事先具有所需环境及 python3 PATH（终端 export 不会改变已运行的服务）。配置与组件生效规则见 [配置与生效边界](configuration/index.md)；真实配置变更/服务重启不是隔离开发验收动作，仍须独立授权。这里不宣称当前线上 Core 已加载该实现。

标准 MCP Python SDK 使用 `StdioServerParameters`，`inherit_env` 是 AI Assistant 的宿主配置扩展，不能原样当作 SDK 字段。其他客户端的配置容器和环境注入规则以其自身契约为准；使用 SDK 时仅在内存传入值，例如：

```python
import os
from mcp import StdioServerParameters

token = os.environ.get("DEVICE_RUNTIME_ADMIN_TOKEN")
if not token:
    raise ValueError("DEVICE_RUNTIME_ADMIN_TOKEN is required")
params = StdioServerParameters(
    command="/absolute/instances/My Mac/run",
    args=["mcp"],
    env={"DEVICE_RUNTIME_ADMIN_TOKEN": token},
)
```

标准 SDK 默认不会继承该 token；不要省略 env 后假定所有宿主变量都会传入。此片段不是将值写回 JSON 的方法。恢复到新 root 时只更新 command 根路径，args 和凭证供给方式不变；原同身份实例须保持离线。

## 同版本环境重建

用户先确认该实例全部角色及直接启动的进程已停止，保留完整身份/config/state。rebuild 没有 `--stopped` 参数，不意味着允许在线重建。活动环境缺失时可以通过外部 bundle 内准确 wheel 引导：

```bash
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance rebuild --root "$ROOT" --bundle "$BUNDLE"
```

省略 `--bundle` 使用活动 release 内的 bundle 副本。指纹必须完全匹配原 bundle；同名同版本但字节或来源不同也会拒绝，这不是升级入口。

重建在新目录安装/检查全部原角色，完成后才切换活动指针。失败时活动指针及 config/state/身份不变，旧环境和失败候选均保留；不删除、不覆盖旧目录、不自动启动进程。持久配置或 state 目录丢失会拒绝，必须采用备份恢复。软件环境重建不重放历史操作、不用旧 journal 覆盖新操作，也不意味着接管丢失的后台进程。

## 只读版本与健康诊断

```bash
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance inspect --root "$ROOT"
# 可选，显式选择要探测的中心地址；凭证仅读取DEVICE_RUNTIME_ADMIN_TOKEN环境变量。
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance inspect --root "$ROOT" --health-url http://127.0.0.1:28765
```

JSON包含活动release、交付版本、准确bundle指纹与角色集合；`persistent`、`installation`和`environments`分别报告持久布局完整性、安装记录/bundle完整性和实际导入/固定依赖闭包可用性，不能仅凭文件存在宣称环境健康。坏metadata、缺失环境或忙租约均返回受控诊断，不启动、修复、切换或创建journal/任务/锁文件。每个角色的只读环境探测上限60秒，不生成prepare.log或Python字节码。

`lock`为`free`、`shared_occupied`、`exclusive_occupied`或`unavailable`，表示探测时的租约状态，而非全部进程检测。若独占租约已被占用，安装和环境状态不可安全读取。`online_health.status`默认`not_checked`；显式探测使用现有认证`GET /v1/devices`，总超时10秒、不跟随重定向、不提交operation。`healthy`只证明**所选中心的认证只读接口**可用，不证明本实例的client/MCP、任务或丢失进程健康；缺失可选HTTP依赖、凭证无效、超时或无效响应均是`unavailable`，不输出凭证、敏感配置或响应载荷。

## 升级准备 → 停机确认 → 受控切换

首期准备也要求停机，不提供热准备。用户先停止**该实例所有角色与直接启动的进程**；`--stopped`明确确认此事实。运行共享租约和安装独占租约都不能替代用户确认，本程序从不停止进程，AI不得替用户停止真实实例。这里的停机/备份仅针对Device实例，不是`ai service stop/restart`指令。

```bash
# 先完成用户停机，再准备；准确NEW_BUNDLE由受控构建/校验提供。
NEW_BUNDLE=/absolute/new-accurate-bundle
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance prepare --root "$ROOT" --bundle "$NEW_BUNDLE" --stopped
# 从prepare输出JSON读取release，并显式选择；不能传任意文件路径。
CANDIDATE='<prepare返回的release-ID>'
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance activate --root "$ROOT" --release "$CANDIDATE" --stopped
# 切换不会启动角色。用户另行确认身份/端口/凭证后使用稳定run入口。
```

准备在独占租约下验证原身份、持久角色配置与state完整性；复制准确bundle并重新校验，在不可覆盖的新release安装角色环境，固定核心版本与完整第三方依赖闭包验证成功后写候选记录。它不改`active.json`、`run`、identity/config/state，也不启动进程。旧活动环境损坏时，仍可由准确wheel引导准备；丢失持久数据则拒绝，不能把重装当数据恢复。

候选绑定原活动指针（含切换代次）、身份字节Hash、角色配置Hash和state格式。切换重新验证持久布局、候选记录、角色、准确bundle与实际环境后，仅原子发布`active.json`。原活动指针、身份或配置发生变化时拒绝陈旧准备，要求重新prepare；切走再回到原release也不会让旧准备重新有效。失败保留候选及准备日志，不覆盖原活动环境，不自动清理；切换输出不包含配置或凭证。

### 软件回退不是数据恢复

```bash
PREVIOUS='<之前保留的release-ID>'
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance rollback --root "$ROOT" --release "$PREVIOUS" --stopped
```

回退要求显式选择已有完整、同角色且环境可运行的release，重新校验后只切软件指针。**不恢复旧journal/state/config，不撤销后来operation，不重放历史，不接管旧后台进程。**

若实例携带必需的恢复输出映射，目标client解释器必须从实际安装的`device_runtime.tasks`导出`SUPPORTED_OUTPUT_MAP_FORMATS`，且包含映射格式`1`。prepare在角色环境建好后探测，activate/rollback重新探测；同版本rebuild及首次restore同样不能绕过。未声明、缺模块或不支持时受控拒绝发布，原active、映射与config/state字节保持不变，失败候选保留；inspect将这样的活动环境标为不可用。支持映射的制品仍可正常切换、重建并读取本地旧输出。不带恢复映射的实例仍允许既有旧到新软件路径，不全局禁止旧bundle。

首期兼容域明确限于Device `0.2.0` 与state格式1，允许交付版本/来源/约束等形成不同的准确bundle指纹。`release_version`不是数据schema兼容性证明。未来Device版本即使能安装，也须另行验证并扩展显式兼容声明；不能承诺任意新旧版本可互相回退。首期无数据schema迁移或数据回滚。

## 停机一致性备份 → 新目标隔离恢复

```bash
BACKUP=/absolute/new-private-backup
RESTORED=/absolute/new-offline-instance
# 用户已确认全部实例进程停止；目标必须连空目录都不存在。
uv run --no-project --python 3.13 --with "$DEVICE_WHEEL" python -I -m device_runtime.cli \
  instance backup --root "$ROOT" --destination "$BACKUP" --stopped
# 恢复不依赖原release/venv；可用备份中的准确Device wheel引导，无源码checkout。
BACKUP_WHEEL="$BACKUP/bundle/wheels/ai_device-0.2.0-py3-none-any.whl"
uv run --no-project --python 3.13 --with "$BACKUP_WHEEL" python -I -m device_runtime.cli \
  instance restore --backup "$BACKUP" --destination "$RESTORED"
```

- 备份要求停机确认和独占租约，拒绝已有目标、源目标交叠、实例内部及其他已有实例内部的目标。只复制`instance.json`、全部`config/`、全部`state/`（含任务输出）、稳定`run`与准确活动bundle；不复制venv、旧软件release全集或凭证环境变量。
- 停机后复制前/后及目标的完整文件/目录清单、大小和SHA-256必须一致，活动指针及恢复映射不得变化。SQLite主DB、WAL、SHM及其他state文件全部保留；仅在一次性完整state副本上执行SQLite完整性检查并显式关闭连接，不打开原DB或对业务数据checkpoint。已存在的正式`state/<center|mcp|client>/operations.sqlite3`及`state/client/tasks.sqlite3`按路径身份校验，magic损坏、截断或空文件均拒绝，不能把坏头视作普通输出；其他非SQLite输出仍可原样保留。无法证明有效快照就失败，绝不只复制主DB而遗漏WAL。
- 私有目录`0700`、备份文件`0600`；身份/config/state可能含敏感信息，用户须保护备份，不能把内容写入日志、聊天或版本库。符号链接和特殊文件拒绝，以免复制实例之外的真实数据。
- `backup.json`记录格式、兼容域、活动bundle、全部可恢复数据Hash及必需的`task_outputs`路径映射；`complete.json`最后写入并绑定整个清单Hash。映射清单覆盖任务journal中每个非空输出引用：受管`state/client/output/`路径记录相对位置，非受管引用显式标记为`null`不可恢复；不按basename猜测，也不复制外部输出。再次备份时按仍保留的任务引用重新生成，并传递原有映射，不累计恢复代次历史。清单漏掉仍存在的任务输出引用同样拒绝恢复。无完成标记、文件缺失/增加、Hash不匹配、缺少任务映射或格式不支持均不可恢复；早期试验备份缺少此映射时必须重新停机备份，不能猜测旧路径。失败目录保留且不能当成有效备份，程序不自动删除。
- 恢复先校验完成标记、清单、持久数据、SQLite与准确bundle，目标只能是全新不存在、与备份及已有实例分离的目录。使用备份准确bundle新建角色环境，不依赖旧venv绝对路径。client环境必须声明支持待发布的恢复映射格式；即使包版本相同，未声明的旧制品也不能建立带必需映射的恢复实例。复核复制出的持久字节及安装环境后才发布新本地release；失败候选保留但没有活动指针，不是有效实例。
- 原device_id、身份字节、URL/端口、config、journal、任务记录与输出全部保留。旧任务journal中的绝对`output_path`保持历史原值，不改写数据库；`bash.get`通过恢复实例的私有`task-output-map.json`只读本地受管副本，保留执行ID和unknown状态，查询返回输出尾部；完整原始内容使用设备文件读取，不优先读取仍存在的原输出目录。缺失本地副本、缺失/损坏映射或非受管旧路径均返回不可用，不fallback至原实例。映射绑定身份Hash和自身校验和，活动指针钉住其文件Hash；在环境与持久数据复核后、发布active之前写入，失败不发布active。软件切换、回退及同版本重建保留该绑定，重复备份/恢复也传递旧任务映射，并能包含后来新增的受管任务路径。
- 不自动启动、迁移身份、重放历史operation或接管旧进程。**恢复同身份实例之前，用户必须避免与原在线实例并行运行，并自行解决端口占用。** 数据恢复与软件回退是两条不同路径。

隔离开发、准确制品冷验收、Mac 跨阶段用户路径和独立整体审核构成交付验证链，不以迁移当前试用为前提。文档或命令存在、源码回归通过，都不能代替这些验证或证明真实部署完成。当前试用迁移、真实部署及主服务配置/重启须另行明确授权，不由生命周期命令自动执行。

## CentOS 7 的本地依赖交付

Linux client 可使用 cp313 / x86_64 / glibc >= 2.17 的完整第三方 wheel 集合。旧格式 v2 继续联网解析；带依赖的 v3 将依赖清单、wheel 和哈希纳入 bundle 指纹。不要通过放开 `UV_*` 环境变量或临时 uv 包装器切换依赖。

本地依赖目录为平面目录，仅包含 `manifest.json` 和每个 constraints pin 对应的一个 wheel：

```json
{"format_version":1,"target":{"system":"linux","machine":"x86_64","python":"cp313","glibc":"2.17"},"wheels":[{"path":"package-1.0-py3-none-any.whl","sha256":"64位SHA256","source":"准确来源URL或构建记录定位","license":"真实许可证声明"}]}
```

清单记录所有固定第三方依赖，而不是只记录 Pillow。构建命令 `bundle build` 原有参数不变，增加 `--dependency-dir /absolute/wheelhouse`；目标必须是 `--target-system linux`。构建器校验 pin、wheel 文件名/元数据、平台标签、哈希、来源和许可证，并复制为 v3 bundle。缺失或未登记文件均拒绝。哈希保护制品一致性，不是来源签名；依赖关系仍由安装 resolver 和 `pip check` 验证。

已有 v2 bundle 可直接传给 `instance install` 或 `instance prepare`，同时指定 `--dependency-dir`，工具先生成临时 v3 快照，再执行标准流程，不修改原 bundle。`instance rebuild --bundle <原v2> --dependency-dir <相同依赖>` 必须生成与 active 相同指纹；通常只执行 `instance rebuild --root <root>`，直接使用已保存的 v3 快照。v3 不允许依赖覆盖；更换依赖须创建新 bundle 并走升级准备/激活流程。`--dependency-dir` 与显式 bundle 配套使用。

v3 环境安装使用 `--no-index --only-binary :all:`、固定 constraints 和准确本地 wheel；安装/重建/升级共享此入口。uv 创建环境可能仍需取得 Python，因此**第三方依赖离线不等于 uv/Python 全离线**。必须事先提供 uv 和可用 Python 3.13。稳定 `run` 的 shebang 可能命中系统旧 Python；systemd `ExecStart` 应显式使用受管理 Python 3.13 的绝对路径，再传 `<root>/run client`。不要清理该解释器。

### 构建与验收边界

仓库 `packages/device-runtime/scripts/build-centos7-dependencies.sh NEW_OUTPUT_DIRECTORY` 仅在显式执行时启动隔离 Docker 构建；固定 manylinux2014 镜像摘要，限制 CPU/内存/PID，仅挂载脚本只读和新输出目录，不挂业务数据或 Docker socket。内部固定 Pillow/JPEG/WebP 源码哈希，JPEG 静态、WebP 共享，再由固定 auditwheel 修复 wheel。其他固定版本 wheel 从 PyPI 下载并核对其发布 SHA256；远程元数据不是独立锁定的供应链签名。输出 `wheelhouse/manifest.json`、wheel、构建日志、源码摘要和 auditwheel 报告。构建工具依赖解析和原生编译不承诺逐字节相同产物，应保存每次实际清单。

构建脚本检查 PNG/JPEG/WebP 编解码及坏图拒绝、完整第三方安装和 `pip check`；完整客户端仍需配套准确 Bash/FS wheel。发布前在隔离 CentOS7 cp313 上执行同样图片检查、FS `validate_image_bytes`、客户端导入和安装/重建/升级/回退验收。结构单测、macOS 图片测试和 manylinux 标签检查不能替代真实目标功能验收。实际隔离构建结果和未覆盖环境见下节，不应将部分平台通过描述成所有环境的一键交付。

环境准备失败会保留 release 下 `<role>/prepare.log`，错误信息包含准备阶段及日志路径；不要删除运行实例来排查，也不要直接重跑初始化覆盖现有实例。真实服务停机/重启另遵守运行安全授权。

### 已执行的隔离构建与验收

本轮已在本机 Apple Container / Rosetta amd64 上实际运行上述固定镜像的内部构建脚本，构建退出 0；容器 glibc2.17、Python3.13.16。通过仅面向指定构建容器的临时宿主网桥转发使用 Clash，未修改全局代理或取消 TLS 校验。Docker 包装入口本身尚未在 Docker 运行时实跑；可用 `BUILD_HTTPS_PROXY=http://<容器可达代理>:<端口> bash scripts/build-centos7-dependencies.sh <新目录>` 显式设置构建代理，宿主 `127.0.0.1` 通常不可用于容器。

新 Pillow wheel SHA256 为 `541fddd78076fe1c4aee8074f63c19a9e38f2feb188dd8091c497eb843def215`；auditwheel 报告确认 manylinux2.17。新建干净容器未挂载 JPEG/WebP 构建前缀，以完整本地 bundle 安装12包；`scripts/accept-centos7.py <bundle> <新验收目录>` 实际验证安装、重建、升级准备、激活、回退，以及每个新环境真实 FS PNG/JPEG/WebP 校验、坏图拒绝和依赖篡改拒绝。入口需 Python3.13，独立可写 HOME/UV_CACHE_DIR 和 PATH 中 uv；drop-cap 环境下默认 `/root/.cache/uv` 可能不可写。验收会有意损坏自身临时快照，严禁指向正式实例。

此验收是 Linux6.18 内核下的 glibc2.17 用户态与 amd64 仿真，不是 CentOS7 原生内核、物理机或systemd测试。未验证公网传输、机器重启恢复及 Docker 包装器实际执行。其他独立验收边界保留。

## 当前版本非兼容边界

不支持旧执行协议或旧 CLI 别名。稳定启动脚本与当前模板不一致的旧实例不承诺原地升级，生命周期检查会拒绝；不要手工覆盖脚本绕过门禁。新安装和当前模板实例的重建/切换仍按上述流程验证。历史 journal、输出及实例不会因移除旧接口被自动删除。
