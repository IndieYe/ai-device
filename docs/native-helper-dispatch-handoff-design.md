# P1 daemon—更新器持久意图交接设计草案

状态：设计待实现、待隔离验证；生产新 job 派发继续 default-deny。与 `native-helper-peer-protocol-review.md`、`native-lifecycle-p0-design.md` 及 `packages/device-runtime/native/HELPER.md` 一起阅读。此草案不改变普通用户权限、包覆盖保护、真实部署或 P2—P5 授权。

## 前提和当前缺口

用户已确认：helper 运行或存在未终态事务期间，管理员不替换 registry、必要祖先与挂载；不能确认则拒绝启动或恢复。迁移需维护窗口、停用旧入口、确认进程和事务收口，再按另行设计协议处理。旧测试 helper 手动退休，不自动迁移其台账。该前提是可信管理员的操作约束，不是对恶意 root 的 OS 防护。

当前 daemon 持 registry 目录独占锁与原 daemon 文件锁；`start` 在实例锁和 package SH 下检查 job/apply intent。独立 dispatch 目前在创建 intent 前无条件拒绝；其后面的 dormant 路径会在实例锁和 package EX 下保存 job intent，释放锁后调用 systemd。当前 helper socket 同时允许 root 和登记用户使用有限动作，因此不能让登记用户继承 root 更新权限。`scoped_helper_unit_peer` 仅只读观察，不证明候选字节或约束后续不可逆操作。

## 推荐职责分离

1. **监督器和受控 helper**：独立 bootstrap 验证实际候选字节，在受控启动中绑定启动实例与进程；监督器与子进程持有同一个目录/daemon 锁描述，运行和退出语义须另行实现。已确认维护前提下，目录锁限制同时存在的合规 daemon 和 socket 恢复；锁的持有或 peer 自报本身不证明运行代码。
2. **root 更新管理方**：负责准备、审批、明确目标操作及请求，不自己取得 daemon 长期持有的目录 EX 锁；在同一活连接上核查被受控监督器证明的 daemon 身份，失败即拒绝。管理员本地调用和已有远程升级的单独授权仍需满足，不得把普通用户自启或 `allow_upgrade` 推导为更新授权。
3. **daemon 作为 intent 提交协调者**：可考虑新增严格、版本化的 root-only 交接消息，**以 socket 接收时内核 peer UID=0 校验**，不接受客户端自报 UID、路径、命令或服务。现有普通用户可调用的 `query/start/reconcile/resolve-*` 权限不变。不得因为消息经受信连接就自动拥有所有更新权限；准备、批准和操作范围须由已存在的权威状态在锁内复核。
4. **独立 worker**：由受控事务快照运行，不依赖 daemon 活着；运行前取得实例锁→package EX→service 锁，重读固定 operation 的 intent/批准/snapshot，验证未关闭、无冲突且准确匹配。它不需要重新获得 daemon 长期目录 EX 锁，但其所有相关路径与持久意图必须位于同一已确认稳定的 registry；需要显式验证这一假设及所有入口。

具体实现不能仅把现有 `dispatch_job` 搬进 `Helper.handle`：后者已持实例锁，`dispatch_job` 会再次取实例锁而冲突。须拆出明确的锁内核心或协调事务入口，统一一次锁序，禁止在持实例锁时回调重新取得同一锁。root-only 验证须在对普通客户端授权前分支，且不得共享宽松默认逻辑。

## 线性化与状态

- `PREPARED`：已经验证来源、准备和 root 批准；不意味着许可派发。
- `INTENT_DURABLE`：受控 daemon 在实例锁→package EX 下，重新验证绑定、活动/unknown start、批准、snapshot、所有冲突 intent，并把唯一 job intent 用 no-replace、文件 fsync、父目录 fsync 持久发布；内容绑定 instance、operation、prepared/approval、worker snapshot、unit、协议版本和受控启动实例证据。intent 的成功持久发布是在所有参与者均遵守相同包锁和 intent 协议的前提下排除新 start 的线性化点；持久化或响应不确定时按 unknown。
- **持久化未知与重启缺口**：如果 link 已成功但目录 fsync 未确认，崩溃后 intent 名称可能消失。对当前 dormant `dispatch_job`，代码在 `_save_new` 成功返回后才调用 systemd；隔离单元故障注入显示文件或目录 fsync 抛错时 runner 未被调用，目录失败且文件仍可见时同 ID 不重派。因此，在明确文件系统持久化语义且证明**所有可能引发该外部动作的路径都严格等待成功返回**的前提下，单个“fsync 未返回且文件丢失”并不单独证明存在已派发动作，不应无条件要求额外围栏。然而仍须验证成功返回后持久性、进程崩溃/独立参与者、所有动作入口及原操作 ID 对账；若不能证明上述顺序，必须采用预先 durable 的不干净/事务围栏或等价机制，未知时保持阻塞。当前仅有注入测试，并未证明宿主崩溃、文件系统或全部入口语义。
- `DISPATCH_UNKNOWN` / `DISPATCH_ACCEPTED`：释放锁后仅针对相同 intent 请求一次固定 systemd unit；响应丢失或进程崩溃保持 unknown。同一 ID 不重派；已存在 intent 必须核验与当前请求全部一致，不能仅因文件存在就视为同一请求。
- `APPLY_STARTED` / `OUTCOME`：worker 重新取得锁并校验 intent，记录持久 apply intent 后才执行包操作。任何未知、缺 guard 回执或不一致不自动重放、不清 intent，active 与包状态分别观察。
- `CLOSED/RETIRED`：只允许既有精确 pre-apply close/retire 证据的范围，保持迟到 worker 必须在包锁内检查 close。不得把锁空闲、PID 消失、超时或 systemd unit 不存在当成操作未发生的充分证据。

