# upgrade 失败恢复契约：方案 B（已实现）

> 版本：3（2026-11-09）｜状态：已实现；离线测试通过，**A→B 升级已真机验收**
> 依据：`docs/requirements/client-native-install-upgrade.md` 已明确"不自动回滚"（第 47、197–199 行）
> 用户决定：**选方案 B，锁确认不了就不恢复。**
>
> 版本 3 修正：版本 2 称“未做真实 launchd 验收”。macminim6 上已完成 0.3.6→0.4.0 升级，
> identity `84f64def`→`eaf47863`、config/token 哈希不变、中心侧 build_id 与安装树一致，
> 本契约的 A→E 路径已实际走通。未验收的是**失败恢复分支**本身：真实设备上没有
> 构造出“已退出旧实例但发布失败”的局面，所以 [B] 之后的恢复路径仍只有离线测试证据，
> 不能因为 A→B 成功就认为恢复逻辑已经被真机覆盖。

## 一、问题定位

`client-macos-registered-upgrade.h` 的 `upgrade_registered_once` 执行顺序：

```
退出旧实例 → [A] unload 服务 → [B] 发布新程序 → [C] handoff 释放 start/runtime
           → [D] load 服务 → [E] 有界 ready 轮询
```

原实现任一阶段失败都 `return 0`，入口统一输出 `AI_DEVICE_UPGRADE_UNKNOWN_OR_REFUSED_V1`，
把"什么都没做"和"可能已经换了程序"混成一句，调用方无法判断能否重试。

**已核实的阶段事实**（草案 1 在此处有错，已修正）：

| 失败点 | 服务 job | 磁盘程序 | 状态确定性 |
|---|---|---|---|
| 输入/资源/guard 校验失败 | 未触碰 | 旧程序 | **确定**：什么都没变 |
| [A] 之前的 lease 请求未确认 | 仍加载（未证实退出） | 旧程序 | **不确定**：退出与否未知 |
| [A] unload 返回失败 | **未知** | 旧程序 | **不确定**：bootout 失败不证明仍在加载 |
| [B] 发布返回失败或校验不过 | 已卸载 | **可能是新版** | **不确定**：swap 可能已完成 |
| [C] handoff 失败 | 已卸载 | 已换新 | 部分确定：锁可能已部分释放 |
| [D] load 结果未知 | **未知** | 已换新 | **不确定**：禁止重放 |
| [E] ready 超时 | 已加载 | 已换新 | 进程可能已在跑 |

关键纠正：

- `unload_registered_service` 只执行 `launchctl bootout`，**不删除 plist 定义**。这只说明定义可复用，
  **不证明** `bootstrap` 一定成功、不证明无需重装、更不证明无需安全门禁。
- 发布失败**不能**假定旧程序完好：exact 校验或 deadline 失败可能发生在 swap 之后。

## 二、需求已定的边界

- 第 47 行：失败策略是"明确失败、保留配置与数据、可通过重装修复；**不自动回滚**"。
- 第 197 行：包安装失败"不承诺旧程序仍可用，不自动回滚"。
- 第 199 行：中断后"能够通过重新安装同版或修复版完整包恢复；**不依赖旧应用成功启动**"。

**无自动回滚是需求既定选择，不是本方案的缺陷。** 这里要解决的是"状态可判定、改动不误报、有明确修复路径"。

## 三、已实现的方案 B

只对**磁盘状态确定**的两个阶段做一次单向补加载：不换程序、不重试 unload、不重放发布。

阶段码定义在 `native/client-macos-upgrade-stages.h`（单一事实源）：

| 阶段码 | 含义 | 是否恢复 |
|---|---|---|
| `OK` 0 | ready 且身份精确匹配 | — |
| `REFUSED` 1 | 任何不可逆动作之前拒绝 | 否 |
| `EXIT_UNKNOWN` 2 | lease 已请求，退出未确认，job 仍加载 | 否 |
| `UNLOAD_UNKNOWN` 3 | bootout 尝试过且失败，job 状态未知 | 否 |
| `AFTER_UNLOAD` 4 | 已卸载，程序仍是旧版（发布未开始） | **是** |
| `PUBLISH_UNKNOWN` 5 | 发布尝试过，程序可能是新版 | 否 |
| `AFTER_HANDOFF` 6 | 已发布且 handoff **返回成功**，只差 load | **是** |
| `AFTER_LOAD` 7 | load 结果未知 | 否 |
| `NOT_READY` 8 | job 已加载但目标未服务 | 否 |
| `HANDOFF_UNKNOWN` 9 | 已发布，但 start/runtime 释放未确认 | 否 |

