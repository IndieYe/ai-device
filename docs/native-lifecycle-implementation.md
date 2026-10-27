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
| P1 | 进行中 | 已实现离线运行时准备子模块和包门禁实验；完整包构建、helper、共用安装接入及实机验收仍缺 |
| P2 | 未开始 | P1 通过，在线配对完整实现与 Linux 安装上线 |
| P3 | 未开始 | 中心单设备远程升级、本机和中心验证及故障恢复 |
| P4 | 未开始 | macOS 原生包、自启与远程升级平台验收 |
| P5 | 未开始 | 跨平台、存量接管、用户文档及最终集成验收 |

接续动作：

1. 核对本记录、P0设计与实际分支状态，保留当前 Worktree 和依赖环境。
2. P0轮次已止于设计交付；用户随后明确要求在当前Worktree推进P1，现按下述P1记录接续。
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

## 8. P1 首个实现检查点（2026-10-26）

当前为 **P1进行中，未通过阶段验收**；本节仅记录已完成的运行时准备子模块与包门禁可行性实验。没有进入P2/P3，没有操作真实设备或服务。

### 8.1 本轮实现

- `packages/device-runtime/native/runtime-lock.json`：固定python-build-standalone 20261021、CPython3.13.17的darwin-arm64与linux-x86_64准确URL、大小、SHA256及来源。
- `native_runtime.py`：离线锁校验、平台/大小/摘要检查、受限tar解包、真实解释器版本及ssl/sqlite3/ctypes探测、目录移动后二次探测、文件锁内拒绝覆盖发布、失败清理；提供独立模块CLI，无网络、服务或active写入。
- `test_native_runtime.py`：21项测试，包含锁与目标拒绝、解包逃逸、正常内部链接、竞态保留已有目的目录、探测失败/超时/错误版本和清理。
- `probe-native-deb-gate.sh`、`probe-native-rpm-gate.sh`：显式opt-in的一次性fixture实验，不是正式Device包或helper。
- `native/README.md`：实际使用入口、许可边界、构建来源与实验局限。

没有改动 `installation.py` 和 `dependencies.py`：glibc217-ses14203仍有在途未提交修改，本轮只读核验，不复制或覆盖。下次修改共用核心前重新协调集成状态。

### 8.2 本轮实际验证

| 验证 | 结果 | 不能推断的能力 |
| --- | --- | --- |
| 新增运行时单元测试 | 21 passed | 不代替准确Linux二进制探测 |
| 新增模块及现有安装/升级/中心连接联合回归 | 368 passed in 21.26s | 不代替全仓Bash/FS、发布和实机 |
| 新增Python文件ruff check/format、两个sh脚本语法 | 通过 | 不等于包构建完成 |
| Ubuntu24.04 amd64容器dpkg实验 | preinst拒绝更新保留旧payload/包版本，dpkg audit干净，旧版本重试收敛 | 不是Device包、systemd或真实机器恢复 |
| Rocky9 amd64容器rpm实验 | pretrans拒绝保留旧payload/包版本，rpm校验通过，旧版本重试收敛 | 不是helper、签名或真实机器恢复 |
| 准确macOS运行时制品 | SHA256匹配；提取后及移动目录后CPython3.13.17、ssl/sqlite3/ctypes均通过 | 不等于完整依赖、macOS pkg或Linux运行时 |

包门禁实验创建并自动移除的本任务容器：`native-p1-deb-probe-ses14204`、`native-p1-deb-gate-ses14204`、`native-p1-rpm-tools-ses14204`、`native-p1-rpm-gate-ses14204`。未触碰其他任务运行中的容器，无端口映射或真实配置挂载；只读挂载本任务实验脚本。容器root仅用于沙箱包事务，不代表真实设备提权授权。

macOS准确制品与探测输出位于忽略目录：`build/native-inputs/cpython-darwin-arm64.tar.gz`、`build/native-runtime-probe-macos/`、`build/native-runtime-probe-result.json`，不纳入提交。该结果是原生arm64本机探测，不是Linux仿真。

Linux制品首次下载和一次有限续传均因网络吞吐低而超时；部分文件保留于 `build/native-inputs/cpython-linux-x86_64.tar.gz`。最终大小不足锁定的35,356,674字节，**未解包、未用于执行、未宣称校验通过**。后台下载已终止，无待回收任务。恢复只能续传同URL并重新核对完整大小及摘要，不能更换版本或降低校验。

许可核查发现：上游构建项目LICENSE是MPL-2.0，不能当成整个CPython分发的唯一许可。原归档含CPython/pip及vendored许可；完整运行时原生依赖的再分发清单仍需关联完整构建元数据，在正式包发布前收口。

### 8.3 尚缺的P1主路径与接续顺序

