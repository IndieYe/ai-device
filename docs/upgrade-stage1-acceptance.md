# 单机升级器真实 Linux/systemd 验收证据矩阵

状态：**部分场景通过，阶段1整体未完成；未部署生产**。更新日期：2026-10-25。

本文件同时保留必测条件、已执行证据和剩余缺口，不是服务操作授权，也不因整理文档自动延期任何门禁。
本轮为文档对账，依据既有验收回执与台账；未重新运行远端测试或重新下载、校验所有报告。

## 本次限定范围与独立验收

用户于2026-10-25明确确认按限定范围收口并开始独立验收，不部署。范围限于已测Ubuntu24.04/glibc2.39环境中的单机升级机制，以及本矩阵列明的准备、拒绝、排空、切换和恢复路径。
主机重启/开机恢复、跨Device软件版本兼容、最低平台与长期负载明确延期，不标为通过；其他未测故障变体保留为已知限制。本次范围调整不改变未来支持承诺所需验证。
当前仅开始独立整体验收，**尚未获得整体验收通过结论**；不得将用户同意范围视为用户确认验收通过，也不授予部署/合并/推送授权。

## 基线与证据口径

- 当前源码基线：`59407a91013783f27974ffb52e2439af218f9f29`（P1持久发布意图恢复修复）。
- **A／早期制品**：安装器修复 `001e64874ece6fdcd963ac84175d6cbca1c0bfa2`（提交定位以台账为准）；wheel SHA-256 `6302eafa7d76da8d1da18b7ce8d03577ba6729b7fed62347a500708f93181dda`。不含后续P1修复。
- **B／当前修复制品**：上述 `59407a9`；wheel SHA-256 `7577f3aa446fddddede930b3960d91557be7e47e48f295de724d105698810f7e`，22个runtime模块匹配。A/B版本号均可能显示0.2.2，不能只按版本号识别制品。
- 源码回归685项及独立专项审核属于代码证据，不替代真实环境验收；早期652项属于历史批次，不代表当前全量数量。
- “通过”仅指列出的场景；“有限通过”表示复合门禁只覆盖部分路径或采用测试包装注入。没有把A证据扩大为B全矩阵重跑，也没有用报告数量计算完成比例。
- 当前试件主要为相同软件字节的post标签／release切换，不证明跨Device软件版本兼容。

## 环境与安全边界

- 独立、不承载业务的 tencent-bj 测试主机：Ubuntu24.04 x86_64、glibc2.39、systemd255、cgroupv2，持久CPython3.13.12；不代表glibc2.27最低平台已通过。
- 独立用户、unit、目录、端口、设备身份和loopback测试中心；中心沿用旧测试工具链，不是新中心制品验收。
- 客户端UID999；软件、root管理的profile和私密state分离。一次性升级器位于客户端unit/cgroup之外。
- 每组资源门禁：可用内存至少600MiB、根盘可用至少10GB；不填满根盘，不降低门禁。
- 所有正常停启、信号及文件系统故障仅限准确授权的隔离试件；不得用正式机替代。报告不得包含凭证。
- 以下PID和策略仅为最后验收快照，不是持续健康承诺：测试客户端runner302771/child302772，中心168388，客户端Restart=on-failure、NRestarts0；profile恢复allow_upgrade=false。

## 必测矩阵

