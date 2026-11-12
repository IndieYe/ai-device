# Linux 客户端安装升级指南（Ubuntu 24.04 amd64）

> 适用版本：客户端包 `ai-device-client` 0.4.x，运行时 0.3.6
> 支持范围：**仅 Ubuntu 24.04 amd64**。不支持其他发行版、其他架构、降级安装。
> 包版本与运行时版本相互独立，见第 5 节。
> 本文是操作说明；实施细节与验收证据见 [`client-native-linux-development.md`](../development/client-native-linux-development.md)，权威需求见 [`../requirements/client-native-install-upgrade.md`](../requirements/client-native-install-upgrade.md)。

## 1. 它是什么

一个自含运行时的设备客户端。包内自带 Python 3.13 和客户端依赖，**目标机不需要安装 Python、pip 或 uv，也不需要联网下载依赖**。

安装后分布如下：

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| 程序 | `/opt/ai-device-client/` | 含私有运行时；卸载时整体删除 |
| 配置 | `/etc/ai-device-client/client.json` | 0644，不含密钥 |
| 凭证 | `/etc/ai-device-client/token` | 0600，属服务运行用户 |
| 业务状态 | `/var/lib/ai-device-client/` | 操作日志 SQLite；卸载保留 |
| 应用日志 | `/var/log/ai-device-client/client.log` | 卸载保留 |
| 服务单元 | `/etc/systemd/system/ai-device-client.service` | 由注册步骤生成 |

程序、配置、状态三者分离：升级只替换程序，**配置、身份和业务数据不会被覆盖**。

## 2. 安装

```bash
sudo dpkg -i ai-device-client_<version>_amd64.deb
```

预期输出：

```
Setting up ai-device-client (<version>) ...
installed; configuration and service registration required
```

此时服务尚未配置，`systemctl status ai-device-client` 会显示 `not-found`，这是正常的——服务单元还没创建。

> 若目标机缺少 `systemd`、`libc6 (>= 2.17)`、`ca-certificates` 或 `util-linux`，`dpkg` 会在解包前拒绝。Ubuntu 24.04 默认已满足。
>
> 最低 glibc 是 2.17，来自包内 CPython 与全部 wheel 的实际构建基线（均为 manylinux_2_17 或更低），不是构建机的版本。客户端启动时也会自行核对一次：低于 2.17 会给出明确的版本提示后退出，而不是在动态链接阶段报错。
>
> glibc 满足不等于系统受支持：还需 systemd 与 cgroup 版本符合要求，见 `docs/client-native-linux-development.md`。

## 3. 首次配置

配置需要**三个值**，从中心的设备登记信息获取：

- 中心地址：`ws://`（本机回环）或 `wss://`（远端，必须 TLS）
- 设备 ID：本机在中心侧的标识
- 设备 Token：与设备 ID 配对的凭证

配置命令**以将要运行服务的用户身份执行**。以下以 `device` 为例：

```bash
sudo -u device /opt/ai-device-client/runtime/bin/python3 -I -B \
  -m device_runtime.client_setup \
  --config-dir /etc/ai-device-client \
  --state-dir /var/lib/ai-device-client \
  --server-url wss://center.example.com \
  --device-id <device-id> \
  --token-stdin
```

执行后交互式提示输入 Token（不回显）。自动化场景可用管道：

```bash
printf '%s\n' "$DEVICE_TOKEN" | sudo -u device /opt/ai-device-client/runtime/bin/python3 -I -B \
  -m device_runtime.client_setup --config-dir /etc/ai-device-client \
  --state-dir /var/lib/ai-device-client --server-url wss://center.example.com \
  --device-id <device-id> --token-stdin
```

预期输出：

```
Configured: /etc/ai-device-client/client.json. Service registration/start is still required.
```

### 配置约束（实测确认）

| 约束 | 说明 |
| --- | --- |
| Token 长度 **16–4096** 字符 | 且必须是可打印 ASCII。少于 16 字符会报 `configuration failed` |
| `--state-dir` 必须是绝对路径 | 相对路径直接拒绝 |
| 配置目录**必须不存在** | 已存在时以退出码 2 拒绝，绝不覆盖 |
| 以服务运行用户执行 | 配置目录会 `chown` 给该用户。用错用户后续会被 register 纠正属主 |
| Token 不进命令行 | 只用 `--token-stdin` 或交互输入，不要写成参数 |

`client_setup` 没有 `/usr/bin` 软链接，必须用上面的完整路径调用。

## 4. 注册并启动服务

注册步骤决定服务以哪个用户运行，并创建 systemd 单元：

```bash
sudo ai-device-client-register --user <username>
```