1. 完成准确Linux制品下载及大小/摘要验证，在Ubuntu/Rocky各自隔离环境执行提取、重定位和动态依赖探测，记录结果。
2. 收口完整运行时及第三方依赖许可、锁定离线Device/Bash/FS精确依赖闭包；不能因解释器可启动而宣称应用可运行。
3. 核对glibc在途修改，设计并接入共用安装核心的自带运行时/离线工具链；不依赖用户PATH、系统Python或uv。
4. 实现包构建器、内容清单、bootstrap、有限权限helper、单实例事务锁和准确systemd绑定；把本轮钩子实验转换为真实包前置检查而不是直接发布fixture。
5. 补完整包安装、更新、失败恢复、卸载与数据保持测试，最后完成P1声明平台实机验收。签名、公网发布及真实操作仍独立授权。

本检查点只形成可独立测试的运行时准备子模块，P1未完成，不能进入依赖其验收的P2。后续继续复用本Worktree，不因下载失败清空环境或重建任务。

## 9. P1 离线应用安装与DEB/RPM开发包（2026-10-26）

P1仍进行中。本轮把运行时准备接到真实离线应用安装，未完成helper、systemd和完整更新／卸载，不进入P2。

### 9.1 当前代码与构建入口

- `native_install.py`：包内解释器＋包内uv，调用现有install_instance，只允许新client实例，拒绝root、错误解释器、缺工具、在线bundle和已有目录。PATH限于包内工具并在失败后恢复，不启动服务、不配对。
- `installation.py`：新增可选runner_python；在发布active.json前校验并写绝对解释器shebang。旧调用默认行为不变。新增直接回归覆盖runner时序和写入前拒绝。
- `assemble-native-payload.py`：三包准确wheel、锁定client依赖和约束组装现有format3离线bundle；内置uv匹配tool-lock.json。开发输入记录基线source_commit及准确wheel摘要，标为dirty开发构建，不宣称准确正式源码发布。
- `build-native-linux.sh`：在amd64构建容器生成真实DEB/RPM开发包，内置CPython3.13.17、离线三包及9个第三方依赖、uv0.13.5；运行时执行前校验固定大小及SHA256。版本和布局为0.2.2-native1开发固定值，不是通用发布器。
- `accept-native-package.sh`：只挂包和验收脚本，不挂源码，容器root安装系统包后，由显式UID12345普通用户创建实例、校验依赖／导入／稳定入口，拒绝重复包安装及删除已存在实例，验证用户数据保留。
- `native/README.md`：更新开发包操作边界与剩余限制。

当前包策略为fail-closed：helper事务未实现时拒绝已有payload/实例的更新；有实例时拒绝卸载。这不是“更新／卸载已完成”。首次安装失败保留现场，不提供盲目删除目录恢复。

### 9.2 本轮实际验证

- Linux运行时归档已完整：35,356,674字节，SHA256与runtime-lock一致。首次失败下载已通过原URL有限续传完成，无后台遗留。
- Ubuntu24.04 amd64一次性容器：runtime离线提取、两次路径探测和第三次最终移动后ssl/sqlite3/ctypes加载通过。
- client约束从uv.lock导出，仅client extra无dev；三包轮子本地构建。目标下载必须包含manylinux2.17/2014/2.28兼容标签，初次只给2.28标签找不到pydantic-core，补标签后同版本下载成功，未放宽版本。
- 最终v5 DEB/RPM均在干净目标包系容器完成真实安装；无源码PYTHONPATH，无系统Python/uv依赖，所有依赖来自包内。包内uv检查12个应用包兼容，client/Bash/FS/Pillow/psutil及Executor导入通过。
- 普通用户验收明确验证UID12345；系统包安装由容器root完成，不是客户端root运行。早期root实验不用于最终权限结论。
- 相同包覆盖和存在实例时卸载均拒绝，实例状态内的user-file保留，原解释器仍可运行。
- 新增原生安装拒绝／PATH恢复／root门禁、内置uv锁拒绝、runner时序测试；相关联合回归 **378 passed in 21.42s**。
- 最终Device包完整回归 **716 passed in 42.47s**；新增和修改Python文件ruff通过，native shell语法通过，git diff空白检查通过。未运行Bash/FS各自全量与正式制品验证。

准确v5开发包（忽略目录，不提交或分发）：

| 文件 | 大小 | SHA256 |
| --- | --- | --- |
| build/native-packages-v5/ai-device-native-client_0.2.2-1_amd64.deb | 63521358 | 9d4d619b03b7aedfea36daebc26d362f3aedc3735fc5e4463fb4c60a5c1c5f0a |
| build/native-packages-v5/ai-device-native-client-0.2.2-1.x86_64.rpm | 64291883 | 3a0ae7cc06e5d2b147e2f1a6032decee3890816395d1d1c0f0ebb415a240a801 |

实际v5 payload包含最终权限改动；随后v6组装用于验证新增uv锁，组件wheel内容相同。构建脚本在v4/v5均重新验证归档摘要。RPM构建等待仓库下载后成功，均已回收结果，无遗留后台构建。容器名称以native-p1-开头、ses14204结尾，全部本任务新建且--rm；没有修改其他任务容器或真实服务。

### 9.3 glibc集成与剩余缺口

