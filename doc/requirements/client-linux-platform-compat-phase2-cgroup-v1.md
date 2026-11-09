# 阶段二：Linux 客户端 cgroup v1 与旧版 systemd 兼容

## 1. 状态、来源与执行入口

- 状态：**需求已确认，尚未实施**。本文描述待做工作，不是已完成事实。
- 日期：2026-11-08 创建。
- 仓库：`ai-device`；worktree `/Users/kongkongye/data/project/ai-device-wt-client-native-ses14497`，分支 `feat/client-native-install-ses14497`。
- 分析基线：`b10165b`。实施前检查最新源码与工作区。
- 前置阶段：`doc/requirements/client-linux-platform-compat-phase1-glibc217.md`。**须先完成阶段一**（或至少确认阶段一的结论仍然成立），本阶段以阶段的 2.17 基线为前提。
- 需求讨论来源：`ses-14497`。本文自包含，不要求新会话读历史聊天。

## 2. 背景：一个真实的保护空洞

### 2.1 现象

客户端在判定服务「已停止」时，会额外检查 cgroup 里是否还有遗留进程。这段逻辑在 `client_service.py:101`：

```python
def confirm_empty_group(self):
    # MainPID/ControlPID can both be zero while timed-out descendants remain.
    group = self._call("show", UNIT, "--property=ControlGroup", "--value").strip()
    if not group:
        return
    if not group.startswith("/") or ".." in Path(group).parts:
        raise ServiceError("client process group is unknown")
    directory = CGROUP_ROOT / group.lstrip("/")     # CGROUP_ROOT = /sys/fs/cgroup
    if not directory.exists():
        return                                       # ← 这里
    try:
        events = dict(
            line.split() for line in (directory / "cgroup.events").read_text().splitlines()
        )
    except (OSError, ValueError) as exc:
        raise ServiceError("cannot confirm client process group is empty") from exc
    if events.get("populated") != "0":
        raise ServiceError("client process group still has live processes")
```

`build-client-deb.py:54-65` 的 `STOP` shell 段有同样逻辑：

```sh
group=$(systemctl show "$unit" --property=ControlGroup --value) || exit 1
if [ -n "$group" ]; then
    case "$group" in /*) ;; *) exit 1 ;; esac
    case "$group" in *..*) exit 1 ;; esac
    if [ -d "/sys/fs/cgroup$group" ]; then
        grep -qx 'populated 0' "/sys/fs/cgroup$group/cgroup.events" || {
            echo 'client process group is not confirmed empty; refusing replacement' >&2; exit 1;
        }
    fi
fi
```

### 2.2 在 cgroup v2 上正常工作

Ubuntu 24.04（cgroup v2）：`ControlGroup` 返回 `/system.slice/ai-device-client.service`，实际路径就是 `/sys/fs/cgroup/system.slice/ai-device-client.service`。`cgroup.events` 存在，内容形如 `populated 0`。检查生效。

### 2.3 在 cgroup v1 上被静默跳过

Alibaba Cloud Linux 3（cgroup v1，hybrid 布局）实测：

```
systemd-journald -> ControlGroup=[/system.slice/systemd-journald.service]
   /sys/fs/cgroup$group 存在?                                      NO
   实际位置: /sys/fs/cgroup/systemd/system.slice/systemd-journald.service
```

`ControlGroup` 返回的路径是**相对 v2 根**的，只有挂到 `/sys/fs/cgroup/systemd/` 下才能找到。v1 根 `user.slice blkio cpu memory systemd ...` 下没有 `system.slice/`。

于是 `directory.exists()` 为 **False** → Python 版 `return`，shell 版 `if` 不成立 → **两处都跳过检查，且不报错**。

v1 目录里也确实没有 `cgroup.events`（实测 sshd 的 unit 目录只有 `cgroup.clone_children`、`cgroup.procs`、`notify_on_release`、`pool_size`、`tasks`）。

### 2.4 为什么这不是理论问题

这段检查的存在理由写在它自己的注释里：`MainPID/ControlPID can both be zero while timed-out descendants remain`。

