# Job 终态持久归属设计

> 状态：**设计稿，未实施**。本文不改变任何生产门禁，不构成授权。
> 关联：`docs/native-lifecycle-implementation.md` 第 273—275 节（实测）。
> 前置事实：InvocationID 在 `stop`/`clean` 后被清空，且与「从未派发」在字段上不可区分（第 274 节实测）。

## 1. 要解决的问题

现在 `observe_job` 把 systemd 的瞬时字段映射成三种状态：

| systemctl show | observe_job |
| --- | --- |
| `loaded` + `activating/active/deactivating` | `job_in_progress` |
| `loaded` + `failed` | `job_failed_requires_review` |
| 其它（含 `not-found`） | `dispatch_unknown` |

缺口不是「读不到状态」，而是**读到的状态无法证明属于哪一次派发**。具体：

- unit 名由 `stem` 确定性派生（`native_update_job.py` 中 `"ai-device-package-" + sha256(str(stem))`），**同名 unit 可被重复创建**。`clean` 之后重派发会得到同名但全新的执行。
- 因此 `failed` 可能是「本次派发失败」，也可能是「上一次派发的残留失败」；`not-found` 可能是「从未开始」，也可能是「跑完并被清理」。
- `dispatch_unknown` 目前把这些全归为一类，安全（不授予 retry），但**不可判定**：管理员无法据此收口，只能永久保留围栏。

设计目标：在不放宽任何门禁的前提下，让「某次执行确实开始过」成为可持久验证的事实。

非目标：不追求证明「执行正确完成」。完成性仍由 `.outcome.json` 与包数据库观测承担。

## 2. 现有约束（设计必须绕开的）

### 2.1 job-intent 键集封闭

`.job-intent.json` 的键集合在 5 处被精确比对，任一不符即拒绝：

- `native_helper.py:130`（`_job_retired`，`set(intent) != {...}`）
- `native_update_job.py:162`、`203`、`347`（`intent != expected`）
- `native_update.py:53`（`_verify_job_intent`）

**结论：不应向 job-intent 增加字段。** 加字段需要同时改 5 处 + 升 `schema_version`，且会让历史意图全部失效，风险与收益不成比例。

### 2.2 apply-intent 同样是封闭比较

`observe_job` 用 `helper._same_json(apply_intent, expected_apply)` 比对，`expected_apply` 仅含 `prepared_sha256` / `runtime_snapshot_sha256` / `target_package`。加字段同样会破坏比对。

### 2.3 现有证据的无序性

`apply-intent` → `outcome` → `sender` 三者由 worker 依次写入，但**没有任何一条记录声明「我从哪一次派发开始」**。`_read_job_evidence` 只做类型与信任校验，不做归属校验。

## 3. 候选机制评估

| 机制 | 能否跨 stop/clean | 能否跨重启 | 结论 |
| --- | --- | --- | --- |
| InvocationID 比对 | 否（实测清空） | 否 | 已否决 |
| `systemd-run --setenv` 运行标签 | 否（unit 消失即丢失） | 否 | 只能辅助在线判定 |
| systemd journal 查 `_SYSTEMD_INVOCATION_ID` | 视 journald 配置 | 视配置 | 容器内易失，不可作唯一依据 |
| unit 文件 `FragmentPath` 内容指纹 | 否（transient unit 无文件） | 否 | 试运行 unit 不适用 |
| **worker 自证 run-start** | **是（普通文件）** | **是（含 boot_id）** | **采用** |

## 4. 推荐设计：worker run-start 自证

### 4.1 新增证据文件

worker（`native_update_job.main()`）在**取得任何锁、读取任何包状态之前**，写入 `.job-run-start.json`：

```json
{
  "schema_version": 1,
  "state": "job_run_started",
  "job_intent_sha256": "<64hex，派发时 job-intent 的摘要>",
  "runtime_snapshot_sha256": "<64hex，与 intent 一致>",
  "boot_id": "<36 字符带连字符，/proc/sys/kernel/random/boot_id>",
  "pid": 12345,
  "start_ticks": 987654,
  "started_at_ms": 1793173548000
}
```