本轮发现glibc补丁已作为 `d0a7940a0e224f6f123e549434b6ff9da74c94cc` 进入独立仓库main；原Worktree只剩未跟踪logs。本任务没有自动合并主线，仍以本任务基线开发，installation仅改install_instance局部，与glibc的_check_target区段不重叠。集成前必须合并最新main并重跑依赖平台和安装测试，不能宣称本任务已包含glibc补丁。

剩余优先序：

1. 独立bootstrap/helper、准确实例／运行用户/systemd绑定、事务锁和显式策略；不能以shell任意argv代替有限权限接口。
2. 现有包fail-closed更新改为批准后的准备／收口／apply／activate事务，补检查点故障恢复；卸载区分程序、身份撤销与数据保留。
3. 下载源认证、签名release清单、离线完整许可/SBOM、通用版本与平台参数、稳定的构建锁/来源审计。本地开发包中许可证声明包含review-pending，不能对外发布。
4. 合并glibc当前主线、补第三方/角色缺失依赖测试，按P1清单做完整构建验证；本轮只是部分应用闭包/包事务验收。
5. Linux systemd与登录/机器恢复、用户工作区、安装失败接续等实机验证。容器包安装不替代这些。

当前P1不得标为通过，也不因已有可安装包进入P2。后续继续当前Worktree，先核查本节的准确包及源码状态。

## 10. P1 helper与systemd绑定实现（2026-10-26）

用户要求优先helper和systemd。本轮新增 `native_helper.py`、30项测试、真实注册验证脚本及 `native/HELPER.md`。P1继续进行中，不进入P2。

实现：root管理员显式注册已安装的普通用户单client实例，生成systemd unit及no-replace持久绑定；摘要校验runner/身份/配置/unit和已加载systemd的准确User、Group、FragmentPath、ExecStart。AF_UNIX请求通过SO_PEERCRED获取可信UID，只接受query/start/reconcile和两个稳定ID，不提供任意命令、服务名或路径。start先记录intent，超时/丢结果保存unknown不重放，新ID不能绕过；query/reconcile只读。实例锁、安装共享锁、旧监督锁和非终态升级检查避免启动绕过维护。

客户端unit生成使用明确普通UID/GID、绝对稳定入口、Restart=on-failure、NoNewPrivileges、UMask0077和SendSIGKILL=no。helper unit生成使用独立bootstrap，不自动启用、reload、启动或清除已有socket。当前未实现package-apply/activate/uninstall及socket遗留恢复，明确拒绝而不伪装完成。

实际验证：

- helper测试30项通过；Device完整 **746 passed in 45.02s**；ruff及构建脚本语法检查通过。
- 客户端unit在新建一次性容器中通过真实systemd-analyze verify。
- Linux容器管理员注册普通UID12345 fixture、双unit verify、重复注册拒绝通过，started=false、enabled=false；不读取真实凭证，不启停任何真实服务。
- 真实注册发现上游CPython解释器为775，root信任校验拒绝；包构建器增加程序树chmod -R go-w。采用root ownership＋规范权限后注册通过，未放松检查。修改后未重建v5包，不以旧包为新权限验证证据。
- 初次macOS生成Linux路径时/etc别名不满足canonical校验，随后用无别名Linux路径完成验证；不降低canonical约束。

隔离验证脚本 `packages/device-runtime/scripts/accept-native-helper.py`。所有本轮验证容器均本任务新建--rm，无后台遗留，无正式服务操作。完整契约、模块CLI与未完成边界见 `packages/device-runtime/native/HELPER.md`。

下一步仍在P1：先重建包含helper与规范权限的新包，完成独立bootstrap、unit安装及显式enable/启动编排，再在隔离systemd环境验证真实普通用户进程和健康；补socket生命周期与未知操作恢复、旧工具互斥，最后完成更新卸载事务及实机验收。不得用当前生成unit替代服务已启动或设备已上线。

## 11. P1独立helper包交付与真实隔离systemd（2026-10-26）

继续同一P1授权，新增独立root bootstrap和显式deploy/recover-socket CLI；包安装不隐式部署、自启或启动。bootstrap只包含标准库helper，不依赖客户端site-packages，客户端目录暂不可用时仍可部署。已有登记/unit时包卸载拒绝；失败可能留下目录或unit，需要明确对账，不宣称完整事务。

动态验收发现call响应ok=false仍以0退出，已修为非零并补回归。serve与管理员socket恢复共用daemon锁；活动daemon/监听端点/非socket/未知结果拒绝删除，只对确认ECONNREFUSED、root-owned且inode未变的遗留socket显式移除。不改变unknown操作记录，不隐式重试start。

验证：