以上每一步均必须绑定准确摘要和版本，不使用启动实例短寿命 token 替代持久 intent。启动实例失效不能解除已提交围栏；新 daemon 在接收 start 前在 package SH 下检查持久 intent，并保持未知事务封锁。

## 关键故障矩阵

| 时序 | 必需行为 |
| --- | --- |
| daemon 验证 peer 前或锁前退出 | 不提交 intent；更新方返回拒绝/未知，查权威状态，不新 ID 自动重试。 |
| 已取得锁、尚未持久发布 | 锁和进程状态需对账；写入结果不确定时不声称没有 intent。 |
| 文件已 link、目录 fsync 未确认 | 当前被测 dispatcher 在 `_save_new` 抛错时不调用 runner；文件仍可见则同 ID 不重派。崩溃后名称可能消失，只有在证明所有动作路径均等待成功持久化及相应文件系统语义成立时，才可判断该次未派发；否则需独立 durable 围栏或等价机制并保持阻塞。 |
| intent durable 后、响应或 systemd 调用前 daemon 退出 | intent 持续围栏；管理方查询原 ID，不能仅凭未见响应重派。 |
| systemd 调用已受理但回执丢失 | 原 ID unknown；查询对应 unit/job、intent、worker/guard回执和包状态，不重放。 |
| daemon 重启，旧 guard/worker 仍持锁 | 新 daemon 获目录锁与 socket 后，start 的包 SH 及 intent 检查阻断冲突；缺证据保持 unknown。目录锁不能替代 package guard 或证明 worker 已退出。 |
| 迟到 worker 与 close 竞争 | 两者在相同实例→包锁下重新检查 close 与 intent；已关闭 worker 必须拒绝。无原子性证明或状态不明则保持封锁。 |
| registry/祖先/挂载稳定前提不能确认或被违反 | 停止准入和恢复，人工诊断；不能自动重建 registry、删除意图或声称锁有效。运行时检查有竞态，不替代维护前提。 |

## 验收前置

- 先实现受控启动及当前连接、运行实例、systemd invocation 的可信绑定，并覆盖监督器先退出、子进程存活、FD继承和响应丢失。`SO_PEERCRED`、pidfd、FD/inode 和自报各自都不足以单独授权。
- 明确 root-only 交接方法的可靠认证、可重复操作 ID、无任意路径/命令、版本兼容和普通 UID 负向测试；不扩大现有权限。
- 验证唯一实例锁序、持久写入故障和竞争：真实多进程/隔离 systemd，故障注入覆盖 intent 前后、fsync 不确定、释放锁/派发间隙、daemon/SIGKILL、guard挂起、迟到worker、宿主重启与显式对账。
- 完整审查所有 start、注册、恢复、旧维护入口和包事务参与者；旧 helper 手动退休与新协议启用是不同门禁。
- 新 schema 与已有 close/retire/recovery 的兼容必须明确；不为迁移删除历史台账。正式信任根、RPM、准确包钩子授权、激活和其他 P1 缺口仍需分别完成。任何局部设计/测试不解除生产 default-deny。


## 已执行 job 对账的观察窗口限制

现有 `recover_job` 在进入 `Updates.recover` 之前读取 systemd unit 的终态；`Updates.recover` 随后在实例锁和 package EX 下重新核对 job intent 摘要、批准、sender 回执和实际包状态并写恢复证据。六字段严格解析只能拒绝格式不确定，**不把观察与锁内恢复变成原子操作**：当前未把同一 systemd InvocationID 与受控 worker 身份绑定，也不能仅凭锁证明 unit 在观察后未被重启或替换。事后再观察无法撤销已写入的恢复证据；简单前后对比也不能消除检查与提交之间的竞态。

