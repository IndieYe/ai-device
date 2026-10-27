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

## 消息边界及监督器退出的补充实验

`scripts/probe-native-message-boundaries.py` 在同一无网络隔离 Linux 6.18.15 amd64/Rosetta 环境中验证（`20261027-201228-eeb8a8.log`）：

- 预创建的 `SOCK_SEQPACKET` socketpair 上，两个真实子进程通过同一端各发送一条记录；接收方开启 `SO_PASSCRED`，两条记录各携带其实际发送进程的 `SCM_CREDENTIALS`。这支持进一步研究有界记录协议，但不证明特权凭据伪造、PID namespace、进程创建竞态或生产授权。
- 缓冲区短于记录时出现 `MSG_TRUNC`；没有足够的附属数据缓冲时出现 `MSG_CTRUNC`。生产接收器应对任一标志、缺失/重复凭据、未知附属数据、超长或格式错误记录一律拒绝，不使用部分数据授权。
- 带 `SCM_RIGHTS` 的记录会实际交付文件描述符；任何拒绝路径仍需关闭所有已接收 FD。实验只展示交付并显式关闭，不证明所有截断或异常路径无泄漏。
- Python 创建的非 inheritable FD 在 `fork` 后仍存在；常规 `exec` 不继承，显式 `pass_fds` 可保留。故 FD_CLOEXEC 不能防止 fork 后子进程使用通道；必须逐一审查 fork、exec、pass_fds 和 FD 传递。没有 OS 沙箱时这只约束可信实现，而非抵御恶意 root。
- 监督器 fork 子进程后退出，后代仍持有端点并可向观察方发送消息；因此通道存在、收到消息或 EOF 未出现都不证明监督器存活。新启动/恢复必须重新确认独立的监督器身份，并在失联或证据不确定时拒绝准入；不能用这些实验声称已有完整失败关闭实现。

## 对可实施方案的收紧

- 初步方向仍是独立可信监督器受控启动，并用 `pidfd_open` 或等价机制在**子进程创建时**持有与创建结果相联系的身份；创建时竞态、系统调用兼容和失败清理仍待实际设计。不要把 pidfd 的整数 PID 当成可长期存储的身份。
- 私有启动通道不能依赖 fork 前 socketpair 的 `SO_PEERCRED` 证明子进程。可研究启用 `SO_PASSCRED` 的有界数据报、逐消息获取内核凭据并与受控子进程身份绑定；必须验证 PID namespace、权限、截断、异常消息、描述符泄漏及对端死亡。不接受只含自报 PID/摘要/token 的消息；可信 root 权限不提供对恶意 root 的防护。
- 如果继续使用公开连接的 `SO_PEERCRED`，socket 必须由受控子进程实际创建与绑定，且明确检查服务监听及已接受 FD 的 fork/exec/传递策略。**即使 child bind 成功，peer 凭据也只提供 socket 创建关联，不能证明后续消息实际由这个进程处理。** 需要协议和实现确保处理者，或使用逐消息实际发送者凭据并验证其与受控进程的对应；不能依赖单次 peer/pidfd 检查来省略这项保证。
- 监督器启动证据必须说明谁持有 pidfd、谁验证启动字节、如何识别 systemd invocation/进程退出以及重启后旧记录失效。监督器退出但子进程继续存在时，旧证据如何失效、是否允许新实例启动、残留 socket 和 guard 如何处理仍未解决。不得将方案标记为已证明或开放派发。
- 即使处理者身份可证，也不取代事务围栏：实例锁→包锁→服务锁；锁内复核身份、批准和冲突 intent；唯一 intent 文件与父目录 fsync 后才跨释放锁/请求 worker 的间隙；所有入口和新实例均需在锁内遵守 intent。未确定 fsync、请求结果或进程树回收时保持 unknown，同 ID 显式对账，不因锁空闲、PID 消失或超时自动重放。

## 隔离逐记录拒绝原型

`scripts/probe-native-strict-record.py` 仅用于实验：使用 SEQPACKET、SO_PASSCRED、有限缓冲和 recvmsg，拒绝空/超长记录、`MSG_TRUNC`/`MSG_CTRUNC`、非唯一凭据、错误 PID/UID 与意外附属数据；收到 SCM_RIGHTS 时关闭可见 FD 后拒绝。隔离 Linux 容器运行结果 `STRICT_RECORD_PROBE_OK`，验证正确 sender、错误 sender、截断、一个意外 FD 和 `/proc/self/fd` 数量无泄漏；日志 `20261027-201451-fa63ab.log`，ruff 通过。

该原型的 expected PID 来自测试父进程，不与 pidfd、可信启动或 systemd invocation 绑定；没有实现完整消息解析、恶意 root/capability、namespace、错误注入、附属数据截断时全部 FD 内核行为、进程死亡竞争或监督器故障关闭，不可进入生产调用链。特别注意 fork 后 FD 能继续使用、PID 可复用，不能把该脚本的整数比较用作授权。

