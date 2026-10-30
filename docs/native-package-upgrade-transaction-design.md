# 原生包级升级事务设计

> 状态：设计草案，尚未实现。  
> 约束：本轮磁盘仅余约 8G，不构建新 DEB/RPM 制品；真实包升级验收留待腾出空间后单独进行。

## 1. 问题

当前制品体系**只支持全新安装**。`build-native-linux.sh` 生成的 `preinst` 在检测到任何既有布局时立即退出：

```sh
if [ -e /opt/ai-device/packages ] || [ -L /opt/ai-device/packages ] \
|| [ -e /opt/ai-device/bootstrap ] || [ -L /opt/ai-device/bootstrap ]; then
 echo 'existing native versioned layout; helper transaction required' >&2
 exit 1
fi
```

三条守卫分别拒绝：任意既有版本目录、既有实例/registry 布局、同名载荷原地替换。因此 `dpkg -i` 新版本或 `rpm -U` 都会被拒。这是「真实跨版本升级无法验证」的直接原因——不是验证方法问题，是能力缺失。

## 2. 现状事实（决定设计边界）

| 事实 | 含义 |
| --- | --- |
| 实例 `run` 读取 `current` 指针，执行 release 私有 runtime | 实例**不依赖**包内解释器 |
| release 私有 runtime 由 `_prepare(runtime_source=...)` 复制而来 | 升级包不影响已装实例的运行时 |
| helper 硬编码 `TRUSTED_HELPER_BOOTSTRAP = /opt/ai-device/bootstrap/0.2.2-native1/runtime` | 管理员侧**强依赖**包布局与版本目录名 |
| `native_update` 止于 `package_applied_pending_activation` | 升级事务已有前半段，缺包替换与激活衔接 |
| 版本目录名同时编码包版本与载荷版本（`0.2.2-native1`） | 换版本即换目录路径，helper 常量需随之变化 |

核心矛盾：**实例侧与包解耦，管理员侧与包强耦合**。升级事务必须同时满足「不打断已装实例」和「让新 helper 指向新布局」。

## 3. 设计原则

1. **不删既有实例数据**：升级不触碰 `releases/`、`current`、`active.json`、`state/`、marker、registry。
2. **实例与包解耦优先于布局并存**：实例依赖自身 `releases/` 私有副本，不依赖 `/opt/ai-device/packages`。包管理器升级会删除旧版本路径（见 4.3 实测），因此保障实例存活靠的是私有副本，而不是保留旧目录。
3. **helper 先于实例切换**：新 helper 必须能服务旧实例，再允许实例激活新 release。
4. **失败必须可诊断且不自动重放**：沿用现有 `unknown` / 永拒门禁语义。
5. **不解除既有永拒门禁**：dispatch、job recovery、候选启动等拦截保持。

## 4. 布局方案

### 4.1 版本目录

载荷目录保持 `packages/<package-version>-<payload-revision>/`，bootstrap 同理。新版本使用新目录，但**包管理器升级会删除旧目录**（见 4.3）：

```
升级前: /opt/ai-device/packages/0.2.2-native1/
升级后: /opt/ai-device/packages/0.2.2-native2/   ← native1 已被 dpkg 删除
```

同一时刻只存在一个版本目录，是当前包清单（绝对路径）的必然结果，也是 preinst 拒绝「多版本并存」的原因。

### 4.2 helper 当前版本指针

新增 `/opt/ai-device/bootstrap/current`（单行，root 所有），替代硬编码常量：

```python
TRUSTED_HELPER_BOOTSTRAP_ROOT = Path("/opt/ai-device/bootstrap")
# 读取 current，校验目录名合法且该路径为管理员可信，再拼 runtime
```

保留常量作为**兜底默认**，仅在指针缺失时使用（兼容既有安装）。指针本身也要过 `_trusted` 校验，不能成为新的绕过点。

### 4.3 dpkg/rpm 的路径所有权（已用真实升级更正）

> 本节结论来自 2026-10-29 的隔离容器真实升级验收，推翻了本节此前的草案假设。

草案原假设「升级时旧版本目录转为 dpkg 未跟踪的残留」。**实测不成立**：`dpkg -i` 升级会先按旧包清单删除旧版本拥有的全部路径，再解开新版本。升级完成后 `packages/` 与 `bootstrap/` 下只剩新版本目录，旧目录直接消失：

```
升级前: packages/0.2.2-native2 + bootstrap/0.2.2-native2
升级后: packages/0.2.2-native3 + bootstrap/0.2.2-native3   ← native2 已被删除
```