| 场景 | 判定 | 已执行证据／制品 | 尚未覆盖或限制 |
| --- | --- | --- | --- |
| 原生安装、在线离线bundle prepare | 通过（限定环境） | A原生安装、rebuild/restore/upgrade；B write03真实prepare，旧active未变 | 无uv自动安装、最低平台及跨软件版本不在结论内 |
| 正常停切启 | 通过（限定场景） | A正常监督器：排空后真实stop/start、健康/在线；B P1及write03真实恢复切换 | A正常路径未独立采样瞬时旧cgroup全部成员；不宣称全部路径B重跑 |
| 收口超时／丢回执 | 有限通过 | A drain-c01真实维护口返回后，调用侧包装丢回执；显式resume且未误stop | B drain-timeout01真实12秒任务、1秒期限，约1.03秒超时后真实resume，任务自然成功并显式收敛cancelled；单任务短时，丢回执仍非真实网络丢包 |
| 8后台任务、并发请求及去重 | 通过（短时限定） | A真实8任务排空/前中后同ID去重、request额度；查询额度为A已安装DeviceClient直接准入+真实LocalExecutor+测试查询屏障（后续成功报告）；B disconnect01真实单socket断连重连去重 | 查询额度不是中心API/WebSocket/systemd入口饱和验收，也不是B重跑；不代表长期高负载、广域网络或中心崩溃 |
| 正常退出／崩溃socket | 有限通过 | B exit-recovery01运行中任务退出后unknown且不重放；crash-socket01空闲SIGKILL、残留socket拒绝连接、单次启动恢复 | 活owner／未知路径拒绝及运行中任务崩溃未完整实测；不是主机重启或自动重启验收 |
| 错unit／实例／进程绑定 | 有限通过 | B negative-bind-policy01：真实Systemd.show查询两个测试unit，runner根路径不符拒绝；profile根路径不符拒绝；无服务动作尝试，原实例未变 | negative-live01另以真实运行实例状态包装外部真实PID，真实cgroup归属检查在排空前拒绝；非真实客户端错误上报或完整进程绑定矩阵 |
| 策略关闭／过窗／版本或指纹不符 | 有限通过 | B negative-bind-policy01：真实prepare和activate分别拒绝关闭/过窗/版本/指纹不符；supervise策略关闭拒绝；active/记录/原服务不变 | negative-live01真实prepare/drain后关闭策略，stop前拒绝并显式恢复准入；仍未测排空后过窗/版本/指纹各变体 |
| 监督器持久断点／真实退出 | 有限通过 | A P0/P2包装抛错；B P1意图持久后抛错恢复；supervisor-exit02真实os._exit(73)绕过finally后恢复 | 非所有断点均真实进程崩溃；不代表掉电/fsync耐久性 |
| start失败／成功但丢回执 | 有限通过 | A recovery-a01在真实start前包装抛错；b01真实start成功后包装丢回执，对账不重启 | 非真实systemd启动失败／传输丢失，且非B全矩阵重跑 |
| 无写权限／只读文件系统 | 通过（明确故障点） | B write-failure01真实UID999 EACCES；write02真实activate EROFS→unknown/拒重放；write03真实监督器→EROFS→Hash钉住recover publish/start→verified_local/在线探针 | 失败点为active临时文件创建；不是任意写入阶段或任意状态事务恢复 |
| 空间不足ENOSPC／中途写入故障 | 有限通过 | B enospc01：私有namespace内1MiB tmpfs真实耗尽，activate在意图落盘后写active报errno28；旧active不变，对账unknown并拒重放，释放填充后写入对照成功 | 无服务静态prepared试件，记录位于外部可写目录；非持久磁盘耗尽、inode耗尽、监督器恢复、fsync/rename故障或掉电 |
| 主机重启／开机恢复 | 未执行 | 人工恢复入口已有；试件未enable | 需整机可重启与准确启用范围专项确认，不能推断开机自启 |
| 权限与独立性 | 有限通过 | A真实UID999、沙箱root只读/state可写、socket0600/state0700/profile root0600；独立监督器；B真实退出与恢复 | 完整错误绑定/权限负向仍缺；不是最小权限broker或正式旧布局纳管验收 |
| 跨版本、非终态与输出映射兼容 | 未完成 | 有本地结构校验与输出映射回归；真实场景有历史任务/标记保留 | 缺跨Device软件版本、非终态兼容、多代输出map/损坏真实负向 |
| 最低平台／长期负载 | 未执行 | 当前Ubuntu24.04/glibc2.39及短时并发 | glibc2.27、长期稳定性未证实；需按支持承诺裁决，非自动延期 |
| 独立整体验收 | 未完成 | P1代码专项审核已过；各场景报告已存 | 尚需对最终证据矩阵及实际制品进行独立整体验收，不以verified_local代替中心认证 |

### 查询额度的证据纠错与限定口径

原冻结矩阵误将首轮报告作为通过证据；该报告实际为`FAILED_OR_INCOMPLETE`，功能断言完成后因测试函数包装器缺少`aclose`而清理失败、退出码1，不能改称PASS。原失败报告及独立审计均保留。