现有 apply intent 已存在时同 ID 迟到 worker 走观察而不重放（实施记录第134节），且恢复证据本身不解除 intent、启动围栏或授权新 ID；这些约束降低了局部风险，但不证明所有并发入口、unit 身份或宿主崩溃恢复。将来启用派发前须为固定 invocation、受控 worker 和不可重放的持久终态设计可验证的协调协议，覆盖恢复锁期间竞态及失败后仍为 unknown；不凭当前只读快照开放派发。


### 已复现：终态过期后仍写恢复证据

实施记录第159节以准确 cf18a80 包、隔离 systemd 和测试专属有限延迟，捕获真实终态后重启测试 unit，并在 unit `activating` 时向 `recover_job` 交还旧观察；实际写出 recovery 与 job-recovery。原 intent/fence 保留、active 不变；没有证明真实包动作重复。由此确认观察窗口是可复现限制，不能只因前后快照一致或 package lock 空闲而开放生产派发。真正修复需要同一受控 invocation 的完成证明和与恢复写入共用的可验证协调；尚未实现。


### 待验证的 worker / 恢复协调方案（设计，不是已实现契约）

本节是对第159节竞态的候选方案，并非生产启用决定。目标不是让 systemd 的一次观察“永远保持终态”，而是在允许发布可用恢复结论前，证明所有被授权的执行入口都无法再对该事务执行包动作。旧 snapshot 无此约定时不能推断它会遵守新规则；现有旧 job、unknown 和已写回执保持原样并继续封锁，不自动迁移或删除。

1. **绑定与准入**：新协议的持久 job intent 必须明确协议版本、instance/operation、批准和候选摘要、固定 worker snapshot 及受控启动实例标识。只能在证明实际处理者和版本受控、管理员维护前提满足且所有参与入口兼容后创建；生产 default-deny 在前置验证完成前不变。记录 systemd InvocationID 可辅助定位，但文本字段本身不是身份认证。
2. **统一竞争锁与迟到拒绝**：worker 每次入口，包括重启和同 ID 重入，必须在实例管理租约→package EX 下重验完整 intent、批准、协议、持久封闭标记及冲突；若封闭标记存在，在任何包动作之前拒绝或只读返回。旧 worker、直接 apply、恢复及其他写入口必须逐一纳入兼容审计；不能仅修改新 worker 而推断旧 snapshot 受约束。
3. **终态证明**：sender/guard 的退出证明只覆盖被其约束并实际回收的进程树；worker 自报退出、锁暂时空闲、systemd Result 或 pidfd 单独均不足。须明确独立事务运行时、子进程/后代、包动作与 inherited FD 的覆盖和持久顺序。guard 异常、回执缺失或目录 fsync 结果不确定时保持 unknown，不能补造成功证明。
4. **恢复线性化候选**：在相同实例→package EX 保护下，重新校验全部权威证据并以 no-replace 与文件/目录 fsync 发布不可逆的持久封闭标记；只有该标记成功持久化后，允许据明确的完成证据生成恢复结论。成功返回前不得授权后续动作；结果未知时按原 ID 对账，任何冲突保留记录和围栏。该标记不等于包升级成功、许可激活或可重试，也不能覆盖未知的旧 worker 能力。
5. **故障与竞争验收**：在真实独立进程中验证 worker 在取锁前后、intent 后、包进程与 guard 存活、终态标记写入前后和目录 fsync 不确定时，与恢复同时竞争；确认先获得锁的一方完成后另一方重读标记和全部状态，不重复包动作、不提前提交可用终态。另覆盖同一 unit 重启、新 invocation、父死子活、锁描述符继承或释放、旧 snapshot 迟到、丢响应及显式人工对账；测试须证明真实锁冲突和故障后进程树终结，不以线程调度暂停替代。

**尚未解决**：该方案需要可验证的受控 worker/handler 身份、所有执行入口一致参与和崩溃持久化前提；它不靠记录 InvocationID 或增加一次 `systemctl show` 消除竞态。若无法证明旧 worker 不可执行或无法建立终态证明，则拒绝自动恢复并保持 unknown；管理员处置流程需另行定义，不能擅自删意图、解除包保护或接管旧登记。


锁协议初步实验见实施记录第160节：真实独立进程分别验证实例/包锁冲突和恢复先封闭、worker先行动两种 fixture 顺序。实际锁是非阻塞，竞争时拒绝，后续由调用者显式重新执行并重验，不承诺自动等待。该原型未使用真实生产 worker 或完成凭证，不关闭上述待验证前提。


