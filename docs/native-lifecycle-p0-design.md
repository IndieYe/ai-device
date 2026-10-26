# Device 原生安装、配对与远程升级：P0 冻结设计

> 日期：2026-10-26。阶段：P0 设计完成，P1 尚未开始。
> 用户已认可首选方案，并要求先完成 P0。本文冻结本期产品与动作契约，不表示已实现或实机验收。
> 权威需求：ai-assistant `doc/requirements/device-native-installation-pairing-and-remote-upgrade.md`，提交 `7be75392c352151d6cb29cbdad2770f2694dcedd`。
> 当前实现基线：ai-device `4f7891d1fc27eeab20cc60f7e5298cb1ab187045`。
> 工作现场、基线测试及后续进度见 [实施记录](native-lifecycle-implementation.md)。

## 1. 冻结原则和范围

只交付 macOS/Linux 客户端原生包、在线配对、自启、中心显式发起的单设备升级、恢复和卸载。中心需增加配对、持久设备身份、发布登记及升级编排，不重做其通用安装。Windows/WSL 专项、大规模灰度、自动追最新版、任意失败自动回退及完整 GUI 不纳入。

原生包负责受控交付，共用 instance/release 核心负责准备和激活。配对、自启、接受远程升级、无人值守切换是四个独立选择。客户端和 Bash 均以普通用户运行；特权组件不提供任意命令执行。

P0 冻结的是计划支持范围、接口、状态、权限和验证要求。发布账号、测试机可用性、真实服务确认是后续执行前置条件，不得冒充已具备，也不因其暂缺阻止本地实现。若证据否定可行性，应修改设计并记录原因，不悄悄删掉范围。

## 2. 平台与交付矩阵（D01、D02、D09、D10）

| 目标 | 包型 | 首批架构 | 基线与服务 | 验收边界 |
| --- | --- | --- | --- | --- |
| Ubuntu 24.04 LTS | deb | x86_64/amd64 | 系统 systemd，指定普通用户 | 干净系统安装、更新、卸载及实机恢复 |
| Rocky Linux 9 | rpm | x86_64 | glibc 2.34，系统 systemd，指定普通用户 | 原生 RPM 事务与实机恢复，不用 Ubuntu 结果替代 |
| macOS 14 或更新的受支持版本 | pkg | arm64 | 登录级 LaunchAgent；有限系统安装 helper | arm64 实机、登录与休眠恢复、机器重启后登录恢复 |

这是首批新增支持目标，不是“已支持”。macOS x86_64、Linux arm64、CentOS 7/glibc 2.17 原生包不作为本期完成条件；已有相关能力不能回归破坏。后续扩矩阵须新增准确制品和平台验收，不按“同属 Linux”推断。

北京服务器先做身份和系统只读探测；若不匹配矩阵，可申请隔离虚拟机作为相应发行版目标。不得修改服务器系统来迁就计划，也不能把容器与仿真结果充当真实 systemd 验收。既有 glibc 2.17 工作保持独立，集成其修改前重跑安装依赖回归。

运行时采用准确锁定的 CPython 3.13 可重定位平台分发，构建来源优先 python-build-standalone 的固定 release；构建输入记录上游版本、URL、摘要、许可及动态库约束。P1 首个构建任务选定经测试的准确补丁版本并写入发布锁，不追最新版、不隐式下载或回退。原生包内含运行时、Device/Bash/FS 精确配套组件和完整离线依赖；终端用户无需 Python/uv 或第三方包联网安装。构建工具可用 uv。

正式 macOS pkg 必须使用 Developer ID Installer 签名、公证并附票；可执行 helper 按 Apple 要求签名。无签名账号时允许明确标注的隔离开发包，只能作为本地测试成果，不能标为正式交付。账号、证书及付费操作另行授权；正常安装不得以关闭 Gatekeeper 为前提。

