# 并发安装互斥：设计方案（待评审）

> 状态：**设计提案，未实施**。本文只给出方案与取舍，等待决定后再改代码。
> 对应未闭合项：`client-native-macos-development.md` 当前状态第 1 条、
> `client-native-macos-installer-write-mutex.md` 第四节第 3 条。
> 需求依据：`doc/requirements/client-native-install-upgrade.md` §9（新旧路径与并发）。

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

> Scope limit, stated so this check is not mistaken for an installation mutex:
> it is a point-in-time test that runs in preinstall. It cannot stop a second
> installer that also observed an absent stage from racing this one between the
> check and the payload write... Serialization against a concurrent installer is
> not provided here.

stage 路径带版本与摘要（`/opt/ai-device-client-stage-{version}-{digest[:12]}`），
所以：

- **同一 PKG 并发** → 撞同一 stage 路径 → 争抢同一目录
- **不同 PKG 并发** → stage 路径不同 → 各自写自己的 stage，但最终都 `RENAME_SWAP` 到同一运行路径

两种都能造成运行树被意外版本覆盖。

### 1.3 为什么不能简单加锁

`preinstall` 与 `postinstall` 是**两个独立进程**，脚本持有的锁在 preinstall 退出时释放。
payload 写入发生在两者之间，由 Installer 自己执行，不遵守我们的协议。

因此“让 postinstall 取锁”覆盖不到写入期，这已是既有结论，不再重复讨论。

## 二、可选方案

### 方案 A：全局安装锁 + preinstall 长期持有（**不可行**）

用 `flock` 在 preinstall 取得 `/var/run/ai-device-client-install.lock`，
但没有机制让它跨进程存活到 postinstall。要跨进程只能靠 daemon 或 systemd socket activation，
而 macOS 上引入常驻 daemon 只为安装互斥，成本与收益不匹配。

**否决。**

### 方案 B：让并发安装变成"可检测的失败"而非"静默错乱"（**推荐**）

不追求真正串行化，而是让第二个安装器**明确失败并说清原因**。

核心改动三处：

**B1. preinstall 用原子创建代替存在性检查**

现在是 `[ -e "$STAGE" ]` 检查后写 payload → TOCTOU 窗口。
改为 `mkdir` 原子占位（失败即说明已被占用）：

```sh
if ! mkdir "$STAGE" 2>/dev/null; then
    echo "PROGRAM_INSTALL_BUSY_V1: 另一个安装器正在使用该 stage 路径" >&2
    exit 1
fi
```

`mkdir` 是原子的，两个安装器只有一个能成功。失败方得到明确 token，
而不是让 Installer 抛出通用文案。

**B2. 用 rename 的重命名语义做"占领"，而非存在性检查**

`mkdir` 占位后，payload 需要写入该目录。但 Installer 的 payload 是直接解包到目标路径的，
无法先建目录再让它写进去——这需要核对 Installer 的实际行为。

**这是方案 B 的关键未知项**，必须先用一个探针包验证：
`payload` 目标目录预先存在但为空时，`installer` 是正常写入还是拒绝。

- 若正常写入 → B1 可行
- 若拒绝 → 退回方案 C

**B3. postinstall 输出结构化失败原因**

现在失败只写 `/var/tmp/ai-device-client-commit-status`，Installer 界面只显示通用文案。
把 token 同时 `echo` 到 stderr（Installer 会收集）并保留状态文件，
让“另一个安装正在进行”可被运维直接读到。

### 方案 C：串行化交给 launcher 侧的重入检查（**兜底**）

若 B2 探针证明 Installer 不接受预建目录，则退一步：

postinstall 在 `RENAME_SWAP` 前检查运行树是否在最近 N 秒内被改动
（比对 `build_id` 与 mtime），若是则拒绝并输出 token。

这只覆盖“两次安装几乎同时完成”的情况，不是真互斥，但能把静默错乱变成显式拒绝。
配合 B3 的报错改进，运维至少知道发生了什么。

### 方案 D：向用户暴露并发为不支持（**最小**）

不改代码，只在指南与发布说明写明“并发安装不支持，请串行执行”，
并在文档记录实测行为。

**这个方案不解决静默错乱**，只降低误用概率。若前三个方案都因成本否决，这是兜底。

## 三、推荐

**先做 B2 的探针实验，再用 B1+B3。**

理由：

1. B2 的未知项决定整个方案走向，且实验成本低（一个只含 preinstall 探针的 PKG，
   在 macminim6 上跑两次即可）。
2. 若 B2 成立，B1 是**很小**的改动（preinstall 脚本加几行），风险低。
3. B3 纯属可观测性改进，无风险。
4. 三者叠加后，并发安装从"静默错乱"变成"一方明确失败并说明原因"，
   这是与当前架构（无法跨进程持锁）相匹配的目标，不追求做不到的真互斥。

**不推荐**追求真正的串行化：那需要常驻 daemon 或 Installer 配合，成本远超收益，
且与"本地单用户自用"的定位不匹配。

## 四、若采纳 B，实施范围

| 文件 | 改动 |
| --- | --- |
| `scripts/build-client-pkg.py` | `PREINSTALL_RESIDUE` 改为 `mkdir` 原子占位；新增 `PROGRAM_INSTALL_BUSY_V1` token |
| `scripts/build-client-pkg.py` | postinstall 把失败 token 同时写 stderr |
| `tests/test_client_pkg_build.py` | 补齐两个安装器并发时的 preinstall 行为测试（可用 shell 层模拟） |
| `docs/client-native-macos-installer-write-mutex.md` | 第四节第 3 条更新为"已改为失败可检测" |
| `docs/client-native-macos-guide.md` | 故障处理补一条：看到 `PROGRAM_INSTALL_BUSY_V1` 该怎么办 |

**不改**：`commit_staged_program` 原语、`RENAME_SWAP` 语义、升级协调器。
并发问题在安装入口解决，不下推到发布原语。

## 五、需要先回答的问题

1. **B2 探针**：`installer -pkg` 对"payload 目标目录已存在但为空"的行为是什么？
   这决定 B1 是否可行。（需在 macminim6 上实测，属只读性质的低风险实验，
   但会真实安装一个探针包，需确认是否授权。）
2. **目标定位确认**：接受"并发时一方明确失败"而不是"并发时自动排队"？
   后者需要 daemon，我不建议。
3. **是否连同 D 一起做**：文档里写明并发不支持，成本近乎为零。

## 六、明确不做

- 不引入常驻 daemon 做安装互斥。
- 不修改 `commit_staged_program` 使其取锁（它无法验证并发，取锁会造成虚假保证）。
- 不在本次处理 Linux RPM/DEB 的并发（它们由包管理器自身互斥，且 RPM 侧已有
  `client_package_stop.sh` 的忙检查；macOS 无等价机制才是问题所在）。