## 受控父子进程原型

`scripts/probe-native-supervised-start.py` 仅在隔离容器 PID 1 环境运行：监督器为自身取得 pidfd，fork 子进程后取得子进程 pidfd，在未回收直接子进程期间接收 SEQPACKET ready 并核对 SCM_CREDENTIALS 与子 PID/UID，再确认两个 pidfd 未报告退出；受控门闩释放后，子进程使用继承的监督器 pidfd 检查监督器是否已退出。实测监督器存活时子进程报告 A，监督器先退出并被回收后遗留子进程报告 R；`SUPERVISED_START_PROBE_OK`，日志 `20261027-201815-c7ac14.log`，ruff 通过。

该原型未执行或校验实际候选字节，未创建 systemd unit、绑定 invocation、服务端 socket、锁或持久 intent；其 A 仅代表一次瞬时存活观察，**无法排除检查后立即死亡**。父进程的 `pidfd_open(child_pid)` 在这个实验中依赖直接子进程未被其他线程回收，不能直接推断并发生产环境中 PID 不会复用。探针的门闩由外部测试者控制并非实际生产协议，pidfd/消息可信链、异常清理及遗留进程收口仍未完成。必须依靠持锁发布的持久 intent 及所有参与者的共同协议跨越不可原子化的死亡间隙；不能以该原型的 A 打开派发。

## 现有代码的局部锁序核查（非闭环证明）

- `Helper.handle` 的 start 路径在实例管理锁下取得 execution lease 与共享 package lease，再检查 `_assert_no_package_intent` 并 `_save_new` start intent；需要启动时才在实例 service lease 下调用 sender，向 sender 传递 execution 与 package fd。该描述限于所读代码，不证明进程树全部故障均收敛。
- `dispatch_job` 先实例事务，再无条件 `_require_proven_job_fence` 拒绝；其当前不可达的新派发路径在包独占锁下检查 pending intents、保存 job intent，释放租约之后才调用 systemd。`_save_new` 对文件及父目录 fsync 并 hardlink no-replace，发布/持久化异常仍需按 unknown 对账。
- `Updates.apply` 取得实例事务、包独占锁与实例 service lease 后核查 job/close、其他 intent、批准与 snapshot 等；这不证明其他入口无遗漏，也不证明实际运行的 helper 是理解围栏的版本。除非独立启动、实例与身份绑定及全入口动态验收通过，不能据此更改 default-deny。

## 真实 bootstrap 的受控启动交接：实现级设计草案

现有 `run_verified_helper_candidate` 按独立 bootstrap 身份检查、完整 snapshot 校验、将三个模块源码读取到内存并二次校验，再在**同一进程**建立模块、执行字节并调用候选 `serve`。不能仅在该函数外包 `fork` 宣称父进程证明了子进程代码：若 fork 发生在校验前，则证据由子进程产生；若 fork 在校验后，须显式控制子进程继承的内存、锁和启动通道。以下仅为待实现、待独立验证的设计，不开放派发。

1. 独立 bootstrap 监督器在单线程、确定独立解释器路径、固定 snapshot 摘要及无缓存条件下，对当前代码同等的证据做全部检查，将 `__init__`、`management_lock`、`native_helper` 的实际字节和相应源码路径保存在内存。最终检查失败不得 fork；不得将调用方提供的 PID/摘要/版本自报代替证据。若抽取校验函数，必须保持所有现有拒绝条件和二次校验，不新增可被外部传入伪造内容后直接执行的公开入口。
2. 监督器持有 daemon 锁后创建私有 SEQPACKET 通道与关闭的启动门闩，再 fork 唯一受控子进程。子进程在收到许可之前不得 bind 服务 socket、执行候选或派生后代；父进程在自身直接子进程未被其他线程/处理器回收的前提下获取 pidfd，核对由内核产生的逐记录凭据、所持 fd 的进程身份和启动实例。若获取失败、子进程先退出、凭据不符或控制通道异常，则拒绝就绪并在可证归属范围内有界回收。不能单凭 fork 返回值、整数 PID 或消息自报证明对外连接身份。
3. 父进程许可后，子进程从 fork 时继承的**已校验内存字节**创建模块、编译执行，不从可变候选路径再次 import 三个模块；其他依赖与运行时均必须证明来自独立 bootstrap 的可信路径。子进程自己创建并 bind 对外 socket；并对监听、已接受连接和私有端点的 fork/exec/传递做明确 FD 生命周期约束。实际对外 peer 和消息处理者还必须分别验证，不以 socket 创建者代替实际处理者。就绪前完成必要锁及 intent 检查，证据无效则不接受变更请求。
4. 守护锁交接须新增明确契约：现有 `serve()` 会自行调用 `_daemon_lease` 打开并加锁，父进程持锁后 fork 的子进程若再次独立 open/flock 可能与原 open-file description 冲突。不得将已持有 FD 的 inode 匹配当作持锁证明，也不能直接绕过 `serve()` 授权检查。需要专门的内部入口及明确所有权/回收测试，使父和子持有同一个已加锁的 open-file description、明确关闭时机与额外继承者；旧 guard 持有的是 execution/package 锁，不能假定代持 daemon 锁。父退出但子存活时，锁不能因父关闭而过早释放；反之若残留后代意外继承可导致锁永久占用，必须拒绝自动恢复并诊断。该选择仍待实验和安全评审。
5. 动作准入仍须连接上的活 peer 与受控启动实例、当前监督器 invocation 和策略关联；只靠子进程自行检查父 pidfd 不足，因为父可在检查后死亡。start 和 job 在既定锁序下以持久 intent 与 guard 回执处理后续并发：发布成功以文件、目录 fsync 为前提；结果不确定按同 ID 对账，任何缺失证据为 unknown。监督器退出时失效的是新动作准入，不可据此认为已开始动作未发生或清除持久 intent；新实例在锁内检查既有 intent 并保留围栏。