它防的是：服务主进程已退出、但子进程还活着时，包就把 `/opt/ai-device-client` 下的文件换掉。这正是「客户端执行长时间任务被杀」类的场景。

在 cgroup v1 机器上，**这条防线不存在**，但也没有任何提示。

**注意**：这个空洞是既有代码的，不是本次改动引入。已发布的 DEB0.4.2 内 `client_service.py` 与基线 `b10165b` 完全一致（实测比对过）。

## 3. 目标与非目标

### 3.1 本期目标

1. 让「确认进程组为空」的检查在 **cgroup v1 和 v2 上都真实生效**。
2. 无法确认 cgroup 版本时，行为必须是被明确定义的，不能再次静默放行。
3. Python 侧与 shell 侧行为一致（两处独立实现不应产生分歧）。
4. 在真实 cgroup v1 机器上验证安装、升级、忙拒绝路径。
5. 验证 systemd 239（或同级旧版本）上的 unit 指令语义。

### 3.2 本期非目标

- 不新增 RPM（阶段三）。
- 不改 glibc 声明（阶段一）。
- 不改变「忙时拒绝、不强杀」的既有语义。
- 不引入常驻进程、独立升级服务或通用兼容层。
- **不试图同时支持 cgroup v1 与 v2 的所有历史变体**，只覆盖实际测试的两种布局。
- 不处理 macOS（其 launchd 体系不需要 cgroup）。

## 4. 已查证现状与代码入口

| 入口 | 已确认事实 |
| --- | --- |
| `packages/device-runtime/src/device_runtime/client_service.py:6` | `CGROUP_ROOT = Path("/sys/fs/cgroup")` |
| 同文件 `:101-120` | `confirm_empty_group()` 全文见 §2.1 |
| 同文件 `:83-90` | `state()` 在 `ActiveState in (inactive, failed) and main_pid == control_pid == 0` 时调用 `confirm_empty_group()`，然后返回 `"stopped"` |
| 同文件 `:130-136` | `stop_for_install()`：持 `idle_lease` → `systemctl stop` → `state() != "stopped"` 则抛错 |
| `packages/device-runtime/scripts/build-client-deb.py:54-65` | `STOP` shell 段的 cgroup 检查，嵌在 `preinst` 与 `prerm` 中 |
| `packages/device-runtime/src/device_runtime/maintenance.py` | `idle_lease(path, timeout=5)`：通过 Unix socket `{"action":"acquire"}` 持有准入，阻止新任务进入。**与 cgroup 独立，本阶段不改** |
| `packages/device-runtime/tests/test_client_service.py:233-252` | `test_stopped_state_checks_descendant_cgroup`，参数化 `populated ∈ {"1","0",None}`。monkeypatch `CGROUP_ROOT` 到 tmp_path，构造 `system.slice/client.service/cgroup.events`。**只测 v2 形态；`populated=None`（不写 events 文件）分支覆盖的是「文件缺失→报错」，没有覆盖「目录不存在→放行」。** |

### 关键：现有测试没覆盖真正的失效分支

`test_stopped_state_checks_descendant_cgroup` 在 `populated is None` 时不写 `cgroup.events`，但**目录仍然创建**（`directory.mkdir(parents=True)`）。所以测的是「目录在、events 不在 → 抛 ServiceError」。

而 v1 上的实际路径是「**目录都不在** → `return` 放行」。这一分支**无测试**。这正是它能长期存在的原因。

## 5. cgroup 版本探测方案

### 5.1 探测方法

实测有效的判断：

```python
def _is_cgroup_v2():
    return Path("/sys/fs/cgroup/cgroup.controllers").exists()
```

- cgroup v2（Ubuntu 24.04）：`/sys/fs/cgroup/cgroup.controllers` 存在。实测返回 v2。
- cgroup v1（alinux3）：该文件不存在，根下是各 controller 挂载点。实测返回 v1。

