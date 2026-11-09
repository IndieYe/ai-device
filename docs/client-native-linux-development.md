# Linux 客户端完整包：开发操作与当前验收边界

> 实施中，未完成阶段交付。仅 Ubuntu 24.04 amd64；本页不是生产部署授权。
> 权威需求：`../doc/requirements/client-native-install-upgrade.md`。
> **面向使用者的安装/升级/卸载操作见 [`client-native-linux-guide.md`](client-native-linux-guide.md)**；本页是开发实施记录与验收证据。

## 当前交付检查点（2026-11-06）

- 源码状态：HEAD `e2231ba`，与远端 `feat/client-native-install-ses14497` 一致，工作区干净；实现已全部推送。
- 功能验收：第 11 节矩阵的 Linux 部分已完成实物验证，含干净目标安装、首次配置、注册、A→B 升级、忙拒绝、异常恢复、损坏修复、unknown 不重放、并发安装、普通用户、旧布局冲突、卸载、中心/MCP 回归。
- 交付文档：已补 `client-native-linux-guide.md`（面向使用者的完整操作说明），全部命令在隔离容器实测。
- 本页下方 `dev1`–`dev24` 逐版本记录为**历史证据**，制品 SHA 与 PID 均属当时快照，不代表当前状态。

本节是当前摘要；下方逐版本记录为历史证据，不把旧的“待确认”“即将重启”视为当前状态。

- 最新制品：`build/client-native/ai-device-client_0.3.4~dev24_amd64.deb`，SHA256 `a86147f81a983b84ab67326427c0d3ea0a5d4681ab492cc189873da582399694`；本地未签名开发包。
- 当前源码：用户已批准0.3.4状态格式1兼容登记，登记后Device926、根级111、生命周期专项165通过，ruff及diff检查通过；根级仅1条既有重复ZIP警告。日志 `/tmp/device-ses14497-compat034-{root,lifecycle,all}.log`。Bash184/FS116为dev23分包回归。dev24已重新构建并实际升级验证登记值为1、本机ready、配置/token摘要保持；PID4265。运行格式及升级判定未改，不把本轮升级核验等同全矩阵重跑。准确制品、历史矩阵和未重跑边界见文末。
- 独立审核：忙提示与旧实例冲突两项问题均由原审核者限定复核关闭；旧实例发现契约已经用户批准，不再等待重复确认。
- 剩余交付工作：本地提交/集成需按授权及工作区边界执行，当前全部实现仍未提交；最终源码制品一致性需在交付时核对。0.3.4人工兼容登记已获批准并通过发布测试，不再作为等待项。不以本摘要代替双平台完成。
- 保留边界：任意离线自定义旧实例需首次注册管理员确认已停用；不能绕过自动发现的冲突。旧共享实例/bundle/runtime保留供Mac及其他角色，不是Linux新客户端安装兜底。
- 非本期发布：未签名/公证、未接入apt或Homebrew、未部署真实设备。Mac完整PKG尚未开始。

## 布局

| 用途 | 路径 |
| --- | --- |
| 包内程序与私有 Python | `/opt/ai-device-client/runtime` |
| 稳定客户端 CLI | `/usr/bin/ai-device-client` |
| 随包依赖/许可证索引 | `/opt/ai-device-client/third-party-inventory.json` |
| 配置和凭证 | `/etc/ai-device-client/client.json`、`token` |
| 操作日志、任务数据库、输出 | `/var/lib/ai-device-client` |
| 文件日志 | `/var/log/ai-device-client/client.log` |
| 用户指定后的 unit | `/etc/systemd/system/ai-device-client.service` |

程序由 DEB 管理。其余配置、凭证和状态默认在升级、卸载时保留。默认普通用户运行，不自动授予 sudo。

## 当前可用的安装和配置入口

包准备好后安装：

```sh
sudo apt install ./ai-device-client_<version>_amd64.deb
```

首次未配置时仅报告待配置，不自动猜用户或中心。先明确已有本机用户 `<service-user>`；示例中的地址、设备 ID 必须替换，凭证不得放 argv。下面是分步管理员配置示例，不代表目前已实现单命令交互安装引导：

```sh
# 仅首次：config-dir 必须不存在，现有配置绝不覆盖。
sudo /opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_setup \
  --config-dir /etc/ai-device-client --state-dir /var/lib/ai-device-client \
  --server-url wss://<center-host> --device-id <device-id>
# 交互隐藏输入凭证；自动化可用 --token-stdin，不把真实凭证写入shell命令。
# 以下只修改明确新建的三个对象，不递归修改用户业务数据。
sudo chown <service-user>:<service-group> /etc/ai-device-client \
  /etc/ai-device-client/client.json /etc/ai-device-client/token
sudo /opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_register --user <service-user> --confirm-legacy-clients-disabled
sudo /opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_package activate
```

首次注册必须先确认旧客户端（包括自定义目录中的旧实例）已停止并禁用自动启动，再传 `--confirm-legacy-clients-disabled`。自动发现的默认目录、服务或运行进程冲突仍拒绝，不能用该参数覆盖；检测不可读也拒绝。不扫描任意离线自定义目录，不自动删除旧数据。

注册默认拒绝 root；确有需要须显式 `--user root --allow-root`。注册拒绝已有同名 unit，不覆盖未知服务。注册完成只 enable 不 start，activate 才启动并验证本机软件身份和 ready。外部中心使用 `wss://`，只有 loopback 允许 `ws://`。

升级复用安装命令。正在执行任务时拒绝，不强杀。系统服务停止失败或状态未知时不替换文件。停止后旧 Python 损坏仍可直接重装完整包修复。安装器按错误退出码报告失败，人工核实后再重试，不自动重放设备命令。

```sh
systemctl status ai-device-client.service
sudo tail -n 50 /var/log/ai-device-client/client.log
/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --version
sudo apt remove ai-device-client
```

日志目录在注册时预创建；当前隔离环境中仅依赖 `LogsDirectory=` 会造成 `209/STDOUT`，故不能省略该步骤。unit 使用 `SendSIGKILL=no`，停止超时不升级为强杀。卸载保留配置/数据；清除数据不属于上述命令的副作用。

