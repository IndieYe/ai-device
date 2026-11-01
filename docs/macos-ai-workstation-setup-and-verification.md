# macOS Device 用户级部署、迁移与验收

> 本文只负责 Device 客户端在 macOS 登录用户下的部署、迁移、回退与连接验收，不要求安装 tmux 或 macOS Harness。
> 交互终端、桌面自动化和可选免密 sudo 的独立说明已移至 [macOS 可选配套工具](macos-optional-tools.md)。这些配置不是 Device 的必要依赖或原生能力。
> 默认命令由用户在目标 Mac 本机终端执行，控制端命令另行标明。真实服务停止／切换由人类管理员执行。

## 1. 选择运行方式

推荐在以下条件下使用单 LaunchAgent：

- Mac 有固定的 AI 工作账号，并登录图形桌面。
- 需要在登录用户的运行上下文执行命令；如另装桌面工具，其图形会话要求单独验证。
- 接受退出登录后 Device 离线、重新登录后自动启动。

若要求未登录时也能远程维护机器，应另行评估系统后台服务方案，不要直接照搬本指南。锁屏不是退出登录，但桌面工具可能拒绝操作锁定界面。不要为方便自动化默认关闭 FileVault、SIP 或启用自动登录。

Device 负责远程命令执行、回执和文件访问。通过命令调用独立工具不代表 Device 原生支持它的全部能力；工具的安装、权限及验收单独管理。

无需 Apple 账户即可运行以上工具；需要 iCloud、App Store 等服务时再单独配置。可以使用现有本地账号；有个人数据的机器更适合分开 AI 账号。授予任意 root 权限后，账号分离不再是严格安全边界。

## 2. 准备账号与 Device 客户端

### 2.1 记录目标身份

```bash
whoami
id -u
uname -s
uname -m
stat -f %Su /dev/console
```

确认正在目标 Mac 上操作，并记录短用户名、UID、HOME。不要假定 UID 一定是 501，也不要按机器名称推断系统类型。

### 2.2 安装 Device

按交付版本文档安装客户端并完成配对：

- [Device 安装与交付](device-delivery.md)
- [Device 配对与运行](device-runtime.md)

使用经过来源和摘要校验的受控交付包；不要用网上同名包替代配套 Bash/FS 执行核心。Device 的 Python 要求、bundle 与实例安装参数以交付文档为准，不能套用 Harness 的 Python 版本。

安装完成后，先确认：

1. 客户端可使用稳定启动入口运行，而不是写死某个 release 内部路径。
2. 有独立的设备 ID 和设备凭证，设备凭证不同于中心管理员凭证。
3. 启动入口能在无交互、无终端、没有 shell profile 的环境中取得配置。
4. 运行状态、输出和日志有持久目录。
5. Device 以普通账号运行，需要时显式提权，不把整个客户端默认作为 root 运行。

本文不规定新的凭证存储机制。复用交付方案支持的受限配置、环境注入或凭证引用；不要将 Token 放入命令行、对话、日志或版本库。plist 若包含敏感环境变量，也应只允许当前用户访问且不可提交到仓库。

## 3. 配置 Device 用户级 LaunchAgent

### 3.1 确定稳定启动入口

记为 `<START_CLIENT>`：它必须是已安装版本提供或经核实的绝对路径，能够以前台方式运行客户端、保留进程监督关系、不自行后台化，并取得正确配置和设备凭证。

不要直接将某台机器的启动脚本或版本化 Python 路径复制到其他机器。若需自行创建入口，按所用版本 `client --help` 编写并校验；不要猜测凭证参数。

### 3.2 稳定入口的具体接法

对于交付文档定义的实例，稳定入口是 `<INSTANCE_ROOT>/run client`，不能省略 `client`。当前带私有 runtime 的交付形式使用 Shell launcher，由它选择活动 release 的 Python；不能用 Python 执行这个 Shell 文件。凭证已由启动环境提供时，可将 plist 的 ProgramArguments 替换为以下数组：

```xml
<array>
  <string><INSTANCE_ROOT>/run</string>
  <string>client</string>
</array>
```

旧版实例的 run 可能是 `#!/usr/bin/env python3` 入口，需要在启动环境提供可用的 Python PATH。先只读核对已安装入口的 shebang 与匹配版本文档，不把新旧解释器方案混用，也不要直接调用内部 run-runner。

如果凭证尚未由受支持的启动机制提供，仅有上述数组仍不完整。优先复用安装时已核实、能在非交互环境注入 `DEVICE_RUNTIME_TOKEN` 的启动入口。首次部署可使用一个私有包装入口，按本地受控凭证获取方式输出到子进程环境：

