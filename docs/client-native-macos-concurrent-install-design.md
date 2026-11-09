# 并发安装互斥：设计方案与探针结论

> 状态：**B1–B2 已实施（`fac99bb`），B3 与方案 D 已随指南更新完成**。
> 对应未闭合项：`client-native-macos-development.md` 当前状态第 1 条、
> `client-native-macos-installer-write-mutex.md` 第四节第 3 条。
> 需求依据：`doc/requirements/client-native-install-upgrade.md` §9（新旧路径与并发）。
> 实施差异：token 文件实际落在管理目录而非原设计的 `/var/tmp`，原因见 B2 节。

## 一、问题与实测证据

### 1.1 已有实测

macminim6 真机并发安装两次同一 PKG：

| 观察 | 结果 |
| --- | --- |
| 结果 | 一成一败 |
| 失败方报错 | “脚本执行出错”（Installer 通用文案，无具体原因） |
| `/opt` 残留 | 两个 stage 目录 |
| 程序树 `build_id` | 属更早的包，不是本次安装的 |

即：**并发下状态会乱，且失败原因对运维不可读**。

### 1.2 代码现状（已核实）

`build-client-pkg.py` 的 `PREINSTALL_RESIDUE` 自己写明了边界：

> it cannot stop a second installer that also observed an absent stage from racing this one between the check and the payload write... Serialization against a concurrent installer is not provided here.

stage 路径带版本与摘要（`/opt/ai-device-client-stage-{version}-{digest[:12]}`），所以：

- **同一 PKG 并发** → 撞同一 stage 路径 → 争抢同一目录
- **不同 PKG 并发** → stage 路径不同 → 各自写独立 stage，但最终都 `RENAME_SWAP` 到同一运行路径

两种都能造成运行树被意外版本覆盖。

### 1.3 为什么不能简单加锁

`preinstall` 与 `postinstall` 是**两个独立进程**，脚本持有的锁在 preinstall 退出时释放。payload 写入发生在两者之间，由 Installer 自己执行，不遵守我们的协议。

因此“让 postinstall 取锁”覆盖不到写入期。

## 二、探针实测结论（macminim6，2026-11-09）

全部实验在 `/tmp/probe14497*` 隔离路径进行，未接触 `/opt/ai-device-client`。
实验后已删除探针包与目标目录并 `pkgutil --forget`，真实客户端 PID 7692 全程未受影响。

| 编号 | 问题 | 结果 |
| --- | --- | --- |
| P1 | 目标不存在时安装 | 成功，rc=0 |
| P2 | **目标已存在但为空** | **成功，Installer 报 `Upgrading`，rc=0** |
| P3 | 目标已存在且非空 | 成功，且**外部文件被保留**（`foreign.txt` 仍在） |
| P4 | 8 进程并发 `mkdir` 同一路径 | **恰好 1 个成功，7 个失败** |
| P5 | preinstall 的 stderr 是否显示给用户 | **否**，终端只有通用文案 |
| P6 | preinstall 的 stderr 去哪里了 | 写入 `/var/log/install.log`，来源 `package_script_service` |
| P7 | 失败时的退出码 | `installer` 返回 1（不经管道时） |

### 关键结论

1. **方案 B 的前提成立。** P2 证明 Installer 接受预先存在的空目录，`mkdir` 占位不会被拒绝。
2. **`mkdir` 原子性可靠。** P4 在 8 进程竞态下恰好一个胜出，足以做互斥占位。
3. **P3 说明占位与残留必须区分。** 非空目录不会被 Installer 清理，外部内容会被合并进发布树。所以现有的存在性检查不能简单删除，要与 `mkdir` 占位结合。
4. **原 B3 设想错了。** 原先以为 `echo` 到 stderr 能让 Installer 界面显示原因；P5 证明不会，P6 进一步表明 stderr 实际写进了 `/var/log/install.log`。

## 三、方案对比

### 方案 A：全局 flock 跨 preinstall/postinstall（**否决**）

两脚本是独立进程，锁无法存活到 payload 写入期。要跨进程只能靠常驻 daemon 或 socket activation，为安装互斥引入 daemon 成本与收益不匹配。

### 方案 B：`mkdir` 原子占位 + 双通道报告（**采用**）

见第四节。

### 方案 C：swap 前比对 build_id/mtime 再拒绝（**不再需要**）

P2 已证明 B 可行，C 作为兜底不再需要。仅记录方案演进。

### 方案 D：文档声明不支持并发（**建议同时做，成本近零**）

写明并发安装需串行执行。与 B 不冲突，降低误用概率。

## 四、采用方案的具体内容

### B1. preinstall 用 `mkdir` 做原子占位，区分三种情形

