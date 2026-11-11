# 阶段三：Linux 客户端 RPM 交付

## 1. 状态、来源与执行入口

- 状态：**需求已确认，尚未实施**。本文描述待做工作，不是已完成事实。
- 日期：2026-11-08 创建。
- 仓库：`ai-device`；worktree `/Users/<user>/data/project/ai-device-wt-client-native-ses14497`，分支 `feat/client-native-install-ses14497`。
- 分析基线：`b10165b`。实施前检查最新源码与工作区。
- 前置阶段：
  - `doc/requirements/client-linux-platform-compat-phase1-glibc217.md`（glibc 2.17 基线）
  - `doc/requirements/client-linux-platform-compat-phase2-cgroup-v1.md`（cgroup v1 与旧 systemd）
  - **本阶段以前两阶段完成为前提**。若平台兼容未解决，RPM 装上后仍跑不起来，等于白做。
- 需求讨论来源：`ses-14497`。本文自包含，不要求新会话读历史聊天。

## 2. 背景

### 2.1 触发场景

一台 Alibaba Cloud Linux 3（OpenAnolis）机器，`ID=alinux`、`VERSION_ID=3`、x86_64，包管理是 `rpm/dnf/yum`，没有 `dpkg`。上面跑着一个 0.2.3 旧架构客户端。需要评估「做一个 RPM 包」的工作量。

评估结论：**平台差异不在 glibc（阶段一已解决），而在 cgroup/systemd（阶段二）。RPM 打包本身是其中最小的一块。**

### 2.2 历史：RPM 曾经验证过

`docs/native-package-upgrade-transaction-design.md` 记录：

- 2026-10-29：Rocky 9.3 隔离容器内 `dnf install rpm-build` 后实测 `rpm -i`、`rpm -U` 连续升级、`rpm -e` 与重装，通过。RPM 版本 4.16.1.3。
- 2026-10-31：**RPM 从支持矩阵移除**。理由记在该文档 8.5 节：

> 首期 Linux 只交付 DEB，RPM 从支持矩阵移除。理由：P1 目标本就不含中心升级，而为 RPM 补验签链需要为 candidate 增加 rpm target 与 cpio 安全提取、为 update 抽出包管理器接口，投入较大且会与 P3 的升级工作重叠。

**关键区分**：当时移除的是「RPM 走**旧架构的签名 prepare→activate 受控升级链**」。限制在旧 0.2.x 体系：

- `native_candidate.py` 的 target 只认 `linux-amd64-deb`
- 解包只实现 Debian `ar` 归档，没有 rpm `cpio` payload 的提取
- `native_update.py` 的包事务全走 `dpkg-deb` / `dpkg-query` / `dpkg --install`，无 rpm 分支

而**当前的 0.4.x native 客户端是另一套架构**：`client_package.activate()` 走的是「systemd unit + Unix socket 探活 + runtime_version 比对」，**不使用** `native_candidate`/`native_update` 这套验证链。

**因此 8.5 节的历史结论不能直接套用到本阶段**，但其中的实测经验（RPM 事务语义、脚本调用顺序）可以参考。

### 2.3 仓库内已有的 RPM 材料

| 文件 | 内容 | 本阶段可用性 |
| --- | --- | --- |
| `packages/device-runtime/scripts/probe-native-rpm-gate.sh` | 独立 RPM 事务探针：构造 v1/v2 两个 noarch 包，v2 用 `%pretrans -p <lua>` 故意拒绝，验证「拒绝后原 payload 与 RPM 版本都未被改动」。要求 `DEVICE_NATIVE_DISPOSABLE_PROBE=yes`、root、且 `/opt/ai-device-p1-probe` 不存在。 | **可直接复用**作为 RPM 事务语义验证工具。它是独立实验，不是安装器。 |
| `packages/device-runtime/scripts/accept-centos7.py` | glibc 2.17 离线生命周期验收（旧架构 `install_instance`/`rebuild_instance`/`activate_release`） | 方法可参考，**代码不可直接用**（依赖旧架构 API） |
| `packages/device-runtime/scripts/build-centos7-dependencies.sh` | manylinux2014 容器构建 wheel，固定镜像 digest `sha256:f6a6f153...` | 阶段一/二已确认依赖链就是 2.17，本阶段不需要重建 |
| `packages/device-runtime/scripts/centos7-build-inside.sh` | 上述脚本的容器内实现 | 同上 |