- helper **39 passed**，Device完整 **755 passed in 40.44s**；ruff、shell语法、git diff --check通过。
- v9 DEB安装，无源码挂载：独立bootstrap、普通用户实例、真实Unix socket peer拒绝、无systemd时查询拒绝、活动daemon恢复拒绝、退出后显式socket恢复均通过。
- 在新建Apple container amd64/Rosetta实例中实际启动systemd255/cgroupv2；初次缺少capability时systemd退出，使用cap-add ALL及container=apple后成功。不可把此实验当目标实机或最低glibc验收。
- v8与最终v9均实际部署/启动helper，普通用户client active/running；/proc UID均12345，重复同ID启动PID不变，另一UID即使加入socket组仍被拒绝；双unit enable链接通过。测试中心为127.0.0.1:9，不连接真实中心，不代表业务健康、配对或中心确认。
- 固化入口 `accept-packaged-helper.py`、`accept-native-systemd.sh`。普通poweroff依赖login1超时，最终仅回收本轮隔离容器；当前native-p1运行实例查询为空，无真实服务操作。
- v8 RPM完成无源码离线安装、依赖检查、普通UID导入、独立helper导入及拒绝覆盖/卸载后数据保留；它不包含后补socket恢复，不能作为最终v9 RPM证据。

制品（未签名开发品，源码基线19867be＋本轮dirty wheel，准确字节由build-inputs记录）：

- v9 DEB `build/native-packages-v9/ai-device-native-client_0.2.2-1_amd64.deb`，90,491,644 bytes，SHA256 `ef647aeb45500d81a80b68f68a5f29e238e387cf77bd4264c43171c83a5184d0`；bundle `a26f79357759dd10b4d4c408700d354ed711f56ba56c5ccc682a79b72bc48e7c`。
- v8 RPM `build/native-packages-v8/ai-device-native-client-0.2.2-1.x86_64.rpm`，91,756,264 bytes，SHA256 `04916138d2e3c57ec21366096c7ea52b1fd80c991ba9c70b32453ea8a5cf2082`。

交付联动：实现/CLI/包脚本/测试/原生文档已更新；中心API、前端、数据库及宿主工作目录不适用，本轮未改变其契约或数据。二进制不提交，不覆盖全局用户配置。

最终v9 RPM已重建并通过无源码离线安装、依赖/普通UID导入、独立helper和recover-socket命令、拒绝覆盖/卸载及数据保留检查。文件 `build/native-packages-v9/ai-device-native-client-0.2.2-1.x86_64.rpm`，91,744,776 bytes，SHA256 `86f64b7094580195346845bdfff7d2a3b3f03325982fa45e33ff813100dc46a5`。未在Rocky重复真实systemd启动，相关证据来自v9 DEB隔离systemd。

P1仍未通过：未知启动结果的明确收敛、旧工具完整互斥、批准更新/卸载事务与故障恢复、glibc主线对齐、完整许可/SBOM/签名、目标实机及宿主重启恢复均仍缺；P2—P5未开始。后续继续P1，不把本地检查点作为整个任务完成。

## 12. P1 unknown启动已运行分支收敛（2026-10-26）

按用户先补unknown收敛与恢复要求，增加有限action resolve-running。匹配原start意图与unit摘要，在当前绑定、维护共享锁、active/running、有效PID及无pending Job证据下，no-replace/fsync发布独立recovery confirmed_running；原unknown或缺失result保持不变。query/reconcile返回原结果及恢复证据，仍只读；同ID不重放，原unknown只有在恢复证据已持久保存后才不再阻塞新ID。不会因响应丢失假设启动未发生，也不会把确认运行说成原操作成功。

验证：helper51项通过，完整Device **767 passed in 42.20s**；ruff通过。新增持久化失败继续阻塞、维护锁、权限/绑定漂移、缺失result恢复、无intent/已完成拒绝、非running/PID/Job证据不足等回归。首次测试误用了op-1而fixture默认one，已修测试，不涉及产品错误。

构建v10 DEB并在全新一次性systemd实例复验基础服务链，再以真实systemctl start成功后抛TimeoutExpired模拟丢完成响应：unknown保留，新ID事前拒绝，重建Helper后resolve-running成功，原文件字节不变，后续新IDalready_active，PID217未变，实际start调用恰1次。故障注入入口 `packages/device-runtime/scripts/accept-native-unknown.py`。仅临时隔离实例，不操作真实服务；无中心连接，无配对或业务健康证明。v10 bundle SHA `1ba3b4197faf8a40cf4ade59f81cbb3e61ce30b54c68925761d3c0c35616b3d0`；v9 RPM不含新增动作，本轮未重建RPM。

实现/CLI/观察字段Job/测试/原生契约已联动；无前端、中心API、数据库或用户配置变更。仍在P1。未运行/失败的unknown仍阻塞，后续需设计旧job终结和显式重试的权限/竞态闭环，不得仅凭inactive放行。更新卸载、glibc对齐、许可签名和实机恢复门禁保持未完成。

## 13. P1未运行unknown安全恢复与显式重试（2026-10-26）

新增resolve-stopped：准确原start intent、当前登记绑定和维护锁下，先要求inactive/failed且PID0，再取消该unit观察到的准确Job，复核dead/failed、PID0、无Job后发布独立confirmed_stopped恢复记录。原unknown不改、不自动start；后续新ID经过所有启动门禁显式重试，原ID不重放。取消失败/过渡态/运行中/记录写失败仍阻塞。

