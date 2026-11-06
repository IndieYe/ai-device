# 旧路径退役与保留清单

> 对应需求 §13.5：说明已删除的旧代码、仍保留的部分及保留原因。
> 这是**交付状态快照**，不是历史过程记录；逐批退役的验证细节见 [`client-native-linux-development.md`](client-native-linux-development.md)。

## 1. 当前唯一正式入口

| 用途 | 入口 | 落地模块 |
| --- | --- | --- |
| 安装 | `dpkg -i ai-device-client_<v>_amd64.deb` | `scripts/build-client-deb.py` |
| 首次配置 | `/opt/ai-device-client/runtime/bin/python3 -m device_runtime.client_setup` | `client_setup.py` → `client_config.py` |
| 服务注册 | `/usr/bin/ai-device-client-register --user <u>` | `client_register_cli.py` → `client_register.py` |
| 客户端主循环 | `/usr/bin/ai-device-client`（systemd 调用） | `client_cli.py` |
| 构建运行树 | — | `scripts/build-client-tree.py` |

`/usr/bin/ai-device-client` 直接进入 `client_cli`，**不经过旧管理 CLI**。

## 2. 已删除

以下均已从仓库移除，历史留在 Git。

| 类别 | 内容 |
| --- | --- |
| 旧 Linux 构建链 | `scripts/build-native-linux.sh`、`scripts/assemble-native-payload.py` 及仅检查二者的测试（56 项旧测试） |
| 旧候选/helper 运行链 | 12 个旧 native Linux 运行模块（bootstrap/candidate/helper/install/layout/orchestrate/package_sender/prepare/prepare_sender/start_sender/update/update_job），17 份专属测试，54 个直接引用脚本，7 个调用已退役包命令的验收脚本 |
| 旧 Linux 监督器 | `upgrade_supervisor.Systemd` 执行适配器；`templates/upgrade-stage1/{client,supervisor}.service.in`、`scripts/render_upgrade_adoption.py` 及专属测试 |
| 旧签名/验收脚本 | 绑定旧多树包格式的签名脚本与 `accept-native-*.py/sh` |
| 旧 Linux 客户端实例入口 | 已收窄关闭，新安装不走该路径 |

退役后 Device 测试数量下降来自明确删除旧测试，**不是覆盖率提高**。

## 3. 保留部分及原因

### 3.1 共享实例层（禁止整文件删除）

| 模块 | 保留原因 |
| --- | --- |
| `installation.py` | 被 `lifecycle` 与 `bundle` 共用，新客户端安装仍依赖 |
| `lifecycle.py` | `_persistent`、健康检查与持久目录同时服务 center 与 MCP |
| `bundle.py` | 供 Mac 阶段及其他角色使用 |
| `upgrader.py` | 通用实现，仍有 center 等角色调用；**仅 Linux 专属适配器被删** |
| `native_runtime.py` | 共享层 `installation` 仍引用 |

这些文件的调用图未完全拆分前不得整删。Linux 专属职责已退役，剩余部分服务 Mac 与中心/MCP。

### 3.2 业务可靠性

| 模块 | 保留原因 |
| --- | --- |
| `journal.py`、`tasks.py` | unknown 不重放与任务恢复的核心，新客户端继续使用 |
| `maintenance` 旧 drain/resume | 仍有旧管理调用者；新包**只用 idle lease**，不以保留旧操作作为兼容层 |

### 3.3 构建期工具（不在目标机执行）

| 脚本 | 保留原因 |
| --- | --- |
| `stage-native-dependencies.py` | 读取构建依赖锁，新完整包仍在用 |
| `prepare-native-manager-wheels.py` | 按锁选取 wheel 的构建工具，不进入安装路径 |
| `probe-native-*.py`、`probe-native-*-gate.sh` | 独立进程/凭据/目录/包事务实验。不是新客户端安装入口，**也不作为 DEB 验收证据**；RPM 不在本期范围 |

不能仅按 `native` 文件名前缀删除。

### 3.4 Mac 侧

Mac 旧路径在阶段二闭合，当前保留。**不得永久双实现。**

### 3.5 历史文档

`docs/native-lifecycle-implementation.md`、`docs/upgrade-stage1-*` 是旧路线历史资料，不作为当前指南或验收证明。`docs/device-linux-client.md` 已标注历史并指向新指南。

## 4. 判定原则

删除前必须确认：

1. 该文件无其他调用者（用导入图核对，不只看文件名前缀）
2. 相关测试是专属测试，而非共享回归
3. 删除后共享角色的回归仍通过

历史多次出现"按前缀判断"或"看起来没用"的误删风险，因此保留此清单作为审计依据。