实测命令与结果：老包 `e1a7`（不含 `native_layout`）升级到 `0.2.2-2` 成功，`ls /opt/ai-device/packages` 只剩 `0.2.2-native2`；`v2 → v3` 同样只剩 `0.2.2-native3`。

**后果**：

- 「并存」不是升级的自然结果，而是需要包刻意不拥有旧路径才能做到的额外工程。当前两包都用绝对路径写 `%files`/清单，做不到并存。
- 已装实例仍然安全，原因是实例 `releases/` 里是**私有 runtime 副本**，不引用 `/opt/ai-device/packages`。升级后实例私有解释器、`current`、`instance.json` 权限与内容均未改变，实测可正常 import 与 `--version`。
- 真正的风险不是「旧目录被删」，而是**升级窗口内新 helper 已就位、旧 helper 已不存在**：如果实例或 systemd 单元仍指向旧 bootstrap 路径，会 203/EXEC。这一点必须由事务与 `bootstrap/current` 一起解决，不能靠「旧目录保留」。

**因此**：

- 不再以「旧目录转为残留」为设计前提。
- 若确实需要回退能力，必须让包显式不拥有旧版本路径（或改用不受包管理的发布目录），并在事务中记录 release 与目录的对应关系；这需要单独立项，不在本轮范围内。
- `rpm -U` 与 `dpkg -i` 在这里语义一致：都会移除旧版本拥有的路径。RPM 的「同名只能一个版本」不是要绕开的限制，而是与 dpkg 行为一致的事实。

**preinst 的实际职责**相应收窄：它不负责「保留旧目录」，只负责判断既有布局能否被安全替换——拒绝不可分类布局、拒绝同版本原地重装、拒绝多版本歧义、拒绝 legacy 管理布局。这些已实现并有真实容器验证。

## 5. 事务状态机（已实现）

在 `native_update` 上扩展，沿用 registry 与 package lease。状态落在更新事务目录的
独立证据文件里，不改动既有 `.prepared/.apply-intent/.outcome/.recovery` 语义：

```
applied_package   .applied.json   包已装，记录包观测与切换前指针
helper_switched   .helper.json    bootstrap/current 已指向新版本并通过探测
pending_activation                包与 helper 就位，等待 owner 侧 activate
```

`updates reconcile` 是只读的，返回 `next_step` 建议：`apply` / `switch_helper` /
`activate` / `manual_review`。它**不执行**下一步——切换 helper 与激活都会改变宿主
上实际运行的东西。

### 5.1 为什么 helper 切换是独立状态

包安装会删除旧版本目录（见 4.3），所以 `apply` 成功的瞬间存在一个窗口：
`bootstrap/current` 仍指向已被删除的路径。这个窗口必须由事务自己关闭，不能留给
运维记忆。`switch-helper` 因此是显式动作，不是 apply 的副作用。

### 5.2 switch-helper 的执行契约

- 双 lease：instance lease + package-manager exclusive lease。指针是宿主全局的，
  两个实例同时切换必须互斥。
- 前置条件：`.applied.json` 存在且为 `applied_package`；包数据库仍显示目标版本。
- 写入：`native_bootstrap.publish_pointer` 原子 replace + 父目录 fsync，只接受目录名。
- 验证：用新解释器实际 `import device_runtime.native_helper`，失败则把指针写回旧值
  并报错——指向不可运行的树比指向旧树更糟。
- 幂等：`expected_version` 已是当前值时只观测、不写入，让丢失结果的调用方可以确认。

### 5.3 失败语义

任何一步失败停在明确状态，不自动回退，不自动重放：

| 失败点 | 记录 | 下一步 |
| --- | --- | --- |
| apply 前 | 无 `.applied.json` | `apply` |
| apply 成功、未切换 | `applied_package` | `switch-helper` |
| 切换探测失败 | `applied_package`（指针已回滚） | 人工核查 |
| 切换成功 | `helper_switched` | owner 侧 `activate` |
| 未知 | 既有 `unknown` 门禁 | 管理员裁决 |

### 5.4 未实现

- `activated` 不由 root 侧写入：激活是 owner 权限内的动作，仍走
  `native_prepare.activate_candidate`。root 侧只报告 `pending_activation`。
- 多实例共享包的排空（drain）仍拒绝，需要先实现多实例协调。
- 回退到旧版本不提供：包已删除旧目录，回退等于重新安装旧包。

