# 安装与升级命令简化改造设计

> 状态：设计稿，未实施
> 范围：`ai-device-native-install` 与 `ai-device-native-update` 两个入口的 CLI 层
> 目标：减少必须人工提供且容易传错的参数，合并可安全合并的步骤

## 1. 现状与问题

### 1.1 全新安装

需要两步，且第二步有 8 个参数：

```bash
dpkg -i ai-device-native-client_0.3.2-2_amd64.deb
/usr/bin/ai-device-native-install \
  --payload /opt/ai-device/packages/0.3.2-native2 \
  --root /var/lib/device-managed/<instance> \
  --instance-id <instance> \
  --server-url wss://device.example.com \
  --username <实例账号> \
  --environment-file /etc/device-client/<instance>.env \
  --unit-dir /etc/systemd/system \
  --registry /var/lib/ai-device/native \
  --register --start
```

问题：

- `--payload` 依赖先执行 `dpkg -i`，再从固定路径取出一段目录名。装包和建实例之间会留下「包已装但没有实例」的中间状态。
- `--start` 必须同时给 `--register`，`--register` 又必须给 `--username` 与 `--environment-file`。三层隐式依赖靠人记。

### 1.2 后续升级

需要三步，第二步有 8 个必需参数：

```bash
systemctl stop ai-device-client-<instance>.service
/usr/bin/ai-device-native-update \
  --registry /var/lib/ai-device/native \
  --instance-id <instance> \
  --operation-id upg032b01 \
  --package /var/lib/ai-device/incoming/xxx.deb \
  --envelope /var/lib/ai-device/incoming/release-xxx.json \
  --release-id rel-0.3.2-20261101b \
  --bootstrap-version 0.3.2 \
  --bootstrap-tree 0.3.2-native2 \
  --stopped
systemctl start ai-device-client-<instance>.service
```

问题：

- 8 个必需参数里只有 2 个是真正的输入（包与信封），其余 6 个都能从注册表、信封或包名推导。
- `--bootstrap-version` 与 `--bootstrap-tree` 是两个不同概念（前者与 `bootstrap_min` 比较，后者是指针目录名），混用会导致升级成功后收据被拒。这个错误已在真机实际发生过一次。
- 停服务与启服务靠人工把握，`--stopped` 只声明状态不执行动作，正确但容易漏。

## 2. 设计原则

1. **不削弱既有安全门禁**。`--stopped` 的现有语义（只确认不代劳）保持不变，新增能力用新开关显式表达。
2. **推导失败必须显式报错，不猜**。能唯一确定的才自动填，有歧义就要求人工指定。
3. **显式参数优先于推导**。手工传入的值永远覆盖自动推导结果。
4. **保持向后兼容**。现有调用方式继续可用，不改已有参数含义。
5. **推导结果要可审计**。dry-run 输出实际使用的参数集，便于核对。

## 3. 升级命令改造

### 3.1 参数推导规则

| 参数 | 现状 | 改造后 |
| --- | --- | --- |
| `--registry` | 必填 | 默认 `/var/lib/ai-device/native` |
| `--instance-id` | 必填 | 注册表只有一个实例时自动取；多个则报错要求指定 |
| `--operation-id` | 必填 | 默认 `<UTC时间戳>-<随机6位>`，保证唯一 |
| `--release-id` | 必填 | 取信封 `payload.release_id`，与显式值冲突时报错 |
| `--bootstrap-version` | 必填 | **从设备注册表/当前 bootstrap 推导，不取信封** |
| `--bootstrap-tree` | 可选 | 从信封 `payload.package.payload_root` 提取 tar 文件名（去 `.tar`） |
| `--package` | 必填 | 保持必填 |
| `--envelope` | 必填 | 保持必填 |

### 3.2 两个 bootstrap 参数的语义差异（重要）

这两个名字相近的字段含义完全不同，是本改造最容易出错的地方：

- `bootstrap_version`：**设备上当前 bootstrap 的兼容版本**，只用于与信封 `bootstrap_min`
  比较，判断设备是否足够新。
- `bootstrap_tree`：**升级后指针要指向的目录名**，来自目标包自身。

因此推导来源不同：

- `bootstrap_tree` 可以安全地从已认证信封的 `payload_root` 提取。
- `bootstrap_version` **不能**取信封的 `bootstrap_min`。若两者同源，
  `Version(bootstrap_version) < Version(bootstrap_min)` 恒为假，这项兼容性检查
  就退化成永远通过，形同删除。

`bootstrap_version` 应来自设备侧事实，优先级为：

1. 显式传入
2. 从注册表记录读当前 bootstrap 版本
3. **都取不到则报错，不提供默认值**

若注册表未记录该字段（现有记录里确实没有），则本参数保持必填，不强行推导。
这属于本方案有意保留的人工输入，不是没做完。

### 3.3 推导顺序

`payload_root` 形如 `opt/ai-device/payloads/0.3.2-native2.tar`，提取最后一段并去掉 `.tar` 即为 tree 名。该值由签名覆盖，可信。

