# macOS 客户端安装升级指南（Apple Silicon arm64）

> 适用版本：客户端包 `ai-device-client` 0.4.x，运行时 0.3.6
> 支持范围：**仅 Apple Silicon / arm64**。不支持 Intel Mac，不支持 Rosetta 运行路线，不支持降级安装。
> 包版本与运行时版本相互独立，见第 5 节。
> 本文是操作说明；实施细节与验收证据见 [`client-native-macos-development.md`](../development/client-native-macos-development.md)，权威需求见 [`../requirements/client-native-install-upgrade.md`](../requirements/client-native-install-upgrade.md)。

## 1. 它是什么

一个自含运行时的设备客户端。包内自带 Python 3.13 和客户端依赖，**目标机不需要安装 Python、pip 或 uv，也不需要联网下载依赖**。

与 Linux 版最大的区别：macOS 客户端**不是系统服务，而是指定普通用户的 LaunchAgent**，在用户登录后运行。系统级部分只有安装器本身。

安装后分布如下。注意除程序树与 registration 外，其余文件都在**该用户的家目录**下，`/var/root` 里不会有任何东西。

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| 程序 | `/opt/ai-device-client/` | 含私有运行时；卸载时整体删除 |
| 升级协调器 | `/opt/ai-device-client-upgrade/installer-inspection` | 由包安装，卸载时一并删除 |
| 配置 | `~/Library/Application Support/ai-device-client/client.json` | 0600，不含密钥 |
| 凭证 | `~/Library/Application Support/ai-device-client/token` | 0600 |
| 业务状态 | `~/Library/Application Support/ai-device-client/state/` | 操作与任务 SQLite；卸载保留 |
| 应用日志 | `~/Library/Logs/ai-device-client/client.log` | 卸载保留 |
| 服务定义 | `~/Library/LaunchAgents/local.ai-device.client.plist` | 由注册步骤生成 |
| 注册记录 | `/Library/Application Support/ai-device-client-management/registration.json` | root 所有，记录 uid/home |

`~/` 指**指定的那个普通用户**的家目录，不是执行命令的人。注册步骤会明确记录 uid 与 home，后续所有操作都以它为准。

程序、配置、状态三者分离：升级只替换程序，**配置、身份和业务数据不会被覆盖**。

## 2. 安装

**安装需要管理员权限，且只能从命令行安装。**

```bash
sudo installer -pkg ai-device-client-<version>-arm64.pkg -target /
```

预期输出：

```
installer: Package name is AI Device Client
installer: Upgrading at base path /
installer: The upgrade was successful.
```

此时服务尚未配置，`launchctl print gui/$(id -u)/local.ai-device.client` 会报 `Could not find job`，这是正常的——服务定义还没创建。

**签名状态分两种，行为不同：**

| 包 | 已签名并公证 | 未签名开发包 |
| --- | --- | --- |
| 标题 | `AI Device Client` | `AI Device Client — UNSIGNED development build` |
| 产物旁警示 | 无 | 有 `UNSIGNED.txt` |
| 双击 .pkg | 可以 | **不可以** |
| `spctl -a -vvv -t install` | `accepted, Notarized Developer ID` | `rejected, no usable signature` |
| 命令行安装 | 可以 | 可以 |

> **未签名包不要双击。** 带 quarantine 属性时会被 Gatekeeper 拦下：Installer 会挂起，payload 根本不会写入，安装实际没有发生，但界面可能只显示"正在验证"。此时必须从命令行安装，并先清除隔离属性：
>
> ```bash
> xattr -d com.apple.quarantine ai-device-client-<version>-arm64.pkg
> sudo installer -pkg ai-device-client-<version>-arm64.pkg -target /
> ```
>
> 已签名并公证的包带 quarantine 双击也属正常路径，不需要清属性。

> 包要求 macOS 11.0 或更高、arm64。这是包内原生组件（launcher 与 commit）的构建目标。

## 3. 首次配置

配置需要**三个值**，从中心的设备登记信息获取：

- 中心地址：`ws://`（本机回环）或 `wss://`（远端，必须 TLS）
- 设备 ID：本机在中心侧的标识
- 设备 Token：与设备 ID 配对的凭证

配置命令**以将要运行服务的那个用户身份执行**（不要用 root）。以下以 `alice` 为例：