> `AFTER_HANDOFF` 的措辞已收紧。上一版把 handoff 失败也报成 6，使“没能释放锁”
> 看起来像“锁已释放、可以安全 bootstrap”。现在 handoff 失败一律报 9，不参与恢复。
> 恢复除此以外还做一道描述符检查：即使阶段写的是 6，只要本进程的 `start`/`runtime`
> 描述符仍 >=0，就拒绝。这是**拒绝**而不是证明——它只能拦住“明显还没释放”的情况。

恢复实现（`upgrade_recover_after_stage`）的两个分支：

- `AFTER_UNLOAD`：恢复的是**旧版程序**，这是重启而非回滚，也不是升级成功。
  由于发布未开始，start/runtime 仍由本进程持有；**必须先 handoff 释放**，
  否则 launcher 非阻塞获取失败会直接不 exec 退出——这正是正常升级链先 handoff 再 load 的原因。
- `AFTER_HANDOFF`：锁已按设计交给 launcher，**不得重新获取或再次 handoff**，只需 load。
- `HANDOFF_UNKNOWN`：**不恢复**。程序已是新版，但至少一个锁可能仍被本进程持有；
  此时 bootstrap 会与一个被 start.lock 阻塞的 launcher 竞争。需人工判断后处理。

另：若 unload 后因 deadline 已过而返回 `AFTER_UNLOAD`，恢复会因同一 deadline 拒绝，
服务可能保持卸载状态。这是已知的可用性边界，本轮未处理。

两种分支都复用同一个 deadline，**只执行一次** `bootstrap`，失败不再重试。
恢复成功只表示"已重新加载"，token 是 `AI_DEVICE_UPGRADE_RECOVERED_LOADED_NOT_READY_V1`，
**不声称 ready**。

## 三之二、`LOADED_NOT_READY` 之后磁盘已经是新树（2026-11-10 实测）

上表 [E] 行的"已换新"不是推测。构造一次 ready 必然失败的安装后实测确认：
**`LOADED_NOT_READY` 抛出时，程序树已经完成替换，失败的是新树的自证，不是发布本身。**

构造方式：包装 `rewrite_client_identity`，在真实 rewrite 之后、inventory 生成之前
把 `client-identity.json` 改成不匹配的假 digest。运行时指纹不包含该 JSON，所以
清单仍然自洽（校验通过），但 ready 比对必然不等。这是唯一能走到轮询并失败的构造
方式——直接改包内 JSON 会先被 `verify_resource_tree` 拦下，只得到 `REFUSED_V1`。

实测输出（`/var/log/install.log`，installer 耗时约 123 秒）：

```
./postinstall: upgrade not ready: runtime_version mismatch; expected version 0.3.6
    build sha256:eeee...(after 707 attempts)
./postinstall: AI_DEVICE_UPGRADE_LOADED_NOT_READY_V1
```

失败后的现场：

| 观察项 | 值 |
|---|---|
| `/opt/ai-device-client/client-identity.json` | 测试包的假 digest |
| 该树运行时实际指纹 | 测试包的真实 digest（与 live 目录一致） |
| launchd job | 已加载，`state = running`，`never exited` |
| 中心连接 | 正常建立 |
| 配置与 token | 未变 |

即：**服务在跑，但它报的身份和登记的身份不一致**。这正是该次失败的含义——不是
"没装上"，而是"装上了但新树与期望身份不符"。修复手段是装一个身份自洽的包，
不是回滚。

### 用重装修复时先清 stage

直接重装正常包会失败在 `preinstall`：

```
PROGRAM_INSTALL_RESIDUE_V1: stage path exists and is not empty;
    its content cannot be attributed, remove it and retry
```

因为失败的那次升级留下了自己的 stage 目录（约 2466 条），preinstall 拒绝在
无法归属的残留上继续。清掉该目录再装即可，本轮实测清掉后同一条命令安装成功、
`READY_EXACT_V1`、身份一致、配置哈希未变。