备选：解析 `/proc/self/mountinfo` 中 `- cgroup` 的类型，判断是否有 `cgroup2`。实测在 v1 机器上输出 13 条 `cgroup cgroup rw,<controller>`，无 `cgroup2`。此法更严格但代码更重。

**推荐 `cgroup.controllers` 存在性判断**：单次 `stat`，无解析，语义清晰。

### 5.2 unit 目录定位

| 版本 | 路径构造 |
| --- | --- |
| v2 | `CGROUP_ROOT / group.lstrip("/")`（现状，不改） |
| v1 | `CGROUP_ROOT / "systemd" / group.lstrip("/")` |

v1 的 `systemd` 子目录来自 systemd 的 `name=systemd` 挂载，实测存在（`cgroup on /sys/fs/cgroup/systemd type cgroup (...name=systemd)`）。

**不要**假定 systemd 一定挂在 `systemd` 这个名字下。mountinfo 里可以确认；但为简单起见，可先按 `systemd` 直连，找不到时再明确定义失败行为（见 5.4）。

### 5.3 空进程组判定

| 版本 | 判定方式 |
| --- | --- |
| v2 | 读 `cgroup.events`，`populated 0` 视为空（现状） |
| v1 | 读 `cgroup.procs`，**内容为空**视为空 |

v1 实测：`/sys/fs/cgroup/systemd/system.slice/sshd.service/cgroup.procs` 返回 1 行（sshd 主进程）；目录里另有 `tasks` 文件。用 `cgroup.procs`（进程级，不含线程）而非 `tasks`（线程级）。

**注意 v1 的 `tasks` 与 `cgroup.procs` 差异**：`tasks` 列线程，`cgroup.procs` 列进程。判定「还有没有活的东西」用哪个都行，但 `cgroup.procs` 更直观。若用 `tasks`，需要确认空文件判断一致。

**不要**用 `systemctl show --property=TasksCurrent`。实测在 systemd 239 上，对一个已结束的瞬态 unit 返回 `not set`，不可靠。

### 5.4 探测失败时的行为（需要决策）

这是本阶段最重要的设计取舍。

现状的 `if not directory.exists(): return` 表达的是「我不是 v2，检查不了，当没事」。加 v1 支持后，如果保留这条兜底，那么**在没有 cgroup 的环境（某些容器、某些 CI）仍然静默放行**。

三种可选策略：

| 策略 | 行为 | 优点 | 缺点 |
| --- | --- | --- | --- |
| A. 全硬失败 | 识别不出 cgroup 版本就抛错，拒绝安装 | 最安全 | 无 cgroup 的环境（容器）完全无法装 |
| B. 保持放行 | 识别不出就当没事 | 兼容性最好 | 保护可在未知环境消失，与现状同样的病 |
| C. 三态（推荐） | v1/v2 明确识别 → 正常检查；两者都识别不出 → 放行但输出明确警告 | 覆盖目标机器，不卡死特殊环境，且不静默 | 需要额外的告警通道 |

**推荐 C**，理由：

- 目标是让 ali-sh（v1）和 Ubuntu（v2）都正常工作，这两者都能明确识别。
- 完全没有 cgroup 的环境不是本期支持目标，但也不该因为修一个 bug 就把它彻底挡住。
- 关键是**不能静默**：必须留下可观测的痕迹（日志/标准错误）。

警告的实现方式需要按实际场景选：
- Python 侧 → `client.log`（unit 已配置 `StandardOutput=append:/var/log/ai-device-client/client.log`）。
- shell 侧（`preinst`/`prerm`）→ stderr，dpkg 会显示。

**这是需要用户确认的取舍点**。若用户选择 A 或 B，需相应调整第 7 节验收矩阵。

## 6. 实施要求

### 6.1 Python 侧：`client_service.confirm_empty_group()`

要求：

1. 抽出 cgroup 目录解析为独立函数（如 `_cgroup_directory(group)`），返回 `Path | None`。
2. 抽出「是否为空」判定为独立函数（如 `_group_populated(directory)`），返回 `bool | None`。
3. 抽出的函数必须**可独立测试**，不依赖 `systemctl` 调用。
4. 保留现有的安全检查：
   - `group` 必须以 `/` 开头
   - 不得含 `..`
   - 这些检查在 v1 上同样有效（`/system.slice/x.service` 也满足）