```bash
sudo -u alice /opt/ai-device-client/runtime/bin/python3 -I -B \
  -m device_runtime.client_setup \
  --config-dir "/Users/alice/Library/Application Support/ai-device-client" \
  --state-dir "/Users/alice/Library/Application Support/ai-device-client/state" \
  --server-url wss://center.example.com \
  --device-id <device-id> \
  --token-stdin
```

执行后交互式提示输入 Token（不回显）。自动化场景可用管道：

```bash
printf '%s\n' "$DEVICE_TOKEN" | sudo -u alice /opt/ai-device-client/runtime/bin/python3 -I -B \
  -m device_runtime.client_setup \
  --config-dir "/Users/alice/Library/Application Support/ai-device-client" \
  --state-dir "/Users/alice/Library/Application Support/ai-device-client/state" \
  --server-url wss://center.example.com \
  --device-id <device-id> --token-stdin
```

预期输出：

```
Configured: /Users/alice/Library/Application Support/ai-device-client/client.json. Service registration/start is still required.
```

### 配置约束（实测确认）

| 约束 | 说明 |
| --- | --- |
| Token 长度 **16–4096** 字符 | 且必须是可打印 ASCII。少于 16 字符会报 `configuration failed` |
| `--state-dir` 必须是绝对路径 | 相对路径直接拒绝 |
| 配置目录**必须不存在** | 已存在时拒绝，绝不覆盖。补全残缺配置用 `--resume`，见下 |
| 以服务运行用户执行 | 目录与文件归属该用户，后续注册会校验 |
| Token 不进命令行 | 只用 `--token-stdin` 或交互输入，不要写成参数 |

### 补全残缺配置（`--resume`）

若上一次配置中途中断，目录已存在但 `client.json` 或 `token` 缺失，普通调用会被拒绝。此时加 `--resume`：

```bash
sudo -u alice /opt/ai-device-client/runtime/bin/python3 -I -B \
  -m device_runtime.client_setup --resume \
  --config-dir "/Users/alice/Library/Application Support/ai-device-client" \
  --state-dir "/Users/alice/Library/Application Support/ai-device-client/state" \
  --server-url wss://center.example.com \
  --device-id <device-id> --token-stdin
```

`--resume` 只补缺失文件，**不覆盖已存在的文件**。要改已有配置，先删除目录再完整配置——不要指望用 `--resume` 修改内容。

`client_setup` 没有 `/usr/bin` 软链接，必须用上面的完整路径调用。macOS 包**不提供** `ai-device-client` / `ai-device-client-register` 这类命令，全部通过 `python3 -m device_runtime.<module>` 调用。

## 4. 注册并启动服务

注册步骤决定服务以哪个用户运行、把服务定义写到哪个家目录、并记录 uid/home 供升级路径使用。

**注册必须用 root**：

```bash
sudo /opt/ai-device-client/runtime/bin/python3 -I -B \
  -m device_runtime.client_macos_register register \
  --user alice \
  --confirm-legacy-clients-disabled
```

预期输出：

```json
{"registered": true, "started": false, "uid": 501, "plist": "/Users/alice/Library/LaunchAgents/local.ai-device.client.plist"}
```

> **`started: false` 是正常的**：注册只写服务定义并记录身份，**不启动服务**，也不代表注册失败。

`--confirm-legacy-clients-disabled` 是必填的确认标志：它表示你已确认这台机器上没有仍在运行的旧版 client。若存在，注册会拒绝而不是覆盖。

> **必须用 root，不能用 `sudo -u <user>`。** 降权执行会失败，且错误信息只有一句
> `macOS registration refused or incomplete: macOS registration requires administrator privileges`——
> 容易被误当成其他故障。

注册完成后启动：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.ai-device.client.plist
```

检查：

```bash
launchctl print gui/$(id -u)/local.ai-device.client | grep -E 'state|pid'
```

期望看到 `state = running` 和有值的 `pid`。

> **服务只在 GUI 登录会话中存在。** 用户登出时 job 会被卸载（`job state = removed`），
> 程序与配置都保留；用户重新登录后由 `RunAtLoad` 自动拉起。
>
> 实测：登录到客户端真正启动之间有**约 60 秒空窗**（launchd 在登录早期处于
> on-demand 模式会延迟 spawn）。这段时间内设备在中心侧不可用。

### 更换运行用户

先卸载再重新注册，不要直接改 plist：

```bash
launchctl bootout gui/$(id -u)/local.ai-device.client
sudo /opt/ai-device-client-upgrade/installer-inspection \
  uninstall-registered-program --confirm-service-not-running
