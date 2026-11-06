# macOS arm64 客户端完整包开发记录

## 当前状态

**阶段二进行中，尚未交付 PKG 安装升级路径，不可用于真实设备部署。**

Mac 侧代码已提交（`e2c4a70` 起），包含 13 个 `client_macos_*.py` 模块、`scripts/build-client-pkg.py` 构建器、105 个测试文件；本节下方各节记录了演进过程，其中“改动未提交”类描述均已过时。

阻塞交付的未解项：

1. **PKG 仍有 5 条 `write: Permission denied` 警告，根因未解决**——不解决不能公开发布或安装。
2. 生产停止证明（launchd TERM 超时后发 KILL 的竞态）仅验证候选方案，未接入生产启动路径。
3. Installer 写入期互斥未完成。
4. 登录/退出/未登录更新生命周期未验收。

权威需求为 `doc/requirements/client-native-install-upgrade.md`；Linux 侧使用者指南见 `client-native-linux-guide.md`。

已完成构建准备：平台锁选择、arm64 uv 工具锁、离线预装运行树。
`build/client-native/mac-tree1` 是本阶段早期构建，不包含后续新增 Mac 布局模块。
其 39 个 Mach-O 均为 arm64、最低系统标记 11.0；不是 macOS 11 实机支持证明。

## 已实现的纯定义

`client_macos_layout.py` 只渲染路径与 plist，不读账户、不写文件、不调用 launchctl。
调用方必须从显式选定的普通账户解析 UID 和 home，再传入；渲染器只验证
基本输入形状，不证明该账户存在或该目录的所有权。

- 程序：`/opt/ai-device-client/runtime`，与构建器固定 shebang 一致。
- 指定用户配置：`<home>/Library/Application Support/ai-device-client/client.json`。
- 凭证：同目录 `token`；状态：同目录 `state`。
- 日志：`<home>/Library/Logs/ai-device-client/client.log`。
- LaunchAgent：`<home>/Library/LaunchAgents/local.ai-device.client.plist`。
- 服务目标：`gui/<uid>/local.ai-device.client`，不猜当前登录用户或 root HOME。

定义使用 `RunAtLoad=true` 和 `KeepAlive.SuccessfulExit=false`；正常退出不要求
自动重启，异常退出按 30 秒节流。该节流不是总重试次数上限，也不是完整的
启动失败处理。设置 `AbandonProcessGroup=true`，避免主进程退出时 launchd
强杀同组残留。不能由此推断后代已结束。没有设置 `ExitTimeOut=0`。

## 尚未解决的安全停止验证

本机 `/usr/share/man/man5/launchd.plist.5` 与 `man1/launchctl.1` 明确：

1. 服务停止的默认 TERM 超时后可发 KILL。不能对运行服务直接 bootout 并声称不强杀。
2. `ExitTimeOut=0` 无限等待，文档警告可能永久阻塞系统关机；不采用此逃避方案。
3. `launchctl kill SIGTERM <service>` 是发送指定信号，不等同停止/卸载定义。
4. `disable` 保证禁止加载并持久化跨重启；文档不足以证明已加载 KeepAlive 服务的
   替换窗口互斥。不能假定 disable 后 TERM 就不会重启。
5. `KeepAlive.PathState` 的文件观察存在竞态和丢事件，文档明确不推荐；不以其作为锁。

旧 `upgrade_supervisor.Launchd` 使用 SIGKILL，不接入新路径。

候选顺序是：管理互斥 → 空闲租约 → 服务管理器发送 TERM → 有界等待正常退出及
后代完全结束 → 卸载已停止定义 → 替换 → 按登录状态加载 → 精确版本 ready。
这只是待验证方案，尚未实现，不能作为验收证据。至少需解决：

- 主进程异常退出/KeepAlive 重启与卸载之间的竞态；是否需要独立运行互斥。
- Bash 使用 `start_new_session=True`，仅检查主 PID/进程组不足；后代脱离或重归属
  时如何保守拒绝，不能通过宽泛 kill 用户进程解决。
- 当前共享 Bash 关闭代码会取消未结束任务，并存在 SIGKILL 清理路径；正常升级
  必须在租约保护下证明无该类活动，不得仅靠 TERM 后的主 PID 为零。
- 安装器退出后的租约释放、停止超时恢复准入、下一次修复的行为。
- root installer 直接安装、用户退出/重新登录、安装过程中 GUI 域变化的竞争。
- 已停止且旧解释器损坏时必须不依赖旧 Python 完成外部修复。

## 验证和边界

当前新增布局/定义 13 项测试、既有构建 17 项测试合计 30 项通过；仅证明
纯定义和构建选择，不证明 launchd 生命周期。测试包含显式用户、路径带空格和
shell 字符、plist 往返、无无限退出超时、无 PathState。

未安装 PKG、未加载 LaunchAgent、未创建测试账户、未操作真实客户端。
本机未发现 tart/prlctl/limactl 命令及常见虚拟机应用；这不证明不存在其他可用环境。
在明确隔离的 Mac 用户/VM 中完成停止竞争、残留后代、A→B、登录退出及未登录
更新验证前，不得声称完整交付。真实用户/虚拟机建立与安装操作需明确环境及授权。

