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

## 5. 事务状态机

在现有 `native_update` 基础上扩展，沿用 `registry` 与 lease：

```
prepared          — 已验签、已提取到隔离目录
applied_package   — 新包已安装，旧布局仍在
helper_switched   — bootstrap/current 指向新 helper
activated         — 实例 current 指向新 release
pending_activation — 包已装，等待显式 activate（现有终态）
```

关键门禁：

- `prepared → applied_package` 需要补的正是**解除 preinst 拒绝**，改为条件放行
- `helper_switched` 前必须验证新 helper 可服务旧实例（只读探测）
- 任何一步失败停在明确状态，**不自动回退**，与现有永拒语义一致

## 6. 恢复语义

| 失败点 | 状态 | 处理 |
| --- | --- | --- |
| 包安装前 | `prepared` | 保留隔离目录，可重试或显式退役 |
| 包安装后、helper 切换前 | `applied_package` | 旧 helper 仍可用；可显式切换或停留 |
| helper 切换后、激活前 | `helper_switched` | 实例仍跑旧 release，可继续或回退指针 |
| 激活后 | `activated` | 完成 |
| 未知 | `unknown` | 保留现场，不重放，需管理员裁决 |

新增 `updates reconcile` 需要的分支：识别 `applied_package` 与 `helper_switched` 并给出下一步建议，但**不自动执行**。

## 7. 明确不做

- 不自动清理旧版本目录（保留数量策略未定，且属数据删除）
- 不自动回退到旧 release（软件回退 ≠ 数据恢复）
- 不做跨 glibc / 跨 Python 主版本迁移
- 不解除任何现有永拒门禁
- 本轮不构建新制品，不做真实 `dpkg -i` 升级验收

## 8. 实施顺序

1. `bootstrap/current` 指针 + helper 读取改造（含兜底与校验）
2. `preinst` 从拒绝改为条件放行，保留对未知布局的拒绝
3. `native_update` 状态机扩展与 reconcile 分支
4. 定向单元测试（状态迁移、失败停留、指针校验绕过）
5. 待腾出磁盘后：构建双版本 DEB，真实升级验收

## 9. 待决策

- **旧版本目录保留几个**：影响磁盘，需用户定策略
- **新包是否要求旧 helper 已被新 helper 取代**：决定升级是否强制两阶段
- **RPM 的 `rpm -U` 是否需要同版本并存**：RPM 不支持同名多版本共存，可能需改为 `rpm -i` 或不同 Name

## 8. 真实验收记录（2026-10-29）

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

### 8.2 未覆盖

- 未验证 systemd 下的 helper 单元切换（容器内无 systemd 服务运行）。
- 未验证 RPM 路径：`rpm -U` 的同名语义只做了脚本层检查，没有真实 Rocky 容器升级。
- 未验证签名与批准消费：本轮包未签名，走的是开发制品路径。
- 未验证升级中断（`dpkg` 在 unpack 阶段被 kill）后的恢复。