```

然后用新用户重新执行第 3、4 节。配置与凭证在旧用户家目录下，需要一并迁移或重新配置。

## 5. 查看版本与状态

### 运行时版本

```bash
/opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_cli --version
```

输出：

```json
{"version": "0.3.6", "build_id": "sha256:eaf47863..."}
```

### 包版本

```bash
pkgutil --pkg-info local.ai-device.client
```

### 连接日志

launchd 把 stdout 与 stderr 都重定向到同一个文件：

```bash
cat ~/Library/Logs/ai-device-client/client.log
```

正常上线时只有一行：

```
ai-device-client: connected to wss://<center>/v1/devices/<device-id>/connect
```

**这行日志是判断设备是否真的连上的唯一本地依据。** 进程存在不等于连上，
TCP 列为 ESTABLISHED 也不等于中心已接纳（welcome 帧验证通过才打印这行）。

日志只在 **连接状态发生变化** 时写入，不是每秒刷。去重依据是状态而非文本，
所以 DNS 解析失败这类每秒重复的故障不会把日志写爆。

断开时会追加一行，带异常类型与消息：

```
ai-device-client: disconnected: <ExcType>: <message>; retrying every <n>s
```

两个运维注意点：

- **文件没有轮转机制。** 持续连接抖动会持续追写，长期运行应自查文件大小，
  必要时手动截断或压缩归档。当前包不提供自动轮转。
- **异常消息原样写入。** 断开行包含底层异常的字符串形式，可能带有 URL 或
  网络栈细节。日志权限为 600（launchd 的 Umask 63），仅该用户可读，
  但把它当成可公开分享的排障材料之前应先看一眼内容。

### 服务状态

```bash
launchctl print gui/$(id -u)/local.ai-device.client | grep -E 'state|pid|last exit|runs'
```

`client_cli` **没有** `status` 子命令，不要尝试 `client_cli status`——它会把 `status` 当参数报用法错误。查服务状态用 `launchctl`。

### 包版本与运行时版本是两件事

`pkgutil` 看到的是**包版本**（发行号），`--version` 看到的是**运行时版本**。两者独立演进，不要求前缀相同：

| 名称 | 示例 | 含义 |
| --- | --- | --- |
| 包版本 | `0.4.0` | 制品发行号，反映打包、依赖、安装脚本的变化 |
| 运行时版本 | `0.3.6` | 客户端程序自身版本，反映业务代码变化 |
| build_id | `sha256:eaf47863...` | 精确实现身份，同版本不同构建也不同 |

所以 `pkgutil` 显示 0.4.0 而 `--version` 显示 0.3.6 是**正常的**，不代表装错包。

判断升级是否成功，看的是**运行时版本 + build_id**，不是包版本前缀。中心侧显示的设备版本也是运行时版本和 build_id。

> **服务 PID 存在不等于设备在线。** 中心是否看到本设备，以中心侧的设备列表为准；本机就绪与连接成功是两件事。

## 6. 升级

**升级要求目标用户已登录，且客户端正在运行。** 用同一个入口安装新包：

```bash
sudo installer -pkg ai-device-client-<new-version>-arm64.pkg -target /
```

预期输出：

```
installer: The upgrade was successful.
```

升级由包内的 `postinstall` 协调：向运行中的实例发出空闲退出请求 → 等它自行退出 → 卸载 job → 原子替换程序树 → 重新加载 → 确认就绪。**配置、凭证和业务数据不会被触碰。**

实测（0.3.6 → 0.4.0，macminim6）：identity 由 `84f64def` 变为 `eaf47863`，PID 更新，`state=running`、`last exit code=(never exited)`，`client.json` 与 `token` 的 SHA256 逐字节未变，中心侧 `build_id` 与安装树一致。

### 升级时的行为

| 情况 | 结果 |
| --- | --- |
| 服务空闲 | 正常升级，配置与业务数据保留 |
| 服务正在执行任务 | **拒绝升级**，服务与任务不受影响（见下） |
| 经本客户端通道执行 installer | **必然拒绝**：该命令本身就是活跃请求（见下） |
| 用户已登出 | 拒绝，旧程序留在原位。**没有未登录的升级路径** |
| 已登录但 job 未运行 | 拒绝，不猜测 |
| 程序损坏到无法应答 | 拒绝，旧程序留在原位 |

### 忙碌时拒绝升级

客户端正在执行任务时，安装会被拒绝，`installer` 只显示一句：

```
installer: The upgrade failed. (运行软件包"..."的脚本时出错。)
```

**真实原因不在安装界面里**，而在下面两个文件：

```bash
sudo cat "/Library/Application Support/ai-device-client-management/install-status"
sudo cat "/Library/Application Support/ai-device-client-management/install-note"
```

`install-status` 是数字退出码：`0` 成功，`70` 表示升级协调器拒绝或状态未知。此时任务的退出标记是 `AI_DEVICE_UPGRADE_REFUSED_V1`，含义是"实例非空闲，已在替换前停止，未做任何修改"。

实测拒绝后：包版本、PID、程序树 mtime、服务状态、中心连接、正在运行的任务全部保持不变。任务结束后重新安装即可成功。

> **`ls` 看不到这两个文件是正常的。** `/Library/Application Support/ai-device-client-management`
> 权限是 `0700 root:wheel`，非 root 用户列目录会得到空结果，容易误判为"状态文件不存在"。用 `sudo cat` 读。

### 不要经客户端自己的通道执行 installer

这是最容易踩、也最难自查的一种"假忙碌"。

客户端的空闲判定是 `active_requests == 0 && active_tasks == 0`。**任何通过该客户端执行的命令都计入 `active_requests`**，其中就包括 `bash` / `script` 这类远程执行通道。因此，如果从客户端通道里运行 `installer`，正在服务这条命令的客户端自己就成了障碍：它要等一个空闲实例，而它此刻正忙着跑这条命令。

这种情况下会得到 `AI_DEVICE_UPGRADE_REFUSED_V1`。

> **不要仅凭这个码断定原因。** 它是 `upgrade_registered_staged_once` 多个拒绝阶段的默认输出，
> 服务忙、stage 残留、job 未运行等都会输出同一行。看到它只能说明"升级被拒绝了"，
> 不能据此认为属于忙、没有副作用或制品有问题。要继续看安装日志和当前状态再判断。

**先探测当前是否可升级**，不必反复尝试安装。下面的脚本通过维护 socket 申请一次租约：

```bash
sudo python3 - <<'EOF'
import socket
p = '/Users/<user>/Library/Application Support/ai-device-client/state/maintenance.sock'
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(p)
s.sendall(b'{"action":"acquire"}\n')
d = b''
while not d.endswith(b'\n'):
    c = s.recv(4096)
    if not c:
        break
    d += c
