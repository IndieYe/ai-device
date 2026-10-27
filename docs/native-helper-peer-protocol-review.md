# Helper 受控启动与 peer 身份：评审和隔离证据

状态：P1 设计验证，非生产协议；job 派发继续 default-deny，原生包覆盖保护不变。原方案及进度见 `native-lifecycle-p0-design.md`、`native-lifecycle-implementation.md` 和 `packages/device-runtime/native/HELPER.md`。不以本实验代替正式信任根、完整事务验证或用户授权。

## 信任范围与待证明链路

可信管理员、内核、systemd 和独立 bootstrap 可能出错，但本设计不防御拥有 root 执行能力的恶意管理员。旧测试 helper 在管理员维护窗口手动退休；旧启动入口和未终态不能核实则拒绝推进，不自动迁移、删除台账或解除围栏。

需要分别证明：独立 bootstrap 对指定字节的受控执行 → 每次启动的实际进程 → 本次连接的实际处理者 → 锁内准入和持久事务围栏。unit 文本、磁盘摘要、子进程自报、固定版本、单次进程存活和单个 pidfd 都不是整条链的证明。当前 bootstrap 在自身进程内执行候选，监督器/子进程机制尚不存在。

## Linux socket 实验：两个反例

通过 `scripts/probe-native-peer-identity.py` 在无网络一次性 amd64/Rosetta Linux 容器执行（kernel 6.18.15；镜像 `device-qual-systemd-amd64-ses13838:probe`）。2026-10-27 运行，最终 `PEER_PROBE_OK`，结果：

1. fork 前由父进程创建 `socketpair(AF_UNIX, SOCK_DGRAM)`，子进程写消息：父端 `SO_PEERCRED` 是创建 socketpair 的父进程，启用 `SO_PASSCRED` 后该消息的 `SCM_CREDENTIALS` 才是实际发送的子进程。
2. 父进程 bind/listen，子进程 accept/响应：客户端 `SO_PEERCRED` 和 `SO_PEERPIDFD` 均指向父进程，不指向子进程。
3. 子进程自己 bind/listen：客户端 peer 与 pidfd 对应子进程；子进程退出并 wait 后，pidfd poll 报告退出。
4. 创建监听 socket 的进程 fork 后代，由后代 accept/响应：客户端 peer 仍是创建者，实际响应进程却是后代。验证脚本明确让创建者保持存活，避免把退出或 PID 复用误当作解释。

5. fork 前创建的 `SOCK_STREAM` socketpair，子进程发送一个字节，父进程 `recvmsg` 在启用 `SO_PASSCRED` 后收到该子进程的 `SCM_CREDENTIALS`，而 `SO_PEERCRED` 仍为创建者。日志 `20261027-200909-30634d.log`；这是单次简单发送，不证明流分帧或多发送者交错时可以无歧义地认证完整请求。

结论：将启动前创建的 socketpair 的 `SO_PEERCRED` 当作子进程证明，以及把客户端 peer/pidfd 等同于实际 accept/响应进程，均不成立。`SCM_CREDENTIALS` 是该次隔离实验的数据报发送者证据，不是代码来源证明；不能据此推断 root/capability、PID namespace、流式分帧、文件描述符传递或任意平台上的完整授权语义。

## 对可实施方案的收紧

- 初步方向仍是独立可信监督器受控启动，并用 `pidfd_open` 或等价机制在**子进程创建时**持有与创建结果相联系的身份；创建时竞态、系统调用兼容和失败清理仍待实际设计。不要把 pidfd 的整数 PID 当成可长期存储的身份。
- 私有启动通道不能依赖 fork 前 socketpair 的 `SO_PEERCRED` 证明子进程。可研究启用 `SO_PASSCRED` 的有界数据报、逐消息获取内核凭据并与受控子进程身份绑定；必须验证 PID namespace、权限、截断、异常消息、描述符泄漏及对端死亡。不接受只含自报 PID/摘要/token 的消息；可信 root 权限不提供对恶意 root 的防护。
- 如果继续使用公开连接的 `SO_PEERCRED`，socket 必须由受控子进程实际创建与绑定，且明确检查服务监听及已接受 FD 的 fork/exec/传递策略。**即使 child bind 成功，peer 凭据也只提供 socket 创建关联，不能证明后续消息实际由这个进程处理。** 需要协议和实现确保处理者，或使用逐消息实际发送者凭据并验证其与受控进程的对应；不能依赖单次 peer/pidfd 检查来省略这项保证。
- 监督器启动证据必须说明谁持有 pidfd、谁验证启动字节、如何识别 systemd invocation/进程退出以及重启后旧记录失效。监督器退出但子进程继续存在时，旧证据如何失效、是否允许新实例启动、残留 socket 和 guard 如何处理仍未解决。不得将方案标记为已证明或开放派发。
- 即使处理者身份可证，也不取代事务围栏：实例锁→包锁→服务锁；锁内复核身份、批准和冲突 intent；唯一 intent 文件与父目录 fsync 后才跨释放锁/请求 worker 的间隙；所有入口和新实例均需在锁内遵守 intent。未确定 fsync、请求结果或进程树回收时保持 unknown，同 ID 显式对账，不因锁空闲、PID 消失或超时自动重放。

## 后续验收门禁

1. 对监督器父进程的实际 fork/exec 与候选内存字节执行顺序给出实现级设计，定义在子进程获得运行许可前监督器已验证的证据；证明验证后不从可变路径重新加载。
2. 用真实父子进程验证私有消息凭据、pidfd 绑定、对端与监督器先后退出、继承/传递 FD、子进程抢先退出和 namespace 条件；缺失或不支持一律拒绝。上述探针只是语义实验，不是完整验收。
3. 审查 daemon/start、guard/sender、登记恢复、dispatch/apply、worker、包钩子等所有入口的描述符、锁序和 intent。对继承监听或连接 FD 的真实代码给出防泄漏证明及负向测试。
4. 隔离 systemd 故障注入覆盖守护进程与监督器退出、后代存活、挂起、响应丢失、fsync 中断、重启、迟到 worker 和重复 ID；证明 unknown 保持封锁并只能基于准确证据显式对账。
5. 上述全部通过后，另行完成 P1 其他包与激活门禁并按有效授权审查，才能讨论调整 default-deny；本设计、探针和文档不授权生产派发或真实设备操作。