```sh
#!/bin/sh
set -eu
# 将路径替换成受信任、不可由无关账号修改的实际程序。
# CREDENTIAL_READER 须为已配置的无交互读取程序：成功时仅向 stdout 输出设备 Token。
# 不将 Token 作为 argv；不要启用 set -x。此程序不能依赖在终端手动解锁或输入密码。
DEVICE_RUNTIME_TOKEN="$(/absolute/path/to/CREDENTIAL_READER)"
[ -n "$DEVICE_RUNTIME_TOKEN" ] || { echo 'Device credential unavailable' >&2; exit 1; }
export DEVICE_RUNTIME_TOKEN
exec "/absolute/instance/run" client
```

这不是新的凭证存储协议，`CREDENTIAL_READER` 也不是现成的 Device 命令；必须替换为实际已配置方案，不能直接复制运行。先单独核实读取失败时不会输出秘密，再验证登录自动启动。环境变量不是对同账号或 root 的保密边界。若没有合适的非交互凭证来源，应停止安装并完成凭证部署，而不是把秘密粘贴进文档或命令行。

保存入口到自有私有目录、设置 0700，用 `/bin/sh -n` 检查语法，再将其绝对路径填入 `<START_CLIENT>`。已有部署迁移通常应直接复用原启动入口，不新建凭证副本。

### 3.3 创建 plist

选择唯一 Label，例如 `com.example.device-client`。以下为模板，所有尖括号占位符必须替换，路径必须绝对；launchd 不展开 `~`、`$HOME` 或 shell 命令。使用 plist 工具生成时可避免路径中的 XML 特殊字符问题。

存为：`~/Library/LaunchAgents/<LABEL>.plist`。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string><LABEL></string>
  <key>ProgramArguments</key>
  <array><string><START_CLIENT></string></array>
  <key>WorkingDirectory</key><string><INSTANCE_ROOT></string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>StandardOutPath</key><string><LOG_DIRECTORY>/client.out.log</string>
  <key>StandardErrorPath</key><string><LOG_DIRECTORY>/client.err.log</string>