新intent含execution_barrier=1，发布前持执行锁，独立标准库guard继承锁直到systemctl退出回收，内部120秒超时。helper丢失时guard仍持锁，阻止旧发送者迟到与恢复竞态。resolve-running对新意图也要求发送屏障。旧意图缺少屏障或执行锁文件缺失均拒绝未运行自动收敛，需要明确管理员核实；不凭inactive放行。

验证：helper65项，Device完整 **781 passed in 40.89s**，ruff通过。真实子进程测试父描述符关闭后仍被guard持锁；新增Job取消竞态/超时、写入失败、缺锁、旧意图拒绝与显式新ID重试。v11 DEB在新建隔离systemd中通过原已运行恢复，再以测试drop-in ExecStartPre=false/Restart=no制造失败：unknown及提前重试阻塞，重建Helper确认stopped，原文件保留，移除测试故障后新ID启动且重复ID PID268不变。隔离环境已回收，无真实服务操作。

v11 DEB `build/native-packages-v11/ai-device-native-client_0.2.2-1_amd64.deb`，90,555,702字节，SHA256 `04ad241cf5f99a73e6af245006dbc0d0b05dedce9625964a2e65adab1128e1f1`；bundle `19370ee14fc6d8a98ebc97ac82e4babf24f7439a1a89d9252b091c2e4930f093`。源码基线1e99681加本轮dirty wheel；未签名，未重建RPM。实现/CLI/测试/文档联动，无数据库/中心/前端变化。

重启前保存边界：本轮到此收敛，不启动后续工作。P1未完成；接续先核实工作区/HEAD，再复核guard进程异常退出和真实pending Job取消的故障覆盖、对齐最终RPM；旧工具完整互斥、受控更新卸载事务、glibc主线对齐、许可签名及实机恢复仍缺，不进入P2。

## 14. 重启后复核：guard回收持久证据（2026-10-26）

服务重启后确认原Worktree HEAD f9041f0、工作区干净、无native-p1运行测试实例；未重复已完成操作。发现guard异常退出会释放锁但不能证明其systemctl子进程退出，补execution_barrier=2：guard回收直接子进程后原子发布匹配intent的sender_reaped回执，fsync文件和目录。两恢复分支均要求执行锁及完整回执；缺失/不匹配继续阻塞，版本1旧意图拒绝未运行自动恢复。

验证：helper69项，完整Device **785 passed in 41.59s**，ruff/diff检查通过；v12准确DEB真实隔离systemd两恢复分支、失败后新ID重试与稳定PID通过。测试实例已回收。缺回执模拟guard异常终态，未执行实际guard强杀实验；真实pending Job取消仅有适配器竞态测试，不能标实机通过。RPM未对齐恢复动作。

v12 DEB `build/native-packages-v12/ai-device-native-client_0.2.2-1_amd64.deb` 90,500,774字节，SHA256 `928f9a4debe0078423b5483d02509efdb3a99c7fad41b9bcd7b2d92c8f8682e1`。基线f9041f0＋本轮dirty wheel，未签名。本轮只修改helper、测试/故障脚本及文档，无前端/中心/数据库影响。

仍在P1：后续真实排队Job取消与guard异常退出故障覆盖、最终RPM对齐；旧工具完整互斥、受控更新卸载、glibc主线、许可签名及实机恢复门禁未完成，不进入P2。

## 15. P1真实故障验证及RPM对齐（2026-10-26）

本轮产品源码保持3ca7941，新增 `packages/device-runtime/scripts/accept-native-recovery-faults.py`，复用v12 DEB和同一v12 payload构建RPM。未进入更新卸载或P2。

真实隔离systemd故障：通过测试专用oneshot sleep依赖让客户端启动Job725排队，sender以--no-block返回并注入丢完成；实际查询failed/PID0/Job725，resolve-stopped取消准确Job后确认无Job，移除测试依赖并以新ID启动成功。此测试替换发送适配器以制造已退出发送者，Job查询/取消/启动均为真实systemd，不宣称生产guard120秒超时路径已实测。

guard故障：准确Popen句柄启动产品guard，fixture发送进程有界等待；仅对该测试guard发SIGKILL，子进程自行退出，匹配回执缺失，重建Helper后resolve-stopped及新ID启动均被拒绝。没有终止真实服务。初版以父PID推断发信号在Rosetta下误终止验收调用者，已改准确句柄；后续测试适配器查询误指向fixture、Popen不支持capture_output参数均已修测试，不放宽产品校验。每次失败实验使用新容器重跑，最终全部通过，临时实例已回收。

验证：完整Device **785 passed in 47.48s**，ruff/diff检查通过；v12 RPM普通用户离线安装、12包依赖检查、独立bootstrap、resolve-running/resolve-stopped动作、拒绝覆盖卸载和数据保留通过。RPM与已验DEB的helper SHA256均 `69a6d47d1f97035c0c8c6c6da3970337d88a40a0c347a6fc77d2ca8a9202942d`，manifest均与v12 payload逐字节一致，SHA256 `0754b8ab1f161fa64528d65a49fb924f02b5fc803ab9ad67be1b1f0b49477c2b`。manifest核对先误用bundle.json、再遇Rocky缺cmp，最终用准确manifest和包内Python完成，不把失败查询算通过。