后续仅修正测试包装器的`__call__/aclose`及重复关闭问题，报告为PASS。原审核者已独立核对后续报告、宿主验证和首轮审计的实际字节与Hash，并核验两轮各保持8查询、第9查询拒绝`client_capacity_exceeded`、查询占满期间普通执行exit0、16/16释放查询exit0、每轮查询计数归0及恢复成功；清理close count=1、task manager关闭、client tasks=0、进程exit0且无测试残留。此为历史证据核验，未重跑测试或核验当前健康。

结论仅支持A早期制品的已安装DeviceClient直接准入与真实LocalExecutor，在测试查询屏障下的查询容量、额度分离、释放后恢复和清理。不支持中心API/WebSocket/systemd服务入口查询饱和、B当前制品重跑或长期负载。独立补证结论不等于原冻结矩阵通过；本文修订仍需新冻结版本的限定复核。

## 报告索引

路径均为独立测试主机上的历史证据，摘要为执行时已记录的SHA-256，不表示本次重新校验。
`I=/opt/device-native-qual-ses13838/installer-fix-001e648`，`P=/opt/device-publish-p1`。
未记录摘要的报告明确留空，不用推测值补齐。

| 场景 | 制品 | 报告路径 | SHA-256（已记录） |
| --- | --- | --- | --- |
| 原生systemd | A | `I/systemd-health-report.json` | `未记录` |
| 维护口权限 | A | `I/maintenance-health-report.json` | `未记录` |
| 8任务排空/去重 | A | `I/eight-drain-dedup-report.json` | `未记录` |
| 请求额度 | A | `I/request-capacity-report001.json` | `121dcd2dc25e6cce23d3d4906a1e31340e5d86215500c85eccb094cbcf2eeb38` |
| 查询额度后续成功（直接准入） | A | `I/query-capacity002-_3fps04z/query-capacity-report.json` | `6a2dcebed71d843b3260b06c18cbd15f5ed2e9f6f9bfbe2cad773d7380561a55` |
| 查询额度后续宿主验证 | A | `I/query-capacity002-_3fps04z/host-verification.json` | `9e92797443f1afc05bab355e64cec395008c69f30dd8c95f80006c73d209b6df` |
| 查询额度首轮失败（保留，不计PASS） | A | `I/query-capacity001-9kwetmkc/query-capacity-report.json` | `b8171992bab3afc4c717c65e310536fbfdf02523252d5859d15dc4a58da7bcc3` |
| 查询额度首轮证据审计 | A | `I/query-capacity001-9kwetmkc/evidence-audit.json` | `07cc2096bf1bf9246f3af672fc706d88d1240a26a1c825072aec78d5370a2003` |
| 离线重建 | A | `I/rebuild-accept001/rebuild-report.json` | `f32a25188669d53a2a393475f8331fa21f3a02c660af97bcb6bfee022adebdd3` |
| 备份恢复 | A | `I/restore-accept001/evidence/final-report.json` | `8ea5da051d24b6041e407c4eee385f86803f2062be351c45b4bb652a56903e90` |
| 离线升级 | A | `I/upgrade-accept001/evidence/upgrade-report.json` | `e1e65e1d6d46e0caf516f7c9b5332cf79fdd9260d5e36ec2961cd7108884af74` |
| 正常监督器 | A | `/opt/device-supervisor-001e648/final-report.json` | `b96ca7af3d8ad1cfc946dfeea8a7d5beddb5c08b3c8eb38a4d25cd2f1f1af4b9` |
| 收口丢回执/resume | A | `/opt/device-drain-c01/final-report.json` | `f09a717b4e1b761fe548887d981e603eb2e5cd22578fedf8ecb5a48517b6bb57` |
| 正常退出恢复 | B | `P/exit-recovery01/final-report.json` | `5cad2d8a8b55f5ba2fec96b5a9338d01a25ad8c20dca3202a5455321b11c65ed` |
| 监督器真实退出 | B | `P/supervisor-exit02/final-report.json` | `704bf1b0e4b952ae2e98e81005639d64b034290ed734fa3f88b7d52fcec957c3` |
| 真实断连去重 | B | `P/disconnect01/final-report.json` | `77944fcfe7f55e9728e0226e5efe47166cb0bd7baddf6f34b109800fc5758388` |
| 崩溃socket | B | `P/crash-socket01/final-report.json` | `fc360803916b858c7b556600346df70901dc75973f21f9546e91b3feeb60bb30` |
| 真实EACCES | B | `P/write-failure01/final-report.json` | `eb4510fe930a46a73b88a77b1d86858e96e39c38b129c03f3e8eeb1be897d0bb` |
| 发布EROFS拒重放 | B | `P/publish-write02/final-report.json` | `3bd2d1a8217ebc50dd548adb8caac016251e58a64d837e96fe6cd331f0b03e76` |
| 监督器EROFS恢复闭环 | B | `P/publish-write03/final-report.json` | `068a8e45ed372d1bdf156027eba834e3b0efa81daafff7d42151933dca89ee28` |
| 真实收口超时 | B | `P/drain-timeout01/final-report.json` | `f99a54430cbfe50505435d86b7b51d1a375877d19e2b615b46f11f581fea4a8c` |
| 进程归属/排空后关闭策略 | B | `P/negative-live01/final-report.json` | `57dfca779448a0e28d9c229324972f68020373c67b8709f73aaadc09f6843805` |
| 最小空间不足 | B | `P/enospc01/final-report.json` | `843895c21b79620bb86f01b83216a9f43775995a19c38bdb1727b8adf3360514` |
| 最小错误绑定/策略拒绝 | B | `P/negative-bind-policy01/final-report.json` | `e031f73188b38a0191a302eff3bb2ee1e16eb1375de6a14c1a588ed3531d9fdf` |
| start前失败 | A | `/opt/device-recovery-a01/final-report.json` | `2d9611112f000770d010f179436bb4fa567ad2f819428ccd5cdd0ef480998245` |
| start成功丢回执 | A | `/opt/device-recovery-b01/final-report.json` | `e91efe6834f7a46ead863080db9f89f7247d212d8e549a0a3122158cf1eee2bd` |
| P0发布意图前 | A | `/opt/device-publish-p0/final-report.json` | `bdaa383015babf06b5d5eadef834d7284303950c91745e2aecb55cc54d1bdf85` |
| P2指针后回执前 | A | `/opt/device-publish-p2/final-report.json` | `d80c0e5872a23bf13d4bd97db79e745ad86dab96b0e5b22461c111487a24c9b0` |
| P1意图后指针前 | B | `P/final-report.json` | `1eaa5698035a5f9e0cba78046e3646331c3aec011e8a5c3ac5542adbcc1b2fd8` |

