# 阶段一：Linux 客户端 glibc 2.17 基线对齐

## 1. 状态、来源与执行入口

- 状态：**需求已确认，尚未实施**。本文描述的是待做工作，不是已完成事实。
- 日期：2026-11-08 创建。
- 仓库：`ai-device`；实施 worktree `/Users/kongkongye/data/project/ai-device-wt-client-native-ses14497`，分支 `feat/client-native-install-ses14497`。
- 分析基线：`b10165b`。实施前检查最新源码和工作区，不回退基线、不覆盖他人修改。
- 需求讨论来源：`ses-14497`。本文包含实施所需全部决策，**不要求新会话读取历史聊天**。
- 上游需求：`doc/requirements/client-native-install-upgrade.md`（客户端交付主体）。本文只处理平台兼容扩展，不重定义安装升级主流程。

### 新会话开始顺序

1. 阅读全文、仓库规则（`.ai-assistant/rules/operational-safety.md`、`PROJECT_GUIDE.md`）。
2. 检查 Git 状态、分支、worktree 归属；确认 HEAD 与最新进展。
3. 沿第 4 节代码入口复核现状。**基线之后可能已有人改动**，以实际代码为准。
4. 按第 8 节顺序推进。
5. 保存提交、准确制品摘要和真机验收证据。

## 2. 背景：为什么需要这一期

当前 Linux 客户端 DEB 的依赖声明是：

```python
Depends: systemd, libc6 (>= 2.39), ca-certificates, util-linux
```

`2.39` 是构建机（Ubuntu 24.04）的 glibc 版本，**人工填写，从未与实际运行时需求核对**。

2026-11-08 对已发布 DEB0.4.2 做了实测：解包后解析 41 个 ELF 的 `.gnu.version_r`（glibc 依赖的权威来源），结果如下：

| 库 | 实测最高符号版本需求 |
| --- | --- |
| `libc.so.6` | **GLIBC 2.17**（由 `libpython3.13.so.1.0`、`python3.13`、`_dbm`、`liblzma` 引入） |
| `libm.so.6` / `libdl.so.2` / `librt.so.1` / `libutil.so.1` | 2.2.5 |
| `libpthread.so.0` | 2.12 |
| `libgcc_s.so.1` | GCC_4.2.0 |
| `libstdc++.so.6` | 无 GLIBCXX 依赖 |

即：**实际只需 glibc 2.17**，声明中的 2.39 高出 22 个小版本。

同时确认依赖链本来就是 2.17 基线：

- `packages/device-runtime/native/dependency-lock.json`：`target = {system: linux, machine: x86_64, python: cp313, glibc: "2.17"}`
- 13 个 wheel 全部是 `manylinux_2_17` 或更低（cffi/cryptography/pillow 为 `manylinux_2_17`，psutil 为 `manylinux_2_12`）
- `packages/device-runtime/src/device_runtime/dependencies.py:13`：`TARGET = {..., "glibc": "2.17"}`
- 同文件 `:18`：`SUPPORTED_GLIBC = ("2.17", "2.27")`

**结论：运行时早就按 2.17 构建，只有包头声明写错。**

后果：`dpkg` 在解包前按 `Depends` 拦截。任何 glibc 低于 2.39 的机器——包括 glibc 2.17/2.27/2.32/2.34 这些实际能跑的机器——都会被直接拒绝安装。

## 3. 目标与非目标

### 3.1 本期目标

1. 把 DEB 的 libc 依赖声明从 `2.39` 改为与实际运行时一致的 `2.17`。
2. 确保该声明有**自动校验**，防止后续再次漂移。
3. 在真实 glibc 2.17 与 2.32 机器上验证包可安装、客户端可运行。

### 3.2 本期非目标

- **不改任何二进制、wheel、Python 运行时**。本期是元数据修正。
- 不改 cgroup / systemd 兼容（阶段二）。
- 不新增 RPM（阶段三）。
- 不扩大其他架构（arm64 Linux）、musl/Alpine、Windows。
- 不改 macOS PKG 的 `min_macos` 声明逻辑（可参考同样思路，但不在本期实施范围）。
- 不做 apt 仓库发布。
- 不迁移任何现存设备。