## 离线构建

在独立目标平台构建环境执行，不在承载实际客户端的主机上构建或启动测试实例。

### 宿主 glibc 基线

包的 libc 依赖声明由 `scripts/build-client-deb.py` 的 `MINIMUM_GLIBC` 决定，值为 **2.17**。这个数字不是构建机的版本，而是包内二进制的真实需求：解包现有制品解析全部 ELF 的 `.gnu.version_r`，最高需求为 `GLIBC_2.17`（由 `libpython3.13.so.1.0`、`python3.13`、`_dbm`、`liblzma` 引入），其余库不高于 2.12；依赖锁 `native/dependency-lock.json` 的 target 也声明 `glibc: 2.17`，13 个 wheel 全部为 `manylinux_2_17` 或更低。

曾长期写成 `2.39`（Ubuntu 24.04 构建机的版本），后果是 `dpkg` 在解包前拒绝所有低于 2.39 的主机，包括实际能跑 2.17 基线的机器。修改基线时需同时更新 `device_runtime.dependencies.MINIMUM_GLIBC`、`client_host_glibc.MINIMUM_GLIBC` 与构建脚本字面量；`test_declared_glibc_floor_matches_the_runtime_requirement` 会拦住只改一处的情况。

客户端启动时会再次核对宿主 glibc（`device_runtime/client_host_glibc.py`）：低于基线则给出含所需/检测版本的明确错误并退出；探测不到（如 musl）不阻断，交由动态链接器报错。之所以要这一层，是因为新客户端由 systemd 用绝对路径直接启动，从不经过包管理器，仅靠 `Depends` 无法覆盖手工启动与服务重启路径。

1. 当前源码用 `uv build --all-packages --wheel --out-dir <inputs>/wheels` 构建三个业务 wheel。
2. 准备 `<inputs>/python.tar.gz`、`uv.whl`、`dependencies/*.whl`。来自仓库 `native/runtime-lock.json`、`tool-lock.json`、`dependency-lock.json` 的准确输入；脚本会校验大小与 SHA256。不要把旧业务 wheel 当成当前源码产物。
3. 在 Linux amd64 构建容器执行：

```sh
python3 packages/device-runtime/scripts/build-client-tree.py \
  --inputs <absolute-inputs> --locks packages/device-runtime/native \
  --output <new-client-tree>
python3 packages/device-runtime/scripts/build-client-deb.py \
  --tree <new-client-tree> --output <new-package-output> --version <deb-version>
```

输出目录必须新建，避免覆盖旧制品。uv 只用于构建，不进入运行目录；目标安装不安装 wheel、不创建 venv、不下载依赖。构建禁用 uv wheel 缓存，避免开发阶段同一 Python 版本号对应的旧源码被缓存复用。正式发布必须变更准确发布版本，不靠禁用缓存容忍同版本重发。

开发包未签名，不代表已满足正式发布、许可审查和来源认证条件。

## 已知限制与未充分验证项

以下为证据偏弱项，构造真实故障场景成本高，已记为已知限制而非宣称通过：

- **业务忙时拒绝升级**：用 `idle_lease` 模拟维护占用，未构造真实前台/后台业务任务期间执行 `dpkg`。
- **停止失败/状态未知**：只验证 preflight 拒绝路径，未制造真实 `systemctl stop` 超时或残留 cgroup。
- **客户端重启后旧 unknown 操作**：只验证 journal 状态转换，未通过真实中心观测是否重放。
- **维护期安装器异常退出**：验证辅助进程 SIGKILL 后租约释放，非 `dpkg` 中途崩溃或断电全过程。
- **错平台/错架构/权限不足**：未测。
- **真实网络故障**：中心离线仅验证本机 ready 与连接失败分离，未做网络分区实验。

## 目前证据与剩余工作

已在本任务无网络 Ubuntu 24.04 amd64/Rosetta 容器验证：

- 预装目录迁移、普通用户启动、本机 ready、服务正常停止。
- DEB 首次安装待配置、配置注册、同版运行中重装及凭证配置不变。
- 停止后解释器损坏，完整包可修复。
- 卸载服务但保留配置和测试业务文件；重装注册后恢复。
- loopback fake center 下发真实有限命令；任务运行时 dpkg 拒绝，PID不变，任务完成；空闲后可重装。
- 旧布局冲突拒绝而服务保持运行。
- dev2→dev3（钩子变更）包版本升级。
- dev3→dev4（服务启动修复代码变化）经 apt 升级；运行源码指纹改变，配置、凭证和测试业务文件保留。
- 后台命令模式：前台请求返回后仍拒绝升级，PID 不变，后台命令完成；空闲后 apt 重装通过。
- 新隔离环境 apt 首次安装及未配置卸载通过。测试包存放在 `/root`，避免刚启动时 `/tmp` 清理影响。
- 构建新增输入摘要清单和业务 wheel 源码逐文件比对，防止安装到缓存旧源码。

### dev5–dev7 故障与准确制品证据