Linux 包需来源认证；全平台共同使用 Ed25519 签名的 UTF-8 规范化发布描述及 SHA-256 包摘要，不仅依赖传输 HTTPS。签名对象为 schema=1 的 release payload 按既有 canonical JSON 规则编码后的字节；外层 envelope 包含 payload、key_id、signature（base64），签名不包含 signature 自身。固定可信公钥由安装包内引导材料提供；允许显式管理员更新信任根，不接受从待验证发布自动引入的新信任根。密钥和签名服务接入是发布资源门禁，不写入源码。

## 3. 布局、实例与运行用户（D04、D05）

### 3.1 布局

Linux 系统路径：

```text
/opt/ai-device/bootstrap/<bootstrap-version>/   # 稳定管理入口所依赖的独立引导环境
/opt/ai-device/packages/<release-id>/           # 原生包交付内容（版本化、不可变）
/opt/ai-device/instances/<instance-id>/         # 安装核心管理的实例
  run                                         # 稳定客户端入口
  active.json                                 # 唯一激活版本事实源
  instance.json
  releases/<release-id>/                       # 由共用核心准备的可运行环境
  config/                                     # 身份引用、角色与本地策略
  state/                                      # 原任务台账、维护门与本地操作记录
/var/lib/ai-device/manager/                    # 管理事务与包状态对账
/etc/ai-device/                               # root 管理的绑定与受限策略
```

macOS 系统程序放 `/Library/Application Support/ai-device/`，对应 bootstrap、packages 和 instances；用户私有身份/配置/任务数据放 `~/Library/Application Support/ai-device/<instance-id>/`，LaunchAgent 放用户 `~/Library/LaunchAgents/`，系统安装 helper 由独立 LaunchDaemon 承载。固定系统程序路径不含用户名；用户数据的绝对路径登记绑定。Linux 和 macOS 的程序与状态路径通过布局契约映射，不向现有核心硬编码新增系统路径。

现有安装核心把身份、配置、state 与实例根绑定：P1 优先允许实例根保留现有布局，以明确指向持久数据根的路径映射支持系统安装；不得复制可变数据形成双事实源。映射、权限、迁移与 active 指针的原子性需有直接测试。

稳定入口用自带解释器的绝对路径启动，不依赖现有 `#!/usr/bin/env python3` 能在用户 PATH 找到正确 Python。日常服务、CLI 和维护工具不依赖活动客户端 venv；bootstrap 在升级器运行期间不能被覆盖。

### 3.2 用户和权限

- Linux 安装向导要求明确指定已有普通运行用户，拒绝 UID 0，不悄悄新建带额外权限的账号。systemd 为系统级服务，使用该用户执行客户端。工作区以该用户原有权限访问。
- macOS 默认当前登录普通用户的 LaunchAgent；安装时记录 UID、home 和 launchd domain，系统 helper 不能把客户端以 root 启动。首期不提供客户端 LaunchDaemon 模式，不承诺登录前在线。
- 系统 helper 只允许管理已登记 instance/service：prepare、drain、apply-package、activate、start、query、reconcile、uninstall。输入只含操作 ID 与已登记引用，不接受 shell、任意 argv、任意服务名和路径。
- helper 本地 IPC 使用系统 socket 权限及 peer UID 识别所属用户；实例绑定、发布来源、策略、批准均需核对。可信本机代码模型下不构造通用沙箱，但特权输入仍必须收窄。
- helper 以 root 负责包及系统服务操作，客户端只能读程序、写自身状态。root 管理的发布和批准引用不能由普通客户端改成任意安装包或实例。
- 远程发起和无人值守默认均关闭。管理员修改本地策略可降低权限；操作在准备、收口及包事务前重新检查最新策略。撤销阻止尚未开始的切换，不伪装可中止已执行的不可逆步骤。

## 4. 原生包和 release 事务（D03）

### 4.1 单一发布与事实源

每一发布有一个签名 `release.json`，包含 schema=1、release_id、三包准确版本、bootstrap_min、平台/架构/系统最低版本、原生包 URL/大小/SHA256、内置 payload 摘要、协议及状态兼容范围、允许源版本、空间需求、签名 key_id。release_id 和摘要确定后不可变；中心发布引用不可被同名覆盖。