## 6. 明确不做

- 不再保留旧版本目录：升级发布成功后删除被替换的树，卸载删除全部树与指针
  （见 6.1；实例依赖自身 `releases/` 副本，旧树无存活依赖方）
- 不自动回退到旧 release（软件回退 ≠ 数据恢复）
- 不做跨 glibc / 跨 Python 主版本迁移
- 不解除任何现有永拒门禁
- 不提供包级回退：旧目录已被删除，回退等于重装旧包

### 6.1 版本树回收（已实施）

单棵树约 330MB（`packages/` 230MB + `bootstrap/` 98MB）。实例从自身
`releases/` 私有副本运行，入口经 `bootstrap/current` 解析，因此被替换的树
没有存活依赖方，保留它们只占磁盘。既然本方案本就不提供包级回退，保留也换
不到回退能力。

- **升级**：`postinst`/`%post` 发布新树并切指针后，删除两个根下除当前指针
  所指之外的所有版本树。
- **卸载**：`postrm`/`%postun` 仅在 `remove`/`purge` 且无剩余实例时执行，
  先删 `current` 指针再删全部树与空父目录。指针若先于树消失，会让下次安装
  把布局判成损坏而拒绝，所以顺序不能反。
- **不跟随符号链接**：两个钩子都跳过指向树外的链接。实测把
  `packages/0.2.2-evil` 链到 `/opt/important` 后升级，目标数据完好、链接保留，
  分类器随后拒绝了这次安装，而不是静默删除外部数据。
- `prerm`/`%preun` 的实例、登记记录与 unit 门禁在删除之前执行，到达回收点时
  已确认没有消费方。

实测（隔离 systemd 容器）：v1→v2 升级后仅剩 v2 树、指针正确、零告警；
`remove` 后 327MB 全部消失；重装回到干净首次安装态；`purge` 同样全删。

### 6.1.1 跨大版本升级（实测）

0.2.2 → 0.3.0 真实升级通过：用临时副本改 `__version__` 构建 0.3.0 wheel、
重新组装 payload 并打成真实 DEB，在隔离 systemd 容器内从 0.2.2 升级。

- 升级 rc=0，零告警；旧 0.2.2 树被删除，仅剩 `0.3.0-x3`，指针正确切换
- 升级后运行版本确认为 0.3.0（`device_runtime.__version__`），真实子命令可用
- 卸载后 `/opt/ai-device` 整体消失；重装 0.3.0 回到干净首装态

制品仍是未签名本地开发包：`build-inputs.json` 的 `source_commit` 是基线提交，
精确字节由 wheel 定义，不构成发布闭环。

### 6.2 包完整性校验的已知限制

Staging 发布让两个包管理器都拥有 `%post` 会 rename 走的 `.staging` 路径，因此
`dpkg -V` 与 `rpm -V` 都把整个 payload 报为 missing（实测 9762 条）；rpm 另在
升级时逐个打印 "remove failed"。`%ghost` 不是解法：它会让 payload 根本不进包
（实测 106MB → 723KB）。

**决定：不为此新增机制。** 损坏不会静默发生，运行时自检已覆盖：

| 损坏形态 | 实际表现（实测） |
| --- | --- |
| 解释器缺失 | wrapper 以 75 退出并提示 "payload being replaced; retry" |
| 指针所指树缺失 | 分类器拒绝安装，报 "names no installed bootstrap" |
| 树不完整（缺 runtime/bundle） | 分类器拒绝，报 "is missing or incomplete" |
| 模块被删 | 解释器报 `No module named ...`，退出码 1 |

替代方案（签名清单、重做归属）都要引入密钥与信任根轮换链路，或退回非原子
发布，超出当前本地单用户环境的需要。真正需要防篡改时再按 6.1 之外的独立
设计处理，届时以签名清单为准，而不是指望 `-V`。

## 7. 实施顺序（完成情况）

| 步骤 | 状态 |
| --- | --- |
| 1. `bootstrap/current` 指针 + helper 读取改造 | 完成（`2bf3238`） |
| 2. `preinst` 条件放行 + 分类器随包携带 | 完成（`2bf3238`、`dd34e23`） |
| 3. `native_update` 状态机与 reconcile 分支 | 完成 |
| 4. 定向单元测试 | 完成 |
| 5. 双版本 DEB 真实升级验收 | 完成（Ubuntu 24.04，见 §9） |

## 8. 待决策