最终RPM `build/native-packages-v12/ai-device-native-client-0.2.2-1.x86_64.rpm`，91,750,067字节，SHA256 `1d02abd52d0339401804dea7c4f81abd81b4091200bdb5e1f8055905884d57fd`；DEB摘要见第14节未变化。二进制未提交，未签名，不是发布品。RPM未在Rocky复跑真实systemd启动，故障证据来自Apple container amd64/Rosetta的DEB实例。

本轮交付仅故障脚本和说明，产品/API/CLI/数据库/前端无新增变化，不修改全局运行配置。P1仍未完成：旧工具完整互斥、批准更新卸载与失败恢复、glibc主线对齐、完整许可/SBOM/签名、目标实机和宿主重启恢复。下轮先对照P1验收逐项梳理剩余生命周期工作，不把故障门禁或RPM对齐当整个P1完成。

## 16. P1旧工具协作互斥与管理绑定（2026-10-27）

定位缺口：旧安装/监督锁不表达实例已交native管理，旧prepare可先创建升级目录。新增management_lock.py，按规范实例路径管理锁，旧写共享/native登记独占；登记另取安装独占锁，拒绝运行中客户端/维护。登记前发布不可覆盖.native-management.json，unit/record后发，record保存marker摘要；中断后保留封锁，不自动接管。旧登记无摘要拒绝，迁移另做。

覆盖旧安装/rebuild及生命周期修改/回退/备份的安装写锁；旧upgrade prepare/activate/reconcile、监督supervise/recover/resume-admission/reconcile持完整操作管理共享锁并先拒绝受管实例，避免创建upgrades或先执行服务动作。读status/inspect保持可用，恢复到新目录不继承管理身份。协作门禁不防旧二进制/手工改文件，不是OS沙箱；不是更新卸载功能替代品。

验证：管理锁13项、helper70项，完整Device **799 passed in 41.58s**；ruff、shell语法、diff检查通过。包含登记与旧维护互斥、跨进程与路径别名、symlink锁拒绝、marker漂移、登记record发布失败保留marker、旧入口无创建升级目录副作用。初次登记中断测试预期marker拒绝但实际更早命中独占管理锁，改测试为准确active原因；未放松产品。

v13 DEB真实一次性systemd环境：管理员登记UID12345，普通用户旧prepare/监督入口拒绝且upgrades未创建，helper启动及已运行/未运行unknown、Job取消、guard异常阻塞全链通过。独立bootstrap包含management_lock。实例已回收，无真实服务操作。制品 `build/native-packages-v13/ai-device-native-client_0.2.2-1_amd64.deb`，90,491,778字节，SHA256 `6c60ec3f0fe1f9026729b4ae10d3880998bc50d3fe8f95cf65691a2e962cca0d`；bundle `2783fa24eac0e436020a65af84f2fb00cb262d9207f8a199de4cebc9485ff1ca`。基线a1a421f加dirty wheel。v13 RPM未建，v12 RPM无本轮门禁。

实现/共享标准库模块/旧工具入口/包bootstrap/测试/契约已联动；前端/中心API/数据库/用户配置无变化。仍在P1，后续对齐RPM，处理受控更新卸载及失败恢复、glibc主线、许可签名和实机重启恢复门禁，P2未开始。

## 17. P1登记中断恢复及RPM再对齐（2026-10-27）

新增管理员recover-registration，与register同参数，管理独占/安装独占锁内重新验证全部实例前置条件。新schema2 marker预存完整binding，恢复仅补一致缺失unit/record，不覆盖冲突；unit改临时文件fsync+hardlink无覆盖发布。旧schema1标记或无marker旧登记缺少固定基准，明确拒绝自动迁移，不伪称旧登记接管完成。

回归：管理锁/登记恢复23项、helper70项，完整Device **809 passed in 42.29s**；ruff/diff通过。覆盖marker-only、unit后缺record、重复恢复、unit/record冲突、身份配置漂移、旧标记拒绝及root marker权限。v14实测发现marker位于普通用户实例目录，不应使用root全祖先校验；修正为marker本体root-owned常规文件不可组/其他写，保留普通owner实例及完整基准校验，v15重建复验通过。

v15 DEB实际隔离systemd：新实例marker发布后注入中断，函数恢复、公开CLI重复恢复、冲突unit不覆盖通过；普通用户运行、旧工具拒绝、unknown两分支/真实Job取消/guard崩溃拒绝链路通过。全部测试实例已回收，无真实服务操作。脚本 `packages/device-runtime/scripts/accept-native-registration.py`。

v15 DEB/RPM准确同源：helper SHA256 `ab716baaa822496a25f737c2291d1d993b2511b32c8b9f8dc9be32730ed78254`，manifest与同一payload逐字节一致，SHA256 `6e8e36bd48f60fbbd1b3335c2a51651e95bf5d24504c01f2d36020e70e73ea19`。RPM Rocky9无源码离线安装、12包依赖、普通UID、独立management模块、recover-registration帮助及拒绝覆盖卸载保数据通过；未在Rocky复跑实际systemd。