第161节对当前源码增加临时默认拒绝：job 专用恢复和带 job 摘要的底层恢复不再写新证据，保留只读诊断、已有记录与围栏。旧冻结 snapshot 不自动获得该门禁；这项收紧不修复旧证据，也不代替上面的受控 worker/终态协议和验收。


### 包进程树回执候选（未接入生产）

`native_package_sender.py` 提供未接入任何 dispatch/apply/recovery 的候选 guard：新 intent 显式标注 `debian-package-tree-subreaper-v1`，守卫在启动固定 dpkg 前启用 Linux subreaper，回收收养后代直至 ECHILD，文件 fsync、无覆盖 link、目录 fsync 后发布绑定 intent 的版本化回执。包锁 FD 仅由 guard 保留，dpkg 不继承；协议不使用超时强杀包树。已有回执及协议不匹配在动作前拒绝，但该本地检查不替代锁内的持久 intent 和防重放协调。候选只为独立实验，不承诺它的调用方已经保证 package lease、固定 snapshot 或身份正确。

实施记录第166节的隔离 fixture 验证普通完成、guard 崩溃、旧协议和已有回执拒绝，以及目录 fsync 故障。特别是 guard 崩溃可导致包锁释放但后代活着；缺回执只能保持 unknown。另一个关键边界是 link 后目录 fsync 失败：即使回执当前可读也不能仅凭存在判定已持久发布；可见回执、进程退出码或锁空闲均不单独授权恢复。尚须设计并验证受控观察、崩溃与持久化语义，审计所有旧入口和只读观察对版本的支持，再决定是否接入新动作路径；旧 snapshot 不自动升级，生产派发及恢复门禁不因此开放。


### 外部服务管理器委派不属于 subreaper 完成证明

实施记录第178节在无网络的一次性 systemd 容器中，使用未接入生产的候选 guard 和替身 dpkg，让替身通过 `systemd-run` 启动独立 fixture；guard 已完成并发布结构匹配的 tree receipt 时，该 unit 的 MainPID 仍存活且父进程为 PID 1。随后 fixture 显式释放并自然结束。此证据说明 subreaper 到 `ECHILD` 只覆盖其收养的子孙，不覆盖向 systemd 或其他外部管理者委派的工作；并未证明当前真实包钩子发生了这种委派，也未执行真实包动作。

启用任何恢复前必须单独核实受支持包动作与钩子是否存在外部委派，并设计对相关外部工作的可靠绑定与终态证明；不能用一次 unit 观察或只凭回执可见、锁空闲来补足。未知或无法证明时继续保留围栏、默认拒绝。旧 snapshot 不自动获得新约束。


### 响应 sender 与动作执行者不是同一事实

实施记录第179节在隔离 systemd 中使用固定 fixture 验证：unit 主进程接收请求后委派子进程完成文件动作、回收子进程并自行发送响应。准确包中的只读 peer scope 在 unit/peer 前后观察一致时成功，`SCM_CREDENTIALS` 的响应发送者也等于 unit 主进程，但 fixture 动作由另一个 PID 完成。它不是生产 helper 的行为证明；它说明即使第177节的真实只读响应 sender 与 peer 一致，也不能倒推出不可逆业务动作的处理者或完整进程树受控。需要单独设计并证明每个动作入口、进程交接、锁与持久终态的联系，不能用响应凭据或事后 unit 快照开放派发与恢复。


### 当前源码对既有 job worker 的额外拒绝

第180节在 `Updates.apply` 的锁内新增默认拒绝：带 job intent 摘要且尚无 apply intent 时，即使是既有 job 也不允许启动新动作；已经存在 apply intent 继续只读观察，现有 close 标记保留原拒绝。该防线不改变普通直接 apply 的授权语义，不自动约束已冻结旧 snapshot 或已经运行的 worker，不代表新受控协议完成。未来要解除该门禁，仍需证明实际执行者、受控交接、全部入口和终态协调；不得用测试替身或新源码反推旧 worker 已受约束。


### 退休标记可见不等于关闭持久

第182节以真实保存流程故障注入：close 的 hardlink 已可见但目录 fsync 失败；旧退休入口重读可见 close 后仍发布 retired，且退休目录 fsync 也失败；旧结构校验仍返回退休。无法仅凭两个可见文件判断关闭标记在崩溃后仍存在；若之后新事务依赖退休放行，旧 worker 的迟到行为可能不再受该关闭记录约束。实验未模拟掉电，不证明真实竞态已经发生。

在持久关闭及旧 worker 协调的完整协议、恢复和崩溃验收成立前，当前源码拒绝新退休、所有 job intent 均保留包围栏（包括既有结构正确的退休记录）。这不会自动修改旧 helper 或冻结 snapshot 的行为，不能用新代码推断旧进程安全。故障记录保留，不自动删除或放行。