- dev5 激活失败：root 包钩子直接读取普通运行用户的 token，错误地触发所有者检查。最初 PID=0→0 的损坏配置探针无效，不能作为运行服务保持的证据。
- dev6 改由 unit 指定用户通过 `runuser` 验证配置，恢复安装后本机 ready；损坏 JSON 时以非零初始 PID 验证安装拒绝且 PID 不变，测试后恢复配置。
- dev7 包复制阶段排除 `__pycache__`、`.pyc`、`.pyo`；构建导入检查及管理 Python 入口使用 `-B`。准确 DEB 中缓存条目为 0；dev6→dev7 仍报告旧缓存目录非空，未递归删除遗留文件；dev7 同版重装无该警告，配置/token SHA256 保持。
- dev7 将 drop-in 检查前移到 shell 钩子，避免服务已停止时跳过检查。在运行状态下拒绝且非零 PID 不变；停止状态下拒绝且 manifest 不变。清除测试 drop-in 后恢复本机 ready。
- dev7 配置 JSON 缺失、token 权限为 0644 时均拒绝安装，非零 PID 保持；探针结束恢复原配置与权限。
- dev8 纳入固定 state 目录前置校验，与首次注册一致；升级就绪后将 state_dir 改为错误路径，重装在停止前拒绝、非零 PID 保持且不创建错误目录。dev8 SHA256：`54cc931bd1c1839cb71307ec0b6518facb2ffaf915d003e62b4fd518cceab6c6`。
- dev9 纳入有效 SendSIGKILL=no / KillMode=control-group 检查；直接修改测试 unit 为 SendSIGKILL=yes 后安装拒绝且 PID 保持，随后恢复 unit。dev9 SHA256：`51f4da5a99e7ce0986c87ce3161bce555b6e9991a3f1b7fe29979cd2bae52e4b`。
- dev9 真实前台/后台有限命令忙拒绝、PID保持和任务完成均通过；空闲后 apt 同版重装就绪，配置/token SHA256保持。
- dev9 干净容器 apt 首次安装待配置、未配置卸载通过；外部 `/usr/lib/systemd/system` 同名 unit 导致安装在解包前拒绝，测试 unit 未被删除，探针结束后清理本次测试文件。
- dev11 真实停止超时注入揭示缺陷：MainPID/ControlPID均为0时服务cgroup仍有后代，误确认停止并进入解包；随后启动失败。单纯sleep会被TERM终止，首次注入未构成故障；使用忽略TERM但12秒自然退出的ExecStop才复现。归档mtime不证明未替换，以dpkg解包日志为准。
- dev12 Python停止确认与包脚本都增加cgroup v2 `cgroup.events` 的populated检查，拒绝残留后代或不可读状态；真实超时重测安装非零退出、无Unpacking，等待测试进程自然结束并恢复unit后重新就绪。没有强杀。
- dev12 临时ExecStartPre失败导致postinst明确失败；恢复unit后通过 `dpkg --configure ai-device-client` 就绪，配置/token哈希保持。
- dev12 SHA256：`683230e6fa37ebe621c5b9d5fc99006b6ab53ccd52fbd046f7d422626cf22dba`。
- dev10 修正预装 console scripts 的构建目录 shebang，新增 `/usr/bin/ai-device-client`。在已安装容器的 `/` 下，稳定入口和 `runtime/bin/device-runtime client --help` 均成功；此前仅用 `python -m` 服务启动不能证明 console script 可迁移。
- dev11 新增随包依赖/许可证索引，真实安装后验证全部列出的许可证路径存在。实际包含 13 个 Python distributions（12 个安装依赖及 CPython 归档自带 pip）；没有在目标机用 pip/uv 安装依赖。索引不等于法律合规审查或完整原生库清单。
- dev11 停止后移走私有 python3，再安装同版包恢复就绪；卸载清除 unit 与稳定 CLI、保留配置/token SHA256及业务标记，重新安装注册后恢复就绪。
- dev13 管理并发：手工注册/停止/激活持有 `/var/lib/dpkg/lock-frontend` 和 `lock` 的 POSIX 记录锁，整个操作再持管理 flock；dpkg 钩子依据标准 DPKG_MAINTSCRIPT_PACKAGE/NAME 上下文仅取管理 flock，包事务锁仍由 dpkg 持有。真实双向竞争验证：手工持锁阻止dpkg安装、另一进程持dpkg锁阻止手工stop且PID保持；解锁后激活就绪。不删除锁文件，不把环境标识当权限边界，不保护任意管理员绕过入口直接操作systemd。
- dev13 SHA256：`b06ebd9374d764358f0de123331319e29e00d5e9c767f6af4ea448deee9991d5`；真实升级及手工激活均通过。
- dev11 SHA256：`8af068ae5ac4c64ca7d4d090aa90f9dfbd19d4b287735b30f2151bd681472d0b`；dev10 SHA256：`154b999e5cdf6e2f814bd5e222e1c4937552686d354d4f5788e5b586c664384e`。
- dev6 SHA256：`f77ecbac4309fb0f7d898dd1f9023fd247f09808e64f02d27b6e3659b2b5c2e4`。
- dev7：`build/client-native/ai-device-client_0.3.4~dev7_amd64.deb`，SHA256 `66c4a58f4ae87800a5a5b6b1481d3d6dddbee7190360c2f57099ba56ef25c429`。
- 最新分包回归（dev13）：Device 1581 passed / 20 skipped / 5 既有多线程 fork 警告；Bash 184 passed；FS 116 passed。根目录混合收集曾因 fastmcp 测试桩冲突失败，按 README 分包执行通过，不将混合收集报告作通过。

**仍需完成**：剩余未知服务、权限、停止失败和就绪失败等验收矩阵复核，构建与生命周期整体审核及旧客户端路径清理。macOS 尚未实现。旧 dev2–dev4 验收不自动代表 dev7 全部路径已重新验证；最新制品仍为未签名开发包。

隔离镜像中 journald/logind 等失败、systemd总体 degraded；客户端采用文件日志，具体单元测试单独验证，不宣称整个操作系统健康。真实机器验收/部署仍须单独授权。

## 旧路径引用与退役清单（分批处理中）