## 4. 已查证现状与代码入口

**本节是基线 `b10165b` 的静态事实。实施时复核。**

| 入口 | 已确认事实 |
| --- | --- |
| `packages/device-runtime/scripts/build-client-deb.py:110` | `Depends: systemd, libc6 (>= 2.39), ca-certificates, util-linux` 写在 `control` 模板的 f-string 里。文件共 174 行。 |
| `packages/device-runtime/scripts/build-client-deb.py` `build(tree, output, version)` | 第 68 行起。写 `DEBIAN/control`、生成 `preinst`/`prerm`/`postinst`/`postrm`，最后 `dpkg-deb --root-owner-group --build`。 |
| `packages/device-runtime/src/device_runtime/dependencies.py:13` | `TARGET` 已是 `glibc: "2.17"`。 |
| `packages/device-runtime/src/device_runtime/dependencies.py:18` | `SUPPORTED_GLIBC = ("2.17", "2.27")`。 |
| `packages/device-runtime/src/device_runtime/installation.py:375-402` | 旧实例体系的宿主门禁：`glibc < minimum` 时拒绝。`minimum` 从**包内清单** `manifest["dependencies"]["manifest"]["target"]["glibc"]` 读取。 |
| `packages/device-runtime/src/device_runtime/client_package.py:20` `activate()` | **新客户端路径，不检查 glibc**。只做：unit 归属校验 → `validate_config()` → `check_conflicts()` → `service.start()` → 轮询 `package_ready` 与 `runtime_version()` 相等。 |
| `packages/device-runtime/src/device_runtime/client_service.py:158-182` `render_unit()` | 生成 systemd unit，含 `StateDirectory`/`LogsDirectory`/`KillMode=control-group`/`SendSIGKILL=no`。 |
| `packages/device-runtime/tests/test_client_deb_build.py` | 185 行，7 个测试：`test_build_hooks_and_layout`、`test_stop_explains_the_failure_consequence`、`test_invalid_version_before_writes`、`test_overrides_rejected_before_python_or_state_probe`、`test_same_interpreter_unrelated_service_refused_before_systemctl`、`test_postrm_rejects_unrelated_command_without_disabling`、`test_postrm_prunes_only_empty_directories`。**当前没有任何测试断言 `Depends` 内容。** |
| `doc/requirements/client-native-install-upgrade.md` §3.1 | 当前支持范围写的是「Ubuntu 24.04 amd64，DEB；不是所有 Linux 的支持承诺」。本期会扩大实际可安装范围，需同步该处的表述。 |

### 重要事实：新客户端路径没有 glibc 门禁

`installation.py` 的 glibc 检查属于**旧实例体系**（被 `upgrader.py`、`lifecycle.py`、`scripts/accept-centos7.py` 引用）。新客户端 `client_cli.py` 的模块 docstring 明确写着 `Client-only CLI, independent of bundle and legacy upgrade management`，`client_package.py`/`client_service.py`/`client_setup.py` 均不引用 glibc。

这意味着：

- 改 DEB 声明后，一台 glibc 2.10 的机器也能装（dpkg 不再拦，客户端自己也不拦）。
- **低于 2.17 的机器从未验证过**，装上去可能在加载 `libpython3.13.so.1.0` 时直接失败（动态链接器报 `GLIBC_2.17 not found`）。

因此本期必须补一个**客户端自身的 glibc 前检**，否则等于把「误拦」换成了「漏拦」。见 §6.2。

## 5. 支持基线定义

| 项 | 值 | 依据 |
| --- | --- | --- |
| 最低 glibc | **2.17** | 实测 ELF 需求 + wheel 标签 + 依赖锁，三处一致 |
| 架构 | x86_64 | 未变 |
| Python | CPython 3.13（包内自带） | 未变 |
| 包格式（本期） | DEB | 未变 |
| 已验证系统（目标） | CentOS 7 / glibc 2.17、Alibaba Cloud Linux 3 / glibc 2.32 | 见 §7 |