</dict>
</plist>
```

说明：

- 若启动入口是 shell 脚本，须有正确 shebang 和执行权限；或在 ProgramArguments 中显式指定解释器和脚本为两个参数。
- 有额外参数时，各参数分别占一个 string，不把整个命令写进单个 string。
- 预先创建工作目录和日志目录，并确认账号可写。
- LaunchAgent 不设置 UserName/GroupName；以目标登录用户身份运行。
- KeepAlive=true 表示持续维持进程运行，正常或异常退出都会尝试重新拉起；快速反复退出会被节流。主动停用应通过服务管理完成，不能只让进程退出。它不能修复错误凭证、损坏配置或未登录状态。

**先选择部署路径，不要顺序执行两种加载流程：**

- 已有 Daemon 或其他同身份客户端：本节只生成并校验 plist，**跳过下面的全新部署加载命令**，进入 3.4 节。
- 全新部署：先确认没有同身份客户端、没有已加载的同 Label Agent，且目标账号已登录，再执行下面的命令。

以下只用于全新部署，变量须替换为实际值；校验失败不执行 enable/bootstrap：

```bash
LABEL='com.example.device-client'
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
USER_UID="$(id -u)"
(
  set -eu
  chmod 600 "$PLIST"
  plutil -lint "$PLIST"
  launchctl enable "gui/$USER_UID/$LABEL"
  launchctl bootstrap "gui/$USER_UID" "$PLIST"
  # 不打印完整配置，避免 EnvironmentVariables 中的秘密进入终端记录。
  launchctl print "gui/$USER_UID/$LABEL" >/dev/null
)
```

不要使用 `sudo launchctl bootstrap gui/...` 代替正确用户上下文。任何失败都停止并调查；enable 已成功但 bootstrap 失败时，并不会自动撤销 enabled 状态，不盲目重跑。

### 查询状态的人工门禁

以下迁移／回退依赖明确的“已加载”或“未加载”事实。查询失败不自动表示对象不存在，也不能用 `if ! launchctl print ...` 决定加载。需要检查目标时，管理员在本机执行对应查询，并保留退出码与 stderr；stdout 丢弃，避免输出完整环境变量：

```bash
# 将参数替换为该阶段需要检查的 system/LABEL 或 gui/UID/LABEL。
launchctl print 'gui/实际UID/实际LABEL' >/dev/null
printf 'query exit code: %s\n' "$?"
```

System 域查询按需在 launchctl 前加 sudo。退出码 0 表示已加载；非零时，必须人工核对 stderr 确实是该服务不存在，且域、UID、Label 都正确。权限错误、认证失败、域不存在或含义不明时立即停止，不执行下一段。这里不依赖跨版本固定错误码／错误文本做自动判定。各代码块是独立步骤，不要整章粘贴；人工门禁不是脚本内的事务或自动暂停。

### 3.4 从 LaunchDaemon 迁移

已有服务时，不直接加载相同身份的第二个客户端。先备份现有定义，保存已校验的启动入口、配置引用与工作目录；准备本机回退通道。读取配置时不要打印其环境变量或凭证。

**以下停止／切换步骤由人类管理员在本机执行。** 确认没有执行中的重要任务，保存工作后，先禁用并卸载原 Daemon，再加载 Agent：

先在本机终端设置变量并校验新文件，不停服务：

```bash
DAEMON_LABEL='替换为原Daemon的Label'
DAEMON_PLIST='/Library/LaunchDaemons/替换为原配置文件名.plist'
AGENT_LABEL='替换为新Agent的Label'
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"
USER_UID="$(id -u)"
plutil -lint "$AGENT_PLIST"
```

确认原配置已备份、启动入口与凭证可用、重要任务已排空、回退变量已填好后，再单独执行以下阶段。每段使用子 Shell 的 `set -eu`，任一步失败不继续；这只能防止后续误操作，不能回滚已经完成的步骤。

**阶段 A：停止旧端并核实。** 仅适用于已确认原 Daemon 当前加载的情形，由管理员执行：

```bash
(
  set -eu
  sudo launchctl print "system/$DAEMON_LABEL" >/dev/null
  sudo launchctl disable "system/$DAEMON_LABEL"
  sudo launchctl bootout "system/$DAEMON_LABEL"
)
```

bootout 后先按上述人工门禁查询 `system/$DAEMON_LABEL`，确认对象不存在；随后还需核实原客户端及监督子进程已退出、实例租约已释放；job 消失本身不是所有后代都已退出的证明。任何检查失败、状态不确定或连接尚有旧端活动，都停止，不执行阶段 B。若原 job 原本未加载，先调查，不忽略阶段 A 报错继续运行。

**阶段 B：加载新端。** 只有阶段 A 和旧进程退出检查均成功，且按人工门禁查询 `gui/$USER_UID/$AGENT_LABEL` 确认新端未加载时才执行。若已加载，停止并调查，不重复加载：

```bash
(
  set -eu
  launchctl enable "gui/$USER_UID/$AGENT_LABEL"
  launchctl bootstrap "gui/$USER_UID" "$AGENT_PLIST"
  launchctl print "gui/$USER_UID/$AGENT_LABEL" >/dev/null
)
```

随后从控制端验证正式连接和命令。加载成功不等于连接成功；若不可用，不反复 bootstrap，保留脱敏日志并在本机回退。

**回退：** 保留本机终端中的变量并确认路径正确。先按人工门禁查询新 Agent：若已加载，管理员单独执行：

```bash
(
  set -eu
  launchctl bootout "gui/$USER_UID/$AGENT_LABEL"
)
```

若已确认未加载则跳过 bootout；其他查询失败不能跳过。然后单独禁用：

```bash
(
  set -eu
  launchctl disable "gui/$USER_UID/$AGENT_LABEL"
)
```

再次按人工门禁确认新 Agent 未加载，并人工确认其客户端及监督子进程已退出。未确认前不能恢复旧服务。

随后按人工门禁查询旧 `system/$DAEMON_LABEL`，区分两条恢复路径；其他查询错误停止：

- **旧服务仍加载**：可能是之前 disable 成功而 bootout 失败。先核实仍是原客户端、进程状态正确，且新端已退出；确认后执行以下 enable 恢复自动启动资格，不执行 bootstrap。身份或状态不明时停止，不凭在线状态猜测：

```bash
(
  set -eu
  sudo launchctl enable "system/$DAEMON_LABEL"
  sudo launchctl print "system/$DAEMON_LABEL" >/dev/null
)
```

- **旧服务确认未加载**：单独执行以下恢复步骤：

```bash
(
  set -eu
  sudo plutil -lint "$DAEMON_PLIST"
  sudo launchctl enable "system/$DAEMON_LABEL"
  sudo launchctl bootstrap system "$DAEMON_PLIST"
  sudo launchctl print "system/$DAEMON_LABEL" >/dev/null
)
```

回退后再次验证正式连接。原 Daemon plist 保留；切换／回退不是跨步骤事务，不允许同一身份两端并发。权限不足、launchctl 查询异常或子进程归属不清时停止并核实，不能把所有查询失败一概当作“未加载”。

## 4. Device 验收

只报告实际完成的验证；客户端在线不代表第三方工具可用。sudo 和桌面工具不是以下基础验收的必选项。

### 4.1 正式远程命令与身份

控制端先检查帮助和设备列表，再使用新唯一操作 ID 下发命令：

```bash
device-ops list-devices --offset 0 --limit 20
device-ops run --help
# 将设备ID、操作ID和目录替换为已确认值。
device-ops run --device-id '<DEVICE_ID>' --operation-id '<NEW_UNIQUE_ID>' \
  --cwd / --timeout 20 -- /bin/sh -c 'id -un; launchctl managername'