- 新主路径：`client_cli/config/setup/register/package/service`、`build-client-tree.py`、`build-client-deb.py`；稳定 `/usr/bin/ai-device-client` 直接进入 `client_cli`，不调用旧管理 CLI。
- 已退役第一批：`scripts/build-native-linux.sh`、`scripts/assemble-native-payload.py`，以及只检查这两者的 `test_native_payload_build.py`、`test_native_package_preflight.py`（56项旧测试）。删除前确认均为无其他改动的已跟踪文件，剩余代码引用仅历史注释并已改写。退役后Device回归1525通过/20跳过/5旧警告；数量减少来自明确删除旧测试，不是漏跑。
- 旧候选/helper 运行链及直接依赖它的 `accept-native-*.py/sh` 已在第二批退役，见下方 dev14 记录；不是当前待实现项。包 README 已指向新完整包，旧 Linux 指南/native README 对应章节标为历史。
- 剩余 `probe-native-*.py` 是独立的进程、凭据、目录和包事务实验，不作为新客户端安装入口或 DEB 验收证据；`probe-native-deb-gate.sh`、`probe-native-rpm-gate.sh` 是独立包管理事务实验，RPM 不在本期交付范围。不能仅按 native 文件名前缀删除。
- 共享旧实例层：`installation.py`、`lifecycle.py`、`bundle.py` 与 `upgrader.py`。`lifecycle._persistent`、健康检查及持久目录同时处理 center/MCP；旧 `upgrader` 经 installation/lifecycle 处理 roles。暂留直到调用图明确拆分，禁止整文件删除。
- 业务可靠性：`journal.py`、`tasks.py`、客户端任务恢复及 unknown 不重放继续保留。`maintenance` 的旧 drain/resume 操作仍有旧管理调用者；新包仅用 idle lease，不以保留旧操作为新旧升级兼容。
- 整体审核后移除旧 `upgrade_supervisor.Systemd` 执行适配器，非 Mac 的真实适配器请求明确拒绝并指向 DEB；Mac/launchd 与注入适配器的共享状态机测试保留。其 Linux 专属 `templates/upgrade-stage1/{client,supervisor}.service.in`、`scripts/render_upgrade_adoption.py` 及 `test_upgrade_adoption.py` 同批退役：引用仅为该生成器、专测和历史文档，删除前均无已有修改。旧监督器只接纳 client profile，不能称其保留是中心/MCP 所需；通用 `upgrader` 仍有 center 等角色调用，不整删。Mac 旧路径在第二阶段闭合，不得永久双实现。此批源码变化尚未进入 dev17。
- `stage-native-dependencies.py` 读取构建依赖锁，新完整包仍使用该锁；`prepare-native-manager-wheels.py` 是按锁选取 wheel 的构建工具，不调用已删除 helper，均不在目标机安装路径执行。历史构建来源不能因目录名 native 被误删。
- `docs/native-lifecycle-implementation.md` 与 `upgrade-stage1-*` 是旧路线历史资料，不作为完整包当前指南或本轮验收证明。

### 第二批旧链退役与 dev14 实物验证

- 按导入图退役12个旧native Linux运行模块（bootstrap/candidate/helper/install/layout/orchestrate/package_sender/prepare/prepare_sender/start_sender/update/update_job），保留 `native_runtime`，因共享 `installation` 仍使用。
- 同批退役17份旧链专属测试、54个直接引用旧模块的脚本，另清理7个调用已退役包命令的验收脚本。共享 `test_management_lock` 仅移除两段旧helper专属测试，其余保留。旧签名脚本绑定旧多树包格式，不能作新DEB发布工具，随候选链退役。
- 删除前冻结清单并核查修改归属；首次拆分测试断言失败时未删除，补齐第二段helper测试后才执行。历史证据仍留在Git与文档；没有操作真实旧安装或业务数据。
- 退役后Device全量917项通过；此前1525项中的旧链测试被明确删除，不宣称覆盖率提高。Bash184、FS116通过；共享安装/runtime/锁定向127项通过。
- dev14从dev13升级，本机ready，安装目录中 `native_*.py` 只余 `native_runtime.py`，旧helper无法导入。SHA256：`b34353290ed3d0369f0cd9add82d3481b762957e6f046c9e8e0bd989e3ad9969`。
- 当时遗留的 Linux bundle/native extra 契约已在下方 dev15 收敛，不再作为当前待办。独立实验、旧实例监督器 Linux 专属职责及 Mac 保留部分仍需按调用方分类；阶段整体审核与 Mac 交付未完成。

### dev15 依赖契约收敛

旧native运行链退役后，删除 `pyproject.toml` 的native extra，Linux bundle角色统一为client，不再保留仅为旧native extra设置的格式放宽；中心/MCP角色不变。uv.lock离线更新，zstandard不再是项目解析依赖。锁定的历史构建输入清单尚保留，未删除用户缓存；新完整包仍只安装client运行依赖。新wheel元数据不含native extra，dev15从dev14升级并本机ready，Device917项通过。SHA256：`c7bfe972c72d660d1b69b4861a4483a761721bb87c426e975bf3fb4aa586f56c`。

整体审核尚需复核历史文档/技能、独立实验与共享旧Mac实例职责，不能把本批删除当作双平台最终清理完成。源码及退役变更尚未提交。

### 根级交付门禁：基线已有版本登记缺口

运行仓库根 `uv run --no-sync pytest tests -q`：110通过、1失败、1个重复ZIP测试警告。失败为 `test_device_version_is_registered_for_state_compatibility`：源码版本0.3.4未登记于 `lifecycle.STATE_COMPATIBILITY`。已核对需求提交HEAD同样版本0.3.4、兼容表仅至0.2.7；本任务未修改journal/tasks/lifecycle。该测试明示需人工确认持久状态兼容，不为通过测试擅自增加登记。此门禁仍未关闭，不能宣称全部交付测试通过；日志 `/tmp/device-ses14497-root-delivery-tests.log`。需完成对应版本状态兼容审核并获适用确认后再登记、重跑根级测试。

### dev16 配置输入故障审核

配置JSON和token改为非阻塞打开后fstat确认普通文件，限制配置读取长度；防止误配置FIFO在类型检查前阻塞。两个真实子进程FIFO回归及超大输入测试通过。dev16升级ready；隔离容器token临时替换FIFO后，dpkg在10秒保护超时前拒绝、非零PID保持，原凭证恢复。Device全量920通过，日志 `/tmp/device-ses14497-runtime-tests-dev16.log`。SHA256：`1e3400fd433517392c530899fc4b2c38323f5e2a2239214e25f11fdc71fe5bb3`。根级版本登记人工门禁仍未关闭。

### 版本登记门禁补充核验（不代替人工确认）

