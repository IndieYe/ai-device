# 原生安装、在线配对与远程升级：实施记录

## 1. 任务与恢复入口

- 创建日期：2026-10-26。
- 当前会话：`ses-14204`；工作标识：`device-native-lifecycle`。
- 源码仓库：`/Users/kongkongye/data/project/ai-device`。
- 任务 Worktree：`/Users/kongkongye/data/project/ai-device-wt-native-lifecycle-ses14204`。
- 任务分支：`feat/native-lifecycle-ses14204`。
- 本地集成目标：`main`；记录目标不表示已授权自动集成或推送。
- 起始基线：`4f7891d1fc27eeab20cc60f7e5298cb1ab187045`。
- 权威需求：ai-assistant 仓库 `doc/requirements/device-native-installation-pairing-and-remote-upgrade.md`，需求提交 `7be75392c352151d6cb29cbdad2770f2694dcedd`。
- 当前需求文件本机入口：`/Users/kongkongye/data/project/ai-assistant/doc/requirements/device-native-installation-pairing-and-remote-upgrade.md`。该路径仅为导航；新环境先核实仓库与准确版本，不依赖本机目录永久存在。

用户已要求开始在 Worktree 开发。本地源码、测试与设计准备属于当前开发范围；正式设备、发布、部署、远程 Git、删除、真实服务操作以及重大未决架构取舍保持独立门禁。需求放在宿主仓库，实际实现留在 ai-device，不复制回宿主。

## 2. 环境与归属

本环境是本任务新建的原生 Git Worktree，复用 main 的已提交基线，不接管或复制其他任务改动。

宿主 `scripts/worktree_env.py` 创建尝试因本仓库没有其要求的 Alembic 目录而拒绝。该尝试只创建了任务分支，未创建 Worktree；已核验分支与 main 的 SHA 相同，再通过 `git worktree add <path> <branch>` 附加。没有绕过真实服务或测试门禁，没有删除／改写已有分支。

本记录兼作独立仓库的最小环境清单：

| 项目 | 当前登记 |
| --- | --- |
| 归属 | work_id=device-native-lifecycle；session_id=ses-14204；非代理 |
| 生命周期 | active；任务现场保留，不自动清理 |
| 源码隔离 | 独立 Worktree、任务分支 |
| 依赖隔离 | 本 Worktree 的 `.venv`，uv 锁定安装 |
| 系统与架构 | Darwin arm64；仅说明本机，不代表 Linux 支持矩阵 |
| 数据库 | 未创建运行实例或真实数据库；测试使用临时目录 |
| 运行目录 | 未复制正式工作目录、配置或凭证 |
| 端口与服务 | 未分配运行端口，未启动常驻服务 |
| 外部设备 | 未访问、未注册、未配对、未升级 |
| 发布与远程 Git | 未执行 |

依赖准备：

```bash
uv sync --python 3.13 --locked --all-packages --all-extras --group dev
```

不使用主仓库 `.venv` 代替本环境；不复制真实中心、客户端身份或密钥。

## 3. P0 当前事实与差异

当前阶段：**P0 设计完成，已按用户认可的首选方案冻结 D01—D10 和动作契约；P1 尚未开始。** 冻结设计见 [P0 设计](native-lifecycle-p0-design.md)。下列源码核查仍以起始基线为准，不表示新功能已实现。

| 范围 | 当前源码事实 | 本期缺口／复用方向 |
| --- | --- | --- |
| 中心认证 | `server.create_app()` 要求非空预置 device_tokens，校验每设备 Token 与管理 Token 分离；传入 DeviceService | 尚不是在线持久登记与配对；不能把现有字典当成可恢复设备注册表 |
| 操作持久化 | `journal.py` 使用 SQLite，完整同步工作单元通过 asyncio.to_thread 隔离，取消时等待事务结果 | 可参考其事务及取消模式；新配对／升级数据不能未经设计塞入任务日志 |
| 安装 | `installation.py` 已有 manifest、实例身份、版本环境、安装锁与稳定入口相关能力 | `_prepare_release()` 要求安装器运行于持久共享路径的 CPython 3.13，尚不满足免用户准备工具链 |
| 本地升级 | `upgrader.py` 与 `upgrade_supervisor.py` 实现候选准备、发布、维护及 Linux 监督升级相关能力 | 优先复用，不新建相互矛盾的 release 切换逻辑；不等于中心确认升级 |
| 策略 | `upgrade_policy.check_release()` 校验明确版本、准确 bundle 指纹到通道的映射和 UTC 窗口 | 新增远程发起及无人值守授权必须分离，不能重新解释旧 allow_upgrade |
| 恢复 | `docs/upgrade-stage1.md` 说明 verified_local 不是 verified_center；存在 unknown 与显式对账／人工恢复边界 | 新中心状态机必须保留这些边界，不把离线判成失败或重放 |
| 原生包 | 当前源码、脚本、文档检索未找到 pkgbuild/productbuild/dpkg/rpmbuild 原生构建入口 | 需设计原生包和 release 生命周期、包数据库及升级器自身更新关系 |
| 配对 | 当前有连接失败中 pairing 的提示文字，无已发现的短期邀请签发、兑换协议 | 需新增邀请与设备凭证持久登记、单次消费及安全响应丢失恢复 |