## 指定账户注册预检（后续增量）

新增 `client_macos_account.py`，尚未接入注册 CLI，不执行注册或服务操作。

- 仅 `pwd.getpwnam` 查询显式名称，不猜 console 用户；首期支持标准 macOS
  UID >= 501 的普通账户，拒绝 root、系统账户、禁止登录 shell 与不一致身份。
- `validate_registration_files` 只读 lstat 校验 home/Library/配置及已有注册目标，
  拒绝错误所有者、不兼容权限、symlink/FIFO、已有 plist；不读取凭证或递归 chown。
  路径检查不是 ACL 或并发修改保证，最终注册器仍须持有管理锁并实际验证账户访问。
- `validate_config_as_user` 生成固定 `/usr/bin/sudo -n -H -u #UID --` 命令，
  使用包内 Python `-I -B` 读取配置，核对 UID、固定 state/token 路径，不启动客户端。
  路径作为独立 argv，不插入 Python 源码；禁用标准输入输出和错误输出，15秒超时。
  本轮只用替身测试此入口，没有实际执行 sudo 或切换账户。该检查不操作服务。
- 完整注册仍缺管理互斥、Mac旧客户端发现、目标用户配置初始化/写入与LaunchAgent
  注册及回滚边界；账户预检通过不能代表这些门禁已满足。

账户模块新增39项测试；客户端相关测试共166项通过。安全停止、离线PKG构建及
注册卸载仍可在既有代码开发授权内继续，真实隔离环境只阻塞动态验收，不阻塞全部开发。

## 离线检查 PKG（不允许安装）

新增 `scripts/build-client-pkg.py`，只在原生 Darwin arm64 构建，读取平台清单，
复制预装树到 `/opt/ai-device-client` payload；输出目录必须新建，不覆盖已有成果。
这是检查用中间产物，不是交付安装器：identifier 为 `local.ai-device.client.inspection`，
产品 Distribution 无条件拒绝安装，内部组件 preinstall 也无条件 exit 1，直接安装
组件同样拒绝。尚未实现生产安装/升级协议，不可删除门禁后当正式包使用。

重新构建业务wheel及 `build/client-native/mac-tree2`，包含布局与账户预检模块。
检查包：`build/client-native/mac-pkg1/ai-device-client-0.3.4.1-arm64-inspection.pkg`。
SHA256：`11730d4bb64039d201cad6e4c7f430faa61959240b936fbfa0b91815064abc1e`。
包版本0.3.4.1是检查构建编号，应用版本仍为0.3.4，不代表已完成跨版本升级。

真实执行了 pkgbuild/productbuild，未签名、未执行 installer、未加载服务。
`pkgutil --expand-full` 展开到 `build/client-native/mac-pkg1-expanded`，比对2175个
普通文件的SHA256及符号链接目标，均与打包输入一致；解包内Python可导入新增模块，
版本0.3.4、build_id `sha256:218f10e7c87cd5749380c3964b92c8293dd4a5f62b50f72981cca10074b9104e`。

构建日志 `/tmp/device-ses14497-mac-pkg1.log` 有5条 `write: Permission denied`，
根因未确认；尽管payload比对通过，不视为无警告构建或发布准入证据。
新增PKG构建10项测试通过，客户端相关176项通过，ruff/diff通过。
最新Device全量983通过发生在PKG构建器新增前，不冒充最新全量验证。

### pkgbuild警告定位与BOM门禁

最小单文件载荷也复现4条权限警告；无载荷脚本包和仅analyze组件扫描没有警告。
COPYFILE_DISABLE=1、指定任务内TMPDIR、显式component-plist均未消除警告。
仅改为 `--ownership preserve` 时警告消失，但BOM所有者变为501/20，不能用于
系统程序包。保留recommended；最小原包3条、完整mac-pkg1的4787条BOM记录均为0/0。
据此只能定位到所有权选项相关路径，底层失败写入对象仍未查明，不声称已消除警告。
探测文件保留于 `build/client-native/pkg-warning-minimal/`，未安装任何探测包。

构建器现于组件打包后使用pkgutil --expand和lsbom验证每条BOM为root:wheel，
失败则不生成产品包；新增5项失败门禁测试，PKG专项15通过、客户端181通过。
真实执行新增门禁生成 `build/client-native/mac-pkg2/ai-device-client-0.3.4.2-arm64-inspection.pkg`，
SHA256 `3a9c751706bbce16bfe39b959b2e5b0902b8eea96c58725591fd41f95cc1d318`。
仍双重禁止安装、仍有5条警告，日志 `/tmp/device-ses14497-mac-pkg2.log`。
mac-pkg2复用mac-tree2应用载荷；本轮未重跑包载荷逐字节展开比较，不借mac-pkg1证据冒充。

## 停止证据研究：不能复用maintenance锁作为退出证明

`client.py` 的关闭链先调用maintenance server.close，再执行executor.aclose；
maintenance.close释放文件锁，因此锁空闲不能证明执行器和进程树已完全退出。
本机launchctl手册明确print等新命令输出不是稳定API，不把任意解析失败当服务不存在。