### 最新恢复闭环定位

- `publish-write03`：真实prepare→真实Systemd排空/stop→真实EROFS→unknown/stopping→Hash钉住CLI recover publish→单独start-candidate→真实reconcile-supervision为verified_local→loopback在线及UID999探针成功。
- release：`0.2.2.post6-ba82ab257e2f463fbaf906c225629f67`；activation：`bf8f36037c4b4c5da170447ed6faf750`，恢复前后完整复用expected_active。
- 最终记录SHA-256：`964a3b934965bade8af3143ba7563a22136cff1503284c911f3bdf1ecd95ff44`；active SHA-256：`124484329359415af88116fcb95bf270fd300f3ea668d71eb9765058df046406`。
- 故障观察包装仅记录真实_save异常并原样抛出，不模拟EROFS；私有mount namespace退出后无宿主挂载残留。策略原样恢复关闭。
- 先前write02无服务试件没有监督器历史，unknown记录原样保留；没有事后补字段冒充真实恢复。

### 最小拒绝验收定位

`negative-bind-policy01`在独立静态试件中完成12项预期拒绝：两个真实测试unit的runner根路径不匹配、profile根路径不匹配、supervise策略关闭，以及prepare/activate各自的关闭、过窗、版本和指纹不符。
实际Systemd.show查询2次，真实校验逻辑返回预期UpgradeError；Systemd.action设有安全拦截，任何调用都判失败，实际调用数0，未模拟成功停启。
每项检查试件active/准备记录未变、无意外prepare记录或临时残留；原实例active/profile/write03记录、服务PID/状态/启动时间均不变，维护口仍ready且owner为空。试件策略最终关闭。
本组候选记录由既有准确候选构建、试件无运行服务，不可将静态布局与真实unit查询说成全部live进程绑定验收；不包含排空后策略变化或同根目录错误PID/cgroup。

### 最小空间不足定位