以上是该基线源码核对结果，不是正式服务现状；没有读取真实机器 Token 或配置。

### 3.1 在途工作冲突

另一个 Worktree `/Users/kongkongye/data/project/ai-device-wt-glibc217-ses14203` 在核查时有未提交的 glibc 2.17 相关修改，涉及：

- `packages/device-runtime/src/device_runtime/dependencies.py`
- `packages/device-runtime/src/device_runtime/installation.py`
- Linux 依赖构建脚本、交付文档与对应测试。

本任务没有进入该目录写入，也没有复制、暂存或提交那些修改。P1 涉及运行环境与 Linux 基线，必须在实际开始相关改动前重新核验该工作的完成和集成情况。不能从主线当前测试通过推断这些在途修改已集成或已验收。无需等待它才能讨论配对协议，但不得并发覆盖同一安装边界后直接合并。

## 4. 已执行基线验证

执行于本任务 Worktree 的 `packages/device-runtime/`：

```bash
uv run --no-sync python -m pytest -q \
  tests/test_upgrade_policy.py tests/test_upgrader.py \
  tests/test_installation.py tests/test_instance_lifecycle.py \
  tests/test_upgrade_adoption.py tests/test_upgrade_maintenance.py \
  tests/test_websocket.py tests/test_service.py
```

结果：**347 passed in 26.09s**。测试前尚无产品代码修改。

覆盖安装、实例生命周期、本地升级策略与恢复、中心连接／服务相关现有回归。未运行全仓库测试、Bash/FS 全量、构建产物验证、原生包测试、Linux/macOS 真实服务或设备端到端；这些不能由该结果替代。

## 5. P0 初始建议与关闭记录

本节保留初始核查时的建议与问题来源，不再作为当前未决清单。用户随后要求“按你的首选方案继续，先完成P0设计”；当前决策以 [P0 冻结设计](native-lifecycle-p0-design.md) 为准，其第10节逐项关闭 D01—D10。涉及发布账号、密钥、实机入口和真实操作的资源门禁仍然有效。

### 5.1 可由现有需求确定的约束

- 只做 macOS/Linux，包型 `.pkg`、`.deb`、`.rpm`，不做 Windows/WSL 专项。
- 复用现有实例／release 核心，保持程序、设备身份与工作区分离。
- 在线配对不授予升级；远程发起不授予无人值守切换。
- 升级请求有稳定身份、持久检查点、幂等和互斥，不用远程 Bash 承担升级监督。
- 日常客户端不以 root 执行；升级需要的有限权限单列。
- unknown 任务不重放，收口不强杀，中心确认之前不标记整体完成。

### 5.2 初始问题清单（已由 P0 设计关闭）