- `--user root`：显式以 root 运行。设备上的命令和文件访问都具备 root 权限，**仅在明确需要时使用**。
- `--user <普通用户>`：推荐。命令与文件访问权限等同于该用户。

预期输出：

```
Registered: <username>. Start is still required.
```

然后启动：

```bash
sudo systemctl start ai-device-client
```

检查：

```bash
systemctl is-active ai-device-client    # 期望 active
systemctl is-enabled ai-device-client   # 期望 enabled
```

> 注册只做 `enable`，**不会自动启动**。这是刻意的：首次配置与启动由人确认。

### 更换运行用户

先停止服务，再重新注册：

```bash
sudo systemctl stop ai-device-client
sudo ai-device-client-register --user <new-user>
sudo systemctl start ai-device-client
```

服务处于 `active` 时切换用户会被拒绝，需先停止。切换后 `/etc/ai-device-client/token` 和 `/var/lib/ai-device-client/` 的属主会转移到新用户；`client.json` 保持 root 所有（0644，不含密钥）。

## 5. 查看版本与状态

```bash
# 运行时版本与构建标识
/opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_cli --version

# 包版本
dpkg -l ai-device-client

# 服务状态与主进程
systemctl status ai-device-client
systemctl show ai-device-client --property=MainPID --value
```

版本 JSON 示例：

```json
{"version": "0.3.6", "build_id": "sha256:6d0d29e8073b..."}
```

### 包版本与运行时版本是两件事

`dpkg -l` 看到的是**包版本**（发行号），`--version` 看到的是**运行时版本**。两者独立演进，不要求前缀相同：

| 名称 | 示例 | 含义 |
| --- | --- | --- |
| 包版本 | `0.4.2` | 制品发行号，反映打包、依赖、安装脚本的变化 |
| 运行时版本 | `0.3.6` | 客户端程序自身版本，反映业务代码变化 |
| build_id | `sha256:6d0d29e8...` | 精确实现身份，同版本不同构建也不同 |

所以 `dpkg -l` 显示 0.4.2 而 `--version` 显示 0.3.6 是**正常的**，不代表装错包。这与 Ubuntu（发行号 24.04 / 内核 6.8）或 Android（Android 14 / 内核 6.1）的做法一致。

判断升级是否成功，看的是**运行时版本 + build_id**，不是包版本前缀。中心侧显示的设备版本也是运行时版本和 build_id。

> **服务 PID 存在不等于设备在线。** 中心是否看到本设备，以中心侧的 `/v1/devices` 为准；本机就绪与连接成功是两件事。

## 6. 升级

用同一入口安装新版本包：

```bash
sudo dpkg -i ai-device-client_<new-version>_amd64.deb
```

预期输出包含：

```
installed; client locally ready
```

`locally ready` 是安装器停止旧进程、替换程序、重新启动并确认新版本就绪后的结论。

实测（0.4.1 → 0.4.2，ali-jp）：主进程 PID 由 125460 变为 131140（旧进程退出，新进程拉起），运行时 0.3.5 → 0.3.6，build_id 更新为 `6d0d29e8...`，`client.json` 与 `token` 的 SHA256 逐字节未变，操作日志 SQLite 保留，无残留 stage 目录。中心侧随后显示该设备 online，运行时版本与 build_id 与安装包一致。

### 升级时的行为

| 情况 | 结果 |
| --- | --- |
| 服务空闲 | 正常升级，配置与业务数据保留 |
| 服务正在执行任务 | **拒绝升级**，服务与任务不受影响 |
| 经本客户端通道执行升级命令 | **必然拒绝**：该命令本身就是活跃请求（见下） |
| 同时执行多个安装 | 只有一个成功，其余被包管理锁拒绝 |
| 正在升级 | 新注册/安装被拒绝，不排队 |
| drop-in override 存在 | 拒绝，需先移除 |
| 旧布局共存 | 拒绝，不破坏现有服务 |

拒绝是立即返回的，不会阻塞等待。忙拒绝的原文输出：

```
client is busy with active tasks; retry when tasks finish; service kept running
```

看到这条说明**任务仍在跑，服务没被动过**。等任务完成后再执行同一条 `dpkg -i` 即可。

### 不要经客户端自己的通道执行升级

空闲判定是 `active_requests == 0 && active_tasks == 0`，而**任何通过该客户端执行的命令都计入 `active_requests`**，包括远程执行通道。从客户端通道里跑 `dpkg -i` / `rpm -Uvh`，执行这条命令的客户端自己就成了障碍：它要等一个空闲实例，而它正忙着跑这条命令。

这种用法会得到 `AI_DEVICE_UPGRADE_REFUSED_V1`。