原生包包含只读、版本化 payload 和唯一元数据成员。prepare 对原生包使用固定工具无脚本提取到私有 staging，校验成员路径、链接及清单；不执行包脚本。然后共用安装核心在最终版本目录准备候选，避免把带绝对路径的 venv 随意移动。候选准备是可恢复的安装步骤，不修改 active。

程序活跃版本只有实例 `active.json`；包管理器版本记录“已安装包”，管理记录关联“候选已准备、包已应用、当前激活”。状态 CLI 必须同时展示这三项。包版本新而 active 尚旧是可恢复的 `package_applied_pending_activation`，不是成功或需要覆盖修复。正常终态要求三者与目标构建一致。

### 4.2 prepare 与 apply

1. 在普通服务仍运行时下载、认证、无脚本提取、检查空间及兼容、准备候选。
2. 用户明确批准或策略允许，进入维护窗口，取得单实例管理锁；核对源 active/activation_id、绑定、用户、策略和目标摘要。
3. 封闭任务准入并等任务收口，停止指定客户端。升级器处于独立 systemd/launchd job，使用不在本次替换范围的 bootstrap。
4. 写入 `apply_intent` 并落盘，调用准确原生包的标准安装机制；包中版本化 payload 不覆盖正在执行的 bootstrap 或旧 release。
5. 包完成后写入并验证安装回执；复用现有 activate 的预期指针比较与原子切换。
6. 按平台恢复同一普通用户的客户端，验证后保持旧 release，清理另行受控，不在关键切换中删除。

同一任务只有一个管理锁所有者；远程 prepare 可在不阻塞旧服务的阶段完成，但不同升级、手工包操作、卸载不能并发激活。系统包事务还要取得包管理器原生锁。

Linux `preinst/pretrans` 与 macOS installer preflight 必须在任何替换前联系独立管理器，校验本次事务 ID。手工 `dpkg -i`/`rpm`/Installer 调用使用同一入口：全新安装可完成软件部署后运行用户向导；已有运行实例则在无已批准维护事务时拒绝并给出 `device-runtime manage install` 入口，不能在包钩子里强停。首次安装脚本不能自动配对、开启升级或启动未配置服务。

包脚本不得调用任意网络下载、修改用户数据或自主更换 active。首次安装尚无管理器时，由包内独立、仅用内置运行时的 preflight 完成“无已有实例、无冲突进程、准确平台和路径”的只读核查，post-install 只部署 bootstrap/helper 及待配置状态；不套用已有实例的升级许可。后续更新前把当前 bootstrap 校验复制到管理器拥有、非包管理器移除范围的事务目录，独立 job 从该目录运行；否则包管理器移除旧包文件可能破坏仍活跃的解释器。候选 bootstrap 通过自检后才在后续事务引用切换，关键事务结束前不删除事务运行副本。无人值守时不能临时弹密码后僵停，缺少预授权 helper 就明确阻塞。

### 4.3 包脚本重入与恢复

包事务标识与预期摘要持久化；脚本重复调用幂等核对同一记录。包事务结果未知时查看 dpkg/rpm/Installer 回执及准确文件摘要，不重复申请另一升级。不手改包数据库，不默认运行系统级修复命令。

| 现场 | 恢复动作 |
| --- | --- |
| 下载/候选失败，未封闭准入 | 旧版本继续服务，可同 ID 重试准备 |
| 收口超时，服务仍是原进程 | 返回阻塞；明确取消可恢复准入 |
| 停服后包未应用 | 待对账；确认源 active 与旧环境完好后提供明确的恢复旧服务动作 |
| 包已应用、active 仍旧 | 核对目标清单、源绑定与批准，再同 ID 继续激活；不自动回滚包数据库 |
| active 已目标、服务未确认 | 对账和按批准恢复目标服务，不重放包安装 |
| 数据版本不兼容旧 release | 禁止旧软件激活；进入人工恢复指引 |

P1 用沙箱包管理器环境验证钩子顺序、文件树及元数据重入；P3 才完成真实升级端到端。若具体包系无法保证写入前钩子门禁，必须停止该包实现、修正设计并审核，不宣称可通过“仅版本化路径”替代事务安全。