| 问题 | 首选建议 | 未决影响 |
| --- | --- | --- |
| 首批平台 | 先以现有 Linux amd64 能力为基础，DEB/RPM 各选一个明确发行版；macOS 优先本机可验证 arm64 | 仍需确认具体发行版、最低系统及是否本期承诺 macOS x86_64、Linux arm64；未获目标信息不猜北京服务器系统 |
| Linux 服务身份 | 系统服务运行显式指定的普通用户，工作区权限与该用户一致；安装／服务管理由有限组件处理 | 固定专用账号还是指定已有用户，会影响工作区访问、目录和包脚本；不能默认选 root |
| macOS 服务与权限 | 默认登录级 LaunchAgent；无人值守系统包升级的有限 helper 单独设计 | 不能因选择登录级就承诺登录前可用；权限 helper 与签名方案是实施门禁 |
| 运行环境 | 经核实可重定位的 CPython 3.13 平台制品及准确依赖，和程序一同受控交付 | 发布来源、再分发许可、动态库及离线边界待冻结，不能复制本机 venv 冒充原生制品 |
| 原生包与 release | 原生包承载受控安装入口和发布内容，所有激活使用同一生命周期核心 | dpkg/rpm 安装事务与“只准备不切换”、活跃版本记录及升级器自身更新必须具体设计，不能只写包装脚本 |
| 配对恢复 | 本地预先保存兑换标识及恢复秘密，中心原子消费邀请，只有原兑换方可恢复 | 凭证的存储、恢复交付及撤销语义需协议评审；不能用已消费配对码公开重取凭证 |
| 中心数据 | 延续本仓库 SQLite/线程隔离风格，独立版本化设备／邀请／升级记录 | 表结构、迁移、静态 Token 过渡及事务边界待定；不套用宿主 PostgreSQL |
| 发布信任及 macOS 签名 | 准确摘要配合可信发布渠道；正式签名／公证不以绕过 Gatekeeper 替代 | 发布位置、信任根、签名账号／资源和首期交付等级需确认 |

这些初始问题现已由 P0 设计关闭，不能再据本表的历史措辞判断 P0 仍未完成。后续实现若否定已冻结方案，须明确修订设计和验收，不创建与设计不符的接口空壳。

## 6. 阶段状态与下一动作

| 阶段 | 当前状态 | 剩余门禁 |
| --- | --- | --- |
| P0 | 设计完成 | D01—D10已冻结，静态覆盖与自审通过；非产品实现或独立第三方审核 |
| P1 | 未开始 | P0 通过，协调 glibc 在途改动后实现原生包及共用核心 |
| P2 | 未开始 | P1 通过，在线配对完整实现与 Linux 安装上线 |
| P3 | 未开始 | 中心单设备远程升级、本机和中心验证及故障恢复 |
| P4 | 未开始 | macOS 原生包、自启与远程升级平台验收 |
| P5 | 未开始 | 跨平台、存量接管、用户文档及最终集成验收 |

接续动作：

1. 核对本记录、P0设计与实际分支状态，保留当前 Worktree 和依赖环境。
2. 本轮用户限定先完成P0，止于设计交付，不在本轮进入P1。
3. 后续获准推进P1时，先核对 glibc 在途工作集成状态，再固定准确CPython构建输入及许可，验证可重定位和各包系写入前钩子门禁。
4. 依P0设计先补契约／回归测试，再实现共用核心和原生包；验证失败先修正设计，不跳到P2/P3伪装进展。
5. 每阶段在本记录追加准确基线和验证索引；不在主仓库直接开发，不推送，不触碰真实设备。

## 7. P0 本轮设计交付（2026-10-26）

- 用户确认：按首选方案继续，先完成P0设计。
- 新增设计：`docs/native-lifecycle-p0-design.md`，覆盖发布信任、离线运行时、原生包事务、稳定入口、有限权限、在线身份持久化、邀请与响应丢失恢复、独立管理协议、升级API/CLI、状态机、批准、任务收口、中心确认、存量迁移和部署顺序。
- 首批计划矩阵：Ubuntu24.04 amd64/deb、Rocky9 x86_64/rpm、macOS14+ arm64/pkg。矩阵是支持目标，尚未实机验证；北京服务器未探测，不能据此声称其匹配。
- macOS正式交付要求签名、公证和附票；账号/密钥等资源须另外取得授权。无资源时仅可标隔离开发成果，不降低正式交付标准。
- 包与release三状态：包数据库版本、已准备候选、active激活版本分别展示；unknown对账不自动重放安装。
- 设计自审补充：包管理器可能移除旧包文件，独立job需事务目录运行副本；首次bootstrap无旧管理器时需包内preflight；配对回执AES-GCM加密、原兑换秘密恢复、落盘后ack。
- 验证：文档静态覆盖、API/状态/权限和失败路径自审；不声称第三方独立审核或新产品动态测试。此前347项是起始代码基线测试，本轮文档修改不把它扩称新接口测试。
- 本轮未改产品代码、未构建原生包、未启动服务、未连接真实设备。P1—P5均未完成。