- 全仓Python引用核对：STATE_COMPATIBILITY仅被根级版本一致性测试读取；实例实际兼容检查由manifest.state_format驱动。因此缺登记不阻断当前DEB正常升级，但发布门禁仍失败。
- 0.3.4发布提交 `1a785f7` 仅改版本号。最近兼容登记基线 `307b8cd` 至需求HEAD，journal/tasks/lifecycle未变化；installation有254行统计变动，仍需按安装/恢复语义审阅，不能仅由数据模块未变断言全兼容。
- 当前日志协议、任务与实例生命周期/备份恢复专项196通过；命令为 `uv run --no-sync pytest packages/device-runtime/tests/test_journal_protocol.py packages/device-runtime/tests/test_tasks.py packages/device-runtime/tests/test_instance_lifecycle.py -q`。未修改兼容表、未将测试替代人工审批，未验证真实设备迁移。

进一步按函数审阅 `307b8cd..HEAD` 的installation差异：主要为锁定私有解释器准备/迁移与重建、平台要求检查、入口参数及launchd管理。journal/tasks存储格式未改；现有共享私有runtime、重建与备份恢复回归通过。尚未进行跨全部历史版本数据样本的迁移实测，不将该静态审阅及当前测试等同于0.3.4人工兼容批准。

#### 该门禁已改为按格式审计（上述版本登记问题已关闭）

上面的失败源于 `STATE_COMPATIBILITY` 按**版本号**记录人工审核结果：每升一次 `__version__` 就要求重新登记，哪怕持久化数据一个字都没变。0.3.4 之后 0.3.5、0.3.6 都未登记，门禁长期挂起。

审计版本号本身是错位的：升版本号并不说明磁盘上的字节还能不能读。真正会破坏升级的是**数据格式**变化，而格式已由 release manifest 的 `state_format` 声明并在 `_compatible` 中校验。

现改为：

- 删除按版本的 `STATE_COMPATIBILITY`，改用 `lifecycle.AUDITED_STATE_FORMATS`——已人工审阅过数据契约的格式号清单。
- `STATE_FORMAT` 收敛到最底层模块 `bundle.py` 单点定义，installation/lifecycle 均引用；不再在各处写 `"state_format": 1` 字面量。
- 根级两个测试分别保证：当前 `STATE_FORMAT` 必须已登记；包内除定义处外不得出现该字段的字面量（防止升格式时只改一半）。

效果：改版本号不再触发失败（已模拟 0.3.7 验证）；真改数据格式仍必须显式登记新格式号，人工确认点没有丢，只是落到了正确的位置。

实测：根级 `uv run --no-sync pytest tests -q` **111 passed**（此前 110 passed/1 failed）；device-runtime 全量 **2156 passed/20 skipped**；ruff 通过。

需要说明的边界：0.3.4→0.3.6 之间 `journal.py`、`tasks.py`、`output_store.py` 均无改动，`STATE_FORMAT` 与 `OUTPUT_MAP_FORMAT` 未变，因此格式仍为 1 有据。但本次并未做跨全部历史版本的数据样本迁移实测，上述结论建立在格式常量与存储代码未变之上。

### dev17 服务归属一致性审核

安装/卸载shell钩子从解释器路径前缀匹配改为完整客户端ExecStart行的固定字符串整行匹配，与Python激活校验一致。dev17仅钩子变化，复用已验证dev16运行树，不重复构建runtime。真实隔离unit同解释器但改为无关模块后安装在停止前拒绝、非零PID保持，unit已恢复；生成postrm在隔离路径执行，确认拒绝且未调用disable、未删除unit。Device全量922通过，日志 `/tmp/device-ses14497-runtime-tests-dev17.log`。SHA256：`ac6d10817bb3c58bd2e56b74b0b6b61594016c2f3efab7978c615134be876737`。

### dev18：旧 Linux 监督器执行适配器退役

整体引用审核发现旧 `upgrade supervise` 的服务执行层属于 client，不是 center/MCP 共享职责。移除其 `Systemd` 类，非 Mac 的真实适配器请求明确拒绝并指向新 DEB；保留旧 Mac/launchd 和注入式共享状态机测试。同步删除四个无其他调用者的 Linux 模板资产：生成脚本、两个 `.service.in` 和专属测试。共享 `upgrader`、installation/lifecycle 仍被其他角色使用，不整删；旧通用客户端实例入口仍需最终按角色核对，不能据此宣布所有旧路径已清完。

- 新增3项适配器平台选择回归，定向98项通过；移除模板专测11项，Device全量从924变为916项通过，不是漏跑。日志 `/tmp/device-ses14497-supervisor-retirement-tests.log`。
- Bash184、FS116通过；根级仍110通过、1人工兼容登记失败、1既有重复ZIP警告。首次Bash/FS测试目录写错且零测试，纠正到 `packages/bash/tests`、`packages/fs/tests` 后得到上述结果。日志 `/tmp/device-ses14497-review18-{bash,fs,root}.log`。
- 以当前源码重新构建三业务wheel、离线运行树 `/tmp/client-tree-deb18` 和 DEB；未复用dev17运行树。dev18 SHA256：`856f80813f5a5a4fea3f14fa5d1d0736d36e2628bd2c32ea28f0a4fd8663b59d`。
- 专属验收容器确认 Ubuntu24.04.5，真实dev17→dev18升级成功，包钩子确认完全停止、升级后本机ready；配置/token摘要保持。准确已安装包验证旧Systemd类不存在、Linux旧适配器调用拒绝。服务active、PID3291、无drop-in。
- 本轮只复验上述升级与旧适配器退役，未把dev17及更早的全部生命周期证据冒充dev18全矩阵复跑。包仍未签名，未部署真实设备、未提交源码、未push；Linux阶段审核与人工登记门禁未完成，Mac未开始。

### dev19：旧 Linux client 实例入口收窄

共享 `installation` 仍可创建 Linux client venv，违背唯一完整包路线，故在 `install_instance` 写入前及 `_prepare` 使用的 `_check_target` 拒绝 Linux `client` 角色（含混合角色）。Mac client 与其他角色检查保持；Linux bundle 原有仅client声明未扩展为center/MCP支持，合成角色测试只证明拒绝条件不误伤其他角色，不代表新增平台交付。

