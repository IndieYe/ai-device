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

## 22. 普通用户最终目录运行候选准备（P1开发子阶段）

新增native_prepare.py入口与专用原生管理租约；非root Linux登记所有者核对schema2 marker完整绑定及源active，持管理EX/安装SH，重新验签提取而不信可编辑完成JSON；当前持久解释器与显式uv需root可信路径，不执行候选内工具。先意图，再共用core._prepare最终目录venv/健康检查，fsync并核对源active/marker后完成。相同ID验证证据后仅观察；中断无完成及失败均禁止换新ID自动重放，保留现场。未实现准备失败管理员收口及候选批准消费，不能称更新已交付。

真实隔离Linux首次发现marker默认0600导致普通用户无法读绑定；修复新登记marker在原子发布前设0644（不含凭证），其他账本默认0600。既有marker不自动改权限，不把权限拒绝当旧登记迁移完成。重新用UID12345在一次性无systemd容器验证：v15包仅dpkg-deb -x无脚本解包，普通用户新建离线实例、root登记未start/enable，内存临时测试签名认证，普通用户最终目录准备成功runtime_prepared，同ID不重建，active字节未变。实际release 0.2.2-a1a9fef8fc39406a88b8d0ee78112c71。容器--rm已回收。验签依赖临时联网安装，不是新模块离线制包验证；运行venv来自v15离线bundle。accept-native-prepare.py保存复现步骤。

测试：原生准备12项、管理锁24项、helper83项（新增marker权限）及完整Device **895 passed in 42.08s**；ruff/diff通过。覆盖同ID不重放、失败/中断阻止同/新ID、源变化、release证据/解释器丢失、共享运行锁允许准备/独占维护拒绝、完整binding漂移、root拒绝、专用管理租约保持旧门禁。尚无真实guard崩溃/后代回收及管理员恢复链验收，完成回执不代表持久OS事务。

实现/测试/开发验收脚本/HELPER文档已更新；无数据库、前端或公开IPC变化。新模块未接构建器、包钩子和更新批准，v15不含新模块，不操作真实服务设备。下一项消费runtime候选证据绑定更新prepare/approve，先明确root重新认证及普通用户结果可信性边界，不因JSON自洽就认可可激活；同时补准备中断恢复。P1未完成，不进入P2。

## 23. 管理员重新认证运行候选并绑定批准（P1原型）

native_update新增prepare-signed及CLI，外部公钥重新认证准确发布与root可信包/envelope，在registry/updates私有临时目录重提取并验证真实离线payload；核对登记所有者直属runtime回执、源active/marker、发布摘要、最终release安装证据、签名manifest与实际bundle及解释器摘要。准确证据纳入prepared，approve绑定总SHA，approve/apply前重新检查实际bundle字节及源/回执/安装/解释器，漂移拒绝且不发送安装。管理员只读不执行用户venv。认证到prepare分开租约，但保留旧源摘要重查，不覆盖源基准。

14项新增更新测试，更新31项，完整Device **909 passed in 42.58s**，ruff/diff通过。覆盖认证失败不落prepared、同ID证据稳定、错误源/发布/state/activated拒绝、六类证据批准前变化、实际wheel验证失败、批准后变化阻止apply且不写意图。真实隔离Linux先普通UID12345准备最终venv，再root复制测试envelope/公钥/包到可信目录，prepare-signed与approve成功，assert无apply意图；未包安装/激活/服务启动。临时测试公钥非正式信任根；新依赖临时联网非离线新包交付。容器--rm已回收。新增accept-native-signed-approval.py复现管理员半段；实际运行使用等价内联调用，脚本静态通过。

旧prepare开发API保留摘要路径，不宣称验签，不作为正式包钩子许可。普通用户venv可变，当前证据不是整个site-packages的运行行为认证或不可变保证；激活/健康再次核验仍缺，不能因批准成功解除包保护。实现/测试/HELPER文档及验收脚本更新；无数据库/前端/IPC变更，构建器未接。下一项准备失败/中断管理员收口，后续包钩子、任务收口/准确停止、稳定bootstrap版本化布局和激活恢复终态仍未完成；P1未通过，不进入P2。

## 24. 重启前保存：准备发送者屏障原型（未提交，未完成）

接续ebfd8f4，新增native_prepare_sender.py及test_native_prepare_sender.py，installation以ContextVar作用域发送者接入，原生prepare在调用共用核心时启用独立guard。每条命令先写匹配request/sequence/argv/cwd意图，guard继承执行锁，等待直接子进程并持久direct_child_reaped回执；明确descendants_confirmed=false，不把直接子进程回收等同任意后代全部退出。原生失败/中断仍阻塞，不提供管理员解除或重试。

重启通知到达后停止扩展，定向发送者/准备/安装82项通过，ruff已修正并通过，diff检查通过。未跑新完整回归，未跑本轮真实Linux准备链、guard异常退出/后代故障或管理员恢复验收。未提交：installation.py、native_prepare.py修改，新增native_prepare_sender.py/test_native_prepare_sender.py及本节；HEAD仍ebfd8f4。无新容器或后台任务遗留。恢复后先复核现场与此屏障覆盖，优先补guard失败回执/后代生命周期证据及管理员观察收口；不可删除失败台账解锁，不把原型称已完成中断恢复。P1继续，P2未开始。

## 25. 准备发送者证据与只读观察收敛（P1仍未完成）

重启后确认服务running、会话同一execution和任务停止检查有效，未提交现场一致，先完整915项通过；继续第24节未提交屏障，补管理员只读observe_senders/CLI。限schema2登记root的管理员入口，按匹配准备request及连续序号校验意图和回执，忙返回sender_busy，空/缺回执unknown，匹配直接子进程回收返回direct_children_reaped_descendants_unconfirmed，所有结果retry_allowed=false。没有写管理员恢复终态或解锁新ID；不能将观察功能称完整准备收口。

11项真实进程/上下文测试通过，完整Device **920 passed in 42.99s**，ruff/diff通过。覆盖正常/失败/超时直接子进程回执、限域发送者复原、生产_invoke使用guard、调用方fd关闭后guard持锁；异常guard自然os._exit后有限寿命子进程仍运行但锁可释放，无回执仍unknown；直接子进程退出且后代尚在活动，即使匹配回执也只报后代未确认。测试子进程按门闩或有限期限自行结束，无kill真实服务或遗留后台。未做本轮Linux完整venv/systemd/cgroup故障链，不把Mac本机隔离进程测试称Linux后代收口验收。

本节与第24节原型一起保存检查点；发送者/安装限域接入、测试及HELPER文档更新，无数据库/前端/配置变化。下一缺口：可靠的准备后代生命周期归属（如独立受控job）及持久完成证据，再实现管理员显式收口/重试。缺此证据继续阻塞，不使用PID快照、锁释放或直接回执伪装全回收，不删除台账绕过。随后仍需包钩子许可、任务收口/准确停止、稳定bootstrap版本化布局与激活恢复终态。P1未完成，不进入P2。

## 26. Linux准备进程树回收与显式退休

新增linux-subreaper-v1强屏障：guard启动命令前prctl CHILD_SUBREAPER，等待直接子进程及收养后代至ECHILD才发布process_tree_reaped/descendants_confirmed=true；持久意图声明屏障类型，旧direct-child回执不升级。无限存活后代将持续阻塞；不主动kill，不以PID/锁释放推断全退出。observe仍retry_allowed=false。

新增native_prepare retire --root --operation-id（Linux root显式）：管理EX/安装SH、准确schema2绑定与源active/marker、失败或缺完成、所有发送意图连续且强回执完整且发送锁空闲，才原子发布root0644退休记录。保留全部旧成果，旧ID永不重放，仅后续显式新ID可绕过已退休失败记录。无发送意图、旧屏障、guard丢回执、源变更仍阻塞；无通用崩溃修复或自动删除。当前退休证据面向可信本地协议，不是恶意owner安全隔离。

隔离Linux UID12345发送者14项通过，含脱离会话后代及后代活动时不发布强回执；真实v15临时测试签名→最终venv→管理员批准仍通过。再注入实际命令exit7，强回执落盘→root退休→普通用户显式新ID准备成功、active不变。首次附加pytest因临时文件权限未收集，修正chmod后重跑通过。后台任务88589已完成，--rm容器退出，无遗留。依赖临时联网，不算离线新包或正式发布验收。宿主完整922 passed, 3 skipped in 45.74s（三项Linux专用已在Linux通过）；ruff/diff通过。

此子阶段不覆盖guard自身崩溃后无回执的恢复；仍保持blocked。未接包钩子/任务收口/准确停止/稳定bootstrap版本化布局及激活健康终态，P1未完成、不进入P2。HELPER先前“完整回收尚缺”由本节修正为仅新强屏障操作可显式退休，旧记录不能推断。后续补正式验收脚本及缺回执管理员修复策略，再继续P1包事务前置。

### 26.1 复核与可复现验收收口

将guard字符串replace改为显式公共片段组合，避免重构静默丢失subreaper逻辑；管理员退休明确要求schema2。补直接拒绝断言：旧direct-child证据、发送锁busy、active漂移、runtime_prepared成功记录及旧schema都不写退休记录。HELPER补齐强屏障、无限后代等待和缺回执阻塞边界。

新增纳入源码的 `packages/device-runtime/scripts/accept-native-retirement.py`（owner fail→管理员CLI retire→owner retry），在隔离amd64 Linux、真实v15临时开发签名包上执行通过；验证旧ID不重放、新ID完成及同ID稳定、旧失败证据保留、active不变。重构后发送者14项通过（4.37s），宿主完整922 passed/3 Linux skipped（43.43s）；后续schema2显式断言另有定向复核。未构建新包，临时联网依赖仍不是离线交付；未安装/激活或调用真实服务。包保护保持不变。

交付联动：实现/CLI、测试、HELPER和实施记录已更新；数据迁移、宿主前端、workdir示例及AI资产不适用（独立Device仓内部准备流程）；发布制品与实机验证未完成，不扩大支持声明。下一项仍为P1包事务前置契约：任务收口/准确停止、稳定bootstrap布局、包钩子准确许可和激活恢复；缺回执现场保持阻塞，不自动修复。

## 27. 包发送者独立CPython事务副本

退休子阶段已以8dc631d保存本地检查点后，继续复核P0 4.2，发现Debian guard仍使用sys.executable，包替换可能移除活动解释器。新增native_bootstrap：可信root源树复制至管理器事务目录、独立文件不硬链接、内部相对链接与50k成员/2GB预算、逐项SHA256/权限、前后源和副本一致性、fsync后无覆盖回执。缺回执不重建，漂移拒绝，无清理或包许可。

Debian apply意图前准备副本，将snapshot.json摘要绑定意图；guard发起前复核副本，使用副本解释器-I -B，不再依赖原包解释器继续存在。仅发送者接入，整个管理器独立job/包钩子许可/稳定包版本布局仍未完成，不以此证明完整更新。

9项新增回归覆盖独立inode与源变更不影响副本、缺回执、快照漂移、越界链接、复制期间源漂移、非管理员、实际适配器argv绑定与快照失败不写apply意图。编写测试时曾错误解包fixture和重复缺参prepare，修正为复用已批准fixture后完整931 passed/3 Linux skipped（46.67s）。隔离amd64 Linux真实v15 CPython6238成员复制、同目录幂等、独立inode、重定位sys.prefix及ssl/sqlite3/ctypes加载通过；未真实dpkg替换、未构建新包或RPM联合验证。验收脚本accept-native-bootstrap.py纳入源码。无真实设备/服务操作，包保护不变。

联动：实现/适配器/测试/HELPER/实施记录已更新；宿主前端、数据库、workdir示例、AI资产不适用。下一项优先真实隔离包替换期间快照guard持续执行和回执、整个管理器独立job，再推进包钩子准确许可与激活恢复。P1未完成、P2未开始。

## 28. 标准dpkg移除源解释器后的发送者验证

新增 `packages/device-runtime/scripts/accept-native-bootstrap-replacement.sh`，仅显式 `DEVICE_DISPOSABLE_CONTAINER=bootstrap-replacement-v1` 的新Linux隔离命名空间可运行。使用v15 DEB只提取CPython材料，另构建独立名 `device-bootstrap-disposable-fixture` v1/v2：v1拥有源运行时，v2标准dpkg更新移除它。通过真实Debian.prepare_execution/apply路径调用快照guard，核对源python3.13和os.py被移除、包数据库v2、匹配sender_reaped/returncode0回执、快照仍一致且ssl/sqlite3/ctypes可加载。未安装Device包，不解除其钩子，不借测试包许可作为Device批准。

首轮发现实际生产路径错误：guard PATH=/usr/bin:/bin使dpkg找不到ldconfig/start-stop-daemon并返回2，未替换；补固定/usr/sbin:/usr/bin:/sbin:/bin并增加单测断言。第二轮标准包替换成功，但父进程写pycache造成源目录残留，原“整个目录消失”断言错误；父进程改-B并直接验证解释器及标准库文件移除，复跑通过。无手工删除或绕过dpkg；所有--rm容器已退出，无后台遗留。

最终931 passed/3 Linux skipped（47.72s）、ruff、sh -n与diff检查通过。该证据仅覆盖独立发送者副本跨真实包替换存活，尚未覆盖整个管理器独立job、Device真实包覆盖许可、激活恢复、RPM或目标实机。P1未完成、包保护不变。下一步整个管理器独立job与标准包事务准确许可；不进入P2。

## 29. 独立guard包观察与终态持久化

将生产Debian发送者的包数据库准确观察及outcome持久化移入快照guard。apply意图加入准确target_package（name/version/architecture），guard等待dpkg回收后先发布匹配sender回执，再有界dpkg-query观察，只有命令0且准确installed目标才记录package_applied_pending_activation；命令失败/观察超时或异常均unknown。不激活、不清意图。dpkg输出直接写私有事务.package.log并fsync，不经父进程PIPE，父管理器不再猜测/覆盖生产outcome。guard缺终态仍unknown；旧意图恢复路径保留。

3项直接执行可信guard源码回归覆盖成功、命令失败及观察超时；隔离Linux真实fixture包v1→v2移除源解释器后独立记录准确终态。进一步BOOTSTRAP_PARENT_EXIT=1启动guard后发起进程os._exit(23)，观察进程确认父已退出，guard仍完成标准dpkg替换、源解释器及标准库移除、匹配回执和pending终态、副本保持有效。没有kill或宿主服务操作。所有容器--rm退出，无后台遗留。