因此"重装修复"的准确步骤是：确认 live 目录的实际身份 → 查 `preinstall-token`
确认是 `RESIDUE` 而非 `BUSY` → 确认 stage 内容归属（可能是被 swap 换下来的
**正常旧树**）→ 清掉该目录 → 重装身份自洽的包 → 核对 live JSON 与运行时指纹一致。
跳过这些步骤会得到一个指向残留的报错，而不是安装失败的真实原因。

**"身份不一致"不是删除依据。** 本节早先写的“删除身份不一致的残留 stage”是
错误的：2026-11-10 诊断那次失败留下的 stage 恰好是**身份正常的旧树**
（`9c155b40`），它只是因为 swap 被换到了 stage 路径上，不是损坏或伪造的树。
用身份是否匹配来判可删性，会误删本来可用于回退的唯一副本。

正确依据是归属与时序：确认这一帧确实完成过 swap、没有并发安装正在进行、
里面不是唯一回退副本，才可清。仅凭目录存在或没有进程占用就删，都是过度推断。

## 四、锁确认的实际强度（不得夸大）

新增 `registered_management_confirmed`：在 `registered_management_current` 之上叠加
`private_lock_held`——后者 open 同一路径再试 `LOCK_EX|LOCK_NB`，只在 `EAGAIN/EWOULDBLOCK` 时返回真。

**它能证明的**：锁对象身份一致，且此刻该锁**不是空的**。

**它不能证明的**：

- 持有者是本进程。本进程已释放、被别的进程拿走的锁，通过这个检查看起来一样。
- **探针被阻挡不等于存在排他持有者**：共享（SH）持有者同样会挡住 `LOCK_EX` 探针。
- 它不是 job 卸载证明，也不是后代进程或非合作实例的停止证明。

因此恢复的语义是"拒绝观察为已空的锁"，**不是"证明自己仍持有锁"**。

关于用户要求"锁确认不了就不恢复"：当前实现只做到"观察到锁为空就拒绝"。如果该要求
指的是“必须确认锁由本进程持有”，那么本实现**尚未满足**——因为通过竞争探针无法区分
持有人。这是一个尚未闭合的差距，不应表述为已符合用户要求。

## 五、已交付与未交付

已实现并有测试覆盖（`packages/device-runtime`）：

- 阶段码单一事实源，协调器返回真实阶段，不再把尝试过副作用的失败报成干净拒绝。
- 入口按阶段输出专属 token：`REFUSED` / `EXIT_UNKNOWN` / `UNLOAD_UNKNOWN` /
  `PUBLISH_UNKNOWN` / `UNKNOWN_AT_LOAD` / `LOADED_NOT_READY` /
  `RECOVERED_LOADED_NOT_READY` / `RECOVERY_REFUSED_OR_FAILED`。
- 12 项恢复契约测试：两个可恢复阶段各自的正向路径、三个前置失败无副作用、
  七个不可恢复阶段一律拒绝。

`tests/test_client_macos_native_upgrade.py` 的阶段断言由探测出的真实步数/阶段码驱动，
不是按旧 0/1 返回值机械改数字。

**未交付**：

- 恢复路径的真实锁竞争验收（当前用真实 `private_lock_held` 逻辑但无并发对手）。
- 独立审核对本轮改动的复核。

**已补做的实机验收**（2026-11-10，macminim6）：

- A→B 实机升级：签名并公证的包升级成功，`READY_EXACT_V1`，身份精确匹配，
  配置与 token 哈希不变，launchd `running`。
- 同包二次安装：同样 `READY_EXACT_V1`，PID 更新，幂等。
- `LOADED_NOT_READY` 现场：见"三之二"节，确认失败时新树已发布，以及清 stage
  再重装的修复路径。
- `REFUSED_V1` 的一条实测成因：包内 `client-identity.json` 与内嵌 inventory 不符
  时，在请求旧实例退出**之前**即拒绝，设备零改动。

## 六、仍然存在的边界

1. `publish_registered_target` 内部复制/校验可能耗时，"调用前 deadline"不等于"rename 紧前 deadline"。
2. `handoff_registered_start` 在三 guards 校验后依次 close runtime、start；close 失败可能已部分释放，
   recovery 只在 `AFTER_UNLOAD` 上调用它并要求三 guards 齐备，未知状态一律拒绝。
3. 恢复成功不等于服务可用；调用方不得据此再触发一次升级。
4. 修复指引本版不打印完整命令：路径与账户组合未经验证，猜路径比不给更危险。