- 新增5项角色边界回归；旧Linux安装成功用例改为拒绝且无目录/uv调用。全量初次26项失败均逐项复核；移除依赖旧Linux venv成功创建的8个函数/14个用例，保留静态bundle、平台/依赖校验和Mac跨平台恢复拒绝。Device最终907项通过，ruff与diff检查通过。日志 `/tmp/device-ses14497-linux-role-final-tests.log`；初次失败保留在 `linux-role-all-tests.log`。
- 重新构建三业务wheel与 `/tmp/client-tree-deb19`，准确包 `ai-device-client_0.3.4~dev19_amd64.deb`，SHA256：`ff18e42c1afd9f39aa2245c28478924d974a88b1d2d960e1f6109a5733527c4c`。
- 专属Ubuntu容器dev18→dev19实际升级、本机ready、配置/token摘要不变；已安装包在bundle不存在时即拒绝旧Linux client安装，未创建目标目录。服务active PID3368、无drop-in。
- dev19未重跑全部历史生命周期矩阵，根人工版本登记仍待确认。旧bundle构建资产与共享生命周期仍需最终按调用方复核；不宣称双平台最终清理完成。

### dev19 补充拒绝与并发验收

- 准确Ubuntu验收容器对截断为64字节的dev19派生包、同包名arm64测试控制包、普通用户安装分别拒绝（退出1/1/2），均未进入Unpacking，原PID3368及配置/token摘要不变。arm64试件仅验证dpkg架构拒绝，不是可运行arm64制品；截断试件仅覆盖早期格式损坏，不承诺任意载荷损坏均在停机前被发现。日志：`/Users/kongkongye/.ai-assistant/logs/bash-output/20261104-135059-1eb17f.log`。
- dev19真实前台与后台命令忙拒绝，非零PID保持、任务完成；随后同版重装本机ready、配置/token摘要保持，PID3575。日志：`/Users/kongkongye/.ai-assistant/logs/bash-output/20261104-135129-27699b.log`。
- 准确安装的dev19租约持有子进程在取得acquire并确认服务显示owner后以 `os._exit(7)` 异常退出，服务自动清除owner、ready=true、PID3575保持；没有杀服务或用resume强行解锁。日志：`/Users/kongkongye/.ai-assistant/logs/bash-output/20261104-135411-726e81.log`。
- 新增确定性交错回归：active_count等待期间新请求无法进入journal/执行；租约建立后新请求维护拒绝，释放后可执行。租约专项9项、Device全量908项通过。本轮仅新增测试与文档，运行源码未变，无需重建dev19。

### Linux 验收矩阵对照（非最终通过声明）

| 需求场景 | 当前证据与边界 |
| --- | --- |
| 离线完整包、普通用户、首次待配置、首次配置 | dev2起Ubuntu实际验收；构建锁定输入且安装不下载，dev19离线新树升级成功；干净容器dev9曾验待配置，本轮未重跑全新配置 |
| A→B、同版重装、中心离线本机ready | dev4业务指纹变化升级；dev18→19真实升级及dev19重装，loopback中心关闭仍ready，不等于公网接入 |
| 前台/后台忙拒绝及空闲重试 | dev19真实命令、PID保持、完成后重装ready；准入未永久关闭 |
| 空闲检查并发、安装器异常退出 | 同锁交错回归；dev19真实租约持有进程异常退出后owner清空、PID保持 |
| 错架构/权限/早期损坏 | dev19上述dpkg真实拒绝；其他平台不承诺运行，任意载荷损坏未穷举 |
| 停止失败、状态未知、启动失败 | dev12真实cgroup残留拒绝且未解包、ExecStartPre失败及configure恢复；状态未知/ready错误有模拟测试，dev19未重跑这些故障 |
| 程序损坏修复、卸载保留、旧布局/服务冲突 | dev11真实损坏修复卸载恢复；dev9旧布局、dev17完整ExecStart归属拒绝；dev19未全矩阵重跑 |
| 并发安装管理互斥 | dev13真实dpkg POSIX锁与管理锁双向竞争，相关回归保留 |
| unknown不重放、共享中心/MCP | 业务journal/tasks未改；专项196与共享测试曾通过，新增并发测试后Device908通过，Bash184/FS116；全量日志 `/tmp/device-ses14497-dev19-matrix-tests.log` |
| Mac专属生命周期 | 不适用Linux阶段，Mac阶段尚未开始 |

仍须完成整体阶段审核及所需独立复核、人工0.3.4兼容登记、本地提交/集成门禁。旧bundle构建与共享实例职责继续按实际调用方核对，不能由上述表宣称全部旧路线最终清理完成。

### 独立阶段审核与 dev20 忙提示整改

只读审核会话 `ses-14507` 核对冻结快照 `/tmp/device-ses14497-linux-review-3tafqwbg`（283文件、98删除，manifest SHA256 `bd5c4ef723d40371c4f219f03f3d34bc42a01af3e1eb8569a45699bd2f900962`），结论不通过：①旧普通实例的默认/任意自定义root未纳入冲突检测，可能并存；②ClientBusy被统一错误处理掩盖。主会话均核实，第一项仍阻塞，不能以两处固定目录检查或运行进程扫描宣称不存在任意离线旧实例。需明确旧安装发现范围及管理员确认处置的产品契约，未擅自增加忽略/强制开关。

第二项已整改：client_package单独捕获ClientBusy，输出固定活动任务/空闲重试说明，不输出原异常。真实前后台探针增加安装器输出断言。dev20重新构建当前三wheel/运行树，SHA256 `322c9a1b6402077cf2453810f5254dfffcad182941543e90d5290f817143e5fc`；Ubuntu专属容器dev19→20及同版重装ready、配置token保持，PID3857。前后台忙拒绝含明确提示且PID保持、任务完成。Device909通过（`/tmp/device-ses14497-dev20-tests.log`）。原审核者尚未做限定整改复审，不能把主会话验证称为独立通过。无在途审核/后台测试；阶段未通过，Mac未开始，源码未提交。

#### 忙提示限定复核结论