print(d.decode().strip())
s.close()
EOF
```

`{"acquired": true, "active_requests": 0, "active_tasks": 0}` 表示此刻可以升级。

> **两点注意。** 一是 acquire 不是纯只读：申请成功后会**暂时占住准入**，直到连接关闭，
> 期间其他安装/升级会被拒绝。脚本结束即关闭连接释放，不要长时间保持这个连接。
> 二是它只是**瞬时快照**，返回 true 不保证紧接着的安装一定成功。

**正确做法**：升级只在目标机器的本地终端或 SSH 会话里执行，不要经 Device 的远程命令通道。这要求该机器至少有一条不依赖待升级客户端的访问路径——升级前先确认它可用。

> 若 `active_tasks` 长期不为 0，先只读对账：查操作 ID、对应进程和数据库记录，
> 判断是仍在执行的任务还是没有收尾的残留。`ps` 没看到同名进程不足以证明已退出。
> **不要为了清状态就重启客户端或改数据库**：重启会中断当前连接和正在跑的任务，
> 需要先确认影响并取得同意。

### 升级失败后重试被拒：stage 残留

失败路径会在 `/opt/ai-device-client-stage-<版本>-<identity>` 留下 payload 副本，`preinstall` 见到同名目录会直接拒绝。这会**掩盖真正的失败原因**。

先看拒绝时写下的 token，它是区分两种情况的唯一依据：

```bash
sudo cat "/Library/Application Support/ai-device-client-management/preinstall-token"
```

| token | stage 目录状态 | 含义 | 怎么办 |
| --- | --- | --- | --- |
| `PROGRAM_INSTALL_BUSY_V1` | 存在且**为空** | 另一个安装器刚 mkdir、正在写入 | **等等再试，不要删** |
| `PROGRAM_INSTALL_RESIDUE_V1` | 存在且**非空**，或是链接/非目录 | 前次失败留下的历史残留 | 确认无安装在进行后清掉再试 |

**这个区分不能搞反**：空目录意味着对方正在装，删了会破坏进行中的安装；非空才是可归因的残留。

确认为残留后，先比对内容再删：

```bash
sudo diff -r /opt/ai-device-client-stage-<...> /opt/ai-device-client && echo "内容相同"
sudo rm -rf /opt/ai-device-client-stage-<...>
```

内容不一致时先查清来源再做决定，不要直接删。失败换下来的 stage 可能是**上一棵正常旧树**，删前应确认它不是唯一的回退副本。

## 7. 卸载

卸载入口在包内，**只接受固定参数**：

```bash
launchctl bootout gui/$(id -u)/local.ai-device.client    # 先停止服务
sudo /opt/ai-device-client-upgrade/installer-inspection \
  uninstall-registered-program --confirm-service-not-running