## 3. 目标与非目标

### 3.1 本期目标

1. 产出可安装、可升级、可卸载的 RPM 包，功能与 DEB 对等。
2. 保留与 DEB 相同的核心安全语义：
   - 忙时拒绝，不强杀
   - 程序/配置/身份/状态分离
   - `remove` 保配置凭证，`purge` 才删
   - 替换文件前必须确认旧进程组为空
3. 在真实 RPM 系机器上完成安装、升级、卸载、忙拒绝验收。
4. 与 DEB 共享同一份运行时树与依赖锁，不引入第二套构建基线。

### 3.2 本期非目标

- 不改变 DEB 的既有行为。
- 不引入常驻升级器、远程升级协议、自动更新。
- 不做签名与公证（与 macOS 现状一致：本地开发包可未签名，公开分发另议）。
- 不支持 arm64 Linux、musl/Alpine、Windows。
- 不做 yum/dnf 仓库发布。
- 不迁移任何现存旧客户端（见 §9）。
- **不为 RPM 实现独立的事务引擎**。RPM 自身的脚本调用顺序即事务边界。

## 4. 已查证现状与代码入口

| 入口 | 已确认事实 |
| --- | --- |
| `packages/device-runtime/scripts/build-client-deb.py` | 174 行。`STOP` 字符串（第 8 行起）承载「阻止在旧进程存活时替换文件」的核心检查；`build(tree, output, version)`（第 68 行起）写 `DEBIAN/control` 与四个脚本。 |
| 同文件 `:8-66` `STOP` | 检查内容：unit 归属、legacy 布局拒绝、`FragmentPath`、`DropInPaths`、`ActiveState`/`MainPID`/`ControlPID` 三态、`SendSIGKILL`/`KillMode` 策略、cgroup 空进程组确认。 |
| 同文件 `:113-120` `postinst` | 只在 `configure` 时执行 `client_package activate`。 |
| 同文件 `:121-155` `postrm` | `remove`/`purge` 时 disable + 删 unit；清理 `__pycache__` 与空目录；`purge` 才删 `/etc/ai-device-client`。 |
| `packages/device-runtime/src/device_runtime/client_package.py` | `activate()`：unit 归属校验 → `validate_config()` → `check_conflicts()` → `start()` → 轮询 `package_ready` 且 `runtime_version()` 相等。**与包管理器无关**，RPM 可复用。 |
| `packages/device-runtime/src/device_runtime/client_service.py` | systemd 交互层：`validate_registration`、`validate_config`、`state()`、`stop_for_install()`、`start()`、`render_unit()`。**与包管理器无关**。 |
| `packages/device-runtime/scripts/build-client-tree.py` | 从 `native/runtime-lock.json` 取 Python，从 `native/dependency-lock.json` 取 wheel，组装 `/opt/ai-device-client/{runtime,client-identity.json,build-manifest.json,third-party-inventory.json}`。**平台无关产物，RPM 与 DEB 共用同一棵树。** |
| `packages/device-runtime/tests/test_client_deb_build.py` | 185 行，7 个测试，全部针对 DEB 脚本生成逻辑。 |

### 关键：RPM 与 DEB 的差异集中在「包管理器契约」，不在业务逻辑

`client_package` 与 `client_service` 两个模块完全不感知包管理器。因此 RPM 的工作量集中在：

1. spec 文件与脚本映射
2. 生命周期动词的语义对齐
3. 路径与所有权策略

## 5. dpkg → rpm 语义映射

