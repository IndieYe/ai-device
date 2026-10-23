# 阶段1：单机升级器（隔离开发，未部署，未完成验收）

## 目标与当前边界

中心负责后续编排，本机独立一次性升级器负责本地准备、任务收口、切换与本机验收。
本阶段最多 `verified_local`，不是中心重新认证成功，更不是整体升级成功。
禁止自动回退、重放 unknown、移动 venv、清理旧候选或覆盖旧 runner 自动纳管。
没有新增中心升级协议、自动更新、MCP/ops 混批或任意安装脚本入口。

当前代码覆盖准备、持久发布、指针对账、本机维护口、Linux/systemd 监督器、有限恢复及本地策略。
已有源码与准确安装 wheel 的隔离测试、独立审核整改，但**真实 Linux/systemd 验收尚未执行**。
本文的命令与样例不是正式部署授权。`supervise` 会真正停止和启动绑定服务，不可在正式实例试用。

## 命令与副作用

所有命令显式传入 `--root <标准实例绝对目录> --upgrade-id <稳定ID>`。

| 子命令 | 额外参数 | 行为 |
| --- | --- | --- |
| `prepare` | `--bundle <准确bundle>` | 在线构建独立候选，保存操作，不改 active/config/state |
| `status` | 无 | 只读记录，不制造缺失记录 |
| `activate` | `--stopped` | 独占安装租约后发布准确指针，不管理服务 |
| `reconcile` | 无 | 仅 publishing 阶段精确指针对账，不发布或管理服务 |
| `supervise` | `--confirm-service-change` | 冻结准入、收口、stop、发布、start、验证本机健康 |
| `resume-admission` | `--confirm-service-change` | 仅 draining 意图核实原客户端后恢复准入，结果 cancelled |
| `reconcile-supervision` | 无 | 读外部证据并保存对账记录，不 stop/start/publish |
| `recover` | `--action`、`--expected-record-sha256`、`--confirm-service-change` | 人工单步发布或启动，不 stop/rollback |

ID 必须复用：同ID同bundle准备返回已有结果；不同请求冲突，不自动重试失败构建。
非终态阻止另一新升级；不得换ID绕过 unknown。所有机器可读输出仅暴露有界摘要，不输出配置与凭证。

prepare 持共享安装租约，允许标准 runner 在线运行，同时排斥独占维护。
监督器持独占协调租约；其他公开变更持共享协调租约后再持升级记录锁，内部发布复用监督租约。
prepare 后与发布前重新检查 active、身份、配置绑定；不冻结正常运行中的 journal 字节。
发布前先保存 publishing/expected_active，再原子保存 active，最后 published；published 不是健康成功。
文件保存采用临时0600文件、fsync、replace与目录fsync；IO结果未知时先对账，不换ID重放。

## 维护口、信号与健康

显式 `client --maintenance` 或标准布局存在 `upgrade-profile.json` 启用维护口。
此自动启用只用于诊断/收口，不代表已通过管理员策略验证或允许升级。
路径为 state/client/maintenance.sock；父目录须本客户端属主且0700，socket0600。
生命周期独占 flock 阻止多个合作进程共用；仅在私密目录、取得独占租约且已有socket连接明确
被拒绝时恢复死socket。活socket、符号链接或其他未知路径均不删除，关闭只删除自身inode。
CLI SIGTERM 取消运行并等待清理；测试只向明确创建的临时子进程发送信号。

单一 upgrade_id 冻结新请求准入，重复请求只返回已有结果，同时等待在途请求和后台任务归零。
首次 drain 前持久化意图，回执丢失保留 unknown/draining。未确定阶段时不无条件 resume。

身份诊断与健康分离：ready 要求 journal 初始化完成、未关闭且无 fatal failure。
停止前检查旧进程，启动后检查候选进程：准确 `<root>/run client`、PID属于绑定unit cgroup、
准确 release、client/venv/bin/python、设备身份。候选还须明确 `maintenance_owner: null`。
读取active本身不证明进程已加载，也不代替中心认证或长期稳定性验收。