- DEB `build/native-packages-v15/ai-device-native-client_0.2.2-1_amd64.deb` 90,508,578字节，SHA256 `da0fadb10d904e48a1d80669cae9eb6d61c8751668e62b9585d093cf9a73e7b0`。
- RPM `build/native-packages-v15/ai-device-native-client-0.2.2-1.x86_64.rpm` 91,748,117字节，SHA256 `d5595450b10fa77658fa100e69eaf6bb6cc71992c4eda647e386b16487f752ac`。

基线f0ce9fa加本轮dirty wheel，未签名不发布。实现/CLI/模块/测试/包/契约联动；无前端、中心、数据库、用户配置变化。P1仍缺受控更新卸载及恢复、旧登记审核迁移、glibc对齐、许可签名和实机重启恢复，不进入P2。

## 18. P1受控更新事务原型（未完成、未提交，2026-10-27）

按用户继续受控更新/失败恢复要求，核对P0 D03后确认现有固定0.2.2-native1包不能直接解除覆盖保护：会替换bootstrap且缺包钩子授权/收口/激活闭环。本轮新增未提交 `native_update.py`、`test_native_update.py` 原型，不接已安装helper，不改构建器，不放开现有包门禁。

原型实现管理员prepare准确DEB摘要/版本/固定包名、源active与binding和包数据库快照；approve绑定准备文件SHA；apply要求服务已停止且无Job、先写意图，独立guard保留全局包执行锁并回收后持久回执；同ID只观察，已有apply意图阻止新ID。reconcile只读，recover依据发送回执和包数据库记录package_applied_pending_activation或source_package_observed_recovery_required；部分包状态拒绝，不自动重装、不自动激活。仅适配Debian，且是尚未交付的开发原型。

验证：原型12项通过，Device完整 **821 passed in 41.48s**，ruff/diff检查通过。测试用fake包适配器，未做真实DEB事务、未构建新包；此前v15制品不包含本轮模块。没有新测试容器或后台任务。

关键未完成/接续先处理：1)持久更新占用与helper启动互锁（当前仅调用期间实例锁，不够）；2)签名发布、无脚本候选提取/普通用户准备；3)任务收口/准确停止和批准策略重核；4)包钩子授权与版本化布局、不替换执行bootstrap；5)发送回执/真实包失败与部分安装验证；6)候选激活、恢复/重试和解除占用的完整终态。当前不要调用原型apply处理真实包，不把测试通过称完整受控更新。保留原Worktree未提交现场，HEAD仍98f470b；P1未完成，不进入P2。

## 19. P1更新持久占用及helper启动互锁（2026-10-27）

接续第18节未提交原型，补registry级共享/独占包执行锁：helper启动、登记/登记恢复取共享锁，包应用/恢复取独占锁；生产启动guard通过pass_fds继承共享锁。独立子进程实验证明调用方fd关闭后仍阻止独占更新，回收后才可获得。共享锁允许多个普通启动，不用持久运行期间锁冒充完整收口。

持久 `updates/*.apply-intent.json` 存在即阻止新启动和登记；旧start ID只返回原结果，不重发；query/reconcile仍可读。包恢复仅观察，不解除封锁；unknown/pending activation/source observed均保持占用。updates目录在写意图前先fsync registry。包应用拒绝未确认旧启动账本，并在全局独占锁内检查仅一份登记；登记入口持同一锁消除检查后的登记竞态。多实例共享包更新尚无统一收口，明确拒绝。调整故障脚本适配器以转发package_fd；未重跑容器systemd验收。

验证：定向helper/更新/管理 **120 passed**，完整Device **836 passed in 41.58s**，ruff与diff空白检查通过。新增15项测试，含持久意图/损坏意图/悬空链接门禁、helper重建、旧ID不重放、独占锁阻止启动、跨进程争用与继承、guard fd转发、各恢复状态仍封锁、登记与登记恢复互锁、多实例及未确认启动拒绝。初次测试暴露root JSON包含启动账本，已修正实例分类；测试夹具权限和登记恢复模式已校正，不是产品验收成功证据。

交付联动：实现/适配器及回归已更新，HELPER文档已更新；无API/IPC公开契约、前端、数据库迁移、配置或工作目录资产变化。构建器/包钩子刻意未放开，无新制品，无真实包事务/设备/服务操作。第18节“未提交”为当时现场，本节与其原型一起保存本地检查点。P1仍未完成，不进入P2。

下一缺口：签名候选与无脚本提取/普通用户准备、任务封闭收口与准确停止、批准策略重核、包钩子许可和版本化payload/不覆盖bootstrap、真实失败包状态验收、激活及明确终态/显式重试/解除占用、RPM。此前旧登记审核迁移、glibc联合验证、完整许可SBOM签名与目标实机门禁仍缺。不要用删除意图绕过封锁，不将此提交当作完整更新交付。