## 5. 中心持久化与静态凭证过渡（D06、D08）

新增 `control.sqlite3`，与现有 `operations.sqlite3` 分离；版本化 schema、显式迁移、事务及取消收敛参考现有 Journal，完整同步事务放在线程中。迁移失败使新管理面启动失败，不部分启用；不修改既有操作终态。

主表：devices、credentials、pairing_invites、pairing_redemptions、releases、upgrades、upgrade_events、control_schema。设备 UUID 为稳定 ID，展示名独立；满足当前 Identifier 长度/格式。随机凭证、邀请码和恢复秘密使用独立域前缀的 SHA-256 校验值，比较采用常量时间函数，不使用低熵密码代替随机秘密。必要的可恢复响应使用 AES-256-GCM 封装，每条记录独立随机 nonce，以记录类型、ID、schema 作为关联数据；密钥位于中心权限 0600 的管理文件，不进入 SQLite 或日志。密钥 key_id 随封装记录，轮换期间保留仍在恢复窗口的旧 key 引用，禁止轮换直接使在途兑换无法恢复。

一次性备份涉及新数据库和密钥；无密钥不能宣称可恢复配对回执。迁移与恢复需要准确版本测试，禁止“表不存在就无条件新建并继续”掩盖损坏。

旧 device_tokens 作为受控导入输入：中心新版本首次启动在事务中登记为 legacy 凭证；来源指纹固定，不在每次启动覆盖已撤销凭证。冲突令导入失败并报告脱敏设备 ID，旧文件原样保留，删除须另授权。新设备认证查询持久凭证；旧映射只在明确迁移模式导入，不能作为绕过撤销的备用认证。

旧设备未纳管时保持原操作协议；不能因导入凭证就获得升级能力。空设备集合允许新中心启动，以便首台配对；管理 Token 校验保持。新增设备和撤销无需重启中心。撤销后旧连接被关闭，后续操作派发拒绝，已接受任务结果保留并对账，不抹掉日志。

## 6. 配对邀请与恢复协议（D06）

### 6.1 数据与时限

- 管理端创建邀请，类型为 new 或 replace；replace 必填目标设备和当前 credential generation。
- 配对码由安全随机源生成至少 128 bit，使用无歧义可复制编码；默认有效期 10 分钟，可配置 1—30 分钟。
- 邀请成功消费一次。每邀请最多 5 次错误恢复秘密尝试；未知码另做来源维度有界限流，避免用公开返回区分有效码。按本地单用户公网实际边界实现，不建复杂审批平台。
- 客户端发请求前持久保存 `redemption_id`、256 bit `recovery_secret`、目标中心与邀请引用。文件 0600、目录 0700；日志只保留操作 ID。
- 中心保留加密配对响应 24 小时用于同一兑换方恢复；客户端在此窗口内显式 ack 后清除封装，保留非秘密审计摘要。超过窗口未收到凭证则需管理员重新邀请／替换，不让已用码再次公开兑换。
- 长期设备凭证为独立 256 bit 随机值；中心仅保存校验值，响应恢复封装可包含原凭证但必须加密。

### 6.2 API（均为计划接口，P2 实现后才可用）

| 方法与路径 | 认证 | 输入／输出 |
| --- | --- | --- |
| POST /v1/pairing/invitations | 管理 Bearer | request_id、kind、display_name、ttl_seconds、replace_target；返回 invite_id、一次性 code、expires_at |
| GET /v1/pairing/invitations/{id} | 管理 Bearer | 脱敏状态：active/consumed/revoked/expired，不再返回码 |
| DELETE /v1/pairing/invitations/{id} | 管理 Bearer | 撤销未消费邀请，重复撤销幂等 |
| POST /v1/pairing/redemptions | 邀请码＋恢复秘密 | code、redemption_id、recovery_secret、平台/架构/显示名；返回设备 ID、凭证、generation、中心标识、管理协议版本 |
| POST /v1/pairing/redemptions/{id}/recover | 原恢复秘密 | 不再提交配对码；只返回原响应，不创建新设备 |
| POST /v1/pairing/redemptions/{id}/ack | 原恢复秘密 | 确认客户端已安全保存响应，可重复；清除恢复封装 |
| POST /v1/devices/{id}/revoke | 管理 Bearer | expected_generation、request_id；撤销并关闭连接 |