**这是本阶段的核心设计，必须逐项确认，不能凭印象。**

| 概念 | DEB（现状） | RPM | 需要注意 |
| --- | --- | --- | --- |
| 安装前脚本 | `preinst` | `%pre` | 调用参数不同（见下） |
| 安装后脚本 | `postinst` | `%post` | — |
| 卸载前脚本 | `prerm` | `%preun` | — |
| 卸载后脚本 | `postrm` | `%postun` | — |
| 版本升级 | 同 `preinst`/`prerm`，靠 `$1` 参数区分 | `%pre`/`%post`/`%preun`/`%postun` 各执行两次（旧包一次、新包一次） | **RPM 的调用顺序与 DEB 不同，必须实测** |
| 脚本参数 | `$1` = `install`/`upgrade`/`remove`/`purge`/`configure`/`abort-upgrade` 等 | `$1` = 本次安装的包实例数（安装前）或剩余实例数（卸载时） | **语义完全不同，不能照抄 case 分支** |
| 依赖声明 | `Depends: systemd, libc6 (>= 2.17), ...` | `Requires: systemd, glibc >= 2.17, ...` | 包名不同：`libc6` → `glibc` |
| 架构 | `Architecture: amd64` | `BuildArch: x86_64` | — |
| 删除配置 | `purge` 动词 | **无 pur[g]e 概念** | 需自行设计，见 §7.4 |
| 文件归属 | `dpkg -L` | `rpm -ql` | — |
| 校验 | `dpkg -V` | `rpm -V` | 历史实测：`rpm -V` 对整树 payload 报 missing（见 §11 风险） |

### 5.1 脚本参数语义（最容易出错的地方）

DEB 现状（`build-client-deb.py`）：

```sh
# preinst / prerm
case "${1:-}" in upgrade|remove|deconfigure) ;; *) exit 0 ;; esac   # prerm
# postinst
[ "${1:-}" = configure ] || exit 0
# postrm
case "${1:-}" in remove|purge) ... ;; esac
```

RPM 的 `$1` 是**实例计数**，不是动作名。已实测（见 `docs/client-native-linux-development.md`「RPM 脚本语义实测」）：

- `%pre`：首次安装 = 1，升级 = **2**（`$1` 是安装后的实例总数）
- `%post`：同 `%pre`
- `%preun`：升级时 = 1，彻底卸载时 = 0
- `%postun`：同 `%preun`
- `$2`：**实测始终未设置**，不要使用

**所以「是升级还是卸载」在 RPM 里靠 `$1` 的数值判断，不是靠字符串。** 已验证。

**行动要求**：实施第一步就是在隔离 RPM 容器里跑一遍 `probe-native-rpm-gate.sh` 的变体，打印每个脚本的 `$1`、`$2`、调用顺序。把实际输出写进开发文档，作为后续实现的依据。

### 5.2 升级时的脚本执行顺序

DEB（`dpkg -i` 升级）实测顺序（2026-11-08，ali-jp）：
```
old-prerm upgrade
old-postrm upgrade
new-preinst upgrade
new-postinst configure
```

RPM（rockylinux:9 / RPM 4.16.1.3 实测，2026-11-09）：
```
new-%pre
[解包]
new-%post
old-%preun
old-%postun
```

**差异的后果**：DEB 的顺序是「旧包先停，再装新包」；RPM 是「新包脚本先跑并解包，再停旧包」。实测确认。

**因此停止动作必须放在新包的 `%pre`**，不能放 `%preun`：升级时 `%preun` 跑的是旧包脚本，但那时新包已经解包且 `%post` 已执行——在 `%preun` 里停服务，停掉的是刚启动的新版本。

`%pre` 拒绝时解包未开始、旧包未擦除，`rpm -V` 干净，`--replacepkgs` 可收敛，因此忙拒绝放在 `%pre` 是安全位置。