推导顺序：

```
读信封 → 验签
       → 从已认证 payload 提取 release_id / payload_root（tree）
       → 读注册表确定 instance_id 与 root
       → 生成 operation_id
       → 设备侧事实推导 bootstrap_version（推导不到则要求显式传入）
       → 人工显式值覆盖
```

验签必须先于推导：不能从未认证的 JSON 里取字段。

信封内的 `bootstrap_min` 只用于与设备侧 bootstrap 版本做兼容性比较，
**不作为 `bootstrap_version` 的推导来源**，原因见 3.2。

### 3.4 新增开关

```
--stop-and-start
```

语义：工具负责在升级前停止实例的 systemd unit，升级成功后重新启动；失败则不启动并保留现场。

与 `--stopped` 互斥。两者都不给时保持现状（要求调用方自行停止，拒绝执行）。

失败时的服务状态约定：

| 阶段 | 失败行为 |
| --- | --- |
| 停止失败 | 终止，不进入升级 |
| 升级失败（任何步骤） | 不启动服务，保留已停止状态，输出诊断 |
| 升级成功但启动失败 | 报告成功与启动失败两件事，退出码非 0 |

绝不自动回滚到旧版本。回滚是独立决策，需要人工判断。

### 3.5 改造后用法

```bash
ai-device-native-update \
  --package x.deb \
  --envelope y.json \
  --stop-and-start
```

### 3.6 dry-run

```
--dry-run
```

输出实际会使用的全部参数与将要执行的阶段序列，不产生任何副作用。用于上线前核对推导结果。

## 4. 安装命令改造

### 4.1 直接接受 deb

让 `--payload` 可以接受 `.deb` 路径：检测到 deb 时，内部解包到临时目录取 payload，完成后清理。

这样省掉手工 `dpkg -i`，也消除「包已装但未建实例」的中间态。

需要注意：这改变了「安装前包已存在于系统」的既有假设。`--register` 会写 systemd unit 和注册表，与包是否已由 dpkg 管理是两件事。设计中保留两条路径：

- 传目录：行为不变
- 传 deb：先解包，后续流程相同

### 4.2 减少必填参数
| 参数 | 改造后 |
| --- | --- |
| `--root` | 默认 `/var/lib/device-managed/<instance-id>` |
| `--registry` | 默认 `/var/lib/ai-device/native` |
| `--unit-dir` | 默认 `/etc/systemd/system` |
| `--payload` | 必填，支持目录或 deb |
| `--instance-id` | 必填 |
| `--server-url` | 必填 |

`--start` 与 `--register` 的依赖保持不变（明确表达意图），但缺参数时的错误信息要说明缺哪个。

### 4.3 改造后用法

```bash
ai-device-native-install \
  --payload ai-device-native-client_0.3.2-2_amd64.deb \
  --instance-id <instance> \
  --server-url wss://device.example.com \
  --username device-client \
  --environment-file /etc/device-client/<instance>.env \
  --register --start
```

## 5. 实施顺序与风险

### 5.1 分阶段

| 阶段 | 内容 | 风险 |
| --- | --- | --- |
| 1 | 升级参数推导 + `--dry-run` | 低，纯增补 |
| 2 | `--stop-and-start` | 中，涉及服务状态 |
| 3 | 安装接受 deb + 默认值 | 中，改动安装路径 |

阶段 1 单独就解决大部分手工负担，建议先做。

### 5.2 循环依赖问题

升级执行的是**升级前的已安装代码**。新代码必须先变成已安装版本才能生效，这与本改造直接相关：

- 改造后第一次升级，仍由旧代码执行，新开关不存在。
- 因此验证路径是：先用**当前 0.3.2-2**（含 4c412ab 之后的代码）升级到带改造的版本，再由那次升级后的版本验证新开关。

这要求改造版本本身能先被旧代码正常升级过去——即不能改动旧代码读取的协议字段。

### 5.3 验收要点

- 推导结果与手工传值一致时，行为完全相同
- 信封字段与显式参数冲突时，明确报错而非静默取值
- `--dry-run` 无副作用（不写注册表、不停服务、不建候选）
- `--stop-and-start` 失败路径下服务保持停止且诊断清晰
- 现有 1433 项测试全部通过，不修改既有语义断言

## 6. 明确不做

- 不自动回滚
- 不自动判断多个实例中该升级哪个
- 不改变 `--stopped` 的现有语义
- 不在推导失败时使用猜测值
- 不改动升级事务的协议字段与收据格式

## 7. 待定决策

1. `--operation-id` 默认生成规则是否需要可预测（便于日志检索）？
2. 安装接受 deb 时，是否需要同时处理包已安装的情况（幂等还是报错）？
3. `--stop-and-start` 是否需要在启动失败时保留停止状态供人工排查，还是仍尝试一次重启？
4. 注册表是否应新增记录当前 bootstrap 版本，以便后续完全推导 `bootstrap_version`？
   这属于协议字段变更，需评估旧版本读取兼容性，建议单独评估而非并入本改造。