秘密只在 HTTPS 请求 body／安全本地输入传递，不放 URL query、argv、错误文本；TLS 验证不能默认关闭。中心对邀请创建 request_id 保存短期加密回执，响应丢失只向同一管理认证主体恢复，不因为幂等需要永久保存明码。

### 6.3 原子消费与替换

消费事务检查邀请、有效期、replace generation，插入设备或轮换 credential、保存恢复校验值及封装、标记 consumed，一起提交。相同 redemption_id 与相同请求摘要返回原结果；相同 ID 不同请求冲突；不同 ID 争用同一邀请码仅一个成功。邀请码已消费不能由另一个 recovery_secret 取得凭证。

replace 在消费提交时撤销旧 generation，并断开旧连接；新客户端即使未收到响应，也可恢复，不产生双有效长期身份。管理员创建替换邀请时必须展示旧设备会失联的影响。客户端写凭证与身份需原子落盘、fsync 后 ack；崩溃前后均可重入，不能先 ack 再保存。

设备凭证保存在 Linux 用户权限 0600 的受限文件，macOS 默认 Keychain；非交互系统管理只能保存凭证引用。macOS Keychain 在登录前不可用是登录级支持边界；不静默降级到明文文件。

## 7. 升级 API、管理协议与策略（D07）

### 7.1 独立管理协议

保留现有 v1 hello/execute/report 的 Bash/FS 数据协议不变。新增专用 `management.v1` 出站 WebSocket `/v1/devices/{device_id}/manage`，使用设备 Bearer；不向严格 v1 Hello 塞新增字段导致旧客户端失败。管理帧 schema=1、type、operation_id、request_digest、sequence、payload，单帧上限 64 KiB；下载内容不走 WebSocket。

管理 hello 上报 instance_id、credential generation、active release/build、platform/arch/os、bootstrap_version、managed_binding_digest、可用动作及策略摘要。中心检查身份与连接绑定。设备未上报 management.v1 或 helper 不可用时，只提供旧操作，不开放升级。

控制帧为 prepare、activate、cancel、reconcile、status；回执是持久接收或准确拒绝，不是最终成功。帧 sequence 和 digest 用于去重、乱序检测及断线重放；本机事务日志是本机动作事实源，中心记录意图与接收证据，不用最后一次网络消息覆盖更高序列状态。

### 7.2 管理 API 与 CLI

所有写请求包含 request_id，同一键相同规范化输入返回原操作，不同输入 409。API 使用管理 Bearer，设备报告用设备认证；返回 schema_version、operation_id、revision、state、reason_code、next_actions，不回传秘密。

| API | 主要输入／效果 | 计划 CLI |
| --- | --- | --- |
| POST /v1/releases | 签名 release.json 和准确包引用，验证后登记不可变发布 | device-ops releases register |
| GET /v1/releases | 平台/架构过滤，分页默认20最大100 | device-ops releases list |
| GET /v1/devices/{id}/upgrade-eligibility | 目标 release_id；返回条件和原因 | device-ops upgrades eligibility |
| POST /v1/upgrades | device_id、expected_source_build、release_id、mode=prepare；创建稳定 upgrade_id | device-ops upgrades create |
| POST /v1/upgrades/{id}/activate | expected_revision、计划 digest、批准引用 | device-ops upgrades activate |
| GET /v1/upgrades/{id} | 本机与中心状态、最后证据时间和缺口 | device-ops upgrades get |
| GET /v1/upgrades | 设备/状态过滤，固定游标分页 | device-ops upgrades list |
| POST /v1/upgrades/{id}/cancel | expected_revision、原因；过取消边界明确409 | device-ops upgrades cancel |
| POST /v1/upgrades/{id}/reconcile | 请求只读对账，不以查询暗含重新执行 | device-ops upgrades reconcile |