新增 `client_macos_processes.py` 是纯证据跟踪模块，未接入服务或安装器：以PID和
创建时间区分进程身份，对完整快照中的已知后代保留身份，即使主进程消失、后代
换组或被重新托管，也仍返回其存活证据。重复PID、无效身份、不完整快照拒绝。
15项新增测试通过，客户端196通过，ruff/diff通过。

该模块没有safe_to_replace能力。未曾被观察到便已脱离的后代无法靠它识别；
空结果不证明完全停止。仍须设计覆盖整个客户端生命周期的运行互斥、阻止替换期间
重新启动的屏障，并与任务完成/后代收口证据结合。不能以已有maintenance锁、
launchctl print主PID或一次进程快照替代这些条件。未对宿主进程发送任何信号。

## 运行/替换互斥基础（未接入客户端）

新增 `client_macos_lock.py`：只打开注册时预置的私有普通锁文件，非阻塞flock，
校验所有者、权限、单硬链接和取得后的inode，不创建、不unlink、不改所有权；
CLOEXEC避免普通exec子进程意外延长锁生命周期。操作体异常保留原义并释放锁。
10项测试含真实有限子进程冲突、os._exit(7)释放、exec不继承；没有强杀测试进程。
客户端206通过、最终Device全量1023通过，日志 `/tmp/device-ses14497-mac-lock-full.log`。

尚未接入client_cli：Python在获取该锁前已经加载解释器和模块，因此单独把锁包在
run外层不能防止替换窗口加载旧程序；必须结合可靠的服务卸载/启动入口屏障。
该锁不继承给命令后代，不能证明子进程树退出；进程证据模块也不单独提供放行依据。
这些基础模块都未使检查PKG变成可安装产品，不开放安装门禁。

## 原生锁前入口实验（非正式架构）

新增 `native/client-lock-exec-prototype.c`，仅验证在读取Python前取得flock后exec。
不fork、不转发信号、不操作服务；私有单链接锁校验后将锁交给fd3，exec目标须
接管并设FD_CLOEXEC再创建其他子进程。当前命令可指定任意实验目标，因此不得
直接作为生产启动器安装；正式入口还须固定目标、验证描述符接管和安全目录。

本机xcrun clang以arm64、macOS最低标记11.0、Wall/Wextra/Werror编译成功。
3项真实进程测试通过：忙时未执行目标、exec前后PID一致且锁持续、异常退出及
exec失败后释放。没有向宿主服务发信号，子进程均通过关闭stdin或自行退出结束。
产物 `build/client-native/lock-exec-probe1/launcher`，未进入运行树或PKG。

原生入口自身也须在替换窗口保持稳定，不能忽略它也会被加载这一事实；该方案
只验证了Python前置锁的可行性，不证明完整升级架构成立。正式采用新增原生构建
链前需明确入口归属、更新策略及复杂度，不把实验默认扩大为新的常驻监督器。

## 实测范围冲突：成功任务仍可留下脱离后代

通过当前LocalExecutor执行一个Python父命令：启动stdin/out/err均DEVNULL、
start_new_session=True的子进程后立即正常退出。子进程仅等待2秒并写测试完成标记。
实测operation_state=succeeded、active_tasks=0时完成标记尚不存在；随后子进程自然
完成。没有发信号或遗留长期进程。脚本 `/tmp/device-ses14497-detached-evidence.py`，
证据目录 `/tmp/device-ses14497-descendant-sie26ju9`，工具日志
`/Users/kongkongye/.ai-assistant/logs/bash-output/20261104-162810-e4883a.log`。

因此现有空闲租约不涵盖任意已完成命令留下的脱离后代。一次快照、PID、运行锁或
原生入口均不能补回此前已失去的归属。若Mac完整停止必须覆盖这种进程，需扩展
共享Bash执行/进程登记与完成语义，影响范围超出包生命周期，不能默认决定。

待用户裁决：A) 将已成功完成命令显式脱离的进程排除在客户端服务停止保证之外，
不强杀它们，仍覆盖客户端与受管活动任务；B) 保持任意后代完整收口要求，另行授权
设计共享执行边界（不能承诺仅靠Mac launchd简单实现）。在裁决前不开放安装，
不改共享Bash语义，也不继续新增不能解决该边界的监督原型。此项是产品/架构范围
门禁，不是单纯缺Mac测试环境。Linux此前systemd cgroup测试仍按原证据有效，
不据本次Mac研究声称覆盖任意跨管理器委派的工作。

## 方案A已批准：受管入口接入

用户已明确同意方案A，权威需求新增§7.3；前文“待用户裁决A/B”是历史研究记录，
该范围门禁现已关闭。不追杀已成功命令显式脱离的后代，不改变共享Bash任务语义；
受管活动/未知任务仍不得作为例外。Linux cgroup要求不变。

新增client_macos_entry.py，LaunchAgent定义改为该模块。核实当前普通UID及固定
配置路径后取得runtime.lock，再延迟加载client_cli并持锁覆盖main的完整退出清理。
锁忙提示并返回0，避免SuccessfulExit=false重启；缺失锁/身份错误失败且不暴露路径。
注册器仍须预置锁，入口不擅自创建；原生exec原型未采用。

