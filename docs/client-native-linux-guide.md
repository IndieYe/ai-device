# Linux 客户端安装升级指南（Ubuntu 24.04 amd64）

> 适用版本：客户端包 `ai-device-client` 0.4.x，运行时 0.3.5
> 支持范围：**仅 Ubuntu 24.04 amd64**。不支持其他发行版、其他架构、降级安装。
> 本文是操作说明；实施细节与验收证据见 [`client-native-linux-development.md`](client-native-linux-development.md)，权威需求见 [`../doc/requirements/client-native-install-upgrade.md`](../doc/requirements/client-native-install-upgrade.md)。

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

> 若目标机缺少 `systemd`、`libc6 (>= 2.39)`、`ca-certificates` 或 `util-linux`，`dpkg` 会在解包前拒绝。Ubuntu 24.04 默认已满足。

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
{"version": "0.3.5", "build_id": "sha256:1deefe4b25cf..."}
```

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

实测（0.4.0 → 0.4.1）：主进程 PID 从 133 变为 232，运行时 0.3.4 → 0.3.5，配置与凭证保留。

### 升级时的行为

| 情况 | 结果 |
| --- | --- |
| 服务空闲 | 正常升级，配置与业务数据保留 |
| 服务正在执行任务 | **拒绝升级**，服务与任务不受影响 |
| 同时执行多个安装 | 只有一个成功，其余被包管理锁拒绝 |
| 正在升级 | 新注册/安装被拒绝，不排队 |
| drop-in override 存在 | 拒绝，需先移除 |
| 旧布局共存 | 拒绝，不破坏现有服务 |

拒绝是立即返回的，不会阻塞等待。忙拒绝的原文输出：

```
client is busy with active tasks; retry when tasks finish; service kept running
```

看到这条说明**任务仍在跑，服务没被动过**。等任务完成后再执行同一条 `dpkg -i` 即可，不需要额外操作。

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

> 凭证在 purge 时随 `/etc/ai-device-client/` 一起删除。若设备已退役，建议同时手动清理 `/var/lib/ai-device-client/`。

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

本文命令均在隔离 Ubuntu 24.04 amd64 容器中实测。

- 包：`ai-device-client` 0.4.0（运行时 0.3.4）、0.4.1（运行时 0.3.5）
- 覆盖：干净目标安装、首次配置、注册、启动、A→B 升级、普通卸载、purge、损坏修复、普通用户运行、用户切换
- 未覆盖：真实网络故障、真实系统 halt/断电、跨大版本状态迁移、真实硬件性能

详细证据与历史迭代记录见 [`client-native-linux-development.md`](client-native-linux-development.md)。