**已实测**（一次性容器）：`/proc/sys/kernel/random/boot_id` 可读，格式为 `394e9cfa-a8bb-4dce-8775-7a40c4368041`（36 字符含连字符），同一启动内重复读取一致；容器 stop 后重建，boot_id 变为不同的值。因此它确实区分启动代际。注意格式与 InvocationID 的 32hex **不同**，校验正则不能复用。

写入使用既有 `helper._save_new`（fsync 文件 → hardlink no-replace → fsync 目录）。**写一次，永不覆盖**；已存在即视为异常并拒绝继续（说明同一次派发被重复拉起）。

### 4.2 为什么是 worker 写而不是派发器写

派发器能在 `systemd-run` 的 stderr 里拿到 InvocationID（实测），但它拿不到「worker 真的开始执行了」这一事实：`systemd-run` 接受作业 ≠ unit 已启动 ≠ worker 已运行。只有 worker 自己有能力声明「我开始了」。

这也正好对应实测结论：派发接受与执行开始之间存在窗口，该窗口内崩溃必须保持 unknown。

### 4.3 归属判定规则

设 `run_start` 为读到的 run-start 证据，`boot` 为当前 `/proc/sys/kernel/random/boot_id`。

```
run_start 缺失或绑定不符:
    任何 systemd 观察 → dispatch_unknown（保持现状，不可判定）
    理由：无法区分「从未开始」与「跑完被清空」

run_start 存在且 job_intent_sha256 == 当前 intent 摘要:
    systemd failed              → job_failed_requires_review
    systemd 活跃                → job_in_progress
    systemd not-found           → job_completed_no_unit_evidence
    systemd 不可达              → job_in_progress_unverified
    且 run_start.boot_id != boot → 追加标记 prior_boot_execution=true
```

关键点：**新增的 `job_completed_no_unit_evidence` 仍然不授予 retry**。它只表示「这次派发确实开始过，且当前没有 unit 证据」，供管理员进入显式对账，而非自动重放。

`run_start` 存在但 `apply-intent` 缺失，是一种新的**可判定**状态：worker 开始了但没有产生任何变更意图。这比现在的「孤儿证据即拒绝」更精确，但必须**仍然拒绝恢复**，只能标记 `job_started_without_mutation`，因为缺少 apply-intent 就无法证明包数据库未被改动。

### 4.4 boot_id 的作用边界

`boot_id` 是每次启动随机生成的，能证明「该记录写于某次启动」。它能回答：

- 本次执行是否发生**当前**这次启动中（`prior_boot_execution`）

它**不能**回答：

- 进程是否正常退出（被 SIGKILL 的记录同样存在）
- 重启前是否已完成包变更（完成性仍看 outcome）
- 该执行与 reboot 的先后因果（boot_id 无时间序）

因此 `prior_boot_execution=true` 时，观察结果应降级为**仅供参考**，不得作为收口依据。

### 4.5 与现有孤儿检查的关系

`observe_job` 现在对 `.sender.json` / `.outcome.json` 有孤儿检查（有执行证据但无 apply-intent 即拒绝）。`job-run-start.json` 必须一并纳入该检查，但语义不同：

- `run-start` 单独存在 → 允许，标记 `job_started_without_mutation`，仍拒绝恢复
- `run-start` + `outcome` 但无 `apply-intent` → 仍按现有规则拒绝（证据链断裂）

### 4.6 派发侧的辅助标签（可选）

若需要**在线**（unit 仍存活时）把进程与派发绑定，可在派发 argv 增加固定环境标签，观察时读回：

```
--property=Environment=AI_DEVICE_JOB_RUN=<派发侧生成的 32hex>
```

它不进 job-intent（避免触碰封闭键集），只出现在 transient unit 的内存配置里。价值有限：unit 消失后即丢失，且 worker 无从校验。**建议首版不实现**，留作在线诊断增强。

## 5. 对状态机的影响

新增状态（全部 `retry_allowed=false`, `fence_retained=true`）：