原审核者 `ses-14507` 对 `/tmp/device-ses14497-busy-review-_mcb32oi` 三文件整改做只读限定复核，确认原问题2可以关闭、未发现直接回归。新manifest SHA256 `aace46c13ac6421856ca8bd1b6dc93a40a9637fbb83fbe940262f09bea6cb9ed`；复核验证清单内部一致并对照原冻结，未独立重跑测试或重验包摘要。主会话dev20实测与909通过仍按各自记录引用。原问题1旧普通实例冲突契约待用户确认，阶段整体仍不通过。复核原输入 `msg-e4bf023f55835c949337b1909b89e796` 已完成，会话idle且队列空，无在途审核。

### dev21–22：旧实例冲突契约实施（待独立复审）

用户批准自动检查已知旧实例/服务/运行进程，首次注册明确确认自定义旧实例已停止并禁用自动启动；确认不绕过已检测冲突。需求第9节及配置示例已同步。新增client_conflicts：检查passwd账户默认实例roles、system/user服务定义、/proc客户端命令；只豁免精确新服务命令且PID匹配systemd MainPID。检测失败拒绝，有界非阻塞普通文件读取，不输出配置/进程参数。任意离线自定义目录由管理员确认，无迁移删除。注册前、激活前及运行服务停止前检查。

- `--confirm-legacy-clients-disabled` 首次注册必需，已有注册升级无需重确认，不能覆盖冲突。
- dev21 Device921通过；随后补MainPID身份及FIFO拒绝，专项26通过，最新全量未重跑。dev22 SHA256：`345e6a908ce8256d4edd4c001ff4770c2cfda6e5f9c0b5d247727e5e948a60b6`，重新构建。
- dev21真实默认旧实例、自定义服务及进程试件：确认后注册/激活仍拒绝、PID保持，试件已清除。进程为真实进程和旧模块命令形状fixture，不是真实联网旧客户端。
- dev22升级ready；默认旧实例使dpkg在Unpacking前拒绝、PID4040保持；自定义路径进程fixture被拒绝，清除后检查正常。
- clean专属Ubuntu首次dev22安装待配置；创建测试用户device及随机凭证，无确认注册拒绝且无unit，明确确认后enable不start，activate ready，User=device、PID450。主deb容器dev22 PID4040 active。clean现在已配置运行，不再是空白环境。
- 收尾因安全重启通知，不代表完成。无后台任务、源码未提交；恢复后跑最新全量/ruff/diff，再由原审核者限定复审问题1。人工0.3.4登记未关闭，Mac未开始。

### 重启后dev23限定整改闭环

重启后原execution/隐私/开关确认有效，dev22全量923通过、静态检查通过；两个专属服务PID4040/450保持。原审核者复核指出遗漏全局用户服务目录，已补 `/etc/systemd/user`、`/usr/lib/systemd/user`、`/run/systemd/user` 及3项路径契约测试。

dev23 SHA256 `75b5bdcfa7bd685da69eb32f1a1925f67562067c4531627d3df4b034e9da55d9`；重新构建并在deb容器升级ready，PID4168。三目录逐一植入自定义旧run client服务定义，注册（含确认）和激活均拒绝、PID保持，试件清除后检查正常。实测日志 `/Users/kongkongye/.ai-assistant/logs/bash-output/20261104-144302-d21773.log`。全量926通过 `/tmp/device-ses14497-dev23-final-tests.log`；ruff/diff通过。

原审核者ses-14507限定复核通过：原问题1按批准发现边界关闭，未发现直接回归。冻结 `/tmp/device-ses14497-conflict-review23-831a7x_f`，manifest SHA256 `8354f641b74f18cbdd7982c7e0d133fc5dcbc46c983c7065123ef08319f5195d`，8文件一致，仅两文件较前次变化；未独立重跑测试/验包。原问题2忙提示此前已关闭。不是Linux整体通过声明；任意离线自定义root仍需管理员确认，人工0.3.4登记、最终交付核对及提交集成仍未完成。无在途审核/后台任务，源码未提交，Mac未开始。clean仍dev22 PID450，不把其验收标成dev23。

### RPM 脚本语义实测（2026-11-09，rockylinux:9 / RPM 4.16.1.3）

阶段三需求文档要求先实测再实现，不能照抄 dpkg 语义。以下为实测输出，样本是 `noarch` 探针包，不是产品包。

**调用顺序与参数**（`rpm -i` → `rpm -U` → `rpm -e`）：

```
### 首次安装
PRE   v1 arg1=[1]
POST  v1 arg1=[1]
### 升级 1->2
PRE   v2 arg1=[2]
POST  v2 arg1=[2]
PREUN v1 arg1=[1]
POSTUN v1 arg1=[1]
### 卸载
PREUN v2 arg1=[0]
POSTUN v2 arg1=[0]
```

`arg2` 在所有场景都未设置（脚本里的 `${2:-unset}` 始终输出 `unset`）。RPM 4.14/4.16 不传第二个参数，不要依据它判断。

**`$1` 是实例计数，且升级时新包脚本看到的是升级后的总数：**

| 场景 | `%pre` | `%post` | `%preun` | `%postun` |
| --- | --- | --- | --- | --- |
| 首次安装 | 1 | 1 | — | — |
| 升级（新包） | **2** | **2** | — | — |
| 升级（旧包） | — | — | **1** | **1** |
| 卸载 | — | — | 0 | 0 |

因此：
- 判断「是否升级」：`%pre`/`%post` 里 `$1 -gt 1`；`%preun`/`%postun` 里 `$1 -ge 1`
- 判断「彻底卸载」：`%preun`/`%postun` 里 `$1 -eq 0`

**升级顺序是「新包先跑，旧包后停」**：

```
new %pre → [解包] → new %post → old %preun → old %postun
```

这是和 dpkg 最实质的差异。dpkg 是 `old-prerm upgrade → old-postrm upgrade → new-preinst upgrade → new-postinst configure`，即旧包先停、再装新包；RPM 相反。**后果**：不能在 `%preun` 里停服务——那时新包已经解开并跑过 `%post`，停掉的是刚启动的新版本。停止动作必须在**新包的 `%pre`**（解包之前）完成。