**锁交接语义实验**：`scripts/probe-native-daemon-lock-handoff.py` 在无网络 Linux 6.18.15 amd64/Rosetta 容器中对临时普通文件验证：独立 open 的描述不能取得已持有的排他 flock；父 fork 后关闭自己的 FD，子进程继承的同一 open-file description 仍保持锁；子进程关闭后独立 open 可取得锁。`DAEMON_FLOCK_HANDOFF_OK`，日志 `20261027-202218-ee55c4.log`，ruff 通过。实验未调用 `LOCK_UN`，未验证异常后代继承、锁文件替换、崩溃后 systemd 行为或实际 daemon 实现；特别注意任一共享描述上的显式解锁可能影响其他持有者，不能把实验结论推广为必然安全的租约交接。


当前实现已将独立 bootstrap 与候选的上述校验提取为内部 `_verified_helper_bytes`，原有公开入口仍在同一进程调用并直接执行返回字节；路径替换回归及完整 Device 回归通过。返回字节并不证明后续由谁执行，未引入 fork、监督器或新的调用授权；实际候选动态回归仍需执行。

重构后实际独立 bootstrap 与包内候选已在无网络一次性容器直接运行验证：正确摘要启动、错误摘要拒绝，peer 与启动 PID 对应；准确制品和日志见实施记录第104节。它不是 systemd 或受控监督器验收，不证明处理者连续身份或事务围栏。

当前 `_daemon_lease` 已在原加锁上下文返回实际 FD；隔离容器对真实实现验证了 fork 后父关闭、子保锁及子关闭后重获锁。该内部变化不证明 FD 未被解锁/替换、不构成持锁授权，也没有为候选 helper 增加接收此 FD 的入口；实际监督器与泄漏/死亡恢复仍未实现。
实施前需完成：真实 bootstrap 字节快照提取的既有功能及漂移回归；fork 前后验证字节与原路径替换对照；受控子进程提前退出、超时、父退出、遗留子进程及描述符泄漏/锁保持测试；systemd invocation/peer 绑定与 PID namespace 核对；start/job 在释放包锁间隙的真实并发和崩溃验收。系统不承诺对可信本机 root 的故意绕过提供 OS 沙箱。即使这些局部测试通过，正式信任根和 P1 其他门禁仍须独立完成。

## 后续验收门禁

1. 对监督器父进程的实际 fork/exec 与候选内存字节执行顺序给出实现级设计，定义在子进程获得运行许可前监督器已验证的证据；证明验证后不从可变路径重新加载。
2. 用真实父子进程验证私有消息凭据、pidfd 绑定、对端与监督器先后退出、继承/传递 FD、子进程抢先退出和 namespace 条件；缺失或不支持一律拒绝。上述探针只是语义实验，不是完整验收。
3. 审查 daemon/start、guard/sender、登记恢复、dispatch/apply、worker、包钩子等所有入口的描述符、锁序和 intent。对继承监听或连接 FD 的真实代码给出防泄漏证明及负向测试。
4. 隔离 systemd 故障注入覆盖守护进程与监督器退出、后代存活、挂起、响应丢失、fsync 中断、重启、迟到 worker 和重复 ID；证明 unknown 保持封锁并只能基于准确证据显式对账。
5. 上述全部通过后，另行完成 P1 其他包与激活门禁并按有效授权审查，才能讨论调整 default-deny；本设计、探针和文档不授权生产派发或真实设备操作。