5. v1 下 `cgroup.procs` 读取失败（权限、竞态）时，抛 `ServiceError`，不得当作「空」。
6. v2 行为完全不变（这是回归基线）。

**注意**：`CGROUP_ROOT` 是模块级常量，被测试 monkeypatch。新增的 v1 前缀逻辑要能在 monkeypatch 后正常工作（即不要硬编码 `/sys/fs/cgroup/systemd`，应基于 `CGROUP_ROOT`）。

### 6.2 shell 侧：`STOP` 段

`build-client-deb.py` 里的 `STOP` 字符串要同步修改：

1. 判断版本：`[ -f /sys/fs/cgroup/cgroup.controllers ]`
2. v2 → 现有逻辑
3. v1 → 路径加 `systemd/` 前缀，判定改为 `cgroup.procs` 是否为空

shell 里判定「文件为空」要小心：

```sh
[ -s "/sys/fs/cgroup/systemd$group/cgroup.procs" ] || ...   # -s 为 false 表示文件为空或不存在
```

**注意 `-s` 无法区分「空文件」和「文件不存在」**。需要先确认文件存在（`-f`），再判断 `-s`。两者都要处理，不能只用 `-s`。

4. `set -eu` 下，`grep -q` 返回 1 会因 `||` 分支正常处理，但要确认整个判断链不会因 `set -e` 提前退出。

**shell 侧的改动必须与 Python 侧语义一致**。两处独立实现是既有事实，本期不强行合并（合并需要引入共享脚本，属于结构改造），但**必须保证同一输入下判断结果一致**，并在测试中体现。

### 6.3 systemd 旧版本兼容验证

ali-sh 实测 `systemd 239`。当前 unit 用了这些指令（`client_service.py:158-182`）：

| 指令 | 引入版本 | 239 支持 | 备注 |
| --- | --- | --- | --- |
| `After=` / `ConditionPathExists=` | 早期 | 是 | — |
| `StartLimitIntervalSec=` / `StartLimitBurst=` | 230（从 `[Service]` 移到 `[Unit]`） | 是 | — |
| `Type=simple` / `User=` / `ExecStart=` / `WorkingDirectory=` | 早期 | 是 | — |
| `StateDirectory=` / `StateDirectoryMode=` | **236** | 是 | 边界内 |
| `LogsDirectory=` / `LogsDirectoryMode=` | **236** | 是 | 边界内 |
| `UMask=` | 早期 | 是 | — |
| `Restart=on-failure` / `RestartSec=` | 早期 | 是 | — |
| `KillMode=control-group` | 早期 | 是 | — |
| `TimeoutStopSec=` | 早期 | 是 | — |
| `SendSIGKILL=no` | 早期 | 是 | **语义需重点验证**，见下 |
| `StandardOutput=append:` | **240** | **否** | **需确认** |

**必须实测确认的两点：**

1. **`StandardOutput=append:` 在 239 上是否被接受**
   该值在 systemd 240 引入。239 上可能报 `Failed to parse output specifier`，导致 unit 无法加载。若确认不支持，需要改用 `file:` 或其它兼容写法，并在文档中说明。
   **这是可能导致「服务根本起不来」的硬阻塞，必须最早验证。**

2. **`SendSIGKILL=no` + `TimeoutStopSec=30` 的实际行为**
   现有 `STOP` 脚本要求 `sigkill:killmode == no:control-group` 才继续（`build-client-deb.py` 中 `killmode=$(systemctl show ...)` 那一段）。语义是「优雅停止，绝不强杀，超时也不能强杀」。
   需验证在 239 上：
   - `systemctl show --property=SendSIGKILL --value` 返回 `no`
   - `systemctl show --property=KillMode --value` 返回 `control-group`
   - 超时后确实不强杀（这是「不强杀业务」原则的落地）