```

预期输出：

```
AI_DEVICE_UNINSTALL_SERVICE_REMOVED_V1
```

`--confirm-service-not-running` 是调用者的断言：你已确认服务停止。卸载会自行核对一次锁，但那只证明"没有合作的实例持有锁"，不证明 job 已卸载或后代进程已退出。

实测删除与保留：

| 删除 | 保留 |
| --- | --- |
| LaunchAgent plist | `client.json`（哈希不变） |
| launchd job | `token`（哈希不变） |
| `/opt/ai-device-client/` | `state/operations.sqlite3`（integrity ok） |
| `registration.json` | `state/tasks.sqlite3`（integrity ok） |
| | `state/output/` 下的输出文件 |

> **`/opt/ai-device-client-upgrade/` 不会被卸载入口删除。** 它属于另一个组件包（升级协调器），
> 卸载入口只删除 plist、registration 与 `/opt/ai-device-client` 三项。重装时它会重新写入。
> 若要彻底清理需手动删除该目录。

> **`SERVICE_REMOVED_V1` 这个名字容易被误读成"只删服务"。** 它同时删除了程序树和 `registration.json`。
>
> 删 `registration.json` 是刻意的：留着它会形成"已注册但无服务定义"的状态，以后每次安装都要人工诊断。代价是**卸载后必须重新执行第 4 节的注册**，这是设计选择，不是缺陷。
>
> 卸载**不会**删除配置、凭证和业务数据。若要彻底清除，需手动删除 `~/Library/Application Support/ai-device-client/` 与 `~/Library/Logs/ai-device-client/`。

### 关于彻底删除的警告

macOS 版**没有** Linux 那样的 purge 入口，也没有"连配置一起清除"的命令。手动删除目录时注意：

- `token` 删掉后设备无法再用原身份连回中心。中心保存着该设备 token，可从中心取回后重新写入；若中心也丢失，只能以新身份重新注册。
- `state/*.sqlite3` 是操作与任务历史。设备退役时若不清理，会随用户家目录一直留着。

## 8. 故障处理

### 安装成功但服务起不来

正常状态。第 2 节安装后需要依次做：**配置 → 注册 → 启动**。跳过任何一步都不会有服务。

依次核对：

```bash
ls "/Users/<user>/Library/Application Support/ai-device-client/client.json"   # 配置做了吗
ls "/Users/<user>/Library/LaunchAgents/local.ai-device.client.plist"          # 注册做了吗
launchctl print gui/$(id -u)/local.ai-device.client                            # 启动了吗
```

### 注册命令报 "registration refused or incomplete"

按顺序排查：

1. **是否用了 root？** `sudo -u <user>` 会失败。必须 `sudo`。
2. `--confirm-legacy-clients-disabled` 是否带上？它是必填。
3. `--user` 指定的用户是否存在、是否为普通用户？root 与 `/var/root` 会被拒绝。
4. 是否已经注册过？重复注册会走"已存在有效注册"分支，不视为失败。

### 注册成功但没有进程

`registered: true, started: false` 表示注册不负责启动。执行第 4 节的 `launchctl bootstrap`。

### 程序损坏、客户端起不来

**不需要旧版本介入，用完整包重装即可修复。**

```bash
sudo installer -pkg ai-device-client-<version>-arm64.pkg -target /
```

若此时 job 已损坏无法应答，协调器会拒绝。按第 7 节卸载后重新安装、配置、注册。

### 首次安装失败后，重装一直报 stage 残留

**这是已知缺陷，不是操作错误。** 首次安装时目标目录不存在，postinstall 会先写
stage 再发布。如果发布被拒（例如旧实例冲突、宿主预检不通过），流程会停止，
**但已经写入的 stage 不会被清理**。

后果是死锁：下一次安装的 `preinstall` 看到同名非空 stage，直接报
`PROGRAM_INSTALL_RESIDUE_V1` 并退出。提示写了 "remove it and retry"，
却**没有告诉你目录在哪、叫什么**。

现场表现（2026-11-10 在 macmini 实测）：

```
第一次安装 → postinstall 报
  offline writer probe: first installation preflight refused
  first program publication refused; no automatic retry or rollback
  （exit 1，但 stage 已留在磁盘）

第二次安装 → preinstall 报
  PROGRAM_INSTALL_RESIDUE_V1
  （退出 1，日志只说 "remove it and retry"）
```

**先定位 stage 目录名。** 它不是固定路径，带版本与 identity 后缀：

```bash
ls -d /opt/ai-device-client-stage-* 2>/dev/null
```

确认里面没有正在进行的安装（目录非空且无安装进程），再看拒绝 token：

```bash
sudo cat "/Library/Application Support/ai-device-client-management/preinstall-token"
```

token 为 `PROGRAM_INSTALL_RESIDUE_V1` 才能删；若为 `PROGRAM_INSTALL_BUSY_V1`，
说明另一个安装器正在用这个目录，**删了会破坏进行中的安装**。

确认是残留后清理再重试：

```bash
sudo rm -rf /opt/ai-device-client-stage-<版本>-<identity>
sudo installer -pkg ai-device-client-<版本>-arm64.pkg -target /
```

这个 stage 由 root 创建，普通用户既看不到也无法删除，必须 `sudo`。
首装失败后不要连续重试——每次重试都会在同一个坑上再撞一次，应先查
`/var/log/install.log` 确认首次失败的真实原因。

### 升级失败但看不出原因

安装界面只显示“脚本执行出错”，**具体原因不在界面上**。这不是提示不全，而是 macOS
安装器的行为：脚本写到 stderr 的内容会被路由到 `/var/log/install.log`，不显示给用户。

按顺序查这三处：

```bash
# 1. preinstall 阶段被拒绝的原因（最常见）
sudo cat "/Library/Application Support/ai-device-client-management/preinstall-token"

# 2. postinstall 阶段的结果与说明
sudo cat "/Library/Application Support/ai-device-client-management/install-status"
sudo cat "/Library/Application Support/ai-device-client-management/install-note"

# 3. 安装器的完整日志
sudo grep -a "ai-device" /var/log/install.log | tail -20
```

#### `preinstall-token` 的两个值

| token | 含义 | 怎么办 |
| --- | --- | --- |
| `PROGRAM_INSTALL_BUSY_V1` | stage 目录被另一个安装器占着，它正在装 | 等它装完再重试，**不要删除那个目录** |
| `PROGRAM_INSTALL_RESIDUE_V1` | stage 目录里有无法归属的内容 | 确认无安装在进行后，`sudo rm -rf` 掉该 stage 目录再重试 |

`BUSY` 与 `RESIDUE` 的区别值得留意：前者说明**有并发安装正在进行**，删目录会破坏对方；
后者说明是**历史残留**，不清理就会一直失败。搞反了会出问题。

stage 目录名带版本与构建摘要，例如
`/opt/ai-device-client-stage-0.4.0-eaf47863e9d4`，不同包的 stage 互不相同。

#### `install-note` 为空时

通常表示失败发生在更早的门禁（如程序树是符号链接、registration 存在但程序树缺失）。
若 `preinstall-token` 也不存在，说明失败发生在 preinstall 之前的包校验阶段，
此时只能看 `/var/log/install.log`。

> 并发安装**不受支持**。请勿同时运行两个 `installer`，即使安装的是同一个包。
> 上述 token 的作用是让冲突明确失败并说明原因，不是让并发安装变得安全。

### 用户登出后设备在中心侧显示离线

预期行为。LaunchAgent 只在 GUI 登录会话中存在，登出即卸载。

### 重新登录后要等约一分钟客户端才起来

**这是 macOS launchd 的行为，不是客户端故障，无需处理。**

登录时 launchd 会把整个 `gui/<uid>` 用户域短暂置为 on-demand-only 模式，期间所有
待启动的 job 一律挂起；约 60 秒后域退出该模式，挂起的 job 才集中拉起。客户端只是
其中之一，同期 Apple 自家的 `usernoted`、`imagent`、Spotlight 索引进程同样被挂起。

`RunAtLoad`、`KeepAlive`、`ProcessType` 都不改变这个行为——挂起发生在域一级，任何
单个 job 的配置都绕不过。实测一次完整登录：

```
20:48:15.763  gui/501 entering bootstrap mode
20:48:15.763  gui/501 entering ondemand mode
20:48:15.765  pending spawn, domain in on-demand-only mode: ...（共 41 个 job）
20:49:15.863  exiting ondemand mode                     ← 恰好 60 秒
20:49:15.868  WILL_SPAWN → Successfully spawned ai-device-client-launcher[7692]
```

想确认当时确实处于这个状态，查 launchd 日志：

```bash
sudo log show --last 5m --predicate 'process == "launchd"' \
  | grep -E 'ondemand mode|pending spawn.*ai-device|Successfully spawned ai-device'
```

看到 `domain in on-demand-only mode: local.ai-device.client` 就是本节描述的情况，
等一分钟即可，不要重装或重新注册。

> 如果超过两分钟仍未启动，那就不属于本节的正常延迟，按下一节排查。

### 登录后等很久客户端仍不启动

先排除上一节的正常延迟：确认登录已超过两分钟，且 launchd 日志里没有
`domain in on-demand-only mode: local.ai-device.client`。之后按顺序查：

```bash
# 1. plist 是否存在（卸载后不会自动重建）
ls -l ~/Library/LaunchAgents/local.ai-device.client.plist

# 2. job 是否已加载，以及现在是什么状态
launchctl print gui/$(id -u)/local.ai-device.client 2>&1 | head -20

# 3. 注册记录是否还在
sudo ls -l "/Library/Application Support/ai-device-client-management/"
```

- plist 不存在：曾执行过卸载，需重新安装并注册（第 2、4 节）。
- `launchctl print` 报 `Could not find job`：plist 在但未加载，执行第 4 节的 `bootstrap`。
- job 存在但反复起退：看 `installed` 与 `runs` 计数，再按"程序损坏"一节处理。

### 双击 PKG 没反应

Gatekeeper 拦截。见第 2 节，必须用 `sudo installer`。

## 9. 支持范围与已知限制

| 项 | 状态 |
| --- | --- |
| Apple Silicon / arm64 | 支持 |
| Intel Mac / Rosetta | **不支持** |
| 未登录用户运行 | **不支持**。必须是已登录的 GUI 用户 |
| 多用户同时安装 | 未验证 |
| 并发安装 | 未验证 |
| 降级安装 | **不支持** |
| 签名与公证 | 已完成（`shipping` 模式）|
| GUI 双击安装 | **未验收**。仅用 `spctl` 与 `sudo installer` 验证过 |
| 中心业务 / MCP 端到端 | **未验收**。仅验证到 WebSocket 建连 |

已签名并公证的包可双击安装，Gatekeeper 不再拦截。当前交付的正式包为
`shipping` 模式（标题 `AI Device Client`，无未签名警示）。`verification`
模式与未签名开发包仍存在，用于隔离验证，不等同正式分发件。

已签名包仍须满足全部前置条件（配置 → 注册 → 启动），签名不改变生命周期语义。

**“连上了”具体指什么。** 当前已验收的范围是连接建立：`client.log` 输出
`connected to …/connect`，`lsof` 看到 TCP ESTABLISHED。这证明客户端拿到中心
`welcome` 帧，但**不证明**远程控制、指令下发、设备侧执行等业务链路可用。
把设备接入真正业务之前，应单独跑一次端到端验证。

## 10. 相关文档

- 实施细节与全部真机验收证据：[`client-native-macos-development.md`](../development/client-native-macos-development.md)
- 权威需求与交付标准：[`../requirements/client-native-install-upgrade.md`](../requirements/client-native-install-upgrade.md)
- 卸载后残留文件策略：[`client-native-macos-stage-cleanup-policy.md`](../design/client-native-macos-stage-cleanup-policy.md)
- 升级失败恢复契约：[`client-native-macos-upgrade-recovery-contract.md`](../design/client-native-macos-upgrade-recovery-contract.md)
- Linux 版对应指南：[`client-native-linux-guide.md`](client-native-linux-guide.md)