**另一个差异**：`%post` 失败时 `rpm -U` 仍返回 **0**，只有 warning，包已升级、payload 已替换。DEB 的 `postinst` 失败会让 dpkg 返回非零。`%post` 不能承担「失败即回退」的职责，也不能独占启动成功判定；需要外部 ready 复核。

## 6. 实施要求

### 6.1 构建脚本

新增 `packages/device-runtime/scripts/build-client-rpm.py`，与 `build-client-deb.py` **并列**，不合并：

- 输入相同的 tree、相同的版本号、相同的 glibc 基线常量（从阶段一的单一来源取）。
- 输出 `.rpm`。
- 不引入 rpmbuild 之外的额外构建依赖；目标机不需要 rpmbuild。

**为什么不合并成一个脚本**：两者输出格式、脚本语义、依赖字段完全不同，强行抽象会造出一个到处是 `if format ==` 的脚本。分列更清晰。共享部分（版本号校验、glibc 常量）如有需要可抽小函数，但不为「少一个文件」而合并。

### 6.2 脚本内容映射

| DEB 脚本 | RPM 对应 | 内容调整 |
| --- | --- | --- |
| `preinst` + `STOP` | `%pre` | 保留全部检查逻辑；参数判断改为 RPM 语义 |
| `postinst`（activate） | `%post` | 保留；注意 `$1` 语义不同 |
| `prerm` + `STOP` | `%preun` | 保留检查；参数判断改为 RPM 语义 |
| `postrm`（清理） | `%postun` | 保留 `__pycache__` 清理与空目录剪枝 |
| `purge` 分支 | **无直接对应** | 见 §7.4 |

**`STOP` 逻辑必须完整搬运，不能简化。** 包括：

- legacy 布局拒绝（`/opt/ai-device/packages`、`/opt/ai-device/bootstrap`）
- unit 归属校验（`FragmentPath`、`DropInPaths`）
- `SendSIGKILL=no` + `KillMode=control-group` 策略校验
- cgroup 空进程组确认（用阶段二的 v1/v2 适配版本）

**这些检查是安全性的一部分，不是可选优化。**

### 6.3 依赖声明

```spec
Requires: systemd, glibc >= 2.17, ca-certificates, util-linux
```

注意：

- DEB 的 `libc6` 在 RPM 系叫 `glibc`
- `2.17` 必须与阶段一的单一来源常量一致
- 需要在真实 RPM 机器上验证 `glibc >= 2.17` 的解析结果符合预期（RPM 的版本比较规则与 dpkg 不同：`2.17` vs `2.17-xxx.el8` 的处理需实测）

### 6.4 文件布局与所有权

与 DEB 保持一致：

```
/opt/ai-device-client/runtime/          程序（包所有）
/opt/ai-device-client/*.json            清单
/etc/ai-device-client/client.json       配置（remove 保留，purge 删）
/var/lib/ai-device-client/              状态（永不删）
/etc/systemd/system/ai-device-client.service
```

RPM 特有注意：

- `%files` 必须列全，包括运行时树的每一个文件。历史实测（`native-package-upgrade-transaction-design.md` §4.3）：RPM 对未在 `%files` 声明的文件会在升级时删除，曾导致「每次 rpm 升级输出 9762 条 remove failed」。
- 运行时有大量 `.py` 文件与 `.so`，`%files` 需用通配或在构建时生成清单。
- **配置与状态目录不能出现在 `%files`**，否则卸载会删掉用户数据。

### 6.5 测试要求

1. 单元测试：spec 生成逻辑（版本校验、依赖字段、脚本内容断言），与 DEB 的测试结构对齐。
2. `probe-native-rpm-gate.sh` 在隔离 RPM 容器内实跑，验证事务拒绝行为。
3. 脚本参数实测：打印每个 hook 的 `$1`/`$2`，记录到开发文档。

## 7. 生命周期语义对齐

### 7.1 安装

`rpm -i` 或 `dnf install`：
- `%pre` 检查通过 → 解包 → `%post` 调 `activate`
- 未配置时 `activate` 应提前返回「installed; configuration and service registration required」（见 `client_package.py:21-22`）

