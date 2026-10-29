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
2. **布局并存优先于原地替换**：新版本落到新目录，旧目录保留，删除交由显式退役。
3. **helper 先于实例切换**：新 helper 必须能服务旧实例，再允许实例激活新 release。
4. **失败必须可诊断且不自动重放**：沿用现有 `unknown` / 永拒门禁语义。
5. **不解除既有永拒门禁**：dispatch、job recovery、候选启动等拦截保持。

## 4. 布局方案

### 4.1 版本目录

载荷目录保持 `packages/<package-version>-<payload-revision>/`，bootstrap 同理。升级新增副本，不覆盖：

```
/opt/ai-device/packages/0.2.2-native1/     ← 旧，保留
/opt/ai-device/packages/0.3.0-native2/     ← 新，升级写入
/opt/ai-device/bootstrap/0.2.2-native1/    ← 旧 helper，保留
/opt/ai-device/bootstrap/0.3.0-native2/    ← 新 helper
```

### 4.2 helper 当前版本指针

新增 `/opt/ai-device/bootstrap/current`（单行，root 所有），替代硬编码常量：

```python
TRUSTED_HELPER_BOOTSTRAP_ROOT = Path("/opt/ai-device/bootstrap")
# 读取 current，校验目录名合法且该路径为管理员可信，再拼 runtime
```

保留常量作为**兜底默认**，仅在指针缺失时使用（兼容既有安装）。指针本身也要过 `_trusted` 校验，不能成为新的绕过点。

### 4.3 dpkg/rpm 的路径所有权

`dpkg` 按文件清单管理，两个版本同时存在时旧文件仍属旧包。这带来一个问题：`rpm -U` 语义要求同一 `Name` 只能有一个版本。

**取舍**：包名保持唯一，升级时**旧版本目录转为 dpkg 未跟踪的残留**，由 prerm 中的显式退役流程清理。理由：

- 实例私有 runtime 已复制，旧目录不再被实例依赖
- 旧 helper 在被新 helper 取代后也不再需要
- 保留不受包管理的目录不引入安全边界变化（本机可信模型）

新 `preinst` 检测到既有布局时，改为**校验而非拒绝**：确认既有目录可读、无 `__pycache__` 冲突，记录到升级意图，交由事务继续。

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