> **不要仅凭这个码断定原因。** 它是多个拒绝阶段的默认输出，服务忙、stage 残留等
> 都会输出同一行。看到它只能说明升级被拒绝了，不能据此认为属于忙、没有副作用
> 或制品有问题。要继续看安装日志和当前状态再判断。

**先探测当前是否可升级**，不必反复尝试。下面的脚本通过维护 socket 申请一次租约：

```bash
python3 - <<'EOF'
import socket
p = '/var/lib/ai-device-client/maintenance.sock'
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

使用安装包自带运行时（未装系统 python3 时）：将上面的 `python3` 换成
`/opt/ai-device-client/runtime/bin/python3`。

> **两点注意。** 一是 acquire 不是纯只读：申请成功后会**暂时占住准入**，直到连接关闭，
> 期间其他安装/升级会被拒绝。脚本结束即关闭连接释放，不要长时间保持这个连接。
> 二是它只是**瞬时快照**，返回 true 不保证紧接着的安装一定成功。

**正确做法**：升级只在目标机器的本地终端或 SSH 会话里执行，不要经 Device 的远程命令通道。这要求该机器至少有一条不依赖待升级客户端的访问路径——升级前先确认它可用。

> 若 `active_tasks` 长期不为 0，先只读对账：查操作 ID、对应进程和数据库记录，
> 判断是仍在执行的任务还是没有收尾的残留。`ps` 没看到同名进程不足以证明已退出。
> **不要为了清状态就重启客户端或改数据库**：重启会中断当前连接和正在跑的任务，
> 需要先确认影响并取得同意。

> **但要注意包状态会停在 `ri`。** prerm 以非零退出后，dpkg 保留包的"待删除"意图，`dpkg -l` 显示 `ri` 而不是 `ii`。这不会影响正在运行的服务，也不会被 `apt upgrade`、`dist-upgrade`、`autoremove`、`install -f` 自动处理；但之后显式执行 `apt remove ai-device-client` 会直接删除，不再就此处的 busy 情况询问。
>
> 任务结束后重装同版本即可回到 `ii`：
>
> ```bash
> sudo dpkg -i ai-device-client_<version>_amd64.deb
> ```
>
> 重装会正常停服、替换、再启动，配置与业务数据保留。不要用 `dpkg --purge --force-all` 之类的方式绕过，那会连配置和凭证一起删掉。
>
> **升级路径不会留下 `ri`，与卸载不同。** `ri` 只在 `dpkg -r` 时出现；`dpkg -i` 遇 busy 时包状态**保持 `ii`**。
>
> 原因是 dpkg 的行为不同：卸载时旧包 prerm 以非零退出，dpkg 保留删除意图；升级时旧包 prerm 失败后，dpkg 会打印 `trying script from the new package instead` 并改用新包的 prerm 重试，新旧脚本都因 busy 拒绝后以 rc=1 结束，但包状态不变。
>
> 实测（ali-jp, 0.4.2 同版本重装, 任务进行中）：
>
> ```
> client is busy with active tasks; retry when tasks finish; service kept running
> dpkg: warning: old ai-device-client package pre-removal script subprocess returned error exit status 1
> dpkg: trying script from the new package instead ...
> dpkg: ... it looks like that went OK
> note: stopping the client; ...
> client is busy with active tasks; retry when tasks finish; service kept running
> dpkg: error processing archive (--install):
>  new ai-device-client package pre-installation script subprocess returned error exit status 1
> ```
>
> 被拒后核对：包状态仍为 `ii`，服务与主进程未变，运行中的任务继续，配置与凭证 SHA256 不变，新发起的任务立即成功（准入保持）。任务结束后再执行同一条 `dpkg -i` 正常完成。

### 关于降级

`dpkg -i` 装低版本在技术上可行，但**降级不在支持范围内**：状态格式可能不兼容。需要回退请用同版本重装修复，或按第 8 节处理。

## 7. 卸载

### 保留数据的卸载

```bash
sudo dpkg -r ai-device-client
```

实测结果：

- `/opt/ai-device-client/` **完全删除**（含私有运行时，无残留空目录）
- `/etc/ai-device-client/` **保留**（配置与凭证）
- `/var/lib/ai-device-client/` 与 `/var/log/ai-device-client/` **保留**（业务数据与日志）
- 服务单元移除，服务停止

重新安装后，配置仍在，注册并启动即可恢复。

### 连配置一起清除

```bash
sudo dpkg --purge ai-device-client
```

实测结果：程序、配置目录一并删除；`/var/lib/ai-device-client/` 下的业务数据（操作日志 SQLite）**仍然保留**。

> **purge 会直接删除凭证，没有备份也没有二次确认。**
>
> `dpkg --purge` 会执行 `rm -rf /etc/ai-device-client`，`token` 和 `client.json` 一并消失。这个动作用户不可恢复：
>
> - 设备侧凭证只此一份，删掉后设备无法再用原身份连回中心。
> - 中心保存着设备 token（`/etc/device-runtime/center.env` 的 `DEVICE_RUNTIME_DEVICE_TOKENS`），可从中心取回该设备的 token 重新写入。这是唯一的恢复路径。
> - 若中心也丢失（环境重建、未备份），该设备只能以新身份重新注册，原设备记录成为孤儿。
>
> 因此 purge 前先确认两件事：设备是否确定退役；中心侧 token 是否已确认可取回。只想重置配置请用「保留数据的卸载」后重新配置，不要 purge。
>
> 另外，`/var/lib/ai-device-client/` 下的业务数据（操作日志 SQLite）**purge 也不会删**，这是有意设计。若设备确实退役，这部分需手动清理。

## 8. 故障处理

### 安装后显示"待配置"，服务起不来

正常状态。`systemctl status` 显示 `not-found` 说明还没执行第 4 节注册。按顺序做：配置 → 注册 → 启动。

### 程序损坏、客户端起不来

**不需要旧版本介入，用完整包重装即可修复。**

```bash
sudo dpkg -i ai-device-client_<version>_amd64.deb
```

实测：删掉 `/opt/ai-device-client/runtime/bin/python3` 后，安装器能恢复解释器。修复后若配置仍在，注册并启动即可。

### 配置失败，提示 `configuration failed`

按顺序排查：

1. Token 长度是否 ≥ 16 字符
2. `--state-dir` 是否为绝对路径
3. `--config-dir` 是否已存在（重复初始化会拒绝）
4. URL 是否以 `ws://` 或 `wss://` 开头，且 `wss://` 用于非回环地址