## 本地管理员策略

supervise 仅支持 Linux、单角色 client，要求 root-owned、非symlink、group/other不可写的profile。
root与unit须准确绑定；以下仅为字段说明，**不是可安装的纳管文件**：

```json
{
  "format_version": 1,
  "role": "client",
  "root": "<实际绝对根>",
  "unit": "<实际client.service>",
  "allow_upgrade": true,
  "channel": "stable",
  "allowed_versions": ["<批准的release_version>"],
  "bundle_channels": {"<准确bundle指纹>": "stable"},
  "maintenance_windows_utc": [
    {"start": "<显式UTC ISO8601>", "end": "<显式UTC ISO8601>"}
  ]
}
```

prepare与activate存在profile时共同校验；supervise首次drain前及stop前复查策略。
缺字段、版本或指纹通道不符、窗口外均失败关闭。窗口为start<=now<end，明确UTC，start<end。
通道是管理员本地准确指纹白名单，不接受远程自报，也不从版本名推断。
窗口在stop和publish间到期会导致拒绝发布并保留待处理状态，不能自动恢复或回退。
本策略约束upgrade命令族，不是对恶意本机代码或其他管理员维护命令的OS ACL。

## 中断与恢复

- draining：`resume-admission` 要求原binding未变、准确原进程仍健康、owner为本ID或已无owner；
  恢复后再次确认准入开放，保存cancelled，不执行任何服务动作。完成后重复恢复明确拒绝。
- publishing：`reconcile` 比较完整 expected_active；匹配则published，否则unknown，不重发发布。
- draining/stopping/starting：`reconcile-supervision` 只对账。准确expected_active（含activation_id）、
  配置/身份binding与候选进程健康均成立才verified_local，否则unknown及有界原因。
- 原服务已停、新服务启动失败、指针冲突：不自动启动、不回退、不清除unknown；需管理员核实。
  人工单步继续见[恢复手册](upgrade-stage1-recovery.md)，不得手改记录或用新ID跳过；真实主机重启验收仍待执行。
- unknown任务不重放；无running数据库记录不代表OS后代已退出。

## 纳管与权限前置条件

管理员须先核实标准installation布局、runner原字节、单client角色、实际ExecStart及unit所属、
软件目录/配置/凭证/state分离、目录链属主权限、journal/output map和所有在途任务。
现有非标准候选venv不得伪造installation/active元数据或覆盖旧runner绕过校验。
标准安装依赖PATH uv、Python3.13及已有平台矩阵；无uv的机器不会自动安装工具。
升级器必须是独立一次性unit/进程，位于客户端unit/cgroup之外，不通过普通Device Bash启动。
审阅模板与生成器见[纳管手册](upgrade-stage1-adoption.md)。模板使用独立root升级器，不宣称最小权限broker；旧布局迁移与真实权限验收仍未交付，不能直接纳管腾讯正式机。

## 验证证据与剩余门禁

纳管与恢复增量最终准确wheel安装后从源码目录外652项通过，22个Python源文件字节匹配，
依赖与静态/格式/diff检查通过。隔离真实socket/WebSocket、flock、指针文件及临时CLI信号均已执行。
安装锁/布局测试部分模拟候选安装，systemd操作目前仅为替身，不能合并声称真实系统升级通过。

独立审核：首批3P1/3P2经R1复核关闭；策略/对账新增2P2经R3复核关闭；均不构成全局PASS。
真实验收矩阵见 [Linux/systemd验收门禁](upgrade-stage1-acceptance.md)。
新增人工恢复/纳管资产已完成独立审核及权限P1整改复核；不代表真实系统验收。未完成：真实隔离Linux/systemd、主机重启/目录权限/模板实际验收、最终独立验收。
正式部署、发布、Git提交/合并/推送和旧目录清理均不在当前持续授权内。