### 19.1 台账身份与恢复基准复核

本地检查点5c5de92后继续自审，发现ID允许连字符，原`instance--operation`拼接可能别名。改用实例ID长度前缀的`length-instance--operation`，不同ID对不共用台账；未发布原型不迁移旧开发台账，遗留意图仍由glob门禁阻止启动。recover还需核对apply意图精确绑定当前已批准准备文件摘要，不能只比较sender和intent彼此一致。新增2项回归；更新测试17项、完整Device **838 passed in 42.29s**，ruff通过。仍无真实包事务或新制品，完整受控更新未交付。

## 20. P1签名候选认证与安全提取（子阶段，2026-10-27）

新增native_candidate.py与CLI，Ed25519外部信任根验证canonical JSON payload；准确release/源版本/target/bootstrap、三包版本、协议/状态兼容及空间门禁，拒绝重复JSON与无效签名。native可选依赖声明cryptography/zstandard并更新uv.lock；不增加默认client依赖，不自写密码算法、不引入正式密钥。现有原生包未重建，未携带新模块或native验签依赖。

先固定签名大小及SHA的包副本，标准ar解析DEB，核对控制元数据而不执行维护脚本；gzip/xz/zstd有界数据提取，拒绝路径逃逸、重名、特权/可写普通文件、特殊文件、逃逸/悬空/循环链接和经符号链接的硬链接目标。验证签名data成员与payload清单摘要、实际离线bundle及组件版本；完成回执最后持久发布，失败清理本次目录，不覆盖已有目标。进程中断残留无candidate.json视为未完成，不能覆盖重试或自动清理已有成果。

实验：macOS提取v15因Linux terminfo大小写文件碰撞安全拒绝；隔离Linux首次发现符号链接0777按普通权限误拒，已修正仅普通文件/目录检查写权限，链接继续严格解析。Zstd窗口预算单位修正，并补有界展开回归。最后使用仓库accept-native-candidate.py在一次性Linux容器、普通UID/GID12345、只读仓库挂载验证v15真实DEB：10443成员，285493398展开字节，package SHA da0fadb10d904e48a1d80669cae9eb6d61c8751668e62b9585d093cf9a73e7b0，signed payload SHA da9f6b3141674f5f47089cb6614d9cae388fd9b94190fed24632ccdd1d8900b4。内存临时测试密钥，不落私钥、不替换信任根、不构成正式签名发布。未调用包管理器、包内程序、服务或active切换。容器--rm已随命令退出回收，无后台遗留；临时容器验证依赖联网安装，不算离线交付验证。

测试：候选40项（真实Ed25519、篡改、未知key/上下文/兼容、JSON重复、包/清单/data摘要、控制元数据、截断、归档越界/特殊文件/重复/链接/权限、预算及完成失败清理）；新增第40项前完整Device877项通过，最终完整结果见接续记录。ruff/diff检查通过。

交付联动：源码、可选依赖/lock、测试、开发验收脚本、HELPER文档已更新；无数据库/API/IPC/前端/用户配置变化。正式信任根、中心release发布完整Schema、原生包native依赖与构建交付仍缺，不扩大支持矩阵。**只完成认证提取子阶段，不是完整候选准备**：结果authenticated_extracted，venv_prepared=false、activated=false。最终目录共用核心venv准备与恢复、候选证据绑定native_update批准仍缺；现有update.prepare仍只是管理员摘要台账，不视为签名候选。包钩子授权、任务收口、稳定bootstrap/版本化布局及激活终态仍缺，包拒绝覆盖保护未解除，不进入P2。

第20节最终验证：候选40项通过，完整Device **878 passed in 44.92s**；uv lock --check、ruff、diff检查通过。最终复现脚本在隔离Linux普通UID12345对同一v15包成功，容器自动回收。共用installation._prepare已核实在最终release目录创建venv并绑定持久解释器，不允许将提取staging直接当已准备运行候选；后续应新增明确原生准备入口及证据消费，不通过旧upgrader绕过受管门禁。

## 21. 任务执行登记及最终目录准备工具接入

用户要求后创建整体任务状态blk-42959，实际Enter成功，关联auto_enters任务模板blk-13687；当前会话状态blk-42545经set-execution-task设置references.role=execution，set-automation开启stop_reason_check，已核验metadata=true。完整任务P0—P5记录于任务Resource bres-3249；授权仍仅隔离P1，不扩大真实服务等边界。

复用Worktree de51eeb基线开始下一准备子阶段。installation._prepare新增显式uv_executable可选参数，必须绝对可执行文件，避免原生入口靠可变PATH选工具；原调用默认不变。解释器仍用当前持久CPython3.13真实路径，不新增任意解释器入口。3项回归验证指定uv、无PATH查询、最终release目录venv、不改active及无效工具提前拒绝。安装64项、完整Device881 passed in 41.28s，ruff通过。未接原生准备入口、未执行真实venv/包/服务实验，不能称完整候选准备交付；下一项是验签候选证据消费与最终目录准备台账。无数据库/前端/公开API或配置变化。