**「最低 glibc 2.17」不等于「所有 glibc 2.17 系统都支持」**。内核、cgroup、systemd 版本另有要求，阶段二处理。本期声明的是**动态链接层面的最低要求**。

## 6. 实施要求

### 6.1 修正 Depends 声明

`build-client-deb.py` 第 110 行：

```python
Depends: systemd, libc6 (>= 2.39), ca-certificates, util-linux
```

改为：

```python
Depends: systemd, libc6 (>= 2.17), ca-certificates, util-linux
```

**不要把这个字面量再复制一份到别处**。应当从单一来源取值。可选方案：

- 方案 A（推荐）：在 `build-client-deb.py` 顶部定义 `MINIMUM_LIBC = "2.17"` 常量，f-string 引用。
- 方案 B：从 `dependencies.py` 的 `TARGET["glibc"]` 导入。

优先 A。理由：构建脚本应当能在不导入整个 `device_runtime` 包（及其依赖）的情况下运行；`build-client-deb.py` 目前只 import `argparse/shutil/subprocess/pathlib`，保持这个独立性。

若选 B，需确认构建机上 `device_runtime` 可导入且不产生副作用。

### 6.2 补客户端自身的 glibc 前检

**这是本期必须做的第二处改动，不能省略。**

理由见 §4「重要事实」：现有新客户端路径完全信任包管理器。声明放宽后，低于 2.17 的机器会一路装到启动才失败，且失败信息是动态链接器的原始报错，很难定位。

要求：

1. 在客户端启动早期（建议 `client_cli.py` 的入口，或 `client_package.activate()` 开头）检查宿主 glibc 版本。
2. 检查方式：`platform.libc_ver()`，或解析 `os.confstr("CS_GNU_LIBC_VERSION")`。**不要**用 `ctypes.util.find_library` 之类的间接方式。
3. 低于 2.17 时给出**明确、可操作的错误**，包含：当前版本、要求版本、无法运行的原因。不要只抛裸 `RuntimeError`。
4. **只检查、不尝试降级或绕过**。不要在失败时静默改用系统 Python 或其他运行时。

**需要判断的点**：这个检查放在 `activate()` 里，还是放在 `client_cli` 主入口里？

- 放 `activate()`：安装时就拦住，但运行时不拦（服务手工启动可绕过）。
- 放 `client_cli` 主入口：安装和运行都拦，但每次启动多一次检查开销（可忽略）。

**推荐放 `client_cli` 主入口**，或两者都放。理由：客户端是长期运行的服务，运行时保护比安装时保护更本质；且 `activate()` 在「首次安装但未配置」时会提前 return（见 `client_package.py:21-22`），放那里会漏掉这个分支。

实施时按实际代码结构决定，但必须在测试中覆盖「低于 2.17 时拒绝」和「等于 2.17 时放行」两种情况。

### 6.3 补依赖声明的自动校验测试

现有 `test_client_deb_build.py` 的 7 个测试**没有任何一个断言 `Depends` 内容**，这正是 2.39 能长期存在的原因。

要求新增测试：

1. 构建一个 DEB（复用现有 `test_build_hooks_and_layout` 的构造方式），读取 `DEBIAN/control`，断言 `Depends` 行包含 `libc6 (>= 2.17)`。
2. 断言**不包含** `2.39`（防止回退）。
3. 若采用 §6.1 方案 A 的常量，断言该常量与 `dependencies.py` 的 `TARGET["glibc"]` 一致。这是跨文件一致性检查，防止两处再次漂移。

第 3 条是关键：如果只在 `build-client-deb.py` 里写 `"2.17"`，将来 `dependencies.py` 升级基线时，两处会再次不一致。

### 6.4 文档同步

需要更新的文档（按实际情况判断，不机械全改）：

- `doc/requirements/client-native-install-upgrade.md` §3.1：支持范围表述。当前写「Ubuntu 24.04 amd64」，需说明实际 glibc 最低要求是 2.17，并指出 cgroup/systemd 的额外限制留待阶段二。
- `docs/client-native-linux-guide.md`：若有 glibc 相关描述需同步。
- `docs/client-native-linux-development.md`：同上。
- `packages/device-runtime/README.md`：若提及平台要求。

