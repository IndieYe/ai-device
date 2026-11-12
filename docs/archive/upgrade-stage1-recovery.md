# 阶段1人工恢复：先取证、后单步授权

> **旧实例路线历史资料**：本文不是新客户端完整包的安装、恢复或验收指南。当前 Linux 路线见 [完整包开发操作指南](../development/client-native-linux-development.md)，权威需求见 [客户端安装升级需求](../requirements/client-native-install-upgrade.md)。旧监督器仍有待拆分的 Linux/Mac 职责；保留历史不表示新包支持旧协议，不应将下文命令用于新 DEB。

适用标准单client纳管实例。不是自动修复脚本，不授予真实服务操作权限。
真实服务停止仍由用户亲自执行；AI不得stop/kill。启动/发布/权限修改依本次明确范围执行。
所有升级器命令使用独立安装的准确绝对Python路径，不能用待修实例的active runner充当升级器。

## 0. 冻结变更并定位原请求

暂停中心后续批次，不换upgrade_id、不重放unknown任务、不删除记录或socket、不修改active。
核实原监督器已结束（包括独立unit/cgroup），取得独占监督租约失败则停止，不能移除锁文件。
通过稳定ID读取status、准确root及绑定unit；限定读取ActiveState/SubState/MainPID/ControlGroup/
ExecStart/EnvironmentFiles路径，不输出env值/密码。核实独立监督器与client不是同一cgroup。

`upgrade status`仅有界摘要；管理员若需查详细记录，**本地查看**root/upgrades/ID.json中的
state、supervisor.phase、recovery、expected_active，不把含baseline配置全文外发。
校验当前active完整对象、配置/身份binding、候选指纹及输出映射；磁盘版本不等于内存版本。
无running journal记录不代表OS后代已经收口，停止现场须另核cgroup/后代及未知任务。

先执行 `upgrade reconcile-supervision --root <root> --upgrade-id <原ID>`；它只读外部证据并保存
对账结果，不启停/发布。记录被改写后旧哈希失效。记录无supervisor时不能调用此入口，按下表。

## 1. 决策表

| 现场 | 允许的下一步 | 禁止/注意 |
| --- | --- | --- |
| preparing/failed、candidate为空 | 保留候选及失败日志，核依赖/空间/权限；当前ID不重做prepare | 不activate；没有自动重新构建或清理命令 |
| prepared、无supervisor、旧client在线 | 用户决定继续正常supervise或取消准备 | 不声称已经冻结准入 |
| draining、旧指针/原客户端健康 | 显式resume-admission，准入开放后cancelled | 丢回执先status；owner他人则停止 |
| stopping、旧指针、服务仍在线 | 核实实际旧客户端后由管理员决策；不再次stop | 不以新请求强行取代原意图 |
| stopping、旧指针、无expected_active、已确认服务停止 | 选择recover publish，或recover start-original | 二选一，每步单独确认；不自动串联 |
| publishing/unknown、有expected_active、仍为完整原指针 | 先对账；审阅新Hash后recover publish完成原意图 | 原样复用完整expected_active，不重新生成activation_id；不start、不回退 |
| 准确expected_active、候选未运行、服务确实停止 | recover start-candidate | 发布和启动分两步；不得从此启动旧版本 |
| 准确候选运行、ready、owner=null | reconcile-supervision → verified_local | 不再start；仍不代表中心认证 |
| 候选运行但owner非空 | 保留unknown，查原owner及实际任务现场 | 不无条件resume别的owner；不报健康 |
| original恢复启动已成功/回执丢失 | 核实原进程后resume-admission → cancelled | 不重复start；不改变active |
| 指针/配置/unit漂移或其他phase | 保留现场、停止动作、管理员核实来源 | 不手改JSON/expected_revision绕过 |

prepared无supervisor、preparing失败等候选退役不在本恢复命令内，不靠把failed改prepared来继续。

## 2. 受控的单步恢复入口

`recover`仅提供publish/start-original/start-candidate，**没有stop、rollback或任务重放**。
先确认准确绑定服务inactive/failed且MainPID=0；发布仍需安装独占租约证明旧runner未持锁。
start步骤同样须人工核实cgroup无旧后代；MainPID=0不是任务安全收口的全部证明。

每步要求**刚审阅过的原记录SHA-256**，避免旧计划在已变现场被重复使用。管理员本地计算：

```bash
sha256sum '<root>/upgrades/<原ID>.json'
```

随后使用准确独立解释器（示例不是可直接执行的正式目标）：

```text
<独立python> -I -B -m device_runtime.cli upgrade recover
  --root <标准root> --upgrade-id <原ID>
  --action <publish|start-original|start-candidate>
  --expected-record-sha256 <已审阅记录哈希> --confirm-service-change
```

- publish：仅旧指针精确匹配时；先保存恢复意图，再复用原候选/策略/独占安装锁校验。
  无expected_active时生成一次准确pointer（P0）；已有expected_active时，严格核验候选字段、
  原activation_id及原输出映射Hash后原样完成发布（P1），不生成新身份、不换upgrade ID。
  安装锁内重新核验记录、完整baseline及候选，漂移即拒绝。仅发布，不start。
  active已等于expected_active时拒绝再次发布，应先对账后单独start-candidate（P2）。
  再次写前失败仍保留同一意图；写后回执丢失按P2处理。失败后先对账、审阅新Hash，
  不拿旧Hash盲重试。普通activate不接受unknown/publishing重放。
- start-original：只在从未产生发布意图、当前仍为原指针时；不修改pointer，**不是回滚**。
  start后保存draining，核实原客户端后执行resume-admission关闭升级为cancelled。
- start-candidate：必须准确expected_active已发布，候选、策略、配置与unit匹配；仅start，
  不立即标成功。随后reconcile-supervision核实实际候选ready、身份、解释器及准入开放。

每步动作前保存意图；进程退出或回执丢失先对账，旧哈希必拒绝，不把相同命令原样重发。
如果已启动但记录停在recovering-original，resume-admission可在准确原指针与健康条件下恢复。
如果发布或start失败，可能保留unknown及manual_recovery_unconfirmed；保留完整发布证据。
重新审阅哈希是必要条件，不是充分授权；须先确认前一动作实际结果和继续是否安全。

恢复发布或候选启动仍检查本地窗口/通道/版本。start-original不执行候选发布，不要求候选窗口，
但仍要求管理员profile启用与原绑定；profile禁用时需管理员决策，不能通过删profile绕过。

## 3. 主机重启后

禁止enable升级器或Restart=on-failure自动重跑一次性任务。先按0取证，再按1决策：
客户端若已按WantedBy自启，先对账，不再发start；候选健康且准入开放才verified_local。
若原指针未变但旧任务未知，不重放其业务命令。死socket由客户端生命周期租约+liveness路径处理，
不手工rm活socket/锁。遇无法证明的OS后代、文件系统持久性或指针差异，保持unknown交管理员。
本流程代码与隔离替身验证不替代真实断电、systemd及重启验收。

## 4. 完成后的事实

记录本地最终状态、准确release/activation_id、unit/cgroup/PID证据、任务unknown处置及后续动作，
不保存凭证。verified_local仍需阶段2中心重新认证；cancelled仅表示原准入恢复且本次升级取消。
不清旧候选/环境、历史journal、Worktree或归档；清理和正式部署需另行授权。