新增7项入口测试，含锁覆盖调用体、异常释放、忙不启动、错误UID/配置、CLI忙正常
退出及缺锁拒绝。最终Device1033通过，日志/tmp/device-ses14497-mac-entry-full.log；
ruff/diff通过。尚未构建包含此入口的新运行树/PKG，既有mac-tree2/pkg2不得作为
当前源码制品。Python锁前加载与服务重启竞争仍须解决，安装门禁继续关闭。

## 注册锁预置实现

client_macos_lock.provision_runtime_lock已实现：显式普通UID/GID、私有父目录、
O_EXCL首次创建、只对新文件设置0600/owner；已有合法文件保留inode和内容，
不取得/释放已有参与者的flock、不unlink。文件已被运行者持锁时重复预置不破坏互斥。
异常创建保留文件供显式修复，不通过删除可能已被打开的inode“回滚”。

新增12项测试，包括重复预置、活动锁保持、已有错误权限不修复、fsync失败保留、
预置后进入run_managed、非法身份及symlink/FIFO/硬链接拒绝。锁专项22通过，
ruff/diff通过；客户端220通过发生在最后8项边界测试追加前，不冒充最终全量。
该函数尚未接完整注册命令，必须在注册管理锁及账户/旧客户端校验后调用。
全部文件系统试验只在pytest临时目录，不操作真实用户注册目录。

## LaunchAgent完整文件发布基础

新增client_macos_plist.publish_agent：在预先校验的LaunchAgents目录内写私有.tmp，
写完flush/fsync后通过硬链接create-only发布最终plist，不覆盖已有文件或悬空链接。
临时文件仅清理本次创建的名字；发布后清理失败明确报告已发布，不自动撤销定义。
未加载服务，不自动创建用户目录；调用前仍须管理锁、身份/旧客户端检查完成。

7项测试覆盖完整plist/权限、已有文件/链接、fsync失败无发布、并发目标胜出、
不安全目录及发布后清理失败保留定义。专项7通过，ruff/diff通过；客户端234通过
发生在最后清理失败测试追加前。所有路径均pytest临时目录，未真实注册。

## Mac已知文件冲突检测