`enospc01`只在私有mount namespace的1MiB tmpfs中填充，填充写入上限2MiB，实际写入1032192字节后空间为0；不填根盘。真实activate先保存publishing/expected_active，再写active收到ENOSPC（errno28），旧active完整、无临时残留。真实reconcile为unknown且重复activate拒绝；释放仅本试件填充文件后，真实_save写入对照成功，未重放发布。
候选prepared记录由既有准确候选构建，记录目录外部可写；试件无服务。退出后tmpfs与挂载释放，原实例active/profile/记录、服务PID/状态/启动时间不变，维护口仍健康。此证据不等于真实磁盘或升级记录与active同时空间不足的验收。

### 进程归属与排空后策略失效定位

`negative-live01`使用运行中独立测试客户端及真实prepare记录。第一例读取真实维护口状态后，仅在调用侧包装PID为验收进程的真实PID（已确认在客户端cgroup外），真实Systemd/cgroup检查在drain前拒绝，准备记录未变；不是让客户端真实上报错误PID。第二例真实drain返回drained后，由包装同步关闭真实profile，监督器在stop前策略重检拒绝，保存unknown/draining且没有expected_active。
随后仅为恢复准入临时恢复有效测试策略，真实resume-admission收敛cancelled/admission_resumed，维护owner清空；finally原profile完整恢复关闭。服务动作拦截实际调用数0，active、原write03记录、客户端/中心PID及启动时间不变。没有停启或发布；不是随机竞态、收口超时或全部策略变体测试。

### 最小真实收口超时定位

`drain-timeout01`真实prepare后只提交一次12秒后台任务，真实supervise期限1秒。维护口多次报告active_tasks=1/drained=false，约1.02985秒后产品真实resume，监督器保存unknown/draining、无expected_active；没有服务动作尝试或发布。任务自然成功结束、marker为一次once换行，再经真实resume-admission收敛cancelled/admission_resumed；profile恢复关闭，owner为空，active/原记录/服务PID和启动时间未变。
首轮验收在上述超时已发生后，因bash.get多传timeout字段收到HTTP422；原failure-report.json保留。对账确认执行与超时已发生，仅以新查询ID和合法参数查询旧任务，不重发execute或supervise。该结果不代表永久挂起、高负载或所有超时路径。

## 失败证据与结论边界

- crash-socket01信号命令返回辅助进程错误，但真实旧PID消失/systemd信号9已确认，未重发信号；socket inode复用导致首版验收断言错误，原失败保留，改以新PID、活动租约和请求成功核实，未重启重跑。
- write02首版父bind遮住release子mount，产品在候选校验阶段拒绝；确认prepared/active未变后纠正挂载顺序，保留失败回执。
- supervisor-exit01缺sudo PATH中的uv，在prepare前期失败，未stop；新ID02在准确PATH下验收。P1期间资源门禁不足与HTTP库假设错误均保留；不计产品通过或缺陷修复。
- A/B中测试包装故障、真实进程退出、真实文件系统拒绝分别记录，不能互相替代。报告PASS不证明正式中心或真实生产设备已更新。

## 剩余门禁与交付

1. 最小错误绑定/策略拒绝及negative-live01有限通过：真实cgroup检查拒绝包装的外部PID、真实drain后关闭策略在stop前拒绝；非完整真实错误身份上报矩阵，也未穷举排空后过窗/版本/指纹变体。最小真实收口超时专项已通过（单任务短时边界）；后续按准确授权推进。
2. 主机重启、跨版本与最低平台/长期负载仍未完成；ENOSPC已有tmpfs限定证据，不扩大为持久磁盘/监督器完整恢复通过。用户已在上述限定范围中明确批准主机重启、跨版本、最低平台及长期负载延期；不代表这些门禁通过，其他限制保留。
3. 基于本矩阵及准确制品完成独立整体验收；代码专项审核不是整体验收。
4. 保存有界报告、制品/环境/指针/记录Hash和失败现场；不输出凭证，不自动清理历史或旧release。
5. 源码和当前制品的本地回归通过不代表生产已部署。部署、真实服务操作、Git提交/合并/推送和环境清理均按各自授权处理。

本机运维索引（非产品运行依赖）：`handoffs/device-bj-lowmem-4zip32wn/progress.json`（相对本机项目数据父目录）。其中历史嵌套not_done/next可能已过时；以本矩阵的明确场景、报告和制品边界及台账最新专项记录对账，不机械使用旧待办。
