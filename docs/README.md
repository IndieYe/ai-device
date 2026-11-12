# 文档索引

本目录按**用途**分层，同一层内按主题排列。先看下面的入口表，不确定时再回到目录说明。

## 我该看哪一份

| 我想…… | 看 |
| --- | --- |
| 在 Mac 上安装、升级、卸载客户端 | [guide/client-native-macos-guide.md](guide/client-native-macos-guide.md) |
| 在 Linux 上安装、升级、卸载客户端 | [guide/client-native-linux-guide.md](guide/client-native-linux-guide.md) |
| 部署 Device 到我的 Mac 工作站 | [guide/macos-ai-workstation-setup-and-verification.md](guide/macos-ai-workstation-setup-and-verification.md) |
| 配置 tmux / Harness 等配套工具 | [guide/macos-optional-tools.md](guide/macos-optional-tools.md) |
| 了解客户端安装升级的需求与验收标准 | [requirements/client-native-install-upgrade.md](requirements/client-native-install-upgrade.md) |
| 了解为什么这样设计（生命周期、锁、并发） | [design/](#design-设计与协议) |
| 查当时的开发记录和验收证据 | [development/](#development-开发记录与验收证据) |
| 找早已被替代的历史方案 | [archive/](#archive-历史归档) |

**遇到安装或升级报错，先看对应平台的 guide。** 两份指南的「故障处理」章节覆盖了当前已知的全部问题。

## guide/ 面向使用者

现在就能照着做的操作说明。

| 文档 | 说明 |
| --- | --- |
| [client-native-macos-guide.md](guide/client-native-macos-guide.md) | macOS 客户端安装升级指南（Apple Silicon arm64，PKG/launchd） |
| [client-native-linux-guide.md](guide/client-native-linux-guide.md) | Linux 客户端安装升级指南（Ubuntu/Debian amd64 DEB、RHEL 系 x86_64 RPM） |
| [macos-ai-workstation-setup-and-verification.md](guide/macos-ai-workstation-setup-and-verification.md) | macOS Device 用户级部署、迁移与验收 |
| [macos-optional-tools.md](guide/macos-optional-tools.md) | macOS 可选配套工具：tmux、Harness 与管理员配置（非必需依赖） |

## requirements/ 需求

定义「要做成什么样」，是设计文档的上游依据。

| 文档 | 说明 |
| --- | --- |
| [client-native-install-upgrade.md](requirements/client-native-install-upgrade.md) | **权威需求**：客户端完整包安装与升级重构 |
| [client-linux-platform-compat-phase1-glibc217.md](requirements/client-linux-platform-compat-phase1-glibc217.md) | 阶段一：glibc 2.17 基线对齐 |
| [client-linux-platform-compat-phase2-cgroup-v1.md](requirements/client-linux-platform-compat-phase2-cgroup-v1.md) | 阶段二：cgroup v1 与旧版 systemd 兼容 |
| [client-linux-platform-compat-phase3-rpm.md](requirements/client-linux-platform-compat-phase3-rpm.md) | 阶段三：RPM 交付 |
| [ai-device-monorepo-migration.md](requirements/ai-device-monorepo-migration.md) | 独立仓库与三包迁移需求 |

## design/ 设计与协议

回答「为什么这么实现」。多数是设计阶段的结论，部分协议仍在演进。

| 文档 | 说明 |
| --- | --- |
| [client-native-macos-lifecycle-design.md](design/client-native-macos-lifecycle-design.md) | macOS 客户端生命周期协议 |
| [client-native-macos-concurrent-install-design.md](design/client-native-macos-concurrent-install-design.md) | 并发安装互斥：设计方案与探针结论 |
| [client-native-macos-installer-write-mutex.md](design/client-native-macos-installer-write-mutex.md) | PKG 写入期互斥 |
| [client-native-macos-launchd-validation-plan.md](design/client-native-macos-launchd-validation-plan.md) | launchd 协调的最小条件与验证计划 |
| [client-native-macos-stage-cleanup-policy.md](design/client-native-macos-stage-cleanup-policy.md) | stage 旧树清理策略 |
| [client-native-macos-upgrade-recovery-contract.md](design/client-native-macos-upgrade-recovery-contract.md) | upgrade 失败恢复契约（方案 B，已实现） |
| [native-lifecycle-p0-design.md](design/native-lifecycle-p0-design.md) | 原生安装、配对与远程升级 P0 冻结设计 |
| [native-package-upgrade-transaction-design.md](design/native-package-upgrade-transaction-design.md) | 原生包级升级事务设计 |
| [native-job-run-attribution-design.md](design/native-job-run-attribution-design.md) | Job 终态持久归属设计 |
| [native-cli-simplification-design.md](design/native-cli-simplification-design.md) | 安装与升级命令简化改造设计（未实施） |
| [native-multi-version-activation-design.md](design/native-multi-version-activation-design.md) | 多版本激活协议（设计稿） |
| [native-helper-peer-protocol-review.md](design/native-helper-peer-protocol-review.md) | Helper 受控启动与 peer 身份评审 |

## development/ 开发记录与验收证据

记录「做过什么、验证到什么程度」。验收结论有时效性，不代表当前状态。

| 文档 | 说明 |
| --- | --- |
| [client-native-macos-development.md](development/client-native-macos-development.md) | macOS arm64 客户端完整包开发记录 |
| [client-native-linux-development.md](development/client-native-linux-development.md) | Linux 客户端完整包开发与验收边界 |
| [implementation-status.md](development/implementation-status.md) | 三包导入实施状态（含过时说明） |
| [stage-c-acceptance.md](development/stage-c-acceptance.md) | 阶段 C 独立交付验收 |
| [release-preparation.md](development/release-preparation.md) | D1 本地发布准备与外部门禁 |
| [glibc217-implementation.md](development/glibc217-implementation.md) | glibc 2.17 兼容基线实施 |
| [native-archive-layout-2026-10-30.md](development/native-archive-layout-2026-10-30.md) | 归档归属布局真实制品验收记录 |

## archive/ 历史归档

**不再维护，仅供追溯。** 其中的命令、路径和结论都不应直接用于当前版本。

保留这些文档是为了查证「当时为什么那样做」，以及核对某个决策的历史依据。每份文档开头通常有状态说明，会指出它被哪份新文档取代。

| 文档 | 说明 |
| --- | --- |
| [upgrade-stage1.md](archive/upgrade-stage1.md) | 阶段1：单机升级器（未部署、未完成验收） |
| [upgrade-stage1-acceptance.md](archive/upgrade-stage1-acceptance.md) | 阶段1 真实 Linux/systemd 验收证据矩阵 |
| [upgrade-stage1-adoption.md](archive/upgrade-stage1-adoption.md) | 阶段1 显式纳管：审阅模板与交接契约 |
| [upgrade-stage1-recovery.md](archive/upgrade-stage1-recovery.md) | 阶段1 人工恢复：先取证、后单步授权 |
| [native-lifecycle-implementation.md](archive/native-lifecycle-implementation.md) | **历史记录（客户端路线已替代）**：旧 native 安装、候选/审批/helper、签名与激活方案 |
| [native-helper-migration-draft.md](archive/native-helper-migration-draft.md) | 旧 helper 安全迁移协议草案（未实现、不得部署） |
| [native-helper-dispatch-handoff-design.md](archive/native-helper-dispatch-handoff-design.md) | P1 daemon—更新器持久意图交接设计草案 |
| [native-query-supervisor-recovery-design.md](archive/native-query-supervisor-recovery-design.md) | P1 查询监督意图与候选验证恢复设计草案 |
| [device-delivery.md](archive/device-delivery.md) | Device 版本化交付包（bundle 独立实例，Mac 首期） |
| [device-runtime.md](archive/device-runtime.md) | 设备执行 Runtime 与中心 Device MCP |
| [device-linux-client.md](archive/device-linux-client.md) | Linux Device 客户端（旧容器实例路径，已替代） |
| [operator-release.md](archive/operator-release.md) | 本机 Operator 稳定入口与发布清单（未部署） |
| [client-native-legacy-inventory.md](archive/client-native-legacy-inventory.md) | 旧路径退役与保留清单 |
| [client-native-legacy-deletion-review.md](archive/client-native-legacy-deletion-review.md) | 旧路径删除核对清单 |
| [client-macos-real-install-verification-plan.md](archive/client-macos-real-install-verification-plan.md) | macOS 真实安装验证方案 |
| [client-macos-stage-path-uniquification-plan.md](archive/client-macos-stage-path-uniquification-plan.md) | stage 路径唯一化小方案 |

## 其他

- [source-manifest.json](source-manifest.json)：迁移来源文件与哈希清单
- [third-party-licenses/](third-party-licenses/)：第三方许可补充资料与再分发边界
- [../README.md](../README.md)：仓库总览
- [../packages/device-runtime/README.md](../packages/device-runtime/README.md)：Device Runtime 使用说明

## 维护约定

- **新增操作说明**放 `guide/`，并同步更新上面的入口表。
- **新增设计文档**放 `design/`；已被替代的方案移入 `archive/`，不要留在原处。
- **归档只移动、不删除、不合并**，保留 git 追溯。移动后需修正全仓引用。
- 文档内部有相对链接，移动文件后必须检查链接是否仍然有效。