- **helper 切换是否需要管理员批准消费**：当前 switch-helper 由 root 侧事务自己完成，
  未接入签名批准链。
- **多实例共享同一包时的排空顺序**：当前 apply 直接拒绝多实例。这是既定行为，
  不是缺陷；要支持需先设计协调，未排期。

已决（不再待议）：

- **包级回退**：不提供。升级会删旧树，回退等于重装旧包（见 6.1）。
- **RPM 真实验收**：已完成。Rocky 9.3 隔离容器内 `dnf install rpm-build` 后实测
  `rpm -i`、`rpm -U` 连续升级、`rpm -e` 与重装；结论见 6.1。

## 9. 真实验收记录（2026-10-29）

环境：Apple container 1.5.0，`--platform linux/amd64 --network none --memory 3G`，
Ubuntu 24.04.5 镜像 `device-qual-systemd-amd64:probe`（digest 前缀 `7b5d57b39d1f`），
`/bin/sh -c 'sleep infinity'`，不使用 systemd。

制品：同一份 payload（含本次改动）构建两次，仅 tree 名与包版本不同：

| 包 | tree | Version |
| --- | --- | --- |
| `pkg-w1` | `0.2.2-native1` | `0.2.2-1` |
| `pkg-w2` | `0.2.2-native2` | `0.2.2-2` |
| `pkg-w3` | `0.2.2-native3` | `0.2.2-3` |

### 8.1 通过项

| 场景 | 结果 |
| --- | --- |
| 空布局安装 | 通过，生成 `packages/0.2.2-native1` 与 `bootstrap/0.2.2-native1` |
| v1 → v2 升级 | 通过，`dpkg -l` 显示 `0.2.2-2`，布局只剩 `0.2.2-native2` |
| **老包 e1a7 → 新包**（老包不含 `native_layout`） | **通过**，升级成功 |
| 同版本重装 | 拒绝，`0.2.2-nativeX is already installed` |
| 多版本并存 | 拒绝（`expected exactly one versioned payload`） |
| legacy `instances/` 存在 | 拒绝（`legacy management layout`） |
| 不完整版本树 | 拒绝（`payload ... is incomplete`） |
| 有实例时升级 v2 → v3 | 通过，实例私有 runtime、`current`、权限均未变 |

老包升级是本轮最重要的验证项：旧实现用**已安装解释器**执行 `-m device_runtime.native_layout`，
老包没有该模块，`dpkg -i` 会以 `No module named device_runtime.native_layout` 失败——
即所有存量设备都无法升级。改为 preinst 随包携带 `classify.py` 并显式执行后修复。

### 8.2 RPM 路径（2026-10-29 补充）

`rpmbuild` 来源：联网 Rocky 容器 `dnf -y install rpm-build`（RPM 4.16.1.3）。
构建容器联网，**验收容器仍为 `--network none`**。

Rocky Linux 9.3 / amd64 实测：

| 场景 | 结果 |
| --- | --- |
| `rpm -i` 安装 v2 | 通过，`current=0.2.2-native2`，helper 可用 |
| `rpm -U` 升级 v2 → v3 | 通过，指针切到 native3，旧目录删除，helper 仍可用 |
| 同版本重装 | rpm 自身按版本号拒绝 |
| 空指针 / `../escape` / 指针为目录 / 指针为符号链接 | lua 钩子全部拒绝 |
| legacy `instances/` 存在 | 拒绝 |
| 多版本并存 | 拒绝（`expected one versioned payload`） |

过程中修掉两个只有真实 `rpm -U` 才暴露的问题：

1. **RPM 缺少 `%post`**：dpkg 有 postinst 发布指针，spec 里没有任何对应脚本。
   结果是 RPM 升级后包是新的、指针还是旧的（指向已删除目录）。已补，且
   spec 经无引号 heredoc 生成，安装期变量需转义、tree 名用占位符替换。
2. **lua 里带锚点的 `string.match` 在 `%pretrans` 环境下不可靠**，会把合法
   的 `0.2.2-native1` 判成非法。改为逐字符校验，不依赖模式匹配。

### 8.3 未覆盖

- 未验证 systemd 下的 helper 单元切换（容器内无 systemd 服务运行）。
- 未验证签名与批准消费：本轮包未签名，走的是开发制品路径。
- 未验证升级中断（`dpkg`/`rpm` 在 unpack 阶段被 kill）后的恢复。
- 未验证跨大版本（`0.2.2` → `0.3.0`）升级。