**`%pre` 拒绝后原包完好**（忙拒绝的成立前提）：

```
before: version=1 payload=payload-1
upgrade refused, rc=1
  error: %prein(aiprobe-2-1) scriptlet failed, exit status 1
  error: aiprobe-1-1: erase skipped
after : version=1 payload=payload-1
rpm -V: clean
rpm -U --replacepkgs <旧包> -> 成功
```

`%pre` 失败时解包未开始、旧包未擦除，`rpm -V` 干净，`--replacepkgs` 重试可收敛。忙拒绝放在 `%pre` 是正确位置。注意：如果安装时用了 `--nodeps` 之类跳过，或 `%pre` 成功但解包中途失败，语义不同，不能外推。

**`%post` 失败不会让 `rpm -U` 返回非零**：

```
POST v2 failing
warning: %post(aiprobe-2-1) scriptlet failed, exit status 1
```

`rpm -U` 返回 **0**，包已是 v2、payload 已替换、`rpm -V` 干净，只有 warning。重跑 `--replacepkgs` 仍返回 0。

这直接影响「重装可修复」的语义：**RPM 里 `%post` 失败是静默的**。如果 `%post` 负责启动服务或做 ready 检查，失败时调用方以为成功。可选项：

- 在 `%post` 失败时写标记文件，由外部验收读取（但 RPM 不提供失败信号）
- 把关键检查留在 `%pre`（会真正阻断）
- 接受该限制并在文档写明，`ready` 由包外流程验证

DEB 的 `postinst` 失败会让 dpkg 返回非零，行为不同。这是移植时必须显式决定的产品取舍，不能默认「照搬即可」。

**环境说明**：Apple container，`rockylinux:9` 镜像（未固定 digest），容器内 `dnf install rpm-build` 获得 4.16.1.3。样本包为纯 `noarch`，不涉及真实 payload、systemd 或客户端进程。这是脚本语义证据，不是产品包验收。

### RPM 构建与事务验收（2026-11-09）

`packages/device-runtime/scripts/build-client-rpm.py`。`STOP` 与 DEB 共用
`scripts/client_package_stop.sh` —— 两份安全逻辑分开维护就会漂移，本轮已经因为
复制式修改出过一次错（`-s` 判断），所以改成单一来源，由测试钉住两者相等。

**spec 与 dpkg 版一致的部分**：payload 布局（`/opt/ai-device-client`、两个 `/usr/bin`
入口）、`ca-certificates`/`util-linux` 依赖、卸载保留配置与 state、`__pycache__` 清理。

**必须不同的部分**：

| 动作 | DEB | RPM | 原因 |
| --- | --- | --- | --- |
| 停止客户端 | `preinst` + `prerm` | 只在 `%pre` | `%preun` 运行时新 payload 已解包、`%post` 已启动新版本 |
| 首次安装 | `preinst` 无条件跑 | `%pre` 在 `$1 = 1` 时直接 `exit 0` | 首次安装没有已装 payload，`client_package stop` 会 127 失败并中止安装 |
| 启动客户端 | `postinst configure` | `%post` | — |
| 删除配置 | `purge` 动词 | **无对应物** | `%postun` 只在 `$1 = 0` 时清理 unit 与空目录，不删 `/etc/ai-device-client` |

**首次安装必须跳过守卫**：`%pre` 在解包之前运行，`STOP` 里调的是
`/opt/ai-device-client/runtime/bin/python3`（**已安装**的那份）。首次安装时它不存在，
实测报 `No such file or directory`、退出 127、整个 `rpm -i` 失败。加 `$1 = 1` 早退后正常。

**容器端到端结果**（rockylinux:9，aarch64 构建，systemctl 与 python3 为桩）：

```
### 首次安装
ver=0.4.3
calls=client_package activate

### 忙拒绝（cgroup.procs 有 PID）
client process group still has live processes; refusing replacement
error: %prein(...0.4.4...) scriptlet failed, exit status 1
error: ...0.4.3...: erase skipped
ver=0.4.3          ← 版本未变
marker=none        ← payload 未被替换
calls=client_package stop   ← 只有停止尝试，无 activate

### 清空 procs 后重试
ver=0.4.4  marker=v2        ← payload 正确替换
calls=client_package stop | client_package activate

### 卸载
payload=gone
```

忙拒绝发生在解包前，旧包保持 `0.4.3` 且未被擦除，这与 §「RPM 脚本语义实测」的
`%pre` 拒绝实验一致。`rpm -V` 在各阶段均干净。

**已验证的边界**：

- 依赖声明生效：容器缺 systemd 时 `rpm -i` 报 `Failed dependencies` 并拒绝，装上后才可安装
- 同版本 `rpm -U --replacepkgs` 重装成功，`%post` 再次执行 activate
- 空 cgroup 组放行；有 PID 拒绝 —— 与 DEB 侧同一 `STOP` 文本

**未验证 / 已知限制**（不可据此声明阶段三完成）：

1. 全程在**容器**内，systemctl 与客户端 python3 都是桩。没有真实 systemd、没有真实客户端进程、没有真实中心连接。
2. 构建用 `--buildarch aarch64`（容器是 arm64）。产品目标是 amd64，**尚未产出 amd64 RPM**。
3. 树是 `runtime/bin/python3` 一个假文件的**最小 tree**，不是真实客户端树，因此没有验证真实 payload 体积、真实 python 启动、`__pycache__` 清理的实际效果。
4. 未测：`%pre` 与 `%pretrans` 的交互、依赖不满足时的解包时机、`rpm -V` 对真实整树的报缺（历史风险见需求文档 §11）。
5. `%post` 失败仍使 `rpm -U` 返回 0（见上一节实测）。当前 `%post` 只调 activate，activate 失败不会让 rpm 报错——**ready 必须由包外复核**，这一点尚未在 rpm 路径上落实。
6. 未在真实 alinux3 / Rocky 机器上验收。ali-sh 有 rpm 但无 rpmbuild、无容器，且跑着现网 0.2.3 实例。