最终934 passed/3 Linux skipped（48.18s），ruff/format/sh-n/diff通过。这证明发起进程退出不阻断独立发送与结果落盘，尚不等于完整systemd独立job或整个管理器切换；锁/任务收口/包钩子准确许可/激活恢复仍需后续验收。Device包保护未解除，P1仍未完成。

第29节后续复核：recover原仅接受旧prepared_sha256单字段意图，会错误拒绝新快照意图。修复为旧意图保持严格原格式，新意图必须完整匹配prepared摘要、快照回执摘要与准确target_package，并重新验证副本；未知附加字段和目标漂移仍拒绝。新增恢复成功/目标漂移回归，确认不重新执行包。最终完整935 passed/3 skipped（51.65s），ruff/format/diff通过；该修复独立检查点保存，不改写前一提交历史。

## 30. 独立systemd job静态绑定设计（未接执行）

新增native_update_job.describe_job仅返回静态描述和unit候选，不写systemd目录、不创建worker/launch gate、不启动。要求既有准确管理员批准、apply意图、有效独立快照及准确target全部匹配，unit名取canonical事务路径SHA256，固定root/oneshot/Restart=no/无限包事务超时，无Install段。描述明确staged_not_dispatchable、dispatch_allowed=false；ConditionPathExists默认不满足。4项回归验证幂等、无文件副作用及批准/目标/快照漂移拒绝；完整939 passed/3 skipped（46.54s）。

检查实际接入依赖发现v15包仍不含最新native_update模块/原生验签依赖，不能把当前静态描述当作完整管理器job。排队锁交接与worker侧准确源/批准复核尚未实现；试写的job-intent启动阻断已撤回，现有行为不变。下一步先构建含最新模块、native依赖的完整离线运行时并验证快照内模块导入，再接一次性job意图与二次校验，最后隔离systemd动态验收。静态job描述不构成systemd运行证据或包许可；P1未完成。

## 31. 完整离线管理运行时与native依赖前置

以4f6af9b干净源码构建三组件wheel（uv build --all-packages --wheel），uv export --locked --package ai-device --extra client --extra native --no-dev --no-emit-workspace --no-hashes导出准确依赖；13个Linux wheel逐项匹配uv.lock URL/size/SHA256。首次手工标签筛选误选psutil cp313t，实际下载未安装，改选锁内cp36-abi3；不清理已有下载。新增prepare-native-manager-wheels.py固化非free-threaded cp313/abi3/py3选择、HTTPS显式下载与摘要校验、无覆盖manifest，5项ABI/架构/准确pin回归通过。固化脚本复现同一13条结构清单（只差末尾换行）。

accept-native-manager-runtime.sh在隔离amd64 Linux、/tmp工作目录，以v15 CPython为种子，三组件与13依赖全部--no-index --no-deps --force-reinstall安装，pip check通过。新解释器-I -B导入native_update/bootstrap/job/candidate/prepare、Ed25519、zstandard、psutil、PIL.Image均位于安装runtime下；管理模块字节逐项匹配输入wheel。再复制完整独立快照，从快照解释器重复导入全部模块通过，无PYTHONPATH/源码回退，无服务操作。构建下载阶段联网，安装阶段仅本地明确输入；不是正式签名或重新构建的DEB/RPM。

准确输入：ai_device wheel SHA256 f25c7b7574ca2d915b05d126e9a05f87ed5404974e06cff9f55d7adf5dbbf424，bash aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3，fs b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626。现场build/native-inputs/wheels-p1-manager-v16、dependencies-manager-v16、manager-v16-reproduced-lock.json保留但忽略，不提交二进制。完整944 passed/3 Linux skipped（44.09s），ruff/sh-n/diff通过。

独立job原先缺完整运行时的前置已在隔离实验解除，但尚未派发：后续需把此完整运行时纳入原生构建，再接job持久排队门禁、锁交接、worker侧源/批准二次复核和隔离systemd动态验收。P1未完成，包保护不变。宿主前端/数据库/AI资产不适用；构建验收脚本/测试/本记录已更新。

## 32. 完整管理运行时纳入开发DEB

以7ffc905干净源码新构建三组件wheel，复用已校验13条client/native锁定wheel（单独目录无cp313t残留），生成精确Linux constraints并通过现有assemble-native-payload.py得到build/native-payload-v17。隔离Linux运行原build-native-linux.sh构建DEB，保留所有原始保护代码不变。新包109756216 bytes，SHA256 ab69060eb6f2eda8281d21ab14584da28f4a233504cf20d6a830bc7ea5c47c8d，路径build/native-packages-v17/ai-device-native-client_0.2.2-1_amd64.deb；仍是0.2.2-1未签名本地开发制品，不发布。

只提取不安装的新临时容器：pip check通过，包内native_update/bootstrap/job/candidate/prepare全部导入，从完整独立快照完成Ed25519真实签名验证和zstd压缩解压；模块路径位于快照，无源码依赖。新旧v15 preinst字节cmp一致，提取已有payload后运行新preinst仍准确拒绝覆盖，未移除/覆盖任何设备安装。固化accept-native-manager-package.sh并在第二新容器复跑通过，快照执行后重新inventory一致。无服务操作、无后台或容器遗留。

源码变更仅新增验收脚本及记录；核心完整回归沿用7ffc905的944 passed/3 skipped（本轮没有核心改动，未声称新跑完整）。sh-n/diff通过。原生构建的完整管理运行时前置已在DEB完成，RPM/macOS未对齐；静态job仍不可派发。下一步接一次性job派发前持久门禁/锁交接与worker准确二次复核，隔离systemd动态验收。P1未完成，包钩子准确许可仍未开放。

## 33. 独立job显式派发与worker二次复核原型

native_update_job增加dispatch_job（Python显式入口，未接公开CLI/IPC）：仅root Linux、生产Debian适配器、准确批准，包锁内复制完整快照并逐字节核对native_update/job/helper与当前实现一致。先无覆盖持久job-intent，释放实例/包锁后仅一次systemd-run固定argv/root oneshot/Restart=no/无限事务超时；受理仅dispatch_accepted，错误或超时dispatch_unknown，不代表包执行。已有job意图仅观察不重派，缺派发回执仍阻塞。

helper启动/登记和其他包apply被持久job意图阻断。worker从完整快照执行固定模块，重新进入原apply准确批准、绑定、active、包数据库、停止状态及锁协议；额外验证job-intent摘要、prepared及runtime快照摘要。worker已在同一快照时只重验不重拷贝。无删除意图、重派、自动激活或包许可。源码审查发现持锁派发会使worker抢锁失败，改为持久门禁覆盖解锁到派发的空隙，定向断言派发前包锁已释放。

5项新增覆盖派发响应丢失/同ID不重发、排队拒绝直接apply/启动、worker意图与源漂移拒绝、同快照不重拷贝；完整949 passed/3 Linux skipped（42.71s），ruff/format/diff通过。尚未执行真实systemd动态验收，新代码未纳入v17 DEB，不能称job已可交付。需要先冻结本检查点重建wheel/完整运行时，再隔离systemd派发和失响应/源漂移验证。v17包仍含e4a1ba7前管理实现，不混算此原型证据。P1未完成，Device包保护保持。

## 34. 隔离systemd独立job拒绝路径动态验收

冻结ae1acb4构建三组件wheel到build/native-inputs/wheels-p1-job-v18，在新amd64 Apple容器cap-add ALL/container=apple启动独立systemd PID1。新增accept-native-update-job.sh：仅job-v1明确隔离标记及systemd/root接受，提取v17完整DEB、离线替换v18三组件wheel，普通UID12345新建并登记临时实例，不启动客户端。准确prepare/approve→dispatch_job→同ID观察；验证持久job意图拒绝start、systemd从事务快照运行worker，原preinst拒绝覆盖后匹配sender非零回执、unknown outcome、日志准确包含保护拒绝、active不变、Restart=no。未放开任何Device包钩子，未安装成功/激活或使用真实凭证。

脚本全部断言通过并输出ACCEPTANCE_EXIT=0；随后systemctl --force poweroff只关闭该一次性临时容器，外层container run返回130，不把130当测试通过码，实际验收以脚本终态与断言证据为准。--rm容器已退出，无后台遗留。完整源码回归仍为ae1acb4的949 passed/3 skipped（新增脚本未改核心，本轮未重跑完整），sh-n/diff通过。证据仅覆盖真实systemd派发与包保护拒绝，不覆盖成功升级、派发失响应真实注入、排队源漂移动态拒绝或激活。

下一步仍需独立job只读诊断/显式恢复入口、排队源漂移与失响应真实故障、稳定版本化包布局、任务收口/准确停止、准确包许可及激活恢复。P1未完成；原包保护不变，P2—P5未开始。

## 35. 派发失响应与排队源漂移（重启前未提交现场）

扩展accept-native-update-job.sh两个真实隔离systemd故障：lost-response在真实systemd-run成功后抛调用方TimeoutExpired，准确dispatch_unknown、同ID不重派，后续包保护仍拒绝、sender/outcome匹配；source-drift以测试专用--no-block/ExecStartPre有限sleep确认SubState=start-pre后改变临时active，worker报source active changed，未创建apply-intent/sender/package.log，job持久门禁保留，测试active恢复。两项最终均ACCEPTANCE_EXIT=0，容器关机外层130单独记录，不混同验收返回码。

源漂移首次未真正命中排队窗口，90秒断言失败；改no-block并观测start-pre后命中，第二轮journal无日志导致证据断言失败；改测试专用stderr文件后准确原因通过。后台63215已回收，所有临时容器已关机退出，无后台遗留、无宿主服务操作。源码核心仍5f92fd4/v18 wheel，未解除包保护。

重启通知到达时新增observe_job只读诊断草稿，尚无单测/动态验证，只有ruff通过；不宣称诊断或显式恢复完成。本轮未提交：验收脚本、native_update_job.py以及本节。HEAD仍5f92fd4，未跑新完整回归，上一核心949+3skip不能背书诊断草稿。git diff-check/sh-n通过。恢复后优先复核诊断的证据绑定/只读副作用并补回归，正式记录本轮故障证据再保存检查点；不得丢弃未提交改动。安全重启由外部处理，本会话未执行重启。

## 36. 重启后只读job诊断审核与回归

外部重启后核实服务running、ses-14204/pro-1非私密、唯一execution和停止检查开关保持，3文件现场及HEAD 5f92fd4完整；重启等待解除。审核observe_job草稿发现复用_transaction会创建锁/目录，改直接读取可信已有registry/updates，不初始化或补写台账。诊断严格绑定事务ID/准确批准/完整apply目标和快照摘要、sender intent/state/整数returncode、outcome intent与枚举；pending必须命令0且准确包数据库目标匹配。systemctl只show，失败或超时保持unknown；任何状态retry_allowed=false，保留持久门禁，不自动恢复。

新增6项回归：active/failed/inactive分类、读取前后全部文件内容及mtime不变/不创建锁、缺目录不初始化、query超时、sender失败不许宣称成功。完整955 passed/3 Linux skipped（88.07s），ruff/format/sh-n/diff通过，后台81358已回收。该函数尚未在新构建runtime内动态调用；第35节真实失响应/排队漂移证据仍来自v18，不能混算为新诊断动态验收。显式恢复及unknown解除仍未实现。原包保护、P1边界不变。

## 37. 新wheel只读job诊断真实systemd验收

以ce9d3f5干净检查点重建三组件wheel至build/native-inputs/wheels-p1-diagnostics-v19，隔离容器离线安装后-I -B运行现有故障脚本，直接调用observe_job。lost-response真实受理后调用方超时、同ID不重发，包钩子拒绝后诊断package_outcome_recorded且准确outcome一致；source-drift确认start-pre后改变测试active，worker执行前拒绝，诊断job_failed_requires_review且无apply/sender/package日志。两路径retry_allowed=false、fence_retained=true。

诊断前后registry下全部目录/文件mode、mtime_ns、文件SHA256快照一致，没有补锁或写台账。两临时容器ACCEPTANCE_EXIT=0，随后关机外层130分开记录；均已退出，无后台遗留，客户端未启动，包保护保持。源码仅扩展验收脚本，无核心变更；核心完整955+3skip沿用ce9d3f5，未声称本轮重跑。sh-n/diff通过。

显式恢复仍未实现：failed/缺unit不可单独证明排队job不会再执行，诊断也不解除持久门禁。下一步需准确unit/worker退出证据与锁空闲联合核验、持久恢复记录及旧ID永不重派；不得直接删除job-intent或因源恢复就复用旧job。P1仍未完成。

## 38. 已执行job管理员显式对账

新增Python recover_job，Linux root显式，先读取匹配job证据且systemd Loaded、inactive/failed、MainPID0、无Job、apply意图存在；复用原Updates.recover的准确批准/源包观察/匹配sender及全局EX锁。recover增加可选job-intent摘要参数，在原锁内重验身份、prepared与意图摘要；最后无覆盖写job-recovery，绑定job-intent及recovery文件SHA，重复同证据幂等。retry_allowed=false且fence_retained=true，未删意图、不执行包、不激活。

新增完整fixture回归验证成功对账幂等、不重执行、活动unit拒绝、启动门禁保留。完整956 passed/3 Linux skipped（46.73s），ruff/format/diff通过。此入口尚未真实systemd动态验收或公开CLI，不能称通用恢复完成；缺apply、缺sender、unit未知、执行前失败仍拒绝，旧job不退休、新ID仍受原门禁约束。下一步新wheel动态验证此对账及缺回执拒绝，再设计执行前失败的一次性worker关闭证据/退休协议；不要把诊断failed当不再执行证明。P1未完成。

## 39. 已执行job真实隔离对账与unit保留

以3d2d075为基线的v20 wheel首次真实对账发现成功oneshot被systemd回收，LoadState=not-found，原恢复门禁正确拒绝但无法观察成功退出；不得把unit缺失当作退出证明。新派发固定RemainAfterExit=yes，恢复除原loaded inactive/failed外，仅接受loaded active/exited、Result=success、MainPID=0且无Job；仍需apply、精确批准和sender证据，门禁不释放。旧已回收unit不会自动恢复。

使用包含此未提交修复的v21 wheel，在一次性amd64 systemd容器中安装低版本0.2.1-1空源fixture，提取未修改v17 Device候选，真实派发故障模拟响应丢失；原Device preinst拒绝覆盖、源包保持原版本。隔离fixture短暂隐藏sender后对账拒绝且未产生恢复回执，恢复sender后显式对账重复幂等、调用systemd仅一次、job/apply意图和启动门禁保留、active不变；测试exec退出0。source-drift路径再次退出0，未执行包且门禁保留。此非真实设备或成功升级验收。首次只解包源导致dpkg从absent变not-installed，recover保守拒绝；同版本真实包安装实验由旧prerm拒绝，不作为目标差异证据。未修改包钩子。完整回归956 passed/3 Linux skipped（43.24s）。执行前失败job关闭证据、退休与新ID仍未实现；P1未完成。