**若任何一项不成立**，需要在阶段内解决：改 unit 写法，或明确记录为不支持 systemd 239 并在安装前拒绝。

### 6.4 测试要求

现有 `test_client_service.py:233-252` 需要扩展：

1. **补 v1 布局测试**：monkeypatch `CGROUP_ROOT` 到 tmp_path，构造 `systemd/system.slice/client.service/cgroup.procs`：
   - 内容为空 → `state()` 返回 `"stopped"`
   - 内容非空 → 抛 `ServiceError`
2. **补「目录不存在」分支测试**：这是当前失效的真实场景。需明确断言在新逻辑下的行为（按 §5.4 选定策略）。
3. **补版本探测测试**：`cgroup.controllers` 存在/不存在两种情况。
4. **保留 v2 原测试**：确保不回归。

`test_client_deb_build.py` 需扩展：

1. `test_build_hooks_and_layout` 或新增测试，断言 `STOP` 内容包含 v1 判断分支。
2. 由于 `STOP` 是生成的 shell 字符串，建议对生成的 `preinst`/`prerm` 做内容断言（现有测试已 `sh -n` 语法检查，可在此基础上加语义断言）。
3. 若能构造，做一次实际 shell 执行测试（在 tmp 目录下模拟 v1/v2 布局）。

**测试不能靠 mock 掉 cgroup 读取本身来「通过」**。必须让被测代码真实读文件，只 mock 路径根。历史教训：本次 bug 正是因为测试桩掉了关键校验。

### 6.5 文档同步

- `docs/client-native-linux-guide.md`：说明 cgroup v1/v2 两种布局的支持状态。
- `docs/client-native-linux-development.md`：记录本节实现的探测方式与已知边界。
- `doc/requirements/client-native-install-upgrade.md` §3.1：更新「支持范围」，说明 cgroup 要求。

## 7. 验收矩阵

**所有真机操作必须在空闲机器上进行。不得在运行生产客户端的机器（ali-jp、bw_hz、tencent-sh、macminim6）上试装。ali-sh 不动（见阶段三）。**

| 场景 | 环境 | 必须结果 |
| --- | --- | --- |
| cgroup v2 回归 | Ubuntu 24.04 | 安装、升级、忙拒绝行为与阶段一完成后一致，无回归 |
| cgroup v1 安装 | alinux3 或 CentOS 7（glibc 2.32 / 2.17） | `dpkg -i` 成功，客户端启动，中心在线 |
| cgroup v1 有遗留进程时拒绝 | 同上 | 构造一个子进程存活的服务状态，包操作被拒绝，且**拒绝信息明确** |
| cgroup v1 正常停止后放行 | 同上 | 无遗留进程时正常通过检查 |
| systemd 239 unit 加载 | 同上 | `systemctl daemon-reload` + `start` 成功，`systemctl status` 无 unknown 指令警告 |
| `StandardOutput=append:` 兼容 | 同上 | 确认日志实际写入 `/var/log/ai-device-client/client.log` |
| 不强杀验证 | 同上 | 停止时超时也不会 SIGKILL，`SendSIGKILL=no` 生效 |
| A→B 升级（v1） | 同上 | 同版本重装与跨版本升级均成功，配置/状态保留 |
| 自动化测试 | 开发机 | 新增 v1/v2 测试全绿；`device-runtime` 全量无回归；`ruff` 通过 |

### 证据要求

- 记录 `getconf GNU_LIBC_VERSION`、`systemd --version`、`stat -fc %T /sys/fs/cgroup`、mount 布局摘要。
- 忙拒绝场景要给出**客户端确实在忙**的证据（如正在执行的长任务的操作 ID 或进程状态），不能只看拒绝返回码。
- 记录改动前后的行为对比。
- 不含 token/密钥/完整配置。

### 验收方法教训（阶段一实测得出）