### 配置目录已存在，但内容不完整

用 `--resume` 补全缺失文件（不覆盖已存在的文件）：

```bash
sudo -u <user> /opt/ai-device-client/runtime/bin/python3 -I -B \
  -m device_runtime.client_setup --resume \
  --config-dir /etc/ai-device-client --state-dir /var/lib/ai-device-client \
  --server-url <url> --device-id <id> --token-stdin
```

`--resume` 的前提：

- 目录**必须已存在**且归当前用户所有、权限为 0700
- 目录内只能有 `token` 和 `client.json`，有别的文件会拒绝
- 输入必须与缺失文件匹配；残留内容不匹配会拒绝并保留现场

> 目录不存在时用 `--resume` 会失败，此时应按第 3 节做首次配置。

### 升级失败，提示 `stopping the client; if unpacking then fails it stays stopped`

安装器在停止服务时发出的提示：解包如果失败，客户端会停留在停止状态。此时**用完整包重装**：

```bash
sudo dpkg -i ai-device-client_<version>_amd64.deb
```

不要尝试用旧版本恢复——旧程序可能已部分替换。

### 旧布局冲突

若目标机存在旧版安装（`/opt/ai-device` 或旧服务单元），安装会**拒绝且不破坏现有服务**。先确认旧服务已停用并移除旧布局，再安装新客户端。新旧客户端不会同时运行。

### 查看日志

```bash
tail -50 /var/log/ai-device-client/client.log
journalctl -u ai-device-client -n 50
```

## 9. 已支持范围与非目标

**支持**：Ubuntu 24.04 amd64；单机单客户端；root 或指定普通用户运行；systemd 生命周期；A→B 升级；同版本重装修复；保留数据的卸载。

**不在本期范围**：

- 非 Ubuntu 发行版、非 amd64 架构、CentOS/RHEL
- 降级安装
- 客户端自行升级自己（升级由包管理器在设备上执行）
- apt 仓库或 Homebrew 分发；包未签名/未公证
- 远程在线升级、旧版本兼容层
- macOS（见 `client-native-macos-development.md`，尚未交付）

## 10. 验收证据来源

本文命令均在隔离 Ubuntu 24.04 amd64 环境实测（容器与独立云主机）。

- 包：`ai-device-client` 0.4.0（运行时 0.3.4）、0.4.1（运行时 0.3.5）、0.4.2（运行时 0.3.6）
- 覆盖：干净目标安装、首次配置、注册、启动、A→B 升级、普通卸载、purge、损坏修复、普通用户运行、用户切换、**busy 时拒绝卸载及恢复**、**busy 时拒绝升级（状态保持 ii）**、**包版本与运行时版本解耦**
- 未覆盖：真实网络故障、真实系统 halt/断电、跨大版本状态迁移、真实硬件性能

详细证据与历史迭代记录见 [`client-native-linux-development.md`](../development/client-native-linux-development.md)。