**不要**把 `docs/glibc217-implementation.md` 当作本期依据。该文档记录的是 2026-10-26 针对**旧 0.2.x 架构**（`device-runtime` + `wx_test` 实例体系）的 glibc 2.17 实施，与当前 0.4.x native 客户端是两套体系。可参考其**方法**（manylinux2014 构建、ELF 校验），但**不可照抄结论**，也不要修改该文档。

## 7. 验收矩阵

所有真机操作遵守 `.ai-assistant/rules/operational-safety.md`。不得在运行生产客户端的机器上做构建或试装实验。

| 场景 | 环境 | 必须结果 |
| --- | --- | --- |
| DEB 构建 | 开发机 | `build-client-deb.py` 成功产出包；`dpkg-deb -I` 显示 `libc6 (>= 2.17)` |
| 低于基线拒绝 | glibc < 2.17（可用 CentOS 6 容器或构造测试） | 安装或启动被明确拒绝，错误信息含当前/要求版本 |
| 基线边界放行 | 真实 glibc 2.17（CentOS 7） | `dpkg -i` 成功；客户端可启动并连中心 |
| 中间版本放行 | 真实 glibc 2.32（Alibaba Cloud Linux 3 或同级） | `dpkg -i` 成功；客户端可启动并连中心 |
| 已有环境回归 | Ubuntu 24.04 | 安装升级行为与改动前一致，无回归 |
| 自动化测试 | 开发机 | 新增测试全绿；`device-runtime` 全量测试无回归；`ruff` 通过 |

### 验收证据要求

- 记录：系统版本、glibc 版本、包 SHA256、执行命令、退出码、关键输出。
- glibc 版本用 `ldd --version` 或 `getconf GNU_LIBC_VERSION` 记录原文。
- 不得只写「安装成功」，必须给出实际的服务状态与中心在线确认。
- 证据不得包含 token、密钥、完整配置内容。

### 已知的验收限制

- glibc 2.17 真机（CentOS 7）上，**cgroup v1 与 systemd 219 会在阶段二才处理**。本期验收 glibc 2.17 时，可用**两种方式**之一：
  - (a) 直接试装并如实记录遇到的 cgroup/systemd 问题，作为阶段二的输入；
  - (b) 若 cgroup 问题阻塞到无法完成 glibc 验证，改在容器内验证动态链接层面（`ldd` 全依赖可解析），并明确标注「未做完整服务生命周期验收」。
  - **不得**为了让验收好看而在 CentOS 7 上安装 cgroup v2。

## 8. 实施顺序与阶段门禁

1. **先改声明 + 常量**（§6.1），补一致性测试（§6.3）。
2. **补客户端 glibc 前检**（§6.2），补对应测试。
3. 本地构建验证：`dpkg-deb -I` 确认 `Depends`。
4. 在 glibc 2.32 机器试装（比 2.17 容易获得，先验证「放宽后确实能装」）。
5. 在 glibc 2.17 机器试装（验证基线边界）。
6. 同步文档（§6.4）。
7. 提交。

### 阶段门禁

本期完成必须同时满足：

- [ ] `build-client-deb.py` 产出包的 `Depends` 为 `libc6 (>= 2.17)`
- [ ] 常量与 `dependencies.py` 一致性测试存在且通过
- [ ] 客户端 glibc 前检存在，拒绝与放行两种情况都有测试
- [ ] 至少一台真实 glibc 2.17 机器完成安装并可启动（若被阶段二问题阻塞，须明确记录阻塞点）
- [ ] 至少一台真实 glibc 2.32 机器完成安装并可启动
- [ ] Ubuntu 24.04 无回归
- [ ] `device-runtime` 全量测试通过，`ruff` 通过
- [ ] 文档已同步

**未满足任一项，不得声称本期完成。**

## 9. 禁止的完成声明