本地新增 `device-runtime manage` 下的 install、pair、status、policy、approve、upgrade-status、reconcile、uninstall。CLI 交互展示权限和数据影响；机器模式明确 JSON 与非零退出码。敏感参数通过 stdin 或受限凭证引用，不在命令行带 Token。旧 upgrade 命令保留原含义，不暗中转为远程协议。

错误码冻结为分类契约：unauthorized、invalid_request、idempotency_conflict、revision_conflict、invite_unavailable、recovery_unavailable、not_managed、policy_denied、approval_required、outside_window、incompatible_release、insufficient_space、busy、tasks_unsettled、cancel_too_late、result_unknown、center_confirmation_pending。具体可附脱敏结构化 details，不依赖解析英文异常字符串。

### 7.3 默认策略与批准

策略 schema=1：accept_remote_upgrade=false、allow_unattended_activate=false、trusted_publishers、allowed_channels、allowed_release_ids、maintenance_windows_utc、drain_timeout_seconds=300、confirmation_timeout_seconds=120。窗口 UTC 半开区间；配置错误默认拒绝；不把时钟不确定当可切换。

接收远程关闭时不接受新升级；远程开启但无人值守关闭时允许准备，切换需本地管理员 `manage approve`。批准绑定 upgrade_id、计划 digest、预期源 build、目标包摘要及有效期，默认30分钟，仍受维护窗口约束。中心 activate 表示请求执行，不代替本地批准。无人值守开启时允许在明确发布范围及窗口内执行，无需逐次交互。

旧 allow_upgrade 继续仅约束旧监督器；接管生成新策略时两个远程字段均 false，需安装向导明确选择。不得复用一个 bool 承担全部授权。

## 8. 升级状态机与任务准入（D07）

### 8.1 持久状态

中心 `upgrade_id` 同时作为本地 upgrade_id；计划固定源 active/activation_id、目标 release/build、签名清单摘要、instance binding，策略 revision 记录且执行前重查。每次本机提交产生单调 local_sequence 和 fsync 的事件，中心保存已确认序列。

| 状态 | 动作／检查点 | 可取消 | 失败收敛 |
| --- | --- | --- | --- |
| created/checking | 校验绑定、版本、权限、签名和空间 | 是 | rejected 或 blocked，未动服务 |
| preparing | 下载、验证、准备候选 | 是，等待当前安全写入收敛 | failed_prepare，旧版本可用，同 ID 重试 |
| ready/awaiting_approval/awaiting_window | 保存候选与批准要求 | 是 | 不因等待自动失败 |
| draining | 封闭新任务、等待在途与unknown收敛 | 条件允许 | blocked_tasks；明确取消核对旧进程后恢复准入 |
| stopping | 写停止意图、执行准确服务动作 | 否 | unknown，先对账不重复停止 |
| applying_package | 原生包事务，保存回执 | 否 | unknown/package_pending，核对包数据库 |
| activating | expected_active 比较及原子指针 | 否 | unknown，核对源／目标，不盲重发 |
| starting/verifying_local | 恢复目标进程并核对本机身份、PID和构建 | 否 | unknown 或 blocked_recovery，明确人工恢复入口 |
| awaiting_center | 本机已验证，中心重连与业务健康 | 否 | 超时保留待确认，不标失败或回滚 |
| completed | 中心独立确认目标身份、构建与健康 | 不适用 | 终态不可改成另一执行结果 |
| cancelled/rejected | 停止前安全退出或明确拒绝 | 不适用 | 保留审计，不创建新副作用 |

blocked 保存 resume_phase 和 reason，不能模糊返回 false。unknown 表示副作用无法确认，不等于失败；reconcile 只读核对状态和现场，可生成观察事件，但不能隐式重启、安装、激活或清锁。继续执行需同 ID 的显式恢复动作、当前授权和准确前置条件。

### 8.2 任务收口