## 40. 执行前job持久关闭屏障（不退休）

派发释放锁后systemd-run可能迟到，不能以failed或unit缺失证明未来不执行。新派发job意图声明package-lease-v1；管理员Python close_before_apply在实例锁与已有全局包EX锁下核验精确job身份、批准和完整快照，逐字节比对快照内三个worker模块及当前代码，仅在没有apply-intent/sender/outcome时无覆盖持久写job-closed，绑定job摘要。apply worker在同一包锁内检查任何closed标记，存在即拒绝；闭锁和apply意图发布互斥，崩溃缺标记不解锁。重复同证据幂等，旧无协议或代码不同快照拒绝。关闭只阻止本job，不删除意图、不解除启动门禁、不授权重试或新ID；不提供CLI。

隔离v22 wheel真实systemd start-pre排队窗口先关闭，迟到worker报告closed before execution，未写apply/sender/package.log，包未执行、active不变、持久门禁保留，测试exec 0。fixture为低版本空源包，非真实升级。单测覆盖幂等、迟到worker、旧协议、包锁占用和已有apply拒绝；完整958 passed/3 Linux skipped（44.23s）。未来退休需单独设计与验证：确认所有路径认可精确关闭证据、旧job不能重放且新ID不绕过其他apply或unknown；当前未实现退休/解除占用，P1未完成。

## 41. 执行前关闭job安全退休与新ID原型

新增管理员Python retire_closed_job：先按关闭协议校验旧快照与精确批准，再持实例锁和包EX锁复核关闭摘要及无apply/sender/outcome，持久无覆盖写job-retired（绑定原intent及closed摘要）。不删除旧意图/关闭记录；旧ID直接apply和迟到worker均拒绝。新helper统一精确验证退休证据，启动/登记及apply/dispatch忽略合法退休job，但其他未退休或损坏job、任何apply仍阻塞。新ID必须重新prepare/approve并重查绑定、源和停止状态。关闭/退休仅适用于新协议及与当前代码一致快照；既有旧helper不识别退休，仍保守阻塞，未提供升级部署或CLI。原包钩子不变。

v23未提交代码wheel在一次性systemd容器使用低版本空源fixture：旧worker处于start-pre时关闭并退休，迟到启动被拒绝、旧ID不重放；新ID独立准备批准后派发，原Device候选preinst拒绝覆盖，新job门禁保留、active不变。exec 0；不代表设备升级、激活或真实生产许可。单测覆盖退休幂等、旧ID、新ID、损坏与其他未知事务阻塞；完整回归960 passed、3 Linux skipped（46.22s，后附加拒绝断言定向52通过）。P1仍未完成，需bootstrap稳定版本化与旧helper升级方案、事务授权和完整生命周期验收。

## 42. 版本布局保护的开发包加固

现有builder硬编码0.2.2-native1，检查单个版本路径会漏掉其他版本。未实现版本切换前，DEB preinst增加packages/bootstrap根路径及悬空符号链接检查；RPM pretrans增加两个根路径检查，任何已有布局一律拒绝普通覆盖。旧实例/manager/指定版本检查仍保留，不改变现有授权门禁。DEB提取自builder的实际preflight脚本，以临时重定向目录验证全新通过、旧版本与悬空链接拒绝且无写入；RPM仅静态检查Lua根路径，未重建包或实测RPM，Lua的悬空链接语义尚未验证。完整回归966 passed/3 skipped为悬空链接补充前，之后定向8通过。此加固不是稳定版本化布局、bootstrap升级切换或旧helper兼容完成；P1继续。

## 43. 新DEB/RPM真实包钩子隔离验收

RPM内嵌Lua无lstat，Rocky9实测posix.readlink可识别悬空链接；pretrans以stat或readlink拒绝已有路径。用v17旧payload、锁定运行时和当前钩子构建未签名v24开发DEB/RPM（不代表最新应用源码发行）：DEB SHA256 f271cad46220ba1d9b85f6ecae5f072cb79caf9268cebe47baee3bdfe2851222，RPM da2731ad27205ab25918740f41c2fd8725fab9550229cf62207694267fb0361b。隔离Ubuntu系dpkg与Rocky9 rpm实际执行：已有其他版本目录与悬空bootstrap链接均拒绝，原哨兵或链接不变、目标未写；全新安装通过，创建manager后卸载保护拒绝且bootstrap仍存在。RPM使用--nodeps，不作为依赖验收。新增accept-native-layout-guard.sh，必须明确一次性容器标记；脚本悬空链接模式两包实跑通过。完整968 passed/3 Linux skipped（44.20s），构建容器临时安装rpm-build，不修改宿主。P1稳定bootstrap、旧helper兼容、签名发行与实际设备升级仍未完成。

## 44. 独立bootstrap候选helper自检原型

新增inspect_helper_candidate，只接受Linux root可信独立事务快照；先完整验证快照，以快照内Python -I -B导入helper，核对base_prefix、模块绝对路径及Python 3.13，超时/导入失败/身份不符拒绝，结束后再次核对清单，返回snapshot及helper摘要和未激活状态。只执行可信管理员候选，不验签、不修改unit、不启动或切换服务，不证明helper协议兼容。单测覆盖身份、超时、运行中漂移及无额外文件；v25 wheel在一次性隔离Linux复制独立CPython并真实运行候选自检成功。完整972 passed/3 Linux skipped（41.43s）。该能力尚未接入版本化候选发布、稳定入口、旧helper安全迁移和事务切换；旧包保护不变，P1未完成。

## 45. 版本化helper候选发布原型

新增publish_helper_candidate，调用方须持包事务锁，在可信管理候选目录以受限版本ID复制独立运行时快照，执行helper身份自检后无覆盖写candidate.json，绑定来源、快照和helper摘要，显式未激活。重复调用重验快照与证据；不完整目录、来源冲突或证据变化拒绝，不自动删除或修复。v26 wheel在一次性隔离Linux中持包EX锁真实发布并重复验证成功；完整974 passed/3 Linux skipped（42.04s）。此函数尚无管理入口、签名验证、活动指针、跨版本协议兼容或服务切换；不证明旧helper已升级，原包保护保持。P1仍未完成。

## 46. 管理目录受锁候选发布与只读重验

新增publish_managed_helper_candidate，要求预先存在可信manager/bootstrap-candidates目录，由入口持全局包EX锁执行候选发布并复核；低层发布仍要求调用者持锁。新增read_helper_candidate，不执行候选代码，重新校验快照与版本、来源、helper摘要、未激活记录。锁占用拒绝且不创建候选；证据或快照漂移拒绝。定向20通过；v27 wheel在一次性隔离Linux实测包锁占用拒绝、受锁发布和只读重验成功。未跑本轮完整回归，未实现签名、稳定入口或旧helper切换，不启动服务。P1未完成。

## 47. 重启后回归与helper切换只读观察

用户完成外部安全重启后只读核实宿主服务running、唯一execution和工作区b2b2a2b干净；先前受锁候选发布完整回归976 passed/3 skipped（43.09s）。新增inspect_helper_transition，在已有包EX锁下重验已发布候选、读取现有helper unit摘要并复核unit未变，返回候选/快照摘要；明确switch_allowed=false、未验证进程静止及协议兼容，不改unit、不重启/启动服务、不授权切换。fixture测试证据漂移拒绝和unit不变；新增后完整977 passed/3 skipped（42.36s）。尚无稳定入口、旧helper安全迁移、签名或实际服务切换验证，P1未完成。

## 48. 更正：v17旧helper不识别job门禁，禁止混合部署

隔离提取v17 DEB，直接用包内独立bootstrap的原helper读取测试registry：仅存在未退休job-intent时，旧`_assert_no_package_intent`返回允许，其源码只检查apply-intent；新helper对精确退休fixture可放行，但不改变旧helper行为。故前文第41节及其他“旧helper仍保守阻塞”的描述对v17错误：**旧helper在job派发到apply-intent落盘之间可能允许启动**。这不是退休证据损坏或旧helper已经升级的证明，也不是生产现场核查。

硬门禁：不得把job派发/退休原型与不识别job-intent的旧helper混合用于真实设备，不得以新wheel或磁盘文件更新推断运行进程已加载新helper；必须先实现并验证版本/进程身份、旧helper受控迁移及未终态台账兼容，或用独立持久屏障在旧helper也认识的协议下覆盖派发间隙。不得删意图、放宽原包钩子或依靠短暂窗口。隔离实验分别验证新helper接受模拟退休记录、v17旧helper忽略未退休job；未验证真实服务运行版本，也未触发真实设备操作。P1阻塞于此兼容缺口，继续隔离整改。

## 49. 新job派发默认失败关闭

针对第48节v17旧helper忽略job意图的已证实派发窗口风险，dispatch_job在已存在job的观察返回之后、任何新job意图/快照/系统调用之前调用固定失败关闭门禁；不得根据静态文件、当前PID或一次能力查询放行，因为旧daemon可在检查后启动。已存在job仍按原ID观察，不重派；恢复与退休不删除原台账。历史第41节的新ID派发仅表示当时隔离实验，现行生产入口关闭，新ID不能派发。测试只在明确monkeypatch门禁时覆盖旧派发机制；独立测试验证默认拒绝、无job意图/receipt/systemd调用，既有job不重放。完整979 passed/3 Linux skipped（46.49s）。此门禁只保护加载了新代码的调用者，不约束已运行旧调度器；未实现跨版本持久屏障、旧helper迁移或真实服务升级，原包保护不变，P1未完成。

## 50. 旧helper兼容缺口可复现隔离回归

新增一次性Linux容器脚本accept-native-old-helper-fence.sh，使用v17 DEB内真实旧bootstrap helper及v28当前wheel，在同一个隔离registry上验证：旧helper忽略未退休job-intent；新helper阻塞该意图；新dispatch默认在写job-intent/receipt及调用systemd前拒绝。脚本需显式old-helper-fence-v1标记，实际隔离运行三项均通过。daemon锁仅持有至进程退出，不能作为跨重启的持久屏障；该回归不验证服务重启、派发竞态或旧运行代码迁移，不解除新派发默认门禁。无产品代码修改，本轮未重跑完整回归；之前979 passed/3 skipped。P1未完成。

## 51. 旧helper迁移锁证据与尚未实现的门禁

在一次性Linux容器中从v17 DEB提取实际旧bootstrap helper，启动独立进程取得`helper-daemon.lock`：另一进程获取被拒；原持有进程自然退出后另一进程立即可取得。证明该锁具有进程存活期间的互斥，但不证明宿主重启后、systemd或人工启动时旧helper不会重新出现。未停止真实服务、未执行迁移。

迁移设计至少须在解除派发关闭前证明：实际运行的旧helper及已接受启动sender收口；systemd等启动入口和旧版本未来启动在持久边界内被协调；未终态start/job/apply按原ID和证据安全恢复或继续阻塞；候选helper及实际加载进程版本绑定且支持相同屏障；切换与崩溃/重启恢复有持久、可重入且不重放的证据；旧版本不能仅因锁空闲或磁盘代码更新被当作已退出。仅有daemon锁、unit摘要或一次PID查询均不足。新job派发仍默认关闭，不能把本实验作为互斥完成或授权切换的证明。当前无实现跨重启持久协议，P1未完成。

## 52. 候选发布清单读取预算

候选提取、普通用户准备和管理员签名准备统一在实际读取时限制发布清单至256000字节，超过即拒绝，而不是先检查文件大小再无界读取；避免两步之间文件增长绕过内存预算。保留签名、身份与目标检查；这不是对并发修改的原子快照证明，也不改变信任根及job派发门禁。增加上限边界、超限时不创建候选的测试；相关测试96通过，完整回归981通过、3跳过（macOS宿主）。未进行真实设备操作，跨版本持久屏障仍缺。

## 53. 管理员运行时回执读取与证据绑定

管理员验签准备对运行时回执打开后校验实际文件为登记用户拥有的普通文件，并在读取时限制256000字节；回执摘要直接来自解析和校验的字节，不再另读路径生成可能不一致的摘要。后续证据复核同样限长；回执在解析后变化时拒绝建立prepared记录。增加超限与解析后变化测试，相关98项通过，完整回归983通过、3跳过。这不承诺对用户可写文件全程原子隔离，也不解除新job派发或原包门禁；未进行真实设备操作。

## 54. systemd mask 与旧代码直接调用的隔离边界

一次性Linux容器提取v17原DEB，以`systemctl --root=/ mask ai-device-native-helper.service`创建指向`/dev/null`的持久unit mask，并核实链接；随后直接运行v17 bootstrap Python调用实际旧`_daemon_lease`，仍成功获取registry锁。该实验只证明unit mask不约束直接调用旧代码的锁函数，并非真实daemon直接启动或宿主重启实验；也未证明其他启动入口均已封闭。迁移不能仅用mask或锁空闲作为旧helper不会再运行的证据。当前没有安全迁移实现，新job派发保持关闭、原包保护不变；无真实服务操作。

## 55. helper只读操作台账身份校验

旧helper台账名以实例ID和操作ID用连字符拼接，不同ID对可能命中同一文件。新版helper的query/reconcile读取前核对意图的action、实例和操作身份；有结果或恢复但无意图时拒绝，恢复证据须绑定同一意图。避免只读查询将另一操作结果当成当前操作；不重放、不修改旧台账。新增碰撞、孤立记录和恢复不匹配测试，helper 88项通过，完整回归988通过、3跳过。已运行v17不受此修复约束；跨重启安全迁移仍缺，新job派发继续关闭，原包保护不变，未操作真实设备。

## 56. helper旧启动结果终态白名单

新版helper在允许新的启动操作前，仅把`started`、`already_active`认作已确认结果；缺结果或`unknown`仍需原有明确恢复证据，缺字段、未识别状态和非对象结果拒绝新启动，不写新意图。新增四种异常结果回归，helper 92项通过，完整回归992通过、3跳过。已运行旧helper不受此改动约束，跨重启安全迁移仍未实现；新job派发和原包保护不变，未操作真实设备。

## 57. helper启动恢复证据统一重验

新版helper在重复resolve、同ID start、只读查询及新ID门禁读取旧恢复记录时，统一要求已知确认状态、原始意图一致和`start_replayed=false`；不匹配或非法记录拒绝，不重放。新增四个入口与三类证据漂移组合测试，helper 104项通过，完整回归1004通过、3跳过。该检查不证明旧进程退出或跨重启持久屏障，不改变新job派发关闭及原包保护，未操作真实设备。

