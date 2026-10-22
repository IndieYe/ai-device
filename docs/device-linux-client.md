# Linux Device 客户端（Ubuntu 容器首期）

> 三包迁移说明：本文保留来源项目的历史交付与验收记录，不代表新仓本次已完成相同平台验收。新包名称和当前支持范围以 [阶段C验收](stage-c-acceptance.md) 为准；AI 助手专属配置留在宿主仓库。

本期复用 Device、Bash、FS 核心，新增 Linux **client 角色**的可重建交付契约。macOS 仍支持 center/mcp/client；不据此声明 Linux center/mcp 正式交付、公网或 systemd 已验收。

## 交付边界

- Python >=3.13，实例安装通过 uv 创建 Python 3.13 环境。Ubuntu 24.04基础镜像不自带满足要求的解释器。
- 稳定 `run` 入口通过 `/usr/bin/env python3` 启动；启动环境的 PATH 必须能找到 `python3`。仅安装 uv 管理的 Python、但不配置可见入口，不足以启动客户端。测试镜像显式提供 `/usr/local/bin/python3`，不依赖源码或测试 harness 的 PATH。
- 构建器新增 `bundle build --target-system linux`，默认仍为 `darwin`。
- Linux bundle 仍包含准确的 Device/Bash/FS 三份 pure Python wheel、来源提交与SHA-256，以及第三方依赖约束。
- Linux manifest 的 `target.system=linux`，`roles` 只含 `client`。中心和MCP角色不能通过篡改manifest或安装参数启用。
- 安装、重建、升级、回退、备份与恢复校验当前主机平台、bundle平台及角色，不能把Mac bundle安装到Linux，也不能将Linux快照直接恢复到Mac。
- 保留现有身份、journal、unknown不重放和任务输出契约。新平台不是迁移旧Mac实例的理由。

## 构建和安装

在具备准确三份wheel、constraints和来源提交JSON的开发环境中：

```bash
device-runtime bundle build \
  --target-system linux --output <new-linux-bundle> --version <release-version> \
  --device-wheel <device-wheel> --bash-wheel <bash-wheel> --fs-wheel <fs-wheel> \
  --constraints <constraints.txt> --sources <sources.json>

device-runtime instance install --help
```

按install帮助选择独立root、instance_id及client角色。不要使用公网同名同版本的Bash/FS包替代交付wheel。首次安装不得覆盖既有目录；普通用户运行，不默认授予root/sudo。

## 连接与凭证

Linux客户端主动连接中心。容器内127.0.0.1是容器自身；远端非回环连接必须使用WSS。现有URL策略不因容器测试而放宽。

使用独立device_id及设备Token，凭证仅通过受控私有文件/进程环境供给，不放命令参数、镜像、交付包、日志或文档。公网安全入口或Tailscale部署另行授权。

隔离容器验收可以在同一容器回环上运行临时中心和MCP测试适配器；它们属于测试装置，不是Linux中心角色的正式安装支持。不得由此宣称Mac主ServerPool或公网已验收。

## 维护与验收

安装/生命周期工具不停止任何进程。`--stopped`是调用者声明实例已停机，不会执行停止。隔离实例的重建/升级/回退/备份恢复应在进程退出后进行；真实服务操作遵守运行安全规则。

首期要求实际Ubuntu ARM64安装、准确模块字节核对、11工具发现与功能验收、两侧journal及任务完整性检查、重建/升级/回退/恢复后持久数据保持。

Ubuntu容器功能通过不代表其他CPU架构、systemd、主机重启恢复或公网链路通过；这些需在目标Linux主机另行验收。

## 本期实测结果

已在 Apple Container 1.5.0 的 Ubuntu 24.04.5 ARM64 环境，以普通用户（非root）和 Python 3.13.12 完成：

- 准确三包安装，Device14/Bash13/FS34个Python模块与wheel逐字节一致。
- 独立stdio MCP → 容器内临时回环中心 → 已安装Linux客户端，11工具发现和19条受理操作成功。
- 中文argv/stdin、同ID回执、文件读写编辑插入及两项dry-run、标准PNG、后台任务和7字节输出游标。
- 中心/客户端操作库各19条成功，任务库1条成功，三个SQLite quick_check均ok。
- 进程退出后的重建、升级、回退、备份恢复，持久文件Hash保持；恢复后的客户端重新上线，操作数未增加。

验收曾遇到工具链PATH缺python3、测试registry包装对象处理错误、SDK环境变量投影遗漏和任务库表名误判；保留失败现场，修正测试装置，没有修改运行代码来绕过网络或认证策略。功能成功后的维护续验没有重放19条操作。

Device源码回归399项通过（包含Linux专属21项），不等于全AI Assistant后端、所有文档或跨平台实机套件通过。Linux bundle为本地准确测试制品，未发布到PyPI、未合入本地主线、未用于正式公网部署。

可复现隔离装置见 [Ubuntu容器验收示例](../packages/device-runtime/examples/linux-container/README.md)。此次功能与维护结果分别取自一次真实功能验收和其后独立续验；不得把最终整合脚本的格式检查说成额外重跑了一轮端到端操作。

### CentOS 7 固定依赖

标准 `bundle build` 与 `instance install/prepare/rebuild` 支持 `--dependency-dir`。完整本地 wheel 清单、v3 快照、无索引安装、解释器前置条件和构建验收限制见 [Device交付说明](device-delivery.md#centos-7-的本地依赖交付)。旧 v2 bundle 继续可用；不通过临时 uv 包装器或取消 UV 环境过滤实现。