### 7.2 升级

`rpm -U`：
- **必须实测脚本顺序**（§5.2）
- 旧服务必须先停止，再替换文件
- 若 RPM 的 `%pre`（新包）在旧包停止之前执行，则「确认进程组为空」的检查会失败（因为旧进程还活着）
- **这是必须解决的设计问题**，可能的方案：
  - 在 `%pre` 中主动停止旧服务（但此时旧包文件还在，需用旧包的 stop 入口）
  - 依赖 RPM 的 `%preun`（旧包）先执行
  - 具体选哪个，由 §8 第 1 步的实测结果决定

### 7.3 卸载

`rpm -e`：
- `%preun` 检查（确认进程已停）→ `%postun` 清理 unit、`__pycache__`、空目录
- 不删 `/etc/ai-device-client`（配置保留）
- 不删 `/var/lib/ai-device-client`（状态保留）

### 7.4 「purge」的替代设计

RPM 没有 `purge` 动词。需要一个等价入口，可选：

| 方案 | 说明 | 代价 |
| --- | --- | --- |
| A. 文档说明手动删除 | 用户执行 `rpm -e` 后自行 `rm -rf /etc/ai-device-client` | 简单，但容易忘 |
| B. 提供独立 CLI | 如 `ai-device-client purge-config` | 需新增入口 |
| C. `%postun` 在 `$1 == 0` 时询问 | 不可行——非交互环境 | — |

**推荐 A**，理由：purge 是低频操作，且现有 DEB 的 purge 也只是「删 `/etc/ai-device-client`」。为一个低频操作新增 CLI 入口（方案 B）不符合「不为内部完整性增加用户概念」的原则。

**但必须在文档中明确写出**：RPM 卸载后配置仍在，彻底清除需手动删除，并给出准确命令。

### 7.5 忙拒绝

与 DEB 一致：

- 客户端正在执行任务时，包操作必须拒绝，且不破坏当前状态
- 依赖阶段二的 cgroup 检查正确生效
- **不得**提供强制安装选项

## 8. 实施顺序与阶段门禁

**严格按序，前一步的产出是后一步的输入。**

1. **RPM 事务语义实测**（不改任何产品代码）✅ 已完成 2026-11-09
   - 环境：Apple container，`rockylinux:9`（未固定 digest），镜像内 `dnf install rpm-build` → RPM 4.16.1.3
   - 实测了 `$1`/`$2`、调用顺序、`%pre` 拒绝后状态、`%post` 失败行为
   - **产出：`docs/client-native-linux-development.md` 的「RPM 脚本语义实测」一节**
   - 结论已回写到 §5.1、§5.2；两处与本文原假设不符的地方已改正
   - 尚未覆盖：真实 systemd payload、`%pretrans` 与 `%pre` 的交互、依赖不满足时的拒绝时机

2. 编写 `build-client-rpm.py` 与 spec 模板
   - 先用最小 spec 验证能构建、能装、能卸
   - 再逐步加入 `STOP` 检查、`activate`、清理逻辑

3. 脚本参数语义实现（依据第 1 步实测结果）

4. 本地验证：
   - `rpm -qpl` 检查文件清单
   - `rpm -qpR` 检查依赖
   - 在隔离容器内安装、升级、卸载

5. 真实 RPM 机器验收（见 §10）

6. 文档同步：
   - `docs/client-native-linux-guide.md`：新增 RPM 章节
   - `docs/client-native-linux-development.md`：构建方式、脚本语义差异
   - `doc/requirements/client-native-install-upgrade.md` §3.1：支持范围

7. 提交

### 阶段门禁