新增client_macos_conflicts.check_known_files，显式接收homes和服务目录，检查默认
.device-runtime/instances/*/config/roles.json以及用户Library/LaunchAgents和传入
系统目录；识别com.device-runtime.*旧标签、旧launchd/run-client.sh包装器、直接
client入口与run client命令。每目录最多4096项，单文件最多256KiB且非阻塞普通
文件读取；无法解析、读取失败、悬空链接均拒绝，不删除或迁移。中心-only实例通过。

14项测试通过，最终Device1066通过（/tmp/device-ses14497-mac-conflicts-full.log），
ruff/diff通过。尚缺真实账户目录采集、/Library/LaunchAgents与LaunchDaemons接线、
运行中进程检查、管理互斥和注册编排；此模块不是完整门禁，不开放注册CLI。
新注册遇到同名定义也拒绝；升级必须单独验证自身定义，不能直接忽略整个目录。

## 宿主冲突预检接线

check_registration_conflicts已接pwd账户home去重、/Library/LaunchAgents及LaunchDaemons，
要求confirm_legacy_clients_disabled严格为true。psutil来自client包含的Bash依赖，
检查运行命令形状；权限不足或无效命令数据失败闭锁，进程已消失可跳过。首次注册
不豁免任何运行客户端，不能拿此检查直接用于已启动服务的升级。

新增15测试，冲突专项29通过、客户端264通过，ruff/diff通过；均替身或临时目录，
未对真实宿主运行扫描或注册。仍需管理互斥、账户目录安全创建和完整注册编排。

## 管理互斥与注册编排已接通

新增client_macos_management：系统默认/Library/Application Support/ai-device-client-management
的root私有非阻塞管理锁，与用户runtime.lock分离，不unlink。目录准备只创建声明的
LaunchAgents、Logs、日志子目录和state；已有不兼容权限失败，不递归chown。
管理锁只协调本项目调用方，不冒充Installer全事务锁，PKG pre/post分离持有期仍待解决。

新增client_macos_register.register：Darwin/admin/确认检查→管理锁→显式账户→宿主
冲突检查→文件预检→实际用户配置探针→目录→运行锁→再次冲突检查→非覆盖发布plist。
不调用launchctl、不主动启动；plist发布可能在随后登录被系统观察，不能当作完全无副作用。
尚未开放CLI或写入可安装包。新建目录/锁失败保留可检查现场，不删除配置或业务数据。

管理专项6、注册专项9通过（含临时目录真实文件组合和重复发布拒绝）；最终Device1096通过，
日志/tmp/device-ses14497-mac-register-full.log，ruff/diff通过。账户和宿主/配置探针仍是替身，
未验证实际root到用户切换、真实登录或launchd。所有文件写入为pytest临时目录。

## 单机注册身份记录

新增client_macos_identity：在root管理目录以完整临时文件+非覆盖link创建registration.json，
记录schema/user/uid/gid/home，不含token。register在配置预检后、用户目录写入前预留身份；
已有记录不论相同/不同/损坏都拒绝新注册，避免先后注册两个用户。后续步骤失败保留记录，
不能自动改绑或删除。只是一条注册身份，不是持久升级事务状态机。

read_identity有界8192字节、普通私有文件及字段校验，用于后续修复/卸载定位；不猜用户。
身份11项测试及注册新增1项失败注入通过，客户端291通过，ruff/diff通过；Device1096
发生在身份模块新增前。完整显式修复入口未实现，不能把保留记录当作恢复闭环。

## 显式恢复未发布的注册

新增resume_registration，与register共用编排但不再次预留身份。必须先在管理锁内读取
原记录并核对schema、用户名、UID、GID及home完全一致，然后重新执行冲突/配置预检，
目录和运行锁准备及非覆盖发布。身份变化拒绝改绑；已发布plist仍按冲突拒绝，不覆写。

7项恢复测试通过，含真实临时目录“register预留后失败→保留原记录→resume成功”链，
客户端298通过，ruff/diff通过。管理员/账户/宿主探针仍替身；无真实服务操作。
恢复仅覆盖未发布阶段，未开放CLI，不承诺修复坏身份记录或已发布服务。

## 生命周期操作前的注册归属检查

新增client_macos_registration.registered_account/validate_agent：原身份与当前pwd账户
四元组必须一致；plist须为指定用户私有普通单链接文件，非阻塞有界读取，规范化后
与生成定义按类型精确比较（布尔true不接受整数1），并检查读取前后inode。
允许等价binary/XML plist，不允许改命令、KeepAlive、ExitTimeOut或附加字段。

新增17测试，客户端315通过，ruff/diff通过。只证明磁盘定义归属，不证明launchd已加载
相同定义，也不证明停止或无重启竞态；未发任何服务命令，不能作为替换许可。

## launchctl列表快照解析

本机launchctl手册list章节定义PID/Status/Label三列，CAVEATS区分legacy list与新命令
不稳定输出；-x已不支持。新增client_macos_launchd_status.parse_job_list，严格校验
表头、行、PID/退出码和重复标签，区分loaded+PID、loaded无PID及本次快照未出现。
13项测试通过、客户端328通过，ruff/diff通过。仅解析器，未调用真实launchctl。

asuser手册只承诺尽量接近目标bootstrap上下文，不切UID/GID，故尚未将其直接作为
已验证GUI域查询。调用方必须证明正确域和查询成功；快照无标签不等于禁止未来启动，
不能据此bootout运行服务或允许替换。生产运行态适配器仍未完成。

## 显式注册CLI与精确GUI域探测

client_macos_register模块增加register/resume CLI，必须--user和
--confirm-legacy-clients-disabled；异常仅固定脱敏文本并提示可能不完整，不自动重试。
8项CLI测试通过，客户端336通过、ruff/diff通过；真实命令仅执行--help，注册调用为替身。
该入口可写用户定义，但不加载服务，不由检查PKG调用；尚未获真实注册验收。

本机只读launchctl print gui/501成功，首行gui/501 = {，输出113658字节；只返回首行
和长度，未展示/保存服务清单或环境载荷。仅证明当前用户域此刻存在。手册明确
user/<uid>与gui/<uid>服务集合不同；未登录判定及服务状态适配仍不能靠失败码猜测。

## 身份读取边界复核

read_identity补充管理目录的私有所有权/非链接校验、用户名控制字符与选项前缀拒绝、
复用MacClientLayout拒绝root/根目录及..路径，并校验文件读取前后inode。新增8项
回归，最终Device1161通过（/tmp/device-ses14497-mac-identity-review.log），ruff/diff通过。
这是持久身份输入约束修正，不改变方案A，不证明任何服务停止/卸载已完成。

## 精确GUI域只读适配器

require_gui_domain(uid)使用/bin/launchctl print gui/UID，stdout/stderr直接DEVNULL，
不捕获整个域列表；10秒超时，非零退出/超时一律unknown并拒绝，不推断未登录。
新增10测试，状态模块23通过，ruff/diff通过；同一适配器在当前宿主只读返回gui/501。
此结果只证明查询瞬间域可访问，不证明具体服务状态，也不为替换或停止提供许可。
未登录安装仍需独立的缺席证据，不能将本函数异常作为可离线更新信号。

## 精确服务观察（尚非生命周期控制）

新增client_macos_service_status.py，精确查询gui/UID/local.ai-device.client，
只返回target/state/pid；错误、超时、非UTF8、错误路径/目标、矛盾PID或未知状态失败闭锁。
解析只采集顶层字段，不将嵌套environment中的pid/state作为服务状态。
observe_registered_service在查询前后复核持久身份及磁盘plist；非原子，不证明launchd
加载内容与磁盘完全一致，不提供safe_to_replace或缺席结论。

print是非稳定诊断格式：当前解析器基于受限格式的合成fixture，未对真实目标服务验证，
不得作为发布级状态API。捕获stdout后有256KiB解析上限，不是子进程捕获内存上限；
不输出/持久化原始内容。查询非零不代表未登录或未加载。未接安装/停止/卸载。

新增30项测试；联合注册归属/GUI域测试70通过，客户端测试384通过（日志
/tmp/device-ses14497-mac-service-observation.log）；新增文件ruff/format、diff检查通过。
未操作真实服务，未重建包。安全正常停止、停止后重新启动屏障、Installer跨pre/post
互斥仍未闭环；不采用bootout超时强杀，也不将此快照当替换许可。

## 服务观察的有界捕获修正

默认查询已改用client_macos_query.run_query：selectors非阻塞读取，最多保留256KiB+1
字节，10秒总时限覆盖管道与进程退出；stderr丢弃，不落盘stdout。失败仅回收本次
Popen创建的诊断子进程，TERM后1秒、必要时KILL后1秒，不操作服务PID或进程组。
这不是客户端停止实现。前文“先捕获再限量”限制已修正；print格式不稳定的限制仍在。
新增10测试，真实有限测试进程覆盖超限、无输出、EOF后仍运行及非零退出；KILL分支
仅替身测试。Device全量1211通过/52.78s，日志
/tmp/device-ses14497-mac-bounded-query-all.log；ruff/format/diff检查通过。
没有真实launchctl服务操作、包重建或安装；生命周期控制及启动屏障仍未闭环。

## Installer外层持锁方案的失败窗口

新增test_client_macos_management_lifetime.py两项真实有限进程对照：父进程在管理锁
内spawn后os._exit(7)，子进程仍在等待本测试结束标记时锁已可重新获取；显式pass_fds
传递同一open-file description并仅close父fd则子进程退出前保持互斥。所有进程有限
自然退出，无服务操作或真实Installer调用。联合管理锁/原生原型11测试通过，ruff、
format、diff通过；Device1211为新增两项之前的全量结果。

结论仅限测试进程：外层Python with management_lock不足以证明整个PKG写入期互斥，
也没有证明installer/installd接收并保留fd。不得据此开放直接写运行目录的PKG，
不得把pre/post各自持锁当作跨安装事务持锁。未增加生产安装包装器，未解除双重门禁。
下一设计须明确实际文件写入者与锁持有者的共同寿命，以及锁前入口启动保护；
已有原生入口实验仍未正式采用。真实Installer异常退出语义仍需隔离环境验证。

## 原生到Python运行锁接管接口

client_macos_lock.adopt_runtime_lock接管继承fd（默认3），立即设置CLOEXEC，核对私有
父目录/普通单链接锁/owner/inode，独立open确认存在冲突，再核对继承描述符可持独占锁，
失败或退出均close，不unlink或改变锁权限。校验仅证明当前持锁，不追溯解释器加载前
时序；原生入口承担该时序。13新增单测及1项真实原生exec到Python测试通过；锁/原型
联合39通过，客户端410通过，日志/tmp/device-ses14497-mac-lock-adoption.log。
ruff/format/diff通过。未接生产入口/plist/运行树/PKG，原生入口自身更新仍未解决；
没有据此开放程序替换或宣称完整生命周期完成。有限测试进程全部结束。

## 入口原子替换的受限证据

新增真实临时目录测试：旧原型已经exec到Python并持锁后，以os.replace替换入口文件
为同内容不同inode，新入口运行返回busy、不执行目标；旧Python自然退出后新入口可执行。
原型专项5通过，ruff/diff通过。只证明exec完成后的运行锁连续性，不覆盖原生入口仍在
取锁前执行时的本体替换，不证明不同版本入口兼容，也不证明Installer采用rename而非
原地写。因此入口自身更新安全仍未关闭，不接生产plist，不放开PKG。没有服务操作。

### 取锁前原生执行窗口补验

原型增加仅-DTEST_BEFORE_LOCK编译时存在的5秒有界握手。临时目录内让旧原生进程在
open/flock之前暂停，os.replace路径为另一个完整编译产物后恢复；锁忙时返回75且
不执行Python，空闲时目标执行。连同exec后替换测试，本轮新增3项，原型专项7通过。
普通编译不含握手，clang Werror、ruff/diff通过。此证据补上前述取锁前窗口，仅覆盖
当前同协议原型与本机原子rename，不覆盖不同版本兼容、Installer原地写或断电恢复。
未接生产plist/PKG；实际写入与锁共同寿命、正常停止等仍未实现。

## 受管入口显式继承锁模式

run_managed增加inherited_descriptor；CLI --inherit-runtime-lock固定接管fd3，非法
交接不回退重新open。锁覆盖invoke及退出清理，配置拒绝也关闭接管fd。账户解析前设置
CLOEXEC，解析失败关闭尚未消费的fd。普通入口不变，plist仍不使用继承模式。
本轮新增7测试（6入口+1真实原生exec到run_managed，业务invoke替身），客户端420通过，
日志/tmp/device-ses14497-mac-entry-handoff.log；ruff/format/diff通过。
不读取真实凭证、不启动client，不接生产原生启动器或PKG；本模式不证明继承fd来源
或Python加载前时序，仍由固定原生启动器保证。实际安装持锁及正常停止未完成。

## 用户批准同实例租约退出并实现协议

用户同意仅Mac维护由租约实例自行正常退出、随后launchd协调，需求§7.1/7.2已更新。
maintenance_acquire仍按旧逻辑忙拒绝；同一socket获租约后可发单字节X请求退出，其他
连接无法指定owner退出。DeviceClient在生命周期锁内复核owner/空闲/启用状态，设置
不可逆closed及exit_committed，再调度原run任务正常取消、走完整close清理。
提交前EOF释放准入，提交后断连不恢复；提交后的release无需等待executor清理锁。
Mac受管入口显式启用回调，Linux和普通CLI默认不启用，普通SIGTERM代码行为不变。

idle_lease现在yield一个IdleLease对象（既有不接收yield的调用不变），request_exit只
使用原socket且只尝试一次。确认包表示退出承诺，不表示进程已退出；响应丢失unknown，
不重连不重放。清理可能先关闭socket导致确认丢失，这是明确未知而非成功推断。
新增15测试（14退出协议+1Mac入口启用）；相关37通过，Device全量1252通过/52.90s，
日志/tmp/device-ses14497-mac-lease-exit-all.log；ruff/format/diff通过。
没有真实客户端/LaunchAgent/installer操作，无包重建；launchd卸载、完整进程退出证据、
运行锁与跨实例启动屏障、Installer互斥仍待接通，不以此宣称可安装。

## 原实例只读退出观察

client_macos_processes新增inspect_identity和wait_original_exit：PID+创建时间双次读取，
NoSuchProcess才视为消失，权限/未知失败；PID复用只证明原身份退出，不证明新实例停止。
有正数有限超时及轮询间隔，不发送信号，僵尸仍视为存在。轮询有deadline，但OS查询自身
不是可强制中断的时间上限，文档明确这一限制。
新增15测试，进程专项30通过，客户端450通过，日志/tmp/device-ses14497-mac-original-exit.log。
有限测试进程自然退出由测试父进程回收，观察器不负责终止；ruff/diff通过。
尚未与租约socket绑定，不作为停止/替换许可。SDK sys/un.h确认SOL_LOCAL=0、
LOCAL_PEERPID=2；仅peer PID不消除对端退出后PID复用问题，必须先设计采集/租约响应
时序再集成。没有真实服务操作，启动屏障和Installer保护仍未完成。

## 租约socket对端身份绑定

新增client_macos_lease：Darwin LOCAL_PEERPID采样创建时间→同socket随机32字节挑战→
复核身份一致。挑战在采样后生成，不能用缓冲旧响应证明对端仍活；假设可信服务不转交
socket fd。失败封锁该lease退出请求，不重连。MaintenanceServer仅允许启用退出的
Mac路径回应一次挑战，之后仍由原连接X请求退出；Linux旧无挑战路径不变。
request_original_exit串联绑定、单次退出、原身份消失观察，不提供替换许可；确认丢失
直接unknown，即使原实例可能已退出也不重放。新增11测试，含真实临时Unix socket
内核peer PID测试（服务与调用线程同进程），串联顺序故障测试为替身；客户端461通过，
日志/tmp/device-ses14497-mac-peer-binding.log，ruff/diff通过。未真实跨进程客户端退出
集成、launchd卸载或PKG安装；启动屏障和写入期保护仍未完成。

## 跨进程退出链路验证

新增test_client_macos_lease_process.py共5项Darwin真实有限子进程测试：空闲绑定/退出、
后台忙拒绝且准入保持、放弃租约后可重新获取、提交后ACK丢失unknown不重放，以及真实
DeviceClient.run/close+MaintenanceServer+运行锁贯穿清理的链路。网络、executor与journal
为替身；真实Unix socket/LOCAL_PEERPID、生命周期状态、取消与close协调、flock及进程
自然消失均跨进程验证。原身份退出后才验证清理结果和锁可获取。测试父进程线程回收
自然退出子进程，无信号/launchd/真实配置/中心访问。5专项通过，Device1283通过/53.38s，
日志/tmp/device-ses14497-mac-cross-process-exit-all.log；ruff/format/diff通过。
不证明后继实例不能启动，不作为替换许可；没有PKG构建或安装，启动屏障/写入期保护仍未完。

## 独立启动门禁与原生交接原型

新增client_macos_start_gate.py：预置start.lock，共享startup_handoff只覆盖启动交接，
管理prevent_start非阻塞独占；与runtime.lock分离，使旧实例仍持运行锁时管理方可以
阻止后继。复用私有普通单链接/inode/CLOEXEC校验，缺文件拒绝不创建，不unlink。
新增7门禁测试+1跨进程run退出组合+1原生门禁测试。原生实验仅-DWITH_START_GATE启用：
共享启动锁→独占运行锁→释放启动锁→exec。真实测试验证门禁关闭不执行目标，交接后
管理可独占启动锁，旧实例退出前后后继均拒绝，管理释放后可启动。普通编译、plist、
注册和PKG未切换；尚未覆盖生产启动路径，不声称已交付跨实例保护。
锁专项42、组合13、原生专项9通过；最终客户端475通过/5.99s，日志
/tmp/device-ses14497-mac-native-start-gate.log，ruff/format/diff通过。仅临时进程自然退出，
无真实服务动作。Installer实际写入者寿命仍未与门禁绑定，管理方死亡后可能释放，因此
不得据此开放PKG或把进程锁当持久事务锁。

## 注册/恢复预置启动锁

register/resume在runtime.lock预置后、冲突复查及publish前调用provision_start_gate。
新增6回归：首次预置故障阶段、恢复失败保身份及runtime inode、已有gate inode保持，
以及symlink/public/FIFO拒绝且不修复不发布。客户端481通过/7.16s，日志
/tmp/device-ses14497-mac-register-start-gate.log；ruff/format/diff通过。
账户/admin/配置探针仍替身，文件预置/恢复在真实临时目录；没有真实注册、服务或安装动作。
普通plist未改用原生启动器，预置不代表生产启动屏障已启用；Installer写入期保护仍未完成。

## 固定原生启动器与构建接入（未切换服务定义）

新增 native/client-macos-launcher.c：无运行参数，不读取HOME；按真实UID/getpwuid定位用户目录，拒绝root及身份不匹配；固定start.lock共享→runtime.lock独占→释放启动门禁→fd3 exec固定包内Python及继承锁入口。忙时正常退出避免KeepAlive重启风暴，异常锁拒绝且不修复。此协议仍不能授权Installer覆盖入口本身。

build-client-tree.py在Darwin arm64分支编译bin/ai-device-client-launcher，固定clang严格警告/arm64/minos11.0，并lipo验证架构；未改Linux分支。测试25通过/2.72s，日志~/.ai-assistant/logs/bash-output/20261104-192402-5c27d6.log。原版启动器实际编译但不运行；执行用单独链接getpwuid/exec替身，真实临时锁，验证固定argv、HOME无效、fd3冲突、启动门禁释放、两锁忙拒绝及缺失/public/symlink/FIFO/hardlink拒绝。没有服务操作。

尚未切换plist、增加注册时启动器存在预检、重建完整运行树/PKG；25项专项不等于当前客户端全量或生产验收。下一步先完成注册预检与正式定义/构建闭环，再继续Installer实际写入者与门禁共同寿命。因安全重启通知收敛，无后台任务、无提交。

## 固定入口正式定义、注册预检及当前树/检查包

正式plist仅执行/opt/ai-device-client/bin/ai-device-client-launcher，无可变参数；注册/resume在写身份/用户文件前验证launcher及root父目录/可执行/非可写/单链接。增加8预检用例、注册故障注入及缺launcher包拒绝。临时目录注册测试新增预检替身，原来失败1项是fixture遗漏，不是已执行真实注册。客户端508通过/9.83s（/tmp/device-ses14497-mac-fixed-launcher-clients.log）；Device全量1325通过/54.17s（/tmp/device-ses14497-mac-fixed-all.log）。ruff/format/diff通过；未Linux实机再验，不声称最终双平台验收。

当前三业务wheel用uv build --all-packages --wheel --offline产于mac-fixed-wheels3；新mac-fixed-inputs3复用原已校验输入（硬链接python/uv、symlink依赖和新wheel），所有旧产物保留。build/client-native/mac-fixed-tree3离线重建成功，manifest验证全部业务wheel内容；四个关键入口/注册模块额外逐字节对源成功，Python -I -B --version输出0.3.4、build_id sha256:89232c48127fd4f03d22bb0c4757ff453a051ab353b2ec4feafcdd2ddd2a19cb。launcher真实Mach-O arm64，SHA256 bd46bcb754c77db25a96d87d0cbf54428c5338cb195cdc9a2afa0ab488f4fca9。未执行生产launcher或客户端连接。

当前检查包build/client-native/mac-fixed-pkg3/ai-device-client-0.3.4.3-arm64-inspection.pkg，SHA256 92470c1c8221061967c77a40f982496fbfb55411aea13100dd876973c38b50c3；recommended BOM门禁通过，expand-full后2199非缓存文件/链接内容目标及普通权限一致，两道拒装门禁核实。仍有5条write: Permission denied，根因未解决，不能公开发布或安装。首次payload比对因忽略了builder排除pycache规则失败，按明确排除规则重验通过；首次manifest投影误用application_files字段，改按实际application_wheels契约核实，无源码修补掩盖错误。证据20261104-193353-1afa53.log及/tmp/device-ses14497-mac-fixed-{wheels3,tree3,pkg3}.log。

下一核心项Installer写入共同寿命需安装架构决策，候选及限制见生命周期设计末节。没有真实服务/PKG安装/账户创建/提交/push。全部Mac变更仍未提交。

## 首次配置中断后的显式续接（2026-11-05）

现有 `device_runtime.client_setup` 支持 `--resume`，与首次配置使用相同的URL、device ID、绝对配置/state目录及凭证输入。仅在确认目录归属且输入与已存文件匹配后使用；凭证继续通过交互或 `--token-stdin`，不要写入命令行。该选项只补缺失文件，不覆盖token/client.json，不注册或启动服务。空私有目录、仅完整token、已完整匹配配置可续接；截断、权限异常、额外文件、链接或输入不匹配会拒绝，保留现场供显式检查。普通初始化失败也保留已创建文件，避免并发resume成功后被清理破坏。不得将此功能当作任意坏配置自动修复；当前历史包不含这次源码更新。