管理准入门与现有 maintenance owner 绑定 upgrade_id；服务端先停止向该设备派发新任务，本机再关闭接受。竞态中已由本机持久接受的任务必须继续执行或被明确记录为未接受，不能只在中心视图消失。

等待集合包括操作日志 running、已接受未开始、后台任务及 OS 后代；unknown 不当作已完成。不存在 running 行不证明子进程已退出。超时返回 blocked_tasks，不强杀、不重放，不自动恢复准入。

取消仅当原 active、维护 owner、原进程健康及服务绑定均未变，调用现有 resume-admission 路径；失败保持待对账。取消与停止竞争由同一管理锁和 revision 裁决，不接受“取消成功”后仍进入停服。

### 8.3 中心完成证据

目标客户端以原 device_id、新的准确 build、同一 managed binding 和期望 activation 重新认证；管理 hello 与本机验证相符。中心再发一个专用只读 health challenge（随机 nonce、30秒有效），检查协议、能力与对应 nonce。它不通过任意 Bash 执行业务命令，也不借返回版本号替代构建绑定。

中心确认事务保存目标证据、local_sequence、确认时间并置 completed；响应丢失后仍可同 ID 查询完成，不重复升级。120秒内未确认只进入 awaiting_center 并说明原因。业务 Bash/FS 健康另由隔离端到端回归覆盖，不把健康挑战当所有业务能力均已验证。

## 9. 存量兼容与部署顺序（D08）

1. 先冻结并测试迁移、备份、恢复及 v1 数据协议回归；对新中心运行离线／隔离升级验证。
2. 经真实部署授权后升级中心；导入旧凭证到新持久库，验证旧设备仍可连接，新增邀请可在线创建。迁移失败不启用部分管理面。
3. 发布原生包后先部署独立测试设备，完成配对与管理通道；未纳管设备不显示“可远程升级”。
4. 手工旧实例执行 inspect，核验用户、单 client 角色、身份、目录、服务、状态版本和旧 nonterminal 升级记录。混合角色、unknown 或在途操作直接阻塞接管，不创建新 ID 跳过。
5. 显式管理员 approve-adoption，备份原配置与摘要，尽量复用原设备 ID；无法安全转换时交付迁移计划而非覆盖安装。新策略远程字段默认关闭。
6. 原实例能安全纳管且目标状态兼容后才执行中心升级。升级时保留旧 release，不能因为包版本新就允许回退数据。

旧中心不支持管理通道时，新客户端明确显示 not_managed；数据 v1 仅在原认证方式明确配置且兼容时可用，不静默退回弱认证。新配对不能在旧中心工作，错误需解释先升级中心。

中心持久化迁移和客户端状态迁移是独立版本；数据升级前检查回退边界。中心旧二进制不认识新 schema 时拒绝启动，不在旧库上边运行边修改。中心回退须根据备份及当前凭证／操作记录制定恢复计划，不默认覆盖新数据。

## 10. 验收、风险与实施动作（D10）

### 10.1 P0 覆盖与关闭表

| 决策 | 冻结位置 | 下阶段执行条件（不等于设计未决） |
| --- | --- | --- |
| D01 支持矩阵 | §2 Ubuntu24.04/Rocky9 amd64，macOS14+ arm64 | 目标实机访问和北京现场探测 |
| D02 运行环境 | §2 固定CPython3.13可重定位分发，包内完整依赖 | P1准确上游release、许可和重定位验证写入构建锁 |
| D03 包与release | §4 签名清单、prepare无脚本、包钩子门禁、active唯一事实源 | 各包系沙箱事务及中断实验；不满足即修订设计 |
| D04 布局/服务 | §3 指定普通用户、系统systemd、登录LaunchAgent | P1目录权限及映射测试 |
| D05 有限权限 | §3 helper限定动作/绑定，默认不无人值守 | 平台helper真实安装授权与签名 |
| D06 配对 | §5—6 持久库、原子消费、恢复秘密、加密短期回执 | P2并发、丢响应、keychain和撤销测试 |
| D07 升级 | §7—8 独立管理v1、API/CLI、状态、批准和对账 | P3状态机、故障注入及Linux实机 |
| D08 兼容 | §5、§9 静态导入、v1不变、中心先行、非终态阻塞接管 | 升级／恢复fixture与旧协议回归 |
| D09 macOS | §2、§3 正式签名公证、登录级边界 | 账号/证书/签名资源批准，P4实机 |
| D10 测试交付 | §10、权威需求P1—P5 | 不把资源未具备写成已通过 |

