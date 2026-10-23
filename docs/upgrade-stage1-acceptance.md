# 单机升级器真实 Linux/systemd 验收门禁

状态：待执行。此文件是测试计划，不是运行结果或正式服务操作授权。

## 环境要求

- 独立且不承载业务的Linux主机/VM，systemd为实际服务管理器，管理员权限可用。
- 平台符合现有installer矩阵；Python3.13、uv及准确离线bundle/依赖已准备。
- 独立测试用户、unit、目录、端口、设备ID/token、临时loopback中心；不复用正式身份或状态。
- 客户端与一次性升级器处于不同unit/cgroup；软件目录不可由客户端写，state/client私密可写。
- 所有stop/start、信号、重启、空间及权限故障仅作用于该隔离环境；禁止腾讯正式机替代。
- 运行前记录OS、systemd、Python、uv、unit属性、安装指纹及测试目录；禁止打印token/env值。

## 必测矩阵

| 场景 | 通过条件 | 当前证据 |
| --- | --- | --- |
| 真实离线bundle prepare | 在线旧runner不停、候选原路径安装、旧active/config/state不变 | 本机核心/制品测试；Linux待执行 |
| 正常停切启 | 独立升级器持续存活；旧请求/后台归零后stop；准确候选ready且准入开放 | 替身与本机socket；systemd待执行 |
| 收口超时/丢回执 | 不误stop；owner意图可定位；显式resume仅恢复原实例 | 隔离注入通过；systemd待执行 |
| 8个后台任务与并发请求 | 冻结后不接新执行；重复仅回既有；收口无遗漏 | 准入与计数测试；真实完整负载待执行 |
| SIGTERM/崩溃socket | 正常清理，崩溃残留准确识别，不删除活owner/未知路径 | 本机真实临时进程通过；systemd待执行 |
| 错误unit/实例/进程 | 在任何stop前拒绝，其他服务不受影响 | 隔离条件通过；真实unit输出解析待执行 |
| 策略禁用/过窗/换版本 | prepare/stop/publish共同底线生效，拒绝不产生错误发布 | 本地测试通过；真实权限矩阵待执行 |
| 监督器在各持久断点退出 | 不盲重放stop/start/publish；记录和指针精确对账 | 文件故障注入通过；真实unit待执行 |
| start失败/实际成功但回执丢失 | 无自动回退；准确健康证据才能verified_local | 替身通过；systemd待执行 |
| 主机重启 | 在途操作不重放；服务自启/残留socket/状态可核实；明确恢复入口 | 未执行，人工恢复入口已补，真实重启未验收 |
| 空间不足/无写权限 | 不破坏旧active/state；失败或unknown可诊断，不以异常当成功 | IO机制局部测试；真实故障待执行 |
| 权限与独立性 | client不能改软件/profile；upgrade受限绑定；不与client同cgroup退出 | 未真实验收，审阅模板已补，未安装验收 |
| 整体验收 | 独立审核确认真实证据；没有把verified_local写成中心认证成功 | 未完成 |

## 收敛与交付

保存有界报告与哈希：准确wheel/bundle、OS/systemd、目录/unit/cgroup、PID代次、操作ID、各断点前后
指针与终态、独立审核报告。凭证不进入报告。保留失败现场，不清理重要状态或旧release。
环境用后清理亦须确认准确范围；不扩大到正式机、已有Worktree或远程Git。
无法提供上述真实环境时，阶段1保持未完成，不能以更多mock测试消除这条门禁。