## 58. 旧锁路径替换候选的提前打开竞态

一次性Linux容器提取v17原helper，在隔离fixture中预先打开其daemon锁文件，再将锁路径替换为符号链接：随后通过原helper `_daemon_lease` 打开路径因`O_NOFOLLOW`以ELOOP拒绝，但此前打开的旧inode描述符仍能成功获取EX flock。证明仅替换锁路径无法排除旧进程在替换前打开、替换后获取旧inode的竞态；更未证明正在运行的daemon、sender及宿主重启的安全收口。该实验不构成受支持的迁移实现，不得用于真实环境或删除/替换真实锁文件。新job派发保持关闭、原包保护不变，未操作真实服务。

## 59. v24 RPM声明依赖测试事务

一次性Rocky Linux 9 x86_64容器对既有v24开发RPM（SHA256 `da2731ad27205ab25918740f41c2fd8725fab9550229cf62207694267fb0361b`）执行`rpm -qp --requires`和不带`--nodeps`的`rpm -i --test`，均成功；声明依赖仅为五项rpmlib能力。该测试事务未安装包，不等于真实脚本执行、动态库完整性、离线运行时依赖闭包或最新源码制品验收；v24仍使用旧v17 payload。旧helper安全迁移未实现，新job派发关闭，原包保护不变，未操作真实设备。

## 60. v24 RPM隔离运行时链接与模块导入

一次性Rocky Linux 9 x86_64容器无`--nodeps`安装摘要为`da2731ad27205ab25918740f41c2fd8725fab9550229cf62207694267fb0361b`的v24开发RPM。包内client Python导入ssl、sqlite3、ctypes、zlib、cryptography、zstandard并完成Ed25519测试签名；独立bootstrap Python导入上述标准库及native_helper成功。对两个运行时目录内普通文件名匹配`*.so`或`*.so.*`逐个运行ldd，未报告`not found`（非动态对象的ldd错误被忽略）。该证据仅覆盖上述导入与扫描，未证明所有可执行文件、延迟加载、目标实机或完整依赖闭包；制品仍是旧v17 payload，不是最新源码发行。旧helper迁移仍缺，新job派发关闭，原包保护不变，未操作真实设备。

## 61. 当前源码隔离DEB开发制品

以干净提交`9debfb6c6d94ac6bdc7162fe1df496e094db1e50`离线构建三组件wheel，用现有v17锁定依赖与uv组装payload，再在一次性amd64 Linux容器使用锁定CPython构建未签名DEB：`build/native-packages-current-9debfb6/ai-device-native-client_0.2.2-1_amd64.deb`，109766104字节，SHA256 `9b37b30b0f915bcbd15dd8d2475559d2e98648438e1ca5f98d4fb63bcab9d00c`。payload记录基线提交及wheel摘要，device wheel SHA256 `92130ff7ee0f17c24e0805efd80074b2db9a19bdddc7deef6c83713be340985f`。另一隔离容器fresh安装成功；包内native_helper与native_update_job字节与构建时源码相同，派发门禁仍拒绝。存在布局时重复安装失败，但实验输出未确认失败原因，不能将其单独作为钩子保护通过的证据。未构建新RPM、未签名发布或验证完整升级；旧helper跨重启迁移仍缺，原包保护未放宽，未操作真实设备。

## 62. 当前DEB已有布局拒绝原因验证

对第61节的当前源码开发DEB（SHA256 `9b37b30b0f915bcbd15dd8d2475559d2e98648438e1ca5f98d4fb63bcab9d00c`），一次性Linux容器先建立已有其他版本目录及哨兵，再执行真实`dpkg -i`。退出码1，输出明确`existing native versioned layout; helper transaction required`；原哨兵内容未变，目标payload和bootstrap目录均未创建。该证据限于已有目录场景，不证明第61节重复安装失败的具体原因，也不证明包升级许可、悬空链接、所有失败路径或真实设备行为。新job派发仍关闭，旧helper跨重启迁移未完成，原包保护未放宽。

## 63. 当前DEB隔离依赖检查

对第61节SHA256为`9b37b30b0f915bcbd15dd8d2475559d2e98648438e1ca5f98d4fb63bcab9d00c`的当前源码开发DEB，在一次性amd64 Linux容器fresh安装后，包内client Python的`pip check`报告无损坏依赖；导入ssl、sqlite3、ctypes、zlib、cryptography、zstandard并执行Ed25519签名成功；独立bootstrap Python导入选定标准库和native_helper成功。两个运行时目录的普通`*.so`和`*.so.*`逐一ldd未报告`not found`，非动态对象的ldd错误被忽略。该证据不覆盖全部extras、可执行文件、延迟加载、目标实机或完整升级；不是正式签名/发布。旧helper跨重启迁移仍缺，新job派发关闭，原包保护不变，未操作真实设备。

## 64. 当前源码RPM隔离构建与验收

使用第61节以`9debfb6c6d94ac6bdc7162fe1df496e094db1e50`为基线的冻结payload和锁定CPython，在一次性Rocky9 amd64容器临时安装rpm-build并构建未签名RPM：`build/native-rpm-current-9debfb6/ai-device-native-client-0.2.2-1.x86_64.rpm`，111037249字节，SHA256 `35e4c70cc438c98fedc84a57dc525eeb20097803bf97c0a50817598eefb0d9a4`。另一一次性Rocky9容器不带nodeps安装成功，client pip check和选定ssl/sqlite3/ctypes/zlib/cryptography/zstandard导入通过，native_helper及native_update_job字节与构建源码一致，新job派发默认拒绝。单独容器预置其他版本目录时，真实RPM pretrans明确拒绝，哨兵不变、目标目录未创建。未证明全部动态依赖、目标实机、成功升级或跨重启迁移；RPM未签名未发布，旧helper迁移仍缺，原包保护不变，未操作真实设备。

## 65. 当前RPM bootstrap与动态链接检查

对第64节SHA256为`35e4c70cc438c98fedc84a57dc525eeb20097803bf97c0a50817598eefb0d9a4`的当前源码开发RPM，一次性Rocky9 amd64容器fresh安装后，独立bootstrap Python导入ssl、sqlite3、ctypes、zlib及native_helper成功。两个运行时目录中普通`*.so`和`*.so.*`逐一ldd未报告`not found`，非动态对象的ldd错误被忽略。该证据不覆盖所有可执行文件、延迟加载、extras、目标实机或完整升级；未签名发布，旧helper跨重启迁移仍缺，新job派发关闭，原包保护不变，未操作真实设备。

## 66. 用户确认旧测试helper手动退休

用户确认旧helper仅为测试原型，不需要自动迁移或原地保留。当前P1改为管理员在维护窗口手动停止并退休旧测试环境，确认所有旧启动入口、在途sender和未终态操作已收口，再走新版干净安装；不自动接管、删除或改写旧台账。`native-helper-migration-draft.md`已标为历史草案，当前决策见P0设计末尾与HELPER说明。手动退休不是实际运行helper身份或新版互斥的证明；新job仍默认关闭，包覆盖保护不放宽。真实服务停止、清理和部署仍须独立授权，本轮未操作真实环境。后续应验证干净安装、新helper运行身份和新版job/start锁及故障恢复，不再实现旧版本自动接管。


## 67. job/start 包锁与意图组件回归

在保持生产派发默认关闭的前提下，新增使用实际文件 `flock` 的组件测试：独占包锁持有期间拒绝 helper start，共享包锁持有期间拒绝新 job 发布；独占锁释放后，持久 job 意图继续阻止 start。派发接受和响应丢失均验证意图保留、同 ID 不重派。派发测试中明确 monkeypatch 固定拒绝门禁，manager 或 systemd 为测试替身；这些测试不证明真实 systemd 竞态、进程退出/重启或实际运行 helper 身份。完整回归 1007 passed、3 skipped。未修改产品代码、未开启派发、未操作真实服务。下一步仍需设计和实现绑定实际运行进程的身份机制，并进行真实隔离 systemd 互斥及故障验证。


## 68. socket peer 进程身份原语（未授权派发）

新增 Linux `_socket_peer_process` 只读观察原语：先校验 socket SO_PEERCRED 的 root UID 与 PID，再通过 SO_PEERPIDFD 直接取得同一 socket peer 的 pidfd，校验 fdinfo PID，并在观察前后检查 peer 未退出；不支持的内核、非 root、身份不一致和退出均拒绝。pidfd 在作用域退出时关闭，不影响进程。一次性 amd64 Linux 容器（内核 6.18.15）实际 Unix socket 验证 peer PID、FD 不继承及进程退出被拒绝；另覆盖非 root 和内核不支持。该原语尚无生产调用方，不识别加载代码、不验证 systemd 单元或不可变启动快照，也不锁定观察结束后的 daemon 生命周期；不能用于开放 job 派发。旧测试环境手动退休、干净安装和实际互斥/故障验收仍是独立缺口；生产默认拒绝和包保护不变。


## 69. systemd 单元与 socket peer 只读关联

新增只读 `observe_helper_unit_peer`：在 peer pidfd 生命周期内两次查询固定 `ai-device-native-helper.service` 的 loaded/active/running、MainPID、InvocationID 和 FragmentPath；缺失、格式无效、两次状态漂移、peer PID 不同或观察时 peer 退出均拒绝。结果明确 `loaded_code_verified=false`、`dispatch_allowed=false`，不做服务操作。一次性 amd64 Linux 6.18 systemd 容器中使用测试 socket 单元验证匹配成功、另一 root 进程拒绝；不是实际 Device helper 安装、加载代码或启动快照验证。测试容器关机命令超时，随后列表确认该 `--rm` 实例已不存在。该观察不承诺返回后的 daemon 状态，未接入生产派发；仍须完成真实代码身份绑定、干净安装及真实互斥故障验收，默认派发拒绝保持不变。


## 70. helper 候选字节码缓存门禁

隔离 Python 实验复现：源文件改变但大小和时间戳保持不变时，`python -B` 仍可读取旧 `.pyc`，因此源码 SHA 及 `-B` 本身不能证明实际执行了对应源码。helper 候选 inspect 与已发布候选 read 在校验快照 inventory 后拒绝任何 `__pycache__`、`.pyc`、`.pyo`；不自动删除缓存，且不改变普通事务快照。新增 inspect 和 read 的拒绝回归，完整 Device 回归 1012 passed、3 skipped，ruff 通过。该保守门禁可能拒绝带常规缓存的 runtime，尚未验证当前完整制品能否满足，需后续隔离构建验收；它只排除字节码歧义，不证明运行进程加载了指定源码、systemd 互斥或持续身份。生产派发仍关闭，包保护不变。


## 71. 无缓存 bootstrap 候选隔离探测

锁定 CPython 归档包含 3 个 `encodings/__pycache__` 条目，原样构建会触发第70节候选门禁。Linux 包构建脚本现仅在创建新的独立 bootstrap 时用 tar 排除 `__pycache__`、`.pyc` 和 `.pyo`，不清理已有安装，也不改变客户端 runtime。测试从实际脚本提取 tar 命令，验证保留源码并排除缓存。在一次性 amd64 Linux 容器中，以锁定归档和当前源码组装无缓存独立 runtime，实际 snapshot_runtime 与 inspect_helper_candidate 成功；快照 SHA256 `7b912930b12deec5fe32a32a72cf7cd1bde18980f3e9ae32e065c251bcd4258c`、helper SHA256 `60988d0c8f2f14d416f1b9183d48c5879c9434354f5e378ef814e7991d2a77ec`，候选未激活。探测不是完整包构建或安装，也不证明运行代码身份、systemd互斥和故障恢复；生产派发仍关闭，包保护不变。首次单文件 bind mount 被容器工具拒绝，改为目录只读挂载后成功。


## 72. 无缓存完整 DEB 构建探测（非发布制品）

在当前源码 `9db38cf` 离线构建 Device wheel（SHA256 `ed2f384155815d5454e18b954300e54d25de0225883c29b138a591f644e91f2a`），在一次性 amd64 Linux 容器中复制 `9debfb6` 历史 payload 并仅在临时副本替换 Device wheel，运行当前 `build-native-linux.sh` 构建 DEB。实验包 SHA256 `0127f17a3b3bcfe80626301dcd63dc1f6cc25deec0639cfc48b686768d7c1f9a`；解包后 bootstrap 无 `__pycache__`/`.pyc`/`.pyo`，其中 `native_helper.py` 与当前源码逐字节一致，SHA256 `60988d0c8f2f14d416f1b9183d48c5879c9434354f5e378ef814e7991d2a77ec`。临时替换未重建 payload 来源清单，该包不是准确来源闭环或正式发布制品；容器和包随实验结束，不用于安装。未验证 RPM、实际运行代码绑定、真实 systemd 互斥或故障恢复；生产派发关闭及包保护未改变。


## 73. 来源一致的隔离 DEB 干净安装

从干净提交 `cc01329b2ebef33c4c5ffa23f39f0769ecd96e17` 离线构建 Device/Bash/FS wheel，分别 SHA256 `ed2f384155815d5454e18b954300e54d25de0225883c29b138a591f644e91f2a`、`aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`、`b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`；使用既有依赖、锁定 uv 和组装器创建 `build/native-payload-p1-cc01329`，核对 `build-inputs.json` 与 wheel 字节一致。一次性 amd64 Linux 容器离线构建 DEB（SHA256 `05eebe5104e31a146f43706df8a72e457b3bfe5208c3be332e31475bbb54618a`），空环境 dpkg 安装成功；bootstrap 无字节码缓存，helper 源码与当前源码逐字节一致，安装清单与输入清单一致，`-I -B` 导入模块路径正确。实验包留在一次性容器中，未签名未发布；未启动 daemon、不证明运行进程代码身份、systemd 重启或 job/start 互斥和故障恢复；RPM 和真实目标未验收。原包保护和新 job 默认拒绝不变。


## 74. 实际 helper 的 systemd/peer 观察及磁盘漂移

一次性 amd64 Linux systemd 容器使用锁定 CPython 及当前源码启动真正的 `native_helper serve`，不是第69节的 socket 替身。`Type=simple` 的首次观察在 socket 就绪前发生并报不存在；随后确认单元 active/running 且 socket 已建立，`observe_helper_unit_peer` 成功关联实际 peer 和 systemd MainPID/InvocationID，结果仍 `loaded_code_verified=false`、`dispatch_allowed=false`。仅在该容器追加修改启动后 helper 源码，观察仍明确不认证加载代码，证明不能把磁盘内容或进程匹配当作运行代码身份。未验证干净安装的包、真实 job/start、进程重启或完整绑定；未打开生产派发。实验容器正常关机命令超时，随后容器列表确认一次性实例不存在。未操作真实设备服务。