- **`dpkg --dry-run` 不校验依赖**：声明 `libc6 (>= 2.17)` 与 `2.39` 在 glibc 2.28 上都返回 rc=0，只有真实 `dpkg -i` 才能测出差异。不要用 dry-run 代替真实安装。
- 捕获 `dpkg` 真实返回码时不要写在管道后面（如 `cmd | tail; echo $?` 取到的是 `tail` 的码）。先重定向到文件，再单独检查 `$?`。
- 容器内验证依赖/运行时可得出可信结论，但**不能代替 systemd/cgroup 层的真机验收**——本阶段正是 cgroup 相关，必须用真实 v1 机器。

## 8. 实施顺序与阶段门禁

**顺序很重要，第 1 步是排雷。**

1. **先验证 systemd 239 能否加载现有 unit**（§6.3）。在 v1 机器上直接 `systemctl daemon-reload` + `start` 现有 unit，看是否报错。
   - 若 `StandardOutput=append:` 不被接受 → 先解决，这是能否继续的前提。
   - 这一步**不需要改代码**，可用手工 unit 文件测试。
2. 实现 cgroup 版本探测与路径解析（§5），先只做 Python 侧。
3. 补 Python 侧测试（§6.4）。
4. Python 侧在 v1 真机验证（安装、停止、忙拒绝）。
5. 同步修改 shell 侧 `STOP`（§6.2），补测试。
6. 完整链路验证：升级、同版本重装、忙拒绝。
7. 文档同步。
8. 提交。

### 阶段门禁

- [ ] cgroup v1 与 v2 上的「进程组为空」检查都真实生效（有测试与真机证据）
- [ ] 「无法识别 cgroup 版本」的行为已按 §5.4 选定的策略实现，且**不静默**
- [ ] Python 侧与 shell 侧语义一致，且各有测试
- [ ] 「目录不存在」分支有测试覆盖（当前无测试）
- [ ] systemd 239 上 unit 可正常加载，日志实际写入
- [ ] 不强杀行为在旧 systemd 上验证通过
- [ ] cgroup v1 真机完成安装 + 升级 + 忙拒绝
- [ ] Ubuntu 24.04 无回归
- [ ] 全量测试 + `ruff` 通过
- [ ] 文档已同步

**未满足任一项，不得声称本期完成。**

## 9. 禁止的完成声明

- 不得以「在容器里测过」代替真实 v1 机器验收——容器的 cgroup 布局与宿主机不同。
- 不得以「单元测试通过」代替「忙拒绝路径实际生效」。历史教训：本项目出现过 2000+ 测试全绿但真实恢复路径不可用的情况（`client-native-install-upgrade.md` §11 有记录），原因是测试把关键校验桩成了恒真。
- 不得声称「已支持所有 cgroup v1 系统」——只验证了具体测试机器。
- 不得把本期完成称为「支持 RPM」。
- 不得在验证不充分时声称「保护已完整」。

## 10. 交付物

1. `client_service.py` 的 cgroup 版本适配实现。
2. `build-client-deb.py` 的 `STOP` 同步修改。
3. 新增/修改的测试（含「目录不存在」分支）。
4. 真实 v1 与 v2 机器验收记录。
5. 同步后的文档。
6. 实现提交。

## 11. 安全与授权边界

- 不停止真实服务；重启真实服务需本次明确确认。
- 不 push，除非本次明确授权。
- 不在生产客户端机器上试装。
- 不碰 ali-sh。
- 临时目录与制品保留。
- 不输出/记录 token、密钥、完整配置。

## 12. 与阶段一、三的关系

- **依赖阶段一**：本阶段以 glibc 2.17 基线为前提；若阶段一未完成，v1 机器（如 CentOS 7）可能因 glibc 声明被 dpkg 拒绝，无法开展本阶段验收。
- **为阶段三铺路**：本阶段结束时，Linux 客户端应能在「glibc 2.17+、cgroup v1 或 v2、systemd 239+」的组合上运行。阶段三只需在此之上更换包格式。
- 本阶段**不改**包格式，仍用 DEB。即便目标机器可能没有 `dpkg`，本阶段验收仍需在该机器上安装 `dpkg`（或使用等价方式）——这是刻意的，目的是把「平台兼容」和「包格式」两件事分开验证。