- [ ] RPM 脚本调用顺序与参数语义已实测记录，不是推测
- [ ] `build-client-rpm.py` 可产出包，`rpm -qpR` 依赖正确
- [ ] `STOP` 全部检查逻辑已搬运，未简化
- [ ] `remove` 后配置与状态保留；文档说明彻底清除方式
- [ ] 忙拒绝在真实 RPM 机器上验证通过
- [ ] A→B 升级完成，配置/身份/状态保留
- [ ] 同版本重装可修复
- [ ] 卸载后无残留 unit、无残留进程
- [ ] 真实机器完成全部验收，有证据
- [ ] DEB 路径无回归
- [ ] 全量测试 + `ruff` 通过
- [ ] 文档已同步

**未满足任一项，不得声称本期完成。**

## 9. 关于现存旧客户端的处理（重要）

调查中发现一台 alinux3 机器跑着 **0.2.3 旧架构**客户端：

- 布局：`/var/lib/device-managed/<id>/{active.json,instance.json,config,state,releases/0.2.3-xxx,run,upgrade-profile.json}`
- `active.json` 记录 `release`、`roles:["client"]`、`release_version:"0.2.3"`、`bundle_sha256`
- `upgrade-profile.json` 内容为 `{"allow_upgrade":false}`
- 进程由一个 `python3.13` 父进程（PPID 1）拉起，**未确认是否有对应的 systemd unit**（此前用 `list-units | grep` 查询被磁盘 `.device` 匹配占满，证据不足）

**本期明确不做的事：**

- 不迁移、不停止、不删除该实例
- 不修改 `upgrade-profile.json`
- 不假定该实例能被新 RPM 覆盖

**原因**：旧布局与新布局完全不同（`/var/lib/device-managed/` vs `/opt/ai-device-client/`），新包的 `STOP` 脚本会检测到 legacy 布局并**主动拒绝**操作（`/opt/ai-device/packages`、`/opt/ai-device/bootstrap` 的检查）。这是设计意图，不是缺陷。

**迁移是独立议题**，需要单独决策：是否支持、如何保留旧数据、是否需要停服窗口。本期只交付「新机器可装 RPM」，不承诺「旧实例可原地升级」。

若用户要求支持原地升级，必须先讨论并新增需求，不在本期悄悄实现。

## 10. 验收矩阵

**所有操作在空闲机器进行。不得在生产客户端机器上试装。**

| 场景 | 环境 | 必须结果 |
| --- | --- | --- |
| 干净机安装 | 真实 RPM 系（alinux3 / Rocky 9 / CentOS 7） | `rpm -i` 成功；客户端可配置、可启动、中心在线 |
| 首次未配置 | 同上 | 安装成功但明确待配置，不错误循环重启 |
| 首次配置 | 同上 | 指定服务用户、身份、URL、凭证后启动成功；凭证不进输出 |
| 同版本重装 | 同上 | 可修复程序，配置/身份/状态不变 |
| A→B 升级 | 同上 | 服务实际使用 B，配置与日志保留 |
| 忙拒绝 | 同上 | 客户端执行长任务时，包操作明确拒绝，任务继续，准入保持 |
| 卸载保数据 | 同上 | `rpm -e` 后程序移除，配置与状态保留 |
| 彻底清除 | 同上 | 按文档手动删除配置目录后无残留 |
| 依赖检查 | 同上 | `rpm -qpR` 显示 `glibc >= 2.17`；在 glibc 2.17 机器上可装 |
| 文件清单 | 同上 | `rpm -V` 无大量 missing（历史问题，见 §11） |
| 旧布局拒绝 | 有 legacy 布局的机器（可用容器模拟） | 包操作明确拒绝，提示需要显式迁移 |
| DEB 回归 | Ubuntu 24.04 | 行为与阶段二完成后一致 |

### 证据要求

- 记录：发行版、版本、glibc、systemd、rpm 版本、包 SHA256、命令、退出码、输出。
- 忙拒绝要证明「客户端确实在忙」。
- 升级要证明「服务实际用的是新版本」（runtime_version + build_id），不能只看包版本号。
- 不含 token/密钥/完整配置。

## 11. 已知风险