## 75. helper 启动禁止写入新字节码

发现第71节只在新 bootstrap 提取时排除缓存，但 systemd 单元和包内 helper 命令此前均用 `python -I -m`，运行时仍可能写入新缓存。两处入口均改为 `-I -B -m`，新增渲染和包入口回归，相关 helper/包保护/bootstrap 142 项通过，ruff 与 shell 语法检查通过。`-B` 不阻止读取已有缓存，仍须第70节候选门禁与干净制品；既有系统 unit 不会自动修改，不做真实服务变更。该修复不证明执行代码身份或持续互斥，派发仍默认关闭，原包保护不变。


## 76. 隔离 systemd 跨进程包锁探测

新增 `accept-native-systemd-package-lock.py`，仅允许显式 disposable 标记、Linux root 和 PID1 systemd。在一次性 amd64 systemd 容器以 transient service 真实持有 `package_lease`，分别验证共享锁阻挡其他进程独占锁、独占锁阻挡共享锁，持有方正常释放后对方可获得锁，脚本两方向通过。该实验不调用 job 派发、不启动 Device 服务、不验证持久意图或崩溃恢复，也不证明 helper 加载代码身份；不能作为完整 job/start 互斥验收或开放派发依据。容器正常关机请求超时，随后列表确认实例不存在。新 job 默认拒绝和包保护未更改。


## 77. 生产派发拒绝与隔离失响应对账

更新 `accept-native-update-job.sh`：先调用未修改的生产派发门禁，要求拒绝且不创建 job-intent、不调用 runner；随后仅在已通过 disposable/systemd/root 空环境门禁的脚本进程内，以明确标记的测试替身继续历史真实 systemd 故障实验，不改产品代码或授权。在一次性 amd64 systemd 容器，历史 DEB `9b37b30...` 作为包保护候选，client runtime 安装 `cc01329` 三 wheel，`lost-response` 模式通过：真实生产门禁先拒绝；测试替身后 systemd 派发响应被模拟丢失，同 ID 不重派，原包钩子拒绝覆盖，缺 sender 对账拒绝，补回后显式对账幂等，门禁保留、active 不变、Device 服务未启动。该证据不是当前HEAD制品、不是成功升级，也不证明实际运行 helper 身份或真实 helper start/job 竞争；测试替身不能授权开放生产派发。容器关机请求超时，列表确认实例不存在。原包保护不变，未操作真实设备服务。


## 78. 排队 job 意图与真实 helper start

扩展隔离 `accept-native-update-job.sh` 的 source-drift 场景：先核验生产 dispatch 默认拒绝，之后仅在 disposable 脚本进程启用测试替身并让 systemd transient job 停留在 `ExecStartPre` 窗口。启动真实 helper daemon，通过 Unix socket 发送 `start`；在 job-intent 已持久化期间收到拒绝，未生成 start 意图，client unit 仍 inactive/MainPID=0。随后改变隔离测试的 source active，worker 拒绝执行包、持久门禁保留，并恢复测试 active。一次性 amd64 systemd 容器实验通过，使用历史 `9b37b30...` DEB 和 `cc01329` wheel；不是当前 HEAD 制品，不证明生产可派发、相反方向的真实 start/job 竞争、运行代码身份或完整故障恢复。容器正常关机请求超时，列表确认实例不存在；真实服务未操作，包保护不变。


## 79. 真实 helper start 持锁与反向竞争

在 disposable systemd job 验收新增 `start-lock` 场景：隔离注册时给 client unit 添加测试专用 `ExecStartPre=sleep 8`，以真实 helper daemon 的 Unix socket 发起 start，并等待实际 systemd `start-pre` 和持久 start 意图。此窗口独立尝试同一 registry 的独占 package lease，实际返回锁冲突；在脚本进程内显式替换派发门禁后，同实例 job 调用在实例管理锁处拒绝，未生成 job 意图、未调用 runner。真实 start 正常结束后独占 package lease 可获取。运行前真实生产派发门禁仍先拒绝。一次性 amd64 Linux systemd 容器通过；输入为历史 `9b37b30...` DEB 和 `cc01329` wheel，非当前 HEAD 制品。此证据区分实例锁拒绝与独立包锁探针，不证明 job 实际走到包锁、不覆盖 sender 崩溃或运行代码身份，也不授权开放派发。容器关机请求超时，列表确认实例不存在；真实服务和包保护未变。


## 80. 未知启动恢复证据与包更新门禁一致性

检查发现包更新对 start 结果只将缺失或 `unknown` 视为待恢复，损坏/未知状态会被错误放行；对恢复证据也未要求 `start_replayed` 严格为 `false`。先以五个负向用例复现，再使更新路径对结果采用 helper 相同的状态白名单，并复用 helper 的恢复证据校验；不完整、错误或无法读取的恢复证据继续阻止包更新。合法未重放恢复后仍需满足既有停止状态和其他包事务门禁。相关测试 171 项通过，完整 Device 回归 1020 passed、3 skipped，ruff 及 diff 检查通过。测试为本地夹具，不证明实际 sender 崩溃、运行代码身份或当前制品/真实 systemd 验收；生产 job 派发保持关闭，原包保护不变。


## 81. 真实 helper 启动 guard 异常退出隔离实验

在一次性无网络 amd64 systemd 容器中，真实 helper 经 Unix socket 启动已登记测试实例，测试专用 ExecStartPre 保持 systemd 启动等待。确认生产 job 门禁拒绝后，按 systemd MainPID、进程父子关系及 guard 命令行识别该隔离 daemon 的 guard，向其注入 SIGKILL；实际 start 结果为 unknown，sender 回执缺失。等待 systemd Job 终态后，真实 helper 拒绝 resolve-running 和不同 ID 的新 start，未生成恢复或新启动意图。使用历史 DEB `9b37b30...` 与 `cc01329` wheel，不是当前 HEAD 制品；实验不覆盖 daemon 重启、resolve-stopped、包应用或运行代码身份，不证明可恢复或允许派发。首次实验因容器不提供 `/proc/.../children` 在注入前失败；改用 `ps` 父子关系并核验 guard 命令后在干净容器成功。两次实例均经关机请求并核实不存在；相关 133 项、shell 语法与 diff 检查通过。真实设备服务及包保护未改变。


## 82. guard 崩溃后的真实隔离 daemon 重启

延续第81节场景，在 guard 异常退出且真实 systemd Job 已终态后，仅对一次性容器内测试 helper 执行停止；保留未知台账和遗留 socket，调用管理员 `recover_socket` 显式检查并移除孤立 socket，然后启动同一测试 unit。通过新连接验证同 ID 仍为 unknown 且 `replayed=false`，原结果字节不变，恢复和新 ID start 仍被拒绝、没有新的意图或恢复证据。实验使用历史 `9b37b30...` DEB 与 `cc01329` wheel，不是当前 HEAD 制品；不证明加载代码身份、宿主重启或可解除阻塞。真实生产派发门禁先拒绝；实例关机请求超时后再次正常关机并确认不存在，未操作真实设备。


## 83. 候选 helper 的校验字节执行原型

新增 `run_verified_helper_candidate`：仅 Linux root、候选自身 Python 隔离模式且禁止写字节码时，对已发布候选证据及完整快照做前后校验，从清单校验后读取 `__init__.py`、`management_lock.py`、`native_helper.py` 的具体字节，直接从这些字节编译加载并调用候选 `serve`，不通过之后的磁盘模块导入。测试模拟最终校验后磁盘替换，验证仍执行先前校验字节，并覆盖解释器/隔离模式/字节码及源码漂移拒绝。该入口仅供可信管理员启动器使用，未接入现有独立 bootstrap、unit 或生产派发；验证器自身、解释器/stdlib 原生依赖的可信来源、peer 与启动实例的持续关联、重启和锁交接仍需闭环。模块替换设计仅适用于专用单线程启动进程，不适合复用的管理进程。152 项相关测试通过，完整 Device 1025 passed、3 skipped，ruff 与 diff 检查通过；尚无真实候选进程或准确新制品验收，不能声称运行身份已证明。生产派发保持关闭，包保护不变。


## 84. 候选 daemon 入口与隔离启动

`native_bootstrap` 增加仅接受 registry、socket、group 和候选版本的实验 daemon 入口，固定从 registry 的 `bootstrap-candidates` 读取候选，调用第83节校验字节执行原型；Linux 包构建时将该模块纳入独立 bootstrap，原有稳定 helper 入口和 unit 不改、不自动激活候选。一次性无网络 amd64 systemd 容器以锁定 CPython 和当前源码创建独立候选，候选 snapshot SHA256 `8d12cc4e0a7988e4707dc8c8079b1b038a0ae179b7414a83ec8322747f75b5e5`、helper SHA256 `bfa1e31ebb262c60f78a3dce0dcc8d9a2079e24c187fd63eacb12e21c5522e90`；真实候选解释器以 `-I -B -m device_runtime.native_bootstrap` 启动测试 daemon，socket 返回预期拒绝响应。停止该隔离 daemon、显式恢复 socket 后改写候选锁模块，新启动失败且 socket 未创建。首次准备因容器 sh 不支持花括号展开而失败，候选尚未创建；改用逐文件复制后完成。实验实例关机后确认不存在。完整 Device 1027 passed、3 skipped，ruff、shell 语法和 diff 检查通过。实验启动器自身从候选解释器导入，尚未建立独立信任锚、与 socket peer 的连续身份或正式制品验证；未接入生产部署，派发仍关闭，不证明完整身份绑定。


## 85. socket peer 观察作用域

新增 `scoped_helper_unit_peer`：在 socket 的 SO_PEERPIDFD 仍持有期间读取固定 helper systemd unit，要求 MainPID 与实际 peer 一致；作用域正常退出时再次读取并要求整个 unit 观察相同，pidfd 退出检查及关闭由内层作用域完成。调用异常时仍关闭 pidfd；测试覆盖调用期间 InvocationID 漂移和异常清理。原 `observe_helper_unit_peer` 改为复用该作用域，保持原只读返回契约。该能力只供只读观察：退出时才检测到漂移，不可在作用域内提交不可逆动作；也不证明运行代码、启动器来源、阻止重启或授权派发。相关153项、完整1027项通过、3项跳过，ruff 和 diff 检查通过；本节仅单元测试，未在真实systemd验证新作用域。生产派发保持关闭。


## 86. 真实候选 daemon 的 peer 作用域重启实验

一次性无网络 amd64 Linux systemd 容器使用锁定 CPython 和当前 `2e843f7` 源码构建独立候选，快照 SHA256 `24828d48bcb1d9e07daf9a1ebe4c3782639462eca426200faac214d7fbbb90cf`，helper SHA256 `1430db765effeb7d5eb90d41f691ed1c7300d3bd57c7ac2abcca5094e5e644b9`。以真实候选 daemon 和固定测试 unit 建立 Unix socket，`scoped_helper_unit_peer` 对真实 SO_PEERPIDFD/systemd 状态正常通过；作用域内只停止并重启此隔离测试 unit，显式恢复遗留 socket，旧作用域因 unit invocation 改变而拒绝，新连接则取得不同 PID 并通过。实验确认进程身份漂移能被只读观察发现，不证明已加载源码、自举信任、不可逆动作内的互斥或生产派发安全。关机请求超时后再次正常关机，确认一次性实例不存在；未操作真实设备。


## 87. 独立 bootstrap 执行候选源码

候选启动原型不再允许候选 Python 在校验前导入候选验证器：入口要求固定独立 `/opt/ai-device/bootstrap/0.2.2-native1/runtime` 的 Python、base_prefix、bootstrap/helper 模块路径及管理员可信路径，使用 `-I -B`，且候选树与 bootstrap 互不包含；随后才校验候选并从校验过的内存字节执行。新增候选解释器自举拒绝回归。一次性无网络 amd64 systemd 容器使用锁定 CPython 和当前源码在该独立路径构造 bootstrap、独立快照；候选 Python 自举明确拒绝且不建 socket，独立 bootstrap 可启动候选，真实 peer pidfd/systemd 观察通过，测试 daemon 重启使旧作用域失效、新 peer 可观察。候选快照 SHA256 `76596ee7faeb02e7712e7476ac60dc66a4dea14314cbb26856081f70d4847ec1`。容器关机后确认不存在。完整 Device 1028 passed、3 skipped，ruff 和 diff 检查通过。此实验假设本机管理员可信、独立 bootstrap 安装可信；尚未建立正式制品来源及升级信任链，未将启动证据与 peer 连续身份作为可授权的事务闭环，测试 unit 非生产部署。生产派发保持关闭，原包保护不变。


## 88. 冻结源码的独立 bootstrap 开发制品来源验证

以干净 `348dd5c35f633803d3e8f0b62da1c25c401bcbda` 离线构建三组件 wheel。Device wheel SHA256 `fa73bca3c46425a4ce39e1d371c8f99de5cd1f4f182486da4dcfe3673a89af7d`，Bash `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`，FS `b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`；Device wheel 内 `__init__.py`、`native_helper.py`、`native_bootstrap.py`、`management_lock.py` 与冻结 Git 源码逐字节一致。使用已有锁定 CPython、uv 和本地依赖组装 `build/native-payload-p1-348dd5c`，在一次性无网络 amd64 Linux 容器构建未签名 DEB，SHA256 `b41021873d981c1dc224ee4ac05ae160e84d4b6e4f0b05bea985620bfa279225`，109773810 bytes；空容器实际 dpkg 安装后，四个独立 bootstrap 模块与冻结源码逐字节一致，安装 build-inputs 与组装清单一致，bootstrap 无字节码且 `-I -B` 导入路径正确。以该已安装 bootstrap 创建候选并启动测试 systemd daemon，真实 Unix socket peer 与 unit 观察通过；候选快照 SHA256 `1d945ccb4715e96e48220bf13e2b613eabe4e091234691faed3b0820c57e6dcf`，观察结果仍为代码未证明、派发未授权。首次 `--no-build-isolation` 因本机缺 hatchling 失败，改为使用本地缓存的离线隔离构建成功，无网络依赖。DEB 仅保存在容器、未签名未发布；容器正常关机请求超时后再次关机并确认不存在。本证据不覆盖 RPM、目标主机、正式信任根/发布签名、运行身份事务绑定或升级成功；原包保护及生产派发关闭不变。


## 89. daemon 崩溃与 guard 继承锁的 systemd 对照