- 不得以「单元测试通过」代替真实机器安装验收。
- 不得以「`dpkg -i` 退出码 0」代替「客户端实际运行且中心在线」。
- 不得声称「已支持所有 glibc 2.17 系统」——本期只验证了具体的测试机器。
- 不得把本期完成称为「支持 RPM」或「支持 cgroup v1」。
- 不得声称制品已可公开分发（仍未签名）。

## 10. 交付物

1. `build-client-deb.py` 的声明修正与常量。
2. 客户端 glibc 前检实现。
3. 新增/修改的测试。
4. 同步后的文档。
5. 真实机器验收记录（含命令、输出、SHA256、平台版本）。
6. 实现提交。

## 10.1 实施与验收结果（2026-11-09）

代码提交 `6034ee6`。制品 `ai-device-client_0.4.3_amd64.deb`，SHA256 `140b4e58fc4ebce0e9ff19e0d4fa5d15078e42d29dcd900f672e030d382725af`，在 tencent-sh 上用真实 `dpkg-deb` 构建。

**声明验证**（真实 dpkg 产出）：`Depends: systemd, libc6 (>= 2.17), ca-certificates, util-linux`。

**关键对比实验**（Debian 10，glibc 2.28，真实 `dpkg -i`）：同一宿主、只改声明，结果不同。

| 声明 | 真实安装结果 |
| --- | --- |
| `libc6 (>= 2.17)` | **rc=0**，`Setting up` 成功 |
| `libc6 (>= 2.39)` | **rc=1**，`dependency problems - leaving unconfigured` |

这证明修复前那种包在 glibc 2.28 上会被拒绝，而该机实际能跑。

**重要方法结论**：`dpkg --dry-run` **不校验依赖**——两种声明都返回 rc=0，只有真实 `dpkg -i` 才能测出差异。后续阶段验收不要用 dry-run 代替真实安装。

**真实 glibc 2.17 运行验证**（CentOS 7.9 / glibc 2.17，从真实 DEB 解出 payload）：

```
包内 Python              → Python 3.13.17
前检                     → detected: (2,17) / PASS 放行
核心依赖导入             → device_runtime.client, mcp_server_bash.core,
                           mcp_server_fs.core, psutil, pydantic,
                           websockets, PIL.Image 全部正常
图像编解码               → PNG/JPEG/WEBP 全部 ok (32,24)
client_cli 入口          → --help 正常
前检拒绝路径（模拟 2.12）→ 拒绝并给出所需/检测版本
```

**生产环境安全**：tencent-sh 生产客户端全程未受影响（`ii 0.4.2`、active、MainPID 2393177 前后一致），只执行 dry-run 未安装；CentOS7/Debian10 均为一次性容器。

**本轮仍未验证**（不得据此声称阶段一全部完成）：

- 真实 systemd 服务生命周期（装 unit、启动、就绪、升级、卸载）。CentOS7 容器无 systemd；alinux3 无 dpkg 且 `/tmp` 仅 944M(tmpfs)。该层与 cgroup 紧密相关，实质上属于阶段二验收范围。
- glibc 2.32 真机安装（ali-sh 缺 dpkg 与磁盘空间）。

容器与真实发行版在 systemd/cgroup 上存在差异，上述结果不覆盖服务层。

## 11. 安全与授权边界

- 不停止真实服务；重启真实服务需用户针对本次操作明确确认。
- 不 push，除非获得针对本次的明确授权。
- 不碰运行生产客户端的机器（ali-jp、bw_hz、tencent-sh、macminim6 等）做试装。
- 不碰 `ali-sh`（旧 0.2.3 在线实例，详见阶段三）。
- 临时目录与制品保留，不擅自清理历史证据。
- 不输出、不记录 token/密钥/完整配置。

## 12. 与后续阶段的关系

- 阶段二处理 cgroup v1 与 systemd 旧版本兼容。本期验收时若遇到这些问题，**记录为阶段二输入，不要在本期修**。
- 阶段三处理 RPM 打包。RPM 的 libc 依赖声明应复用本期确定的 2.17 基线与单一来源常量。
- 本期结论（实测 ELF 需求 = 2.17）是阶段二、三的共同前提。
