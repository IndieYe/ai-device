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