在一次性无网络 amd64 systemd 容器，以历史 `9b37b30...` DEB 和 `cc01329` wheels、真实 helper IPC/start 及测试 `ExecStartPre=sleep 8` 制造启动窗口，只向该隔离测试 unit 核对过的 MainPID 注入 SIGKILL。默认 helper unit 的 systemd `KillMode=control-group` 场景中，daemon 退出后独占包锁已可取得，sender 回执缺失、启动意图保留，客户端处于 activating/auto-restart；实验未能证明 guard 在 daemon 崩溃后存活，原假设不能成立。对照实例仅在测试 unit 注入 `KillMode=process`：同样故障后 guard 持有包锁直至发送完成并产生匹配回执，显式恢复 socket 与重启测试 daemon 后，同 ID 不重放，resolve-running 记录 confirmed_running；生产派发默认拒绝未变。两个实例均关机后确认不存在；相关134项、shell 语法和 diff 检查通过。对照不是生产修复：改变 KillMode 还需定义正常停止、失控或残留 sender、重启与锁竞争、服务管理及异常恢复；当前生产 unit 未修改，正式派发保持关闭。两次使用历史制品，不代表当前 HEAD 制品或目标设备通过。


## 90. helper daemon 退出时 guard 生命周期修复

新建 helper unit 显式 `KillMode=process`、`Restart=no`，使 systemd 不因 daemon 退出自动终止仍持锁的 start guard；已有 unit 不自动覆盖、重启或迁移。使用含该未提交源码的测试 Device wheel SHA256 `d2432a074804b2093708bc36ef3a659a897e6c0a5bb24f1651de18f9ddca32d4`，历史 `9b37b30...` DEB 只作为隔离包保护 fixture，在一次性无网络 amd64 systemd 容器分别验证 daemon SIGKILL 与正常 systemctl stop：旧 guard 窗口包锁拒绝独占，显式恢复 socket 并重启 daemon 后新 ID 拒绝且不产生意图；sender 回执出现后同 ID 不重放，显式 resolve-running 写 confirmed_running。正常停止使用登记前绑定的有限期测试 runner，避免真实客户端因缺中心自动重启；因此不是目标设备验收。早期正常停止测试先因对 inactive unit 使用 reset-failed 中断；随后尝试直接改 ExecStart 被完整性检查拒绝，另一次 runner shebang 链接被可信路径检查拒绝，修正后在全新容器通过。所有实验容器关机后确认不存在。完整 Device 1028 passed、3 skipped，ruff、shell 语法与 diff 检查通过。若 guard 挂起、异常退出或回执缺失，仍须保持 unknown 和显式诊断；`KillMode=process` 不承诺 systemd 回收残留 guard，也不证明运行身份或事务授权。生产派发继续关闭，包保护不变，正式制品及目标主机尚未验收。


## 91. guard 持续占用与缺回执回归

新增真实 guard 子进程和受控阻塞 sender 的本地测试：guard 同时继承执行锁和包锁，父侧描述符关闭后，对旧 unknown 的停止对账及新 ID 启动仍被拒绝，独占包锁不可取得；释放测试 sender 后，只有无关回执时仍不得对账。测试通过 finally 释放并回收子进程，不以超时为完成依据。完整 Device 回归 1029 passed、3 skipped；ruff 和 diff 检查通过。此测试模拟 sender 持续阻塞，不是 SIGSTOP guard、systemd 或宿主重启验收，也不证明实际运行代码身份、事务交接或可安全派发。


## 92. 暂停 guard 后的持锁与显式对账

新增本地故障测试，对真实 `Systemd.start` 创建的 guard 子进程发 SIGSTOP，并用 waitpid 确认处于暂停状态；随后让受控 sender 退出。父侧描述符已关闭时，guard 仍持有执行锁与包锁，匹配 sender 回执尚未写入，停止对账、新 ID 启动和包锁独占均被拒绝。对测试捕获的该子进程发 SIGCONT 后，guard 回收 sender、写入匹配回执，显式 resolve-stopped 才确认终态。finally 恢复暂停进程并回收测试线程。完整 Device 1030 passed、3 skipped，ruff 与 diff 检查通过；这是本机子进程实验，adapter 为测试替身，不是隔离 systemd、宿主重启或真实设备验收。生产派发仍关闭；Unix socket 传递文件描述符或 inode 匹配本身不能证明预期 flock 正被持有，也不能仅凭单次 peer 存活检查授权不可逆操作，启动实例与事务锁连续绑定仍待完成。


## 93. 隔离 systemd 中暂停真实 guard

扩展 disposable `accept-native-update-job.sh` 的 `start-guard-pause`：先确认生产派发默认拒绝；登记有限无网络测试 runner，并在真实 systemd start-pre 窗口核对 helper MainPID、guard 直接父子关系及命令行后暂停 guard，核对 `/proc` 停止状态，再仅对该隔离 daemon 注入 SIGKILL。显式恢复 socket、重启 daemon，早期新 ID 拒绝且无新意图；真实 systemd job 已终结时 guard 仍暂停、回执缺失、独占包锁冲突、resolve-running 拒绝。恢复 guard 后匹配回执出现，同 ID 不重放，显式 resolve-running 确认运行。finally 恢复已识别的测试 guard；两次无网络 amd64 systemd 容器均 exit 0，日志 `20261027-182407-08a029.log`、`20261027-182633-34474d.log`；关机调用超时后以列表确认两个 --rm 实例已退出。测试使用历史 DEB SHA256 `9b37b30b0f915bcbd15dd8d2475559d2e98648438e1ca5f98d4fb63bcab9d00c` 和修复 wheel `d2432a074804b2093708bc36ef3a659a897e6c0a5bb24f1651de18f9ddca32d4`，非正式制品；helper 回归110通过，shell语法和diff检查通过，完整回归沿用前一检查点1030通过3跳过。此实验不证明宿主重启、真实设备、启动代码身份或事务授权；新派发持续拒绝、包保护不变。


## 94. 显式候选 bootstrap unit 入口

新增 `deploy --candidate-version`：限定固定独立 bootstrap Python，要求现有 registry 的包锁和已发布候选，独占包锁内复核候选后只创建全新 unit；unit 使用 `-I -B -m device_runtime.native_bootstrap` 和精确版本，启动时现有入口重新核验并执行内存中的候选字节。默认无版本仍走原 unit；不覆盖已有 unit、不自动 reload、启用或启动。测试覆盖错误解释器、非法版本、候选失败、包锁冲突、旧 unit 不覆盖和显式成功；完整 1035 passed、3 skipped，ruff/diff 检查通过。本项仅提供受控启动路径，部署时检查和启动间可能变化，由启动时重新检查；未用本次制品执行真实 systemd 干净安装，不证明实际 socket peer 身份或事务锁连续性，生产派发保持关闭。


## 95. 当前制品候选 unit 与实际 socket peer

干净提交 `2e526edad9be922794f437160f8c097f8c14f3f5` 离线构建 Device wheel SHA256 `fbd637c12e4e8abc89cbce6b8962ae23a4523bef1d7dda32c5976d821aca5cc5`；其 `__init__`、helper、bootstrap、management_lock 四模块逐字节等于提交，helper SHA256 `a16b7ea80397fa7ef8a3dc48edb663cffa8970126878896a5d6f40a6139d290b`。沿用锁定依赖和 uv 组装 payload；无网络 Ubuntu 24.04.5 x86_64 systemd 容器构建未签名 DEB SHA256 `a9883d35190ede5a51ee468fa237634d26adedaebb4996a63466d72a21a795d5`，空环境 dpkg 安装后核对独立 bootstrap 四模块摘要。通过包内稳定 helper 命令显式发布候选并 deploy unit，候选快照 SHA256 `d26b47c7482ca022cb22c41115ec71d539fa40677b4c26df9d6e96f1e57c43a1`；deploy 报告未启用、未启动，随后测试显式 daemon-reload/start，实际 unit 为固定独立 Python 的 `-I -B -m device_runtime.native_bootstrap` 和精确版本。真实 AF_UNIX peer 的 pidfd、unit MainPID 与 InvocationID 对应，返回 `loaded_code_verified=false`、`dispatch_allowed=false`；未知实例查询拒绝。只在隔离环境停止 daemon 并显式恢复 socket 后修改候选锁模块，unit 变 failed、无 socket，直接启动报告 `runtime snapshot drift`。初次安装后摘要命令因 `/bin/sh` 不支持花括号展开失败，逐文件核验后继续；reset-failed 对已卸载 unit 失败不作为通过依据。容器关机请求超时，后列表确认 --rm 实例不存在；DEB 仅容器内、随实例退出，不是正式制品。此结果不证明运行代码身份已与 peer/事务锁连续绑定，不证明目标设备、RPM、升级或宿主重启；生产派发及包保护不变。完整源码回归沿用第94节1035通过3跳过，本轮未重跑。


## 96. 候选 unit 固定快照摘要

修复候选 unit 仅固定版本名、未固定部署时内容的问题：deploy 在独占包锁下读取已验证候选的 snapshot SHA256，并写入新 unit 的 `--snapshot-sha256`；bootstrap 在执行候选前验证摘要格式与已验证候选相等。当前 unit 不会因同版本候选及其自有记录一起变更而静默执行另一快照。旧 unit 不改写，原实验命令允许省略摘要且不构成受控 unit；已有候选不覆盖、不自动启用或启动。测试覆盖匹配、不同摘要、非法摘要及 unit 生成，相关148通过、完整1037通过3跳过，ruff/diff检查通过。本次尚未进行新制品隔离systemd验收；unit 摘要不证明当前socket peer加载的代码、事务锁连续性或防止可信管理员修改 unit；生产派发仍关闭，包保护不变。


## 97. 固定候选摘要的隔离 systemd 对照

以干净 `38b63599c782e2015b09d4a24b5720b0c932c275` 离线构建 Device wheel SHA256 `352394c7881a83fa7a016ddca6eef88f738e1642db7ceac14a1f53d186f23d51`，四个 bootstrap 模块逐字节等于提交；用锁定依赖构建未签名 DEB `a60910a675dd7b4384f691ae88caad1e927b3decffd9d8ffc97b7e1eb8c096ba`，在无网络 Ubuntu 24.04 amd64 systemd 容器全新安装，核对 bootstrap 模块摘要。发布 `pinned` 候选、用稳定命令 deploy，仅生成未启用 unit；显式启动后 unit 固定摘要 `9abdcd7ce956e92f4068213b8fd680ba8067ec60c6c587307876c3350b78622e`，真实 peer 与 MainPID/InvocationID 对应，仍报告代码未证明、派发不允许。首次立即查询 socket 早于就绪失败，稍后核实 unit active/socket 存在再查询成功。隔离停止 helper、显式恢复 socket 后，以不同源码目录生成相同版本名、完整自洽的另一个候选 `c53537d7cc9e44c46419b9c76d52a60250206f3e093c6c60ed46e57fb70ff4db`，先独立 read 成功，再仅在容器内交换：原 unit failed、无 socket，直接入口报告 `candidate snapshot differs from unit`。恢复原候选后，同一 unit 启动成功并观察到新的 peer/invocation。容器关机请求超时后列表确认 --rm 实例已退出，DEB 未保留。此实验只验证 unit 对自洽候选替换的拒绝，不证明当前 peer 执行代码的连续身份、事务锁、目标设备或正式发布；派发保持关闭。完整回归沿用第96节1037通过3跳过，本轮未重跑。


## 98. Linux peer 身份反例与监督器方案评审

在网络隔离的一次性 amd64/Rosetta Linux 6.18.15 容器执行 `scripts/probe-native-peer-identity.py`：fork 前 socketpair 的 SO_PEERCRED 指向创建者，而数据报 SCM_CREDENTIALS 指向实际子发送者；父进程 bind、子进程 accept 时客户端 SO_PEERCRED/SO_PEERPIDFD 指向父进程；子进程 bind 时 pidfd 对应并能观察其退出；创建者 fork 后代由后代 accept/响应时，客户端 peer 仍指向创建者。最终 `PEER_PROBE_OK`，日志 `20261027-200712-01d01c.log`，实验容器退出；脚本 ruff 通过。详细反例、设计修正和门禁见 `native-helper-peer-protocol-review.md`。证据不证明候选代码执行身份、真实 systemd、宿主重启或事务围栏；仍须验证消息凭据和描述符继承以及监督器死亡后的恢复。未实现监督器，不改产品 default-deny 或包保护；完整回归沿用第96节，本轮未重跑。

补充：同一探针在 fork 前创建的 `SOCK_STREAM` socketpair 上，开启 SO_PASSCRED 后用 recvmsg 收到子进程发送的单字节消息及该子进程 SCM_CREDENTIALS，而 SO_PEERCRED 仍为创建者；`PEER_PROBE_OK`，日志 `20261027-200909-30634d.log`。仅验证单次简单消息，不证明多发送者交错、完整帧认证或具备特权的负向情形。


## 99. 消息边界、FD 继承与监督器退出隔离探针

`scripts/probe-native-message-boundaries.py` 在无网络一次性 Linux 6.18.15 amd64/Rosetta 容器中验证：SEQPACKET 两个子发送者各记录的 SCM_CREDENTIALS、数据与附属信息截断标志、意外 SCM_RIGHTS 交付及关闭、FD 在 fork/exec/pass_fds 间的继承差异；模拟监督器退出后，其子进程持有的通道仍可发送。`MESSAGE_BOUNDARIES_OK`，日志 `20261027-201228-eeb8a8.log`，ruff通过。评审及局限见 `native-helper-peer-protocol-review.md`。尚未实现生产接收器、监督器或真实 systemd 故障恢复；不证明 namespace、特权伪造、全错误路径 FD 关闭、锁/intent 或 P1 完成，保持派发关闭。完整回归未重跑。


## 100. 隔离逐记录认证原型

新增 `scripts/probe-native-strict-record.py`，无网络隔离 Linux 验证受限 SEQPACKET 记录接收：正确测试子进程通过，错误 PID、超长截断和 SCM_RIGHTS 被拒绝，测试收到的 FD 被关闭且数量不增加。`STRICT_RECORD_PROBE_OK`，日志 `20261027-201451-fa63ab.log`；ruff通过。原型仅凭测试父进程提供 PID，不绑定 pidfd、监督器或 systemd，未涵盖所有凭据/截断/并发故障，绝不接入生产；详细限制见 `native-helper-peer-protocol-review.md`。生产派发保持拒绝，P1未完成。


## 101. 受控父子进程与监督器退出原型