```

应返回目标用户、`Aqua` 和成功退出码。可选管理员配置的验收见 [配套指南](macos-optional-tools.md)，不属于 Device 基础验收。

受理不等于完成。pending、unknown、提交超时都按原 operation_id 对账，不换 ID 重发；独立新测试使用新 ID，不能复用历史去重结果。后台最终状态用当前版本 get 接口回收。

### 4.2 网络断开再恢复

1. 记录正式 Agent 的 pid、runs 和启动时间。
2. 用户暂停重要网络任务，断开实际网络 20～30 秒再恢复。
3. 不手动重启客户端。
4. 控制端验证重新在线、远程命令成功。
5. 比较 PID 和启动次数：不变支持原进程重连，变化则需核实进程重启原因。

没有离线期间采样，不能宣称准确重连耗时。需要测量时在控制端进行有界状态采样，区分网络恢复、中心在线和业务可执行时刻。

### 4.3 退出登录再登录

1. 用户保存工作，真正退出目标账号，不是锁屏或快速用户切换。
2. 重新登录，不手动启动 Device。
3. 控制端验证自动上线，核对新进程、Aqua 和普通命令；所选管理员配置及第三方工具另按配套指南验收。
4. 确认旧 Daemon 没有重新加载。

整机重启、FileVault、休眠唤醒、多用户和长任务恢复必须分别验收，不能由以上测试推断全部支持。

## 5. 运行边界与排障

### 网络恢复但不在线

检查 Agent 加载、启用和进程状态，随后检查脱敏的网络与认证错误。不要输出完整 launchctl 环境变量或配置文件。不要因控制端未收到结果就重新执行有副作用命令。

### 登录后没有自动启动

检查 plist 是否在目标用户的 Library/LaunchAgents、语法是否通过、路径是否绝对、日志目录是否存在、启动入口是否可执行、凭证能否在非交互环境取得，以及 disabled 状态。错误配置配合 KeepAlive 可能反复重试，不能靠扩大重试掩盖问题。

### 调用第三方工具的边界

Device 的用户身份、图形会话上下文、sudo 和 TCC 权限是不同条件。Device 在线或能执行命令，不代表桌面工具具有辅助功能、屏幕录制或用户认证能力。

远端截图等文件必须通过同一设备的文件读取能力取回，不将远端路径直接交给控制端本地文件工具。具体 Helper、socket、权限和功能验收见 [可选工具指南](macos-optional-tools.md)。

## 6. Device 完成检查表

- [ ] Device 配对、正式连接和远程命令通过，凭证不泄露。
- [ ] 只有一个正式设备身份在运行，旧部署不会自动抢占。
- [ ] LaunchAgent 以目标登录账号运行，启动入口、凭证和日志目录可用。
- [ ] 断线重连与退出再登录恢复分别通过。
- [ ] 回退入口保留；未测试场景明确列出。
- [ ] 第三方工具仅按实际选择单独验收，不作为 Device 安装完成条件。

## 7. 兼容性与证据基线

历史实际验收日期为 2026-11-01：Apple Silicon macOS 27.0.1、Device 客户端 0.2.3，通过正式连接、命令、网络恢复后原进程重连和退出再登录后自动恢复。未测量精确重连时延，未完成整机重启、FileVault、休眠、多用户或长任务恢复验收。

稳定入口以所用交付版本及其实际实现为准；本指南的 LaunchAgent 是手工部署方案，不宣称安装器自动支持。历史配套工具验收见可选工具指南，不作为 Device 产品能力证明。

本轮仅整理文档职责、核对语法及链接，未重新安装、执行真实迁移或回退，也未重新测试设备。历史通过结果不替代新机器和新版本验收。