| 新状态 | 触发 | 允许动作 |
| --- | --- | --- |
| `job_completed_no_unit_evidence` | run-start 匹配 + unit 消失 | 仅显式对账，不得自动重放 |
| `job_started_without_mutation` | run-start 匹配 + 无 apply-intent | 仅标记，不得恢复 |
| `job_in_progress_unverified` | run-start 匹配 + systemctl 不可达 | 保持等待 |

三个新状态都**不进入** `recover_job` 的成功路径；`recover_job` 的既有前置条件（`apply_intent_present` 且 unit 终态满足）保持不变。

## 6. 迁移与兼容

- 历史事务没有 `run-start`：按「缺失即 unknown」处理，与现状一致，**不需要回填**。
- 不回填、不推断、不为老事务补写证据。
- `run-start` 的键集从第一版起就固定；若将来扩展，升 `schema_version` 并在读取处做显式分支，不静默放宽。

## 7. 测试计划

必须覆盖（全部可用替身，不需要真实 systemd）：

1. 无 run-start + unit failed → `dispatch_unknown`（不变）
2. run-start 匹配 + unit failed → `job_failed_requires_review`
3. run-start 匹配 + unit not-found → `job_completed_no_unit_evidence`
4. run-start 摘要与 intent 不符 → 拒绝（`job run evidence differs`）
5. run-start 为 symlink/FIFO/目录 → 拒绝，且在 metadata 查询前拒绝
6. run-start 存在但无 apply-intent → `job_started_without_mutation`，且 `recover` 仍拒绝
7. boot_id 不同 → 追加 `prior_boot_execution`，且观察不得用于收口
8. 重复写 run-start（文件已存在）→ 拒绝启动
9. 所有新状态断言 `retry_allowed is False` 且 `fence_retained is True`

需要真实 systemd 验证的（一次性容器，沿用 `scripts/containers/systemd-probe/Containerfile`）：

- worker 被杀于 run-start 之后、apply-intent 之前 → 停在第 6 项状态
- `clean` 后 → 停在第 3 项状态

## 8. 未解决 / 待决

1. **run-start 由被执行方自己写，是否足够？** 它只能证明「我开始过」。若可信边界要求「不可自证」，则需要外部监督者（如独立 helper）代写，代价是新增进程与协议。当前项目的信任模型（本地单用户、可信本地代码）下，自证是可接受的；但这一点应在实施前由用户确认。
2. ~~`boot_id` 在容器内是否可读~~ — **已验证可读且跨启动变化**，见 4.1。
3. 是否需要为 `job_completed_no_unit_evidence` 提供管理员显式收口入口，还是永久只读？首版建议**只读**。
4. 与 `_job_retired`、`close_before_apply` 的相互作用未设计：retire 要求三件证据齐全且无 apply/sender/outcome，新增 run-start 后是否要在 retire 时清除或保留，需要单独决策。
5. `start_ticks` 取自 `/proc/<pid>/stat` 第 22 字段，用于区分 pid 复用。容器内可读（实测返回 `1508`），但**尚未验证**重启后与 systemd 记录的对应关系，首版可只作诊断字段、不参与判定。

## 9. 实施路径（供后续采用）

已核实的前提：worker 已通过 argv `--job-intent-sha256 <digest>` 收到 intent 摘要（`_prepare_dispatch_locked` 第 242 行），`main()` 已解析该参数。**因此不需要新增派发参数，也不需要触碰封印的 job-intent 键集。**

建议顺序（每步独立可验收）：

1. 在 `main()` 解析参数之后、调用 `Updates.apply()` 之前插入 `_record_run_start()`；失败则退出非零，不进入 apply。
2. `observe_job` 读 `run-start`；先只把结果放在报告的 `run_start` 字段里（不改任何 `state`），用现有 1425 项回归确认无回归。
3. 再引入 4.3 的状态映射，逐条补齐第 7 节测试。
4. 最后才考虑是否允许管理员对 `job_completed_no_unit_evidence` 做显式对账（首版建议不做）。

步骤 2 之前不得修改 `recover_job` 或任何门禁。

## 10. 一句话

**InvocationID 证明不了的事，改由执行者自己留下一条不可覆盖、绑定意图摘要与启动代际的开始记录来证明——但它只证明「开始过」，永远不证明「做对了」。**