新增隔离脚本 `scripts/probe-native-supervised-start.py`：监督器 fork 子进程、持有子 pidfd、核对 ready 的 SCM_CREDENTIALS 与直接子进程，子进程检查继承的监督器 pidfd；监督器存活时报告 A、先退出后报告 R。Linux 6.18.15 amd64/Rosetta 无网络容器 `SUPERVISED_START_PROBE_OK`，日志 `20261027-201815-c7ac14.log`，ruff通过。仅验证固定时序，不执行真实候选、不使用 systemd 或事务锁；一次存活检查不能防检查后死亡，不能证明真实身份或失败关闭。详见 `native-helper-peer-protocol-review.md`。生产派发保持拒绝，完整回归本轮未跑。


## 102. 实际 bootstrap 字节交接设计和 flock 实验

基于实际 `run_verified_helper_candidate`、`serve`、start/dispatch 代码，在 `native-helper-peer-protocol-review.md` 描述父校验内存字节、受控 fork、pidfd/消息身份、daemon 锁、外部连接及持久 intent 的拟议交接和明确验收门禁；尚未实现或独立评审。`scripts/probe-native-daemon-lock-handoff.py` 在隔离 Linux 6.18.15 amd64/Rosetta 验证：独立 open 冲突；父关闭后子继承 FD 保持排他 flock；子关闭后锁释放。`DAEMON_FLOCK_HANDOFF_OK`，日志 `20261027-202218-ee55c4.log`，ruff通过。未验证显式解锁、真实 daemon、systemd、崩溃或锁文件漂移；原生包保护和生产派发不变，P1未完成。


## 103. 提取 bootstrap 已验证内存字节

将 `run_verified_helper_candidate` 中独立 bootstrap 环境、snapshot 和候选模块字节检查提取为内部 `_verified_helper_bytes`；原入口仍在同一进程调用它、从返回字节创建模块并执行原有 `serve`，未新增公开执行入口、fork 或派发授权。扩展既有漂移回归，在最终检查后替换候选路径，断言返回内存字节仍为已验证原值、路径为替换值且执行仍使用原值。bootstrap测试33通过；Device完整回归1037通过3跳过（日志 `20261027-202402-576004.log`），ruff通过。此改动仅建立内部校验边界，不证明运行进程身份或跨崩溃事务；`_require_proven_job_fence` 仍无条件拒绝，P1未完成。实际候选的独立bootstrap/systemd动态回归本轮未做。


## 104. 重构后实际独立 bootstrap 与候选隔离验证

以干净提交 `0dbcf8d21f6fa3561b083ceea596ba17fc989728` 离线构建 Device wheel SHA256 `8041dd7192bcddb59ed7433625af6796328ca40cbcb346edbf7e32d5adf72680`，四个 bootstrap 模块逐字节与源码一致；使用原锁定运行时及依赖组装并在隔离无网络 Linux 构建未签名 DEB `909d10088315e1e64bfb9fdaad264ee41c6b71797d1032e43752c53a494c1d2a`。首次使用系统 Python 误加载另一个项目环境的 `device_runtime`，payload 组装因依赖兼容检查失败，残留目录保留；使用当前 Worktree `uv run --no-sync` 与新目录重试成功。

在另一个无网络、全新 Linux amd64/Rosetta 容器安装此 DEB，核对独立 bootstrap 源文件 SHA256 `d596e3ee06f1248df048a0cb5447d06e3cb87c20a3f1952e620a48f58aa7fbd1`；从包中独立 bootstrap 发布并读取实际候选 snapshot `61da94558bc53e4b6b70895a1798ad313b9ac8352aeb0999de23f753bbb22c6b`；错误固定摘要拒绝且未创建 socket，正确摘要直接启动后通过 SO_PEERCRED/SO_PEERPIDFD 观察到 socket peer 与启动 PID 一致，畸形请求被拒绝。`ACTUAL_BOOTSTRAP_OK`，脚本 `scripts/accept-bootstrap-byte-extraction.sh`，日志 `20261027-203008-5c0c06.log`；测试进程仅在一次性容器内结束。此结果不证明 systemd invocation、可信监督器、实际请求处理者、包事务互斥或故障恢复，生产派发保持 default-deny；正式签名、RPM及P1整体验收未完成。


## 105. daemon 租约描述符与真实 fork 隔离验证

`_daemon_lease` 在原有同一加锁上下文中返回实际 FD，现有调用者不变；明确 FD 自身不证明锁存续或授权。新增单测核对 FD、并发租约拒绝和退出关闭；`scripts/probe-native-daemon-lease.py` 在无网络 Linux 6.18.15 amd64/Rosetta 容器加载当前源码、以实际 `_daemon_lease` 获取锁后 fork，验证父上下文关闭后子仍阻止独立租约、子关闭后新租约可取得。`ACTUAL_DAEMON_LEASE_FORK_OK`，日志 `20261027-203530-988a12.log`；相关测试149通过，完整Device回归1038通过3跳过，ruff通过。此改动未新增监督器、候选fork或公开入口，不证明故障恢复/peer/intent；生产派发仍拒绝。实际包及systemd尚未对本改动构建验证。


## 106. 真实候选字节与 daemon 租约的隔离 fork 机制实验

新增 `scripts/accept-native-verified-fork.sh`，在无网络一次性 Linux amd64/Rosetta 容器安装第104节 `0dbcf8d` DEB，再仅在容器内将当前 `e7d146a` 的四个源码模块覆盖到独立 bootstrap 并逐字节比较；这不是 `e7d146a` 的准确重建包。真实 bootstrap 发布并检查候选、调用 `_verified_helper_bytes` 后，在容器内把候选 helper 路径替换为立即失败的内容；父进程持真实 daemon 租约 fork 子进程，子进程仅执行继承的验证字节并临时调用内部 `_serve`。观察 socket peer 是子进程，畸形请求拒绝；父关闭租约后另一个租约被拒绝，终止仅本实验子进程并回收后 pidfd 表示退出、租约可重新获取。`VERIFIED_FORK_FIXTURE_OK`，日志 `20261027-203953-8d6757.log`，容器一次性退出。

**限制**：脚本为机制试验，特意绕过生产 `serve` 锁包装，不是受控生产入口；没有启动握手、监督器死亡并存活子进程的交易故障处理、systemd invocation、所有 FD 清理、锁漂移或持久 intent/包事务验证。候选路径替换仅为容器内对照，不应在真实安装中使用。未开放派发、未验证 P1 完成。


## 107. daemon 锁获取窗口漂移拒绝与剩余反例

`_daemon_lease` 加锁后核对 fd 为非组/其他可写普通文件，当前可信路径同为普通文件且 dev/inode 与已锁 fd 一致；获取过程路径缺失或替换则拒绝进入租约。回归模拟 flock 后替换路径，验证拒绝；另覆盖锁路径为目录。相关测试151通过，完整Device回归1040通过3跳过，ruff通过。

`scripts/probe-native-daemon-lock-drift.py` 在无网络一次性 Linux 容器故意在成功获取锁后重命名锁路径，再通过同一路径重新取得另一 inode 的锁，确实同时得到两把不同锁（`POST_ACQUISITION_SPLIT_REPRODUCED`，日志 `20261027-204550-302f47.log`）。这是可信管理员/系统误操作边界下的真实可靠性缺口，不通过该项修复宣称已解决；不得据此开放派发。运行期路径漂移处理、守护进程间相互隔离、受控启动与持久 intent 闭环仍待设计和验证。


## 108. 目录锁锚点的隔离边界

`scripts/probe-native-directory-anchor.py` 在无网络 Linux 6.18.15 amd64/Rosetta 容器验证：对 registry 目录 flock 后，仅替换目录内锁文件，另一次对同目录加锁仍冲突；但重命名整个 registry 并创建新目录后，两目录可分别独占加锁。`DIRECTORY_ANCHOR_PROBE_OK`，日志 `20261027-204946-4bce53.log`，ruff通过。目录锁不能消除目录或其祖先被替换、挂载变化及旧 helper 不参与的问题；未修改生产锁协议或派发。下一步需定义所有参与者共同遵循的部署/恢复稳定锚点与失败条件，不以此实验声称已解决 P1。


## 109. 用户确认维护前提下的 daemon 目录锚点

用户确认采用 registry 稳定维护前提：helper运行或存在未终态事务时不替换registry、必要祖先及挂载；不能确认则拒绝启动/恢复，迁移需管理员维护窗口、旧入口停用和事务收口，不自动迁移清理。`_daemon_lease` 先对可信registry目录取非阻塞独占flock、核对打开FD与路径dev/inode，再获取原daemon文件锁；`serve`与`recover_socket`既有调用自动使用两锁。测试覆盖目录内锁文件替换时仍冲突、获取时目录替换拒绝、旧文件锁竞争，以及隔离Linux fork后父关闭子进程仍保持租约（日志 `20261027-210107-8b2b2f.log`）。相关154测试、完整Device 1043通过3跳过（日志 `20261027-210141-099b46.log`）、ruff及diff检查通过。

这是daemon和socket恢复的局部锚点，不解决管理员违反稳定前提导致整个目录/祖先/挂载替换；旧helper必须事先手动退休。更新器/包事务入口尚未形成共同目录协议，受控启动、活peer绑定、故障恢复、真实systemd与准确制品尚未验收。保留原文件锁，生产派发继续default-deny；P1未完成。


## 110. daemon 与更新器的持久意图交接设计

基于现有普通UID socket权限、daemon长期registry目录独占锁、实例锁→package锁及job close/retire路径，新增 `native-helper-dispatch-handoff-design.md`：候选方向为受控daemon在严格root-only分支协调锁内持久intent，worker按锁序复核后独立执行，更新器不重新取得daemon长期目录锁；明确故障矩阵、同ID一致性、daemon退出和跨崩溃围栏、普通用户权限及验收门禁。当前 `dispatch_job` 已有intent分支直接返回记录，不能据此声称同ID完整请求一致性；新设计须核验。此为设计草案，不新增IPC、不开启派发、不修改生产代码；受控启动、实际peer和systemd故障、准确制品和P1整体仍未完成。用户确认的registry稳定维护前提不替代运行身份或事务协议。


## 111. job intent fsync 失败不派发的限定回归

为 `dispatch_job` 的 dormant 测试路径注入 intent 文件 fsync 和目录 fsync 错误：两种情况均传播异常且未调用 systemd runner；目录 fsync 失败后 intent 仍可见时，同 ID 仅返回已记录且不重派。仅测试中 monkeypatch 关闭 default-deny；生产门禁未变。job 测试17通过，ruff通过，日志 `20261027-211245-8b6320.log`。静态核查 `Updates.apply` 在调用包管理器前 `_atomic` 写 apply intent，但本轮未对该路径注入故障。

修正第110节草案：对严格保证 `_save_new` 成功返回后才派发、且文件系统持久性和所有参与者遵守该顺序的路径，fsync 失败后 intent 可能消失并不单独证明外部动作已执行；不能无条件断言必须增加独立durable围栏。全入口、成功返回后的实际崩溃/文件系统保证、响应丢失和未知对账尚未验证，不可因此解除阻塞或开放派发。完整回归本轮未跑。


## 112. start 与 apply intent 的 fsync 故障注入

在真实 `_save_new` 文件与目录 fsync 处分别注入错误：helper start 两种情况下均未调用 fake sender；目录 fsync 失败且 intent 可见时，同 ID 返回 unknown、不重放，新 ID 拒绝。Debian apply 分支以测试替身模拟运行时准备与包操作，两种失败均未调用 fake 包管理器、active 不变；目录失败且 intent 可见时同 ID 仅观察、不重放。此前 dormant job 测试仍保留。针对性 helper/update/job 共190通过，相关测试 ruff通过；Device 完整回归1049通过、3跳过（日志 `20261027-212038-c7ec3d.log`）。

限制：测试运行于本机进程并注入异常，非真实 systemd、dpkg、进程崩溃或断电；Debian runtime 准备为替身，故不证明快照自身持久化、外部参与者一致遵守顺序或全部入口安全。未开放生产派发，受控身份及事务闭环仍未完成。


## 113. 继承锁显式解锁的隔离反例

一次性无网络 Linux 6.18 amd64/Rosetta 容器运行 `scripts/probe-native-daemon-explicit-unlock.py`，实际使用 daemon 目录锚点和文件锁。父子共享 open-file description 时，子进程先 `LOCK_UN` 文件锁，父 FD 仍有效但竞争者可单独取得文件锁；目录锁仍阻止完整 daemon 租约。子再 `LOCK_UN` 目录锁后，父两个 FD 仍有效，竞争者可同时取得完整租约。输出 `INHERITED_EXPLICIT_UNLOCK_CONFIRMED`，日志 `20261027-212307-33dd04.log`，脚本 ruff通过、一次性容器已退出。

此为受控本机进程协作的可靠性反例，不将其描述为恶意 root 防护。必须约束所有持有相同锁描述的参与者不得提前解锁，并设计退出/FD传递/故障语义；有效 FD 不证明锁存在。尚未实现生产监督器，也未验证 systemd invocation、所有参与者、其他内核或宿主崩溃；生产派发仍关闭。


## 114. daemon 租约在父进程直接退出后的存续

`probe-native-daemon-parent-exit.py` 在一次性无网络 Linux 6.18 amd64/Rosetta 容器通过实际 `_daemon_lease` 取得双锁，父 fork 后 `os._exit`、不执行上下文清理。父确认退出而子仍存活时，独立完整租约仍拒绝；子退出后租约可取得。`DAEMON_PARENT_EXIT_WORKER_LEASE_OK`，日志 `20261027-212458-725f1d.log`，脚本 ruff通过。

该实验只说明继承锁的内核行为；父死亡不会自动使持锁子退出，不验证 systemd、socket/peer、已校验候选、pidfd握手或生产监督和故障回收。仍须设计可信进程关系及故障关闭，不可用此反例开放派发。


## 115. 父 pidfd 与实际 daemon 双锁的限定时序

`probe-native-daemon-pidfd-exit.py` 在一次性无网络 Linux 6.18 amd64/Rosetta 容器中，让监督父持实际 `_daemon_lease` 并把自身 pidfd 随 fork 交给子。子就绪后父退出，子观察 pidfd 变为可读并主动退出；随后独立完整租约可取得。输出 `PIDFD_PARENT_EXIT_DAEMON_LEASE_OK`，日志 `20261027-212613-22a6f9.log`，脚本 ruff通过。

仅证明该受控单次时序，不保证死亡即时可见、挂起/阻塞子进程退出、不可逆操作被取消或所有FD正确收口；不含真实 systemd、运行候选、socket处理者和事务。pidfd检查后仍存在死亡竞态，生产派发保持关闭。