### 10.2 阶段产物与回归入口

- P1：release清单schema、平台构建锁、安装核心适配、包事务和helper基础、Linux包及失败恢复测试；先协调 glibc 在途修改，不复制其工作区。仍未实现配对时不得标整体安装上线成功。
- P2：control.sqlite3迁移、邀请/凭证/恢复API和CLI、Keychain/受限文件、管理握手基础、Linux新安装上线及自启验收。
- P3：发布和升级API、持久状态机、批准、独立job、中心确认、幂等与故障注入、Linux A→B。
- P4：macOS包及helper签名/launchd、登录与恢复、A→B实机。
- P5：旧凭证迁移、旧协议、接管、撤销、卸载、全平台与宿主/Bash/FS回归、文档和整体审核。

现有基线命令见实施记录（347项通过）；P0 无产品代码修改，不以历史测试证明新接口实现。每个新增模块应有成功、拒绝、取消与崩溃恢复测试，异步管理面补阻塞I/O响应性测试；数据库事务／包进程／文件校验整单元迁到线程或独立job，不能只把一条SQL移出事件循环。

测试证据记录源码commit、制品指纹、平台、操作ID、步骤与真实结果，不记凭证。必须覆盖：包事务各检查点重入、包数据库与active不一致、配对并发/响应丢失、中心/客户端/helper重启、策略撤销、task unknown、取消竞争、维护窗口过期、签名错误、磁盘不足、旧非终态升级接管拒绝、数据不兼容回退拒绝。

### 10.3 P0 自审发现及处置

| 风险 | 设计处置 | 验证阶段 |
| --- | --- | --- |
| 把pkg/deb/rpm解包等同于可运行 | 区分无脚本prepare和原生包apply；准确运行时和最终路径测试 | P1 |
| 包已更新但旧程序仍活跃 | 三项状态展示，unknown对账，不伪报完成 | P1/P3 |
| helper在升级时被覆盖 | 版本化bootstrap，独立job，活动版本不替换 | P1/P3/P4 |
| 配对已消费但响应丢失 | 原兑换方恢复秘密＋加密24小时回执，落盘后ack | P2 |
| 扩展严格hello破坏旧端 | 独立管理v1通道，数据v1不变 | P2/P5 |
| 升级请求比策略撤销先到 | 准备/收口/apply前重查；不可逆阶段明确边界 | P3 |
| 旧nonterminal操作被新计划绕过 | 接管先核验，unknown阻塞，不新建ID逃逸 | P3/P5 |
| 资源未具备被当功能完成 | 平台测试、签名发布和真实操作独立门禁 | P1—P5 |

P0 采用需求到决策的静态映射、API/状态/权限及失败路径自审，不声称独立第三方审核或动态原型已完成。P1 包事务与重定位实验是强制早期验证，发现矛盾必须回到对应设计修订，不带缺陷进入P2。

## 11. P0 结论与接续

P0 设计已覆盖权威需求的 D01—D10、阶段前置及失败恢复契约，可作为 P1 实施输入。用户已确认采用首选方向；具体接口与默认值在本次设计中冻结。上述支持矩阵是本期计划，签名、发布密钥、测试机及真实服务操作仍需相应资源和明确授权，不把开发授权扩大。

本轮只完成P0设计，不进入P1、不访问真实设备、不发布、不推送、不停止或重启真实服务。接续P1时先核对本设计、任务Worktree和glibc在途集成状态；完成准确运行时锁和包钩子可行性验证后再扩大实现。若用户要求改变平台、权限或批准方式，应先更新本设计和验收矩阵。