```sh
if mkdir "$STAGE" 2>/dev/null; then
    :   # 本次新建，可以继续安装
elif [ -d "$STAGE" ] && [ -z "$(ls -A "$STAGE" 2>/dev/null)" ]; then
    echo "PROGRAM_INSTALL_BUSY_V1: stage 为空但已被占用，可能有并发安装正在进行" >&2
    exit 1
else
    echo "PROGRAM_INSTALL_RESIDUE_V1: stage 路径存在且非空，内容无法归属；请清理后重试" >&2
    exit 1
fi
```

这三种情形对应：本次新建（放行）、空目录被占（并发）、非空残留（拒绝）。

**边界说明**：`mkdir` 成功只意味着本次安装器新建了该目录，不能证明全程无并发——并发的另一方会在 `mkdir` 失败后进入拒绝分支。这是“让冲突可检测”，不是“让并发排队”。

### B2. 失败原因写入可见位置

P5/P6 证明 stderr 不进终端，只写 stderr 等于用户看不到。改为：

| 位置 | 内容 | 用途 |
| --- | --- | --- |
| `/var/log/install.log`（自动） | 脚本 stderr | 事后诊断，无需改代码 |
| `.../ai-device-client-management/preinstall-token` | 失败 token | 运维可立即读取，不依赖日志格式 |
| `.../ai-device-client-management/install-status` | 复用已有机制 | 与现有 postinstall 结果口径一致 |

**与原设计的差异**：原计划写 `/var/tmp/ai-device-client-install-token`。读代码后发现
`COMMIT_STATUS_DIR` 的注释已明确指出 `/var/tmp` 全局可写且开机不清理，本地用户可
预置符号链接让 root 脚本写穿。改用管理目录；创建时用 `0700`，与 postinstall 一致，
不因报错而放宽已有目录权限。

### B3. 指南明确告知去哪看

用户指南故障处理补一条：安装报“脚本执行出错”时，先看 `/var/log/install.log` 与 `/var/tmp/ai-device-client-install-token`，再对照 token 含义。这是 P5 的直接后果：原因不在界面上。

## 五、实施范围（已完成）

| 文件 | 改动 |
| --- | --- |
| `scripts/build-client-pkg.py` | `PREINSTALL_RESIDUE` 改为 `mkdir` 占位 + 三分支；新增两个 token |
| `scripts/build-client-pkg.py` | 失败时写 `/var/tmp/ai-device-client-install-token` |
| `tests/test_client_pkg_build.py` | 补 preinstall 三分支的 shell 层测试 |
| `docs/client-native-macos-installer-write-mutex.md` | 第四节第 3 条更新 |
| `docs/client-native-macos-guide.md` | 故障处理补“去哪看原因” |

**不改**：`commit_staged_program` 原语、`RENAME_SWAP` 语义、升级协调器。并发问题在安装入口解决，不下推到发布原语。

## 六、决策记录

三项均已决定：

1. **目标定为“并发时一方明确失败”，不做排队。** 排队需常驻 daemon，与本地单用户自用定位不匹配。
2. **连同方案 D 一起做**：用户指南已写明并发安装不受支持。
3. **B1–B3 已实施**，见第八节。

## 七、明确不做

- 不引入常驻 daemon 做安装互斥。
- 不修改 `commit_staged_program` 使其取锁（它无法验证并发，取锁会造成虚假保证）。
- 不处理 Linux RPM/DEB 的并发：它们由包管理器自身互斥，RPM 侧另有 `client_package_stop.sh` 的忙检查；macOS 无等价机制才是问题所在。

## 八、实施结果（`fac99bb`）

实际改动与前述设计一致，差异仅有 token 位置（管理目录而非 `/var/tmp`）。

| 验证项 | 结果 |
| --- | --- |
| 10 进程抢同一 stage | 1 放行、9 `BUSY`、0 误判为残留 |
| 全新路径 | 放行，rc=0，目录被创建 |
| 空目录已存在 | `BUSY`，rc=1 |
| 非空目录 | `RESIDUE`，rc=1 |
| 符号链接 | `RESIDUE`（not a directory），rc=1 |
| 管理目录权限 | `0700`，未因报错放宽 |
| 受影响测试 | 96 passed（build-client-pkg + nopayload）|
| 全量测试 | **2204 passed / 20 skipped** |
| ruff | 通过 |

仍存在的边界：本方案让并发冲突**可检测、可说明**，不让并发安装排队。
第二个安装器失败后，第一个仍可能已把程序树换成它自己的版本。
要真正串行化需常驻 daemon 或安装器配合，本方案不做。

## 九、探针残留说明

探针包 `local.probe14497.test`、`local.probe14497.p2` 已 `pkgutil --forget`；
`/tmp/probe14497*` 全部删除。设备当前状态与探针前一致：客户端 PID 7692 运行中，
`/opt` 下仅 `ai-device-client`、`ai-device-client-upgrade`、`homebrew`。