1. **脚本执行顺序与 DEB 不同**（§5.2）。可能导致「新包 `%pre` 在旧进程停止前执行」，使空进程组检查失败。**必须在第 1 步实测清楚。**

2. **`rpm -V` 对整树 payload 报 missing**。历史记录（`native-package-upgrade-transaction-design.md` §4.3、§6）：`dpkg -V` 与 `rpm -V` 都把整个 payload 报为 missing（实测 9762 条）。原因是运行时树的文件归属与包清单不完全匹配。
   - 本阶段需确认当前 `build-client-deb.py` 的做法（`dpkg-deb --root-owner-group --build`，文件由 dpkg 自动记录）在 RPM 上如何对应。
   - RPM 需要显式 `%files`，若遗漏会触发删除。**这是必须解决的问题**（§6.4）。

3. **`%ghost` 不是解法**。历史结论：`%ghost` 让包不再携带文件（实测 106MB → 723KB），是回避而非解决。不要重蹈。

4. **`glibc >= 2.17` 的版本比较**。RPM 比较 `2.17` 与 `2.17-106.el7` 的结果需实测。若比较不通过，需调整写法（如 `glibc >= 2.17-0`）。

5. **RPM 无 purge**。已按 §7.4 方案 A 处理，文档必须写清。

6. **旧架构的历史结论不完全适用**。8.5 节移除 RPM 的理由是「验证链不支持 rpm」，而当前 native 客户端不使用那条链。但仍需确认：RPM 包内是否需要 `native_candidate` 之类的机制？答案应当是否——`client_package.activate` 足够。若实施中发现需要，说明架构假设有误，应停下来讨论。

## 12. 禁止的完成声明

- 不得以「`rpm -i` 退出码 0」代替「客户端实际运行且中心在线」。
- 不得以「单元测试通过」代替真实机器验收。
- 不得在未实测脚本顺序的情况下按 DEB 逻辑照抄。
- 不得声称「支持所有 RPM 系发行版」——只验证了具体测试机器。
- 不得声称「旧客户端可原地升级」——本期不做。
- 不得声称制品可公开分发（未签名）。
- 不得把本阶段完成称为「多平台交付完成」——macOS 与 DEB 是独立路线。

## 13. 交付物

1. `build-client-rpm.py` 与 spec 模板。
2. RPM 事务语义实测记录（脚本顺序、参数）。
3. 新增测试。
4. 真实机器验收记录。
5. 文档：Linux 指南新增 RPM 章节、开发文档记录语义差异、需求文档更新支持范围。
6. `probe-native-rpm-gate.sh` 的使用说明更新（若有调整）。
7. 实现提交。

## 14. 安全与授权边界

- 不停止真实服务；重启真实服务需本次明确确认。
- 不 push，除非本次明确授权。
- 不在生产客户端机器上试装。
- **不碰运行旧架构客户端的机器**（不迁移、不停服、不改配置）。
- 临时目录与制品保留。
- 不输出/记录 token、密钥、完整配置。

## 15. 三阶段总览

| 阶段 | 核心内容 | 主要成本 | 完成后的能力 |
| --- | --- | --- | --- |
| 一 | glibc 2.17 声明修正 + 客户端前检 | 小（元数据 + 少量代码） | 现有 DEB 可用于 glibc ≥ 2.17 的机器 |
| 二 | cgroup v1/v2 适配 + 旧 systemd 验证 | 中（两处实现 + 真机验证） | 可用于 cgroup v1 机器（alinux3、CentOS 7） |
| 三 | RPM 打包与生命周期对齐 | 中（脚本语义 + 验收） | 可用于 RPM 系发行版 |

**三阶段完成后**，Linux 客户端可在「glibc ≥ 2.17、cgroup v1 或 v2、systemd ≥ 239、DEB 或 RPM」的组合上运行。

**仍不在范围内**：arm64 Linux、musl/Alpine、Windows、yum/dnf 仓库、签名分发、旧实例原地迁移。