## 116. pidfd 存活检查与后续动作的竞态

一次性无网络 Linux 容器运行 `probe-native-pidfd-check-race.py`：持 daemon 双锁的子先通过父 pidfd 活性检查；观察者随后令父退出并确认回收，再允许子写入模拟动作的管道标记。该时刻父 pidfd 已可读但标记仍成功，输出 `PIDFD_CHECK_THEN_ACTION_RACE_CONFIRMED`，日志 `20261027-212744-292f64.log`；脚本 ruff通过。

该探针未触发任何真实外部动作；说明活性检查与后续动作非原子，不证明生产路径具体会这样执行。已开始动作的意图持久化、异常、unknown 与显式恢复仍需整体协议，不得仅靠一次 pidfd 检查授权。派发保持关闭。


## 117. intent 持久化后外部动作之前的进程退出

在独立 pytest 进程内 fork，分别让 start、apply、dormant job 的子进程在真实 `_save_new` 完成文件与目录 fsync 后、外部动作之前 `os._exit`。父进程确认 intent 仍可见且 sender/包管理器/runner 替身未调用，原 ID 不重放；start 和 apply 的新 ID 被未确认事务阻塞。job gate 仅在测试中替换，生产仍默认拒绝。针对性193通过，ruff通过，完整1052通过3跳过（`20261027-213220-dcd223.log`）。首次完整运行有多线程 fork 警告；改为独立 pytest 子进程后重跑无该警告。

这是本机文件系统上的进程退出测试，不是宿主断电、文件系统持久性承诺、真实 systemd/dpkg/生产监督或全参与者测试；apply 与 job 使用部分替身，不能证明真实外部动作完整闭环。进程退出发生于 intent 成功返回后，并不覆盖成功前掉电或动作已发生后的结果丢失。生产派发仍关闭。


## 118. 已记录 job intent 的重入一致性校验

`dispatch_job` 的已有 intent 分支在既有 `_prepared` 授权校验之后，验证 runtime snapshot，并逐字段精确比对 schema（严格整数）、state、close 协议、确定性 unit、instance/operation ID、prepared 与 snapshot 摘要；畸形、多字段、证据变化或快照验证失败均拒绝，不修改 intent、不调用 runner。准确记录仍可在生产新派发门禁关闭时只读返回 `dispatch_already_recorded`，不重派。新增12种字段/内容漂移负向用例；相关205项通过，ruff通过，完整1064通过3跳过（`20261027-213718-f18965.log`）。

这是已记录路径的一致性检查，不是跨进程身份认证、持锁授权、恢复或新派发许可；快照不可用/漂移会拒绝观察分支，不能由此推断动作未执行或删除封锁。首次派发仍 default-deny。未构建新包或进行真实 systemd 验收；旧测试helper继续手动退休，不自动迁移。


## 119. 外部动作已开始、结果未保存时的进程退出

在独立 pytest 进程内使用 fork，让 start sender、apply 包操作与 dormant job runner 测试替身分别先写入已开始标记，再在进程内 `os._exit`，不产生结果或 sender 回执。父进程确认持久 intent 存在：start 返回 unknown、同 ID 不重放、新 ID 阻塞；apply 同 ID 只观察、无 outcome，新 ID 被未终态事务阻塞；job 返回已记录且不重派。job 的 default-deny 仅在测试中替换。针对性208项、ruff通过；完整1067通过3跳过，日志 `20261027-214219-dd9bf9.log`。

此处“动作已开始”是写测试标记，不是真实 systemctl、dpkg 或 systemd-run；未验证真实后代存活、动作完成与回执丢失、systemd重启或宿主断电。unknown 必须保持封锁并经准确证据显式对账，不因本测试推定动作失败、已完成或可重试；生产派发仍关闭。


## 120. 已验证候选与监督父退出的隔离组合实验

无网络 Linux 6.18 amd64/Rosetta 一次性容器，用 SHA 校验旧 0dbcf8d DEB 并仅在容器覆盖当前 bootstrap/helper 模块；独立 bootstrap 发布及校验候选内存字节后篡改磁盘候选，父持 daemon 双锁 fork，子使用验证字节并直接调用内部 `_serve`。观察者确认 socket peer 为子、畸形请求拒绝、双锁阻止并发租约。父退出后，子内实验 watchdog 观察父 pidfd 并退出；观察者确认子退出、双锁释放、socket 文件保留且连接被拒绝。`VERIFIED_PARENT_EXIT_PIDFD_FIXTURE_OK`，日志 `20261027-214658-bd5b5d.log`；Python ruff及shell语法检查通过。

仅为旧包加当前源码覆盖的单次机制实验，不是当前准确包、真实 systemd 或生产监督器；内部 `_serve` 绕过生产租约入口，watchdog 不证明处理者身份、无竞态或动作回收，残留 socket 不自动清理。生产派发仍关闭，未操作真实服务。


## 121. unknown 恢复的 sender 回执格式校验

start barrier 2 与 package apply 显式恢复共用 sender completion 校验：须为对象，state 为 `sender_reaped`、intent 精确一致，且 `returncode` 是整数（不接受 bool）。缺失、空值、字符串、浮点或非对象拒绝；不写恢复证据、不改原 intent，受影响的新事务继续阻塞。负数、非零与超时码均允许作为进程退出证据，但恢复仍依真实服务或包状态判断，不能把退出码视为成功或隐式重放。相关 guard 已写入整数 returncode；旧或损坏回执不自动补造、迁移或删除，需人工核查。原有 barrier 1 与新 job default-deny 未改变。

测试覆盖 start 的运行/停止恢复、apply 恢复、错误返回码及非对象回执、原 intent 保留、新 ID 封锁、有效非零退出码和无重放；完整 1102通过3跳过、ruff通过（日志 `20261027-215138-6a9750.log`）。未重新构建包或在真实 systemd/dpkg 验收；不证明 sender 回执之外的崩溃或 peer 闭环。


## 122. 57714ae 离线开发制品及隔离 DEB 验证

在干净提交 `57714ae9d0d18c55a275ea9ebeafb39e12f99bce` 离线构建三组件 wheel；Device wheel SHA256 `e220912487effa908801eaf63116c6ff35df425b5579948981555bfa8b7277fa`，bash `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa`，fs `b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`。wheel 内六个 bootstrap/helper/update 模块与该提交源码逐字节一致。使用锁定依赖、uv和 CPython 离线组装 `build/native-payload-p1-57714ae/`，一次性无网络 Linux amd64/Rosetta 容器构建未签名 DEB：`build/native-packages-p1-57714ae/ai-device-native-client_0.2.2-1_amd64.deb`，109817790 bytes，SHA256 `b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03`。

在另一个一次性无网络容器中验证包摘要、preinst/prerm 保护文案、全新安装、安装的六个主 runtime 模块与四个独立 bootstrap 模块与源码一致；调用包内 bootstrap helper 验证非整数/缺失 returncode 拒绝、负整数接受，并验证重复安装被原覆盖保护拒绝。`CURRENT_DEB_RECEIPT_GUARD_OK`、`CURRENT_DEB_FRESH_INSTALL_AND_PROTECTION_OK`，日志 `20261027-215650-b18253.log`。脚本 `scripts/accept-native-current-receipt-deb.sh` 固定该包摘要，仅为隔离验收。

该制品是未签名本地开发包，非正式发布，不包含后续文档和验收脚本变更；不证明真实 systemd、真实 sender 回收、RPM、目标设备或宿主断电验收。未部署或修改真实设备，包覆盖保护未放宽。


## 123. 57714ae DEB 的隔离 systemd sender 回执恢复

一次性无网络 Linux amd64/Rosetta 容器以独立 systemd PID1 运行，准确校验并安装第122节 DEB。复用 `accept-native-systemd.sh` 验证普通 UID12345 新装、helper/systemd 启动、同 ID 不重放、非 owner 拒绝；容器无中心连接或真实凭证。新脚本 `packages/device-runtime/scripts/accept-native-receipt-systemd.py` 在容器内停止其测试 client，然后让真实 `systemctl start` 和 sender guard 完成，再注入丢响应使 intent 维持 unknown。实际 sender 回执包含整数 returncode；仅在本次容器篡改该回执移除 returncode，验证 resolve-running 拒绝、无 recovery、新 ID 阻塞且原 intent 不变。恢复该测试回执原始字节后，显式 resolve-running 确认运行，PID不变、原 intent 不变且真实 sender 仅调用一次。

`SYSTEMD_RECEIPT_UNKNOWN_RECOVERY_OK`，日志 `20261027-220022-6ca4bd.log`，基线日志 `20261027-220001-162cfe.log`，脚本 ruff通过。测试后仅对该一次性容器 systemd 执行 poweroff，确认 --rm 容器退出；未停止真实服务。此实验不覆盖真实宿主重启/断电、package apply、目标设备、逐消息处理者认证或生产监督器；恢复测试只用已有 P1 隔离授权，不开放新 job 派发。


## 124. 准确开发DEB的隔离 systemd peer 只读观察

无网络一次性 Linux 6.18 amd64/Rosetta systemd 容器安装第122节准确 DEB 并通过普通用户 helper 基线。新脚本 `packages/device-runtime/scripts/accept-native-systemd-peer.py` 使用独立 bootstrap 内的真实 `scoped_helper_unit_peer` 连接 helper socket，确认 systemd unit MainPID 与 SO_PEERCRED/pidfd 的 peer PID 相同；作用域前后固定 unit 的 LoadState、ActiveState、SubState、MainPID、InvocationID、FragmentPath 保持一致，作用域内仅发无害 query 并收到成功响应。结果明确 `loaded_code_verified=false`、`dispatch_allowed=false`。输出 `SYSTEMD_PEER_OBSERVED_NOT_AUTHORIZED_OK`，日志 `20261027-220507-6784e1.log`；脚本 ruff通过，临时容器退出。

该证据仅为本机隔离 systemd 单次只读观察，不证明 socket 后续消息由同一进程处理、不绑定已验证候选字节、不消除父死亡竞态，也不授权 job 派发或证明目标设备。未修改生产门禁或真实服务。


## 125. 隔离 systemd peer 观察期间退出拒绝

在新的无网络一次性 systemd 容器重新安装第122节准确 DEB并运行普通用户基线；扩展 `accept-native-systemd-peer.py`：在只读 scope 内仅对该容器的测试 helper 执行 systemctl stop，确认 peer 退出或 unit 不再稳定时 scope 后置检查拒绝返回成功。`SYSTEMD_PEER_OBSERVED_NOT_AUTHORIZED_OK` 与 `SYSTEMD_PEER_EXIT_DURING_OBSERVATION_REJECTED`，日志 `20261027-220738-fe5644.log`；ruff通过，该一次性容器已退出。

这是隔离测试服务的受控停止，不触碰真实服务。scope 后置失败不能撤销作用域内已有副作用，故仍仅可观察，不证明实际处理者或加载代码，也不授权派发；systemd 调用与 peer 死亡的其他竞态尚未覆盖。


## 126. 后代继承端点的逐记录 sender 凭据

`probe-native-inherited-record.py` 在一次性无网络 Linux 6.18 amd64/Rosetta 容器复用严格 SEQPACKET+SO_PASSCRED 接收原型：worker 正常记录与观察者 fork 返回的 PID/UID 一致并接受；worker 后代继承同一个 sender 端点发送时，内核逐记录凭据的 PID 不同，拒绝该记录。`INHERITED_ENDPOINT_DESCENDANT_RECORD_REJECTED`，日志 `20261027-220920-1b90c3.log`，ruff通过。

这只覆盖实验性消息发送者，并不证明公开 socket 的实际响应处理者或已验证代码；预期 PID 来源于测试 fork，未绑定生产 pidfd/systemd invocation、父死亡与事务闭环。生产派发保持关闭，未修改现有 IPC 权限。


## 127. 子进程 pidfd 与逐记录凭据的退出队列边界

重启后只读核实 AI Assistant 服务运行、当前会话唯一 execution 及停止检查开关、原 Worktree 干净并处于 `efa1479`；未重启或修改真实服务。新增 `probe-native-pidfd-record.py`，一次性无网络 Linux 6.18 amd64/Rosetta 容器中，观察者对等待握手的直接子进程取得 pidfd，接受预期 worker 记录，拒绝继承同一端点的后代记录；子发送最后记录后退出，pidfd 已就绪但该记录仍可读取且凭据正确。输出 `PIDFD_CREDENTIALS_DESCENDANT_REJECTED_QUEUED_AFTER_EXIT`，日志 `20261027-221331-1e89b9.log`，ruff通过。

逐记录身份只说明发送时主体，不能代替读取时 pidfd 活性、动作时序与未知恢复；活性检查后仍有竞态。该机制实验不含真实候选、公开 stream、systemd invocation 或生产监督器；生产派发仍关闭。


## 128. 隔离直接子进程 pidfd/逐记录挑战握手

`probe-native-pidfd-handshake.py` 在一次性无网络 Linux 6.18 amd64/Rosetta 容器组合直接子 pidfd、SEQPACKET/SO_PASSCRED、有界一次性挑战及读取前后活性检查。正常仅得 `observed_not_authorized`，错误挑战、后代冒用继承端点、子退出和有界超时均拒绝。`PIDFD_RECORD_HANDSHAKE_FIXTURE_OK`，日志 `20261027-221612-c43041.log`，ruff通过。

挑战不证明加载代码或公开 socket 实际处理者；退出检查与后续动作非原子，也不处理 systemd invocation、生产双锁交接、消息重放持久状态或 unknown 恢复。仅为机制原型，不改变生产 IPC 或 default-deny。


## 129. 准确 DEB 验证候选字节与直接子握手

`accept-native-verified-handshake.sh` 在无网络一次性 Linux 6.18 amd64/Rosetta 容器安装第122节准确 `57714ae` 未签名 DEB（摘要 `b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03`），无需覆盖源码。独立 bootstrap 发布并校验候选字节，随后测试性篡改候选路径；父持 daemon 双锁 fork，子从继承的验证字节加载候选并以 SEQPACKET 回应随机挑战。父核对直接子 pidfd、逐记录 PID/UID、活性和双锁互斥，子退出后释放租约。输出 `VERIFIED_CANDIDATE_PIDFD_HANDSHAKE_FIXTURE_OK`，日志 `20261027-221821-c360ce.log`；ruff与shell语法检查通过。

实验代码不是生产监督器，未绑定公开 socket 实际处理者或 systemd invocation，未验证父死亡、持久事务及unknown；测试握手不得授权新 job 派发。未操作真实设备或服务。
