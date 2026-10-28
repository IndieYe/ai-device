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

在干净提交 `57714ae9d0d18c55a275ea9ebeafb39e12f99bce` 离线构建三组件 wheel；Device wheel SHA256 `e220912487effa908801eaf63116c6ff35df425b5579948981555bfa8b7277fa`，bash `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`，fs `b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`。wheel 内六个 bootstrap/helper/update 模块与该提交源码逐字节一致。使用锁定依赖、uv和 CPython 离线组装 `build/native-payload-p1-57714ae/`，一次性无网络 Linux amd64/Rosetta 容器构建未签名 DEB：`build/native-packages-p1-57714ae/ai-device-native-client_0.2.2-1_amd64.deb`，109817790 bytes，SHA256 `b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03`。

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


## 130. 准确 DEB 复验候选 socket 与父退出

新入口 `scripts/accept-native-exact-parent-exit.sh` 在一次性无网络 Linux 6.18 amd64/Rosetta 容器安装第122节准确 `57714ae` DEB，校验 SHA256 `b172b35a6882eb61b89b48dba4b27808f3bf6da1d06da8510cb31ca4f2e45d03`，不挂载或覆盖源码；复用第120节 `probe-native-verified-parent-exit.py`。候选字节经独立 bootstrap 验证后，子加载继承内存字节并 bind socket，观察到 peer 为子且持双锁；监督父退出后，实验 watchdog 观察父 pidfd 并令子退出，双锁释放，陈旧 socket 文件保留且连接拒绝。`VERIFIED_PARENT_EXIT_PIDFD_FIXTURE_OK`，日志 `20261027-222019-747f97.log`，shell语法检查通过。

这消除了第120节旧包源码覆盖的制品歧义，但仍是内部 `_serve` 与测试 watchdog，非生产监督器、systemd invocation、逐消息实际处理者、事务恢复或宿主重启验收；不开放派发，不自动删除陈旧 socket。


## 131. 真实 systemd peer 与实际响应者反例及 pidfd 清理回归

在无网络、一次性 Linux 6.18 amd64/Rosetta systemd 容器安装准确 `57714ae` DEB；仅在容器内创建测试 unit，主进程创建监听 socket、fork 子进程处理连接并保持主进程存活。包内真实 `scoped_helper_unit_peer` 前后观察通过，unit MainPID 等于内核 socket peer，返回未证明代码且不允许派发；客户端 `SO_PASSCRED`/`SCM_CREDENTIALS` 实际响应凭据显示发送者为另一 root 子进程。`SYSTEMD_PEER_MAINPID_DIFFERS_FROM_HANDLER_OBSERVED_NOT_AUTHORIZED`，日志 `20261027-223138-a4b7e2.log`；复现脚本 `scripts/accept-native-systemd-handler.sh` 另一次通过（`20261027-223246-dcaf57.log`）。测试 unit 不是生产 helper，两个容器均已关闭；不能将观察成功解释为处理者或加载代码证明。

为只读观察函数增加五种 pidfd 生命周期回归：身份不匹配、进入前退出、退出时复核失败、调用者异常和成功路径均关闭描述符；精确说明内核报告的 peer 可能不是消息处理者。完整 Device 测试 `1107 passed, 3 skipped`（日志 `20261027-222924-4a543e.log`），ruff 和 diff 空白检查通过。修正第122节 Bash wheel SHA256 截短笔误，实测文件摘要为 `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`。本次不改变认证、派发或生产行为；生产受控启动和事务闭环仍未实现。


## 132. guard 和 sender 的 helper socket 描述符隔离

新增 Linux 专用 `test_systemd_guard_and_sender_do_not_inherit_helper_sockets`：为监听与已接受的 Unix socket 显式设置可继承，再执行真实 `Systemd.start` guard 和受控替身 sender；由 sender 对照 `/proc` 中 guard 与自身全部描述符的设备号、inode，检查两种 socket 均不存在，并核对 sender_reaped 回执。macOS 完整回归为 `1107 passed, 4 skipped`（新增 Linux 测试在 macOS 跳过），日志 `20261027-223621-57cd7e.log`。

另在一次性无网络 Linux 6.18 amd64/Rosetta 容器通过 `scripts/accept-native-guard-fd.sh` 安装准确 `57714ae` DEB，执行 `probe-native-guard-fd.py`，以包内真实 `Systemd.start` 和同样的受控替身 sender 检查，输出 `GUARD_SENDER_HELPER_SOCKET_NOT_INHERITED_FIXTURE_OK`，日志 `20261027-223607-466555.log`。ruff、shell 语法和 diff 空白检查通过。

仅覆盖该 guard 的 exec 路径；测试 socket 为独立 fixture，sender 不是 systemctl，不覆盖真实 systemd、其他 fork/exec/FD 传递路径、恶意 root、启动监督或事务收口，不证明消息实际处理者。生产派发仍默认拒绝。


## 133. job 对账的 systemd 观察失败封闭

`observe_job` 原先以字典推导式解析 systemctl 输出，重复属性最后值会覆盖前值、畸形行被忽略；针对重复 `ActiveState` 的回归先复现 `recover_job` 错误接受并进入恢复路径。现请求 `--all --no-pager`，仅在输出不超过 16384 字符、六个指定字段齐全且各出现一次、没有畸形行或其他字段时提供 unit 观察；否则观察不可用，`recover_job` 保持阻塞，不重派、不删除 intent 或释放围栏。独立有效的包结果仍可在只读报告显示，但不能代替 unit 终态对账。

回归覆盖重复、缺失、畸形、额外及过长输出，拒绝时无 recovery 文件或新增包动作，完整有效终态仍可显式恢复。完整 Device 回归 `1107 passed, 4 skipped`（`20261027-224304-9b7522.log`）；ruff/diff 检查通过。一次性无网络 systemd 容器中，准确 `57714ae` DEB 提供运行时，以只读挂载的当前 `native_update_job.py` 和隔离 fixture 台账观察真实 `/bin/true` 瞬态 unit，完整字段解析成功且禁止重试、围栏保留，`REAL_SYSTEMD_JOB_OBSERVATION_PARSER_OK`（`20261027-224508-1103f2.log`）。未制作包含此次代码的新包；该实验不覆盖真实包事务、systemd 重启或完整 unknown 恢复，生产派发继续关闭。


## 134. 对账后迟到 worker 的同 ID 不重放回归

扩展 `test_explicit_job_recovery_retains_fence_and_never_replays`：有效已执行 job 显式对账完成后，模拟同一 intent 的迟到 worker 调用真实 `Updates.apply`，确认只返回已有恢复观察、`reapplied=false`，包执行次数不增加，job/apply intent、sender 与两类恢复证据字节不变。针对性测试通过（`20261027-224733-4f2116.log`），ruff/diff 检查通过。

该测试为顺序性替身包管理器回归，不代表真实 systemd 并发、unit 身份连续性、迟到 worker 与 close 的完整竞争或宿主重启验收；生产派发仍关闭。


## 135. job 锁内恢复绑定完整事务意图

原 `Updates.recover(..., job_intent_sha256=...)` 在实例事务及 package EX 锁内只核对 job 文件摘要、实例/操作和 prepared 摘要，未约束 schema、状态、close 协议、确定性 unit、快照摘要或额外字段；回归先复现不兼容 schema 仍生成恢复证据。现在锁内重新验证 snapshot，要求完整精确的当前 job intent（严格整数 schema、固定状态/close 协议、确定性 unit、实例/操作、prepared 与 snapshot SHA），以及调用方携带的 intent 文件摘要相符；不兼容或漂移拒绝，不自动迁移、删除或重放。

回归覆盖布尔 schema、错误协议/状态/unit/snapshot、额外/缺失字段、非对象和实际 snapshot 漂移，拒绝时无 recovery 证据及额外包动作；有效意图的显式对账、重复对账、迟到 worker 不重放仍通过。完整 Device `1107 passed, 4 skipped`，日志 `20261027-225158-b48c21.log`；ruff 与 diff 检查通过。未构建包含此次修复的新包，未执行真实包更新。systemd 终态观察与恢复之间的 invocation 竞态仍未解决，生产派发保持关闭。


## 136. worker 执行与恢复统一完整 job 意图校验

锁内恢复修复后复核发现，worker `Updates.apply` 原先只核验 job 文件摘要、prepared 与 snapshot 摘要，不核验 schema、协议、unit、实例和操作等完整字段；新增回归先复现布尔 schema 被接受。现由共享 `_verify_job_intent` 在实例事务及 package EX 下对执行与 job 恢复统一检查完整固定字段、严格整数 schema、准确 prepared/snapshot 和意图文件摘要；已有 apply intent 的同 ID 观察仍不重放，直接 apply 的互锁行为不变。

测试覆盖错误字段、额外/缺失字段、非对象、摘要不符，均在发布 apply intent 和包动作之前拒绝；完整意图仍重新检查源状态后可通过测试替身执行，恢复回归保持通过。相关 97 项通过，完整 Device `1107 passed, 4 skipped`（`20261027-225546-d241a2.log`），ruff/diff 检查通过。旧格式不自动迁移；未构建包含新修改的 DEB/RPM、未验证真实 systemd 包事务或宿主崩溃；受控身份与 invocation 竞态仍未解决，生产派发继续默认关闭。

已有 worker snapshot 是冻结字节，当前源码修改不会自动升级其执行逻辑；新派发原型创建快照时核对关键 worker 模块摘要，但派发仍关闭。旧测试 helper 和旧非终态必须依已确认维护窗口由管理员手动处理，不以本次修复宣称旧 worker 已受新校验保护。


## 137. b4158df 准确离线开发制品与隔离 DEB 验证

干净提交 `b4158df96752ab2ff38cf3f1b7b179995118124e` 离线构建 `build/native-wheels-p1-b4158df/` 三 wheel；Device SHA256 `3fd99bf4ea3f2d1280a4dfa56894ab2928a81b1eae5e2c2c37d286cd77c104c6`、Bash `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`、FS `b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`。Device wheel 的 init、bootstrap、helper、management_lock、update、update_job 六模块逐字节与固定 Git 提交一致（`20261027-230041-123595.log`）；分发 metadata 声明 Python ≥3.13 和 native cryptography/zstandard 依赖。以已锁定 CPython（SHA `f829adc9d888e76c8801b4799ed444188d35239fcae3a9808a99ec5cb0d8d92c`）、uv 与本地依赖组装 `build/native-payload-p1-b4158df/`，build-inputs 标记未签名开发构建。

无网络一次性 Linux amd64/Rosetta 容器构建 `build/native-packages-p1-b4158df/ai-device-native-client_0.2.2-1_amd64.deb`，109830426 字节，SHA256 `0d3ccfd5647bf27b1d6965fec90c0ed20e18cb486939e7837e8231af4df10001`（`20261027-230057-5aad2e.log`）。另一个干净容器安装该准确包，主 runtime 六模块、独立 bootstrap 四模块与冻结源码对应；安装模块的完整 job 意图校验 fixture 接受正确/拒绝 bool schema，派发仍拒绝，原 preinst/prerm 存在且重装受保护拒绝。脚本 `scripts/accept-native-b4158df-deb.sh`，`B4158DF_DEB_FRESH_INSTALL_AND_PROTECTION_OK`（`20261027-230230-23075f.log`）；fixture 的 snapshot 验证使用替身，不代表真实 job 执行。

独立无网络 systemd 容器安装同一准确包，普通用户 UID12345、稳定 PID、非 owner 拒绝和服务基线通过（`20261027-230309-2e66aa.log`）；真实隔离 systemd start 丢响应、损坏回执阻止恢复与新 ID、恢复回执后显式对账通过（`SYSTEMD_RECEIPT_UNKNOWN_RECOVERY_OK`，`20261027-230327-3b16e9.log`）。测试容器已关闭。这些结果不覆盖真实包 job 派发或 invocation 竞态、正式设备、中心、宿主重启与崩溃。当前构建镜像无 rpmbuild（`20261027-230400-16ea2c.log`），未联网安装、未构建新 RPM；旧 RPM 不能代表本提交。生产派发关闭，P1 未验收。


## 138. b4158df 事务 snapshot 及依赖验证

独立无网络 Linux 容器对准确 b4158df DEB 执行已有 `accept-native-manager-package.sh`：新旧 preinst 一致、覆盖拒绝、包内 `pip check` 通过，独立事务 snapshot 验证通过，snapshot 内 cryptography Ed25519 和 zstandard 实际操作成功、模块从 snapshot 自身导入；没有安装或启动服务，日志 `20261027-230511-832315.log`。

另一个干净容器安装准确 DEB，以 `scripts/accept-native-b4158df-snapshot.sh` 创建真实事务 snapshot，逐字节核对 snapshot 的 native_update、native_update_job、native_helper、native_bootstrap、management_lock 与包内模块一致；snapshot 独立解释器可加载完整意图校验，派发门禁仍拒绝。`B4158DF_TRANSACTION_SNAPSHOT_MODULES_MATCH_GATE_CLOSED`，日志 `20261027-230555-9bf5aa.log`。这些是候选快照和依赖验证，不是活跃 worker 身份、真实包事务或恢复闭环，不覆盖旧 snapshot，也不改变 P1 或生产派发状态。


## 139. b4158df 隔离 systemd 包 job 失败与显式对账

无网络一次性 Linux amd64/Rosetta systemd 容器使用准确 b4158df DEB（`0d3ccfd5647bf27b1d6965fec90c0ed20e18cb486939e7837e8231af4df10001`）与准确三 wheel 运行既有 `accept-native-update-job.sh`。容器中安装惰性旧版本 source-package fixture，提取而非安装候选原生包；真实生产派发门禁先拒绝，runner 未调用、intent 未创建。仅测试进程临时替换门禁后，真实隔离 systemd 调用 snapshot worker，原候选 DEB 的 preinst 拒绝包覆盖；sender 回执匹配、outcome unknown、active 不变、设备服务未启动。缺 sender 的显式恢复拒绝，恢复原证据后对账幂等、一次派发、job/apply intent 和围栏保留。

输出 `production_dispatch_fence_refused`、`disposable_test_fence_substitute`、`explicit_job_reconciliation_verified`、`independent_job_refused_by_package_guard`，完整日志 `20261027-230750-3f03cb.log`；临时容器已退出。此实验在测试进程替换了派发门禁并使用隔离旧包 fixture，仅覆盖包钩子拒绝后的真实 systemd job/unknown 对账，不证明生产身份绑定、invocation 竞态、成功升级、宿主断电或目标设备，不能开启派发。RPM 仍未构建。


## 140. b4158df 隔离 job 丢响应与保留围栏

独立无网络 systemd 容器用同一准确 b4158df DEB 和三 wheel、既有 `accept-native-update-job.sh` 的 `DEVICE_JOB_FAULT=lost-response` 运行故障场景。该 fixture 在真实 systemd-run 返回后由测试 runner 模拟 TimeoutExpired，原调度报告 unknown；同 ID 重入不重派，原包 preinst 仍拒绝覆盖、sender 匹配且包 outcome unknown、active 不变；缺 sender 显式恢复拒绝，复原证据后原 ID 对账幂等并保留围栏。生产派发门禁首先拒绝，之后仅测试进程替换门禁。日志 `20261027-230922-e35ac6.log`，容器已退出。

该实验只模拟返回丢失，不证明 systemd 内部失败、宿主断电、生产派发授权、实际处理者或 invocation 连续性；P1 和 RPM 新制品仍未完成。


## 141. b4158df 隔离 systemd 互斥、guard 退出与迟到 worker

四个独立无网络 Linux amd64/Rosetta systemd 容器均核对准确 b4158df DEB SHA `0d3ccfd5647bf27b1d6965fec90c0ed20e18cb486939e7837e8231af4df10001` 并使用对应三 wheel，运行既有 `accept-native-update-job.sh` 的不同故障模式：

- `start-lock`：真实 helper start 的有限排队窗口持有 package lease，排他竞争拒绝；同实例 job 请求因实例管理忙拒绝，未生成 job intent；start 完成后包锁可取得。`real_start_holds_package_lease`，日志 `20261027-231302-51395d.log`。
- `source-drift`：真实隔离 systemd job 排队、持久 intent 存在时，真实 helper IPC start 拒绝且无 start intent；随后测试漂移 active，worker 在包操作前拒绝，job 围栏保留。`real_helper_start_fenced_by_queued_job` / `queued_source_drift_refused`，日志 `20261027-231418-9c4cb4.log`。
- `start-guard-crash`：仅对该容器的已识别测试 guard 注入异常，缺 sender 回执时显式恢复与新 start 拒绝；测试 daemon 停止、显式恢复残留 socket 并重启后，原 ID 不重放且新 start 仍拒绝。`real_guard_crash_remains_unknown` / `restarted_daemon_preserves_unknown`，日志 `20261027-231529-9bc504.log`。
- `close-before-apply`：systemd job 处于受控排队窗口时先持锁 close，迟到 worker 拒绝执行包，持久围栏保留。`delayed_worker_refused_after_close`，日志 `20261027-231627-0d16bf.log`。

每次先证明生产派发门禁拒绝、未创建 intent；涉及 job 派发的场景仅在隔离测试进程替换该门禁，未修改产品代码或放宽 DEB 包保护。容器均已退出。测试范围不覆盖生产受控启动、实际消息处理者与 invocation 绑定、任意并发时序、宿主断电、目标设备或 RPM；生产派发继续关闭，P1 未完成。


## 142. b4158df 隔离 daemon 异常退出与暂停 start guard

一次性无网络 Linux amd64/Rosetta systemd 容器使用准确 b4158df DEB 和对应 wheel，运行 `accept-native-update-job.sh` 的 `start-guard-pause`。真实 helper start 进入 systemd `start-pre` 窗口后，测试先核对容器内 daemon MainPID、guard 直接父子关系及命令行，再暂停 guard 并对该测试 daemon 注入异常退出；显式处理测试残留 socket 并启动新 daemon。systemd job 已终结时 guard 仍暂停、sender 回执缺失、package EX 冲突，恢复拒绝且新 start 无新意图；释放 guard、确认匹配回执后，同 ID 不重放，显式 resolve-running 确认运行。生产新 job 派发门禁先拒绝且无 intent。

输出 `paused_guard_blocks_recovery` 与 `daemon_exit_guard_reaped_and_reconciled`，日志 `20261027-231814-288e12.log`；测试 guard 在脚本 finally 恢复，容器已退出。本实验验证的是 start sender guard 而非尚未实现的候选代码生产监督器；不证明宿主断电、目标设备、实际消息处理者或 systemd invocation 绑定，派发保持关闭。


## 143. peer 观察初始化中断时回收 pidfd

`_socket_peer_process` 获取 pidfd 后、进入只读观察作用域前，原实现只在限定异常类型时关闭 FD；其他异常或中断可能泄漏描述符。新增模拟 FD 的 `RuntimeError` 和 `KeyboardInterrupt` 回归，修复前两例均复现未关闭；改为同一个 `finally` 覆盖初始化和作用域生命周期，原有身份失败错误转换及 caller 异常语义不变。helper 定向测试 153 通过、1 跳过，完整 Device 回归 1109 通过、4 跳过，ruff 和 `git diff --check` 通过。新增用例使用模拟 getsockopt 和真实 pipe FD，并非实际 Linux pidfd 的故障注入。

只读观察仍不证明实际消息处理者、已加载代码或 invocation 绑定，不允许在观察作用域内执行不可逆动作。生产派发仍关闭；本次修改后旧 b4158df DEB 不包含此修复，尚未构建新包或 RPM。

真实 Linux 验证：`accept-native-peer-pidfd.py` 在无网络一次性 amd64/Rosetta 容器，以准确 b4158df DEB 提供解释器，显式核对导入当前 Worktree `native_helper.py`。真实 `SO_PEERPIDFD` 获取后注入初始化异常，确认 `/proc` 中 pidfd 对应子进程，异常后 FD 返回 EBADF；随后同一真实连接正常观察并检测 peer 退出。输出 `real_pidfd_initialization_interruption_closed`、`peer_pidfd_bound_and_exit_refused`，日志 `20261027-232952-49a114.log`。这不是新包验收，不证明生产监督、逐消息处理者或所有初始化窗口；生产派发依旧关闭。


## 144. 关闭退休证据类型与启动围栏

复现 Python JSON 对象比较中 `true == 1`、`false == 0`：对 job intent、关闭及退休证据分别注入 schema 布尔值、retry/fence/replay 整数，并逐层重算准确摘要，原 `_job_retired` 四例均错误判定为已退休。修复后拒绝四例、保留原证据并阻止 start；关闭入口严格检查完整 intent、schema 和 root 批准类型，关闭/退休幂等读取使用类型敏感 JSON 比较，类型漂移不新增证据。合法关闭、退休、迟到 worker 拒绝和不同 ID 路径沿用既有测试。helper/update/job 回归 254 通过、1 跳过，完整回归 1113 通过、4 跳过，ruff 与 diff 检查通过。测试使用隔离 fixture 和重算摘要，不证明真实生产事务或身份闭环。

旧 worker 冻结 snapshot 不会自动更新，旧测试环境按管理员维护窗口手动退休；不得删除旧意图或迁移围栏。新包未构建，生产新 job 派发仍 default-deny。`Updates._prepared` 等其他批准消费者尚须逐项审查，本节不宣称所有批准/恢复链完成。


## 145. 更新批准回执一致性

`Updates._prepared` 曾只校验批准回执中的 prepared 摘要，缺 UID、布尔 UID 或多余字段可进入包动作；非对象则产生未预期的 AttributeError。通过实际 `Updates.apply` 和替身包管理器先复现四例，再统一采用类型敏感的精确管理员批准回执校验，覆盖批准重入、prepare 消费、job 描述/观察及关闭。畸形首次记录在 apply intent 和包动作之前拒绝；已执行后批准漂移时 apply/reconcile/recover 和重复 approve 拒绝，包次数不增、原证据字节与文件集合不变。旧意图不自动迁移或改写。

完整 Device 测试 1118 通过、4 跳过；ruff、diff 检查通过。使用本地 fixture，不代表生产包事务或外部认证测试；正式签名、批准策略、受控监督和生产派发门禁仍未完成。旧 b4158df DEB 与冻结 snapshot 不含第143—145节代码修复，新 DEB/RPM 尚未构建或验收。


## 146. 46c2f06 准确离线 DEB 与隔离 systemd

干净提交 `46c2f06b92d14afc8ad1ca75eb6e3724e893e910` 用 `uv build --offline --wheel --all-packages` 生成 `build/native-wheels-p1-46c2f06/`。Device wheel SHA256 `9cec69887c3d43c134a4b219ef456348ca56b4bbb2485a8334ee3e4a50ac1eea`、Bash `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`、FS `b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`；Device 六个关键模块逐字节匹配冻结 Git，metadata 声明 Python ≥3.13 和 native 依赖。`build/native-payload-p1-46c2f06/build-inputs.json` 记录来源、wheel 和锁定 uv，标记 unsigned development build。一次性离线 Linux 容器构建 `build/native-packages-p1-46c2f06/ai-device-native-client_0.2.2-1_amd64.deb`，109815240 字节，SHA256 `be58c0e5fcecc6f468a62aa5b01cfa588361049a9057c3915eedc08dfe13f6d8`；不发布、不覆盖旧制品。

`accept-native-46c2f06-deb.sh` 在一次性无网络容器核对 Hash、干净 dpkg 安装、包内六模块及独立 bootstrap 四模块与冻结源码一致；独立解释器从包内导入，对批准和退休类型漂移进行替身 fixture、默认派发门禁与原覆盖保护检查通过，日志 `20261027-234333-aef3a0.log`。真实隔离 systemd 基线普通 UID12345、稳定 PID、非 owner 拒绝通过，日志 `20261027-234412-76f8de.log`。另一次真实隔离 systemd job 先确认生产门禁拒绝、runner 0 且无 intent，再由测试进程替换门禁；worker 遭原包 preinst 拒绝，missing sender 阻恢复、显式对账幂等、只派发一次、围栏保留且 active 不变，日志 `20261027-234506-3d6b30.log`。两 systemd 测试容器已正常退出。

上述证据不证明生产监督、消息处理者或 invocation 绑定、成功升级、宿主重启、真实设备或正式签名。新 RPM 未构建；本次无网络隔离镜像确认缺少 rpmbuild，不以旧 RPM 代替。


## 147. 已退休 job 的完整身份围栏

针对 `_job_retired` 只检查 schema/state/protocol 的缺口，构造缺失或漂移 instance、operation、unit、prepared/snapshot 摘要及多余字段的七组证据，并重算 close/retire 摘要；修复前七组均错误放行。现要求完整字段集、严格 schema、合法 ID 与事务 stem 匹配、确定性 unit 及两摘要的严格 SHA256 格式，再检查原关闭/退休证据与无 apply/sender/outcome。失败保留原字节并阻挡 start；合法退休与迟到 worker 测试不变。不读取或重建历史 snapshot，不把格式一致宣称为批准或执行来源认证。

helper/update/job 266 通过、1 跳过；Device 全量 1125 通过、4 跳过，ruff/diff 检查通过。第146节 DEB 源码 `46c2f06` 不含本次更改；须重新构建准确包和相关隔离验收。旧快照不自动迁移，生产派发仍关闭。


## 148. baa0797 准确 DEB 与退休身份隔离验收

干净提交 `baa0797e641c7c6f59ef10511b7e6e2d3059fff6` 离线构建 `build/native-wheels-p1-baa0797/`；Device wheel SHA256 `820c683653980dc149eda1f625c348ee22dd30d90064191f451ad79bd9ab0a54`，Bash `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`、FS `b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`，六个关键模块逐字节匹配提交。锁定输入组装 `build/native-payload-p1-baa0797/`，一次性离线 Linux 容器构建未签名开发包 `build/native-packages-p1-baa0797/ai-device-native-client_0.2.2-1_amd64.deb`，109816504 字节，SHA256 `7f0935ad186ece580d3d3e6e7aba6c0c128c52ace649b9dff8866f07257ae542`；不覆盖或发布。

固定 Hash 的 `scripts/accept-native-baa0797-deb.sh` 在干净容器核对包内六模块及独立 bootstrap 四模块与源码一致，用包内解释器测试合法及不完整退休身份、类型漂移、批准回执、派发默认拒绝和重装保护，日志 `20261027-235221-a95165.log`。两次独立真实隔离 systemd job 实验先验证生产门禁拒绝，再仅以测试进程替换门禁：`close-before-apply` 的迟到 worker 在包动作前拒绝并保留围栏（`20261027-235243-d48952.log`）；默认模式包 preinst 拒绝，missing sender 阻恢复、显式对账幂等、一次派发且 active 不变（`20261027-235333-d32e6b.log`）。两个容器已退出。

这是隔离 fixture 和 systemd 证据，不证明生产监督、实际处理者、固定 invocation、成功升级、真实宿主或正式签名。RPM 无离线 rpmbuild，仍未构建；旧快照不迁移，生产派发关闭。

`baa0797` 准确 DEB 另在独立一次性 systemd 容器运行 `retire-retry`：先验证生产派发门禁拒绝，再以测试进程替身执行关闭和幂等退休；旧迟到 worker 拒绝且不重放，新 ID 的隔离派发仍遭原包保护拒绝并保留新围栏。输出 `retired_old_delayed_worker_refused_new_id_dispatched`，日志 `20261027-235501-cd1e7a.log`，容器正常退出。不证明生产派发或升级成功。


## 149. start unknown 恢复屏障类型与嵌套回执

实际 `Helper.handle` 恢复路径复现：意图 `execution_barrier=true` 或 `1.0` 被 Python 当作旧整数 1，缺 sender 回执也能生成 `confirmed_running`；其他畸形版本可在同 ID 路径被接受。新增严格支持缺字段的历史格式或精确整数 1/2；恢复、同 ID 和新 ID 扫描均拒绝其他类型/值。旧整数 1 及无字段行为按原兼容语义保留，不补造旧回执。

sender 完成证据和 recovery 的嵌套 intent 原字典比较也会把 `2.0` 当作 `2`；已先复现可错误恢复及放行新 start，再改为类型敏感 JSON 比较。负向测试确认没有新恢复记录、没有重放/新 start 且旧证据字节保留；既有合法恢复、旧版本和互斥回归通过。helper/update/job 277 通过、1 跳过；Device 完整回归 1136 通过、4 跳过，ruff 和 diff 检查通过。使用隔离本地 adapter，不证明真实 systemd 竞态或宿主崩溃；第148节 `baa0797` DEB 不含本次修复，须重新构建准确制品。生产派发依旧关闭，旧记录不删除或自动迁移。


## 150. e298fdb 准确 DEB 的恢复故障验收

干净提交 `e298fdb8c3a084dafab5bb19cbc550cde0d8464a` 离线构建 `build/native-wheels-p1-e298fdb/`；Device wheel SHA256 `c19645d9cd2ebe20c5e0265aacb69cf6b0d61fbb852252cadbca8aa2e53b20cd`，Bash `aee883b74bb2fc816abb77ba428ed4339c6901726a9ac3098a3ba8737fa16dd3`、FS `b872aa775eaacc9836c0ec94a2abf1e41c8395ef725eecb37d05b521053ab626`；六个关键模块逐字节匹配 Git。锁定输入组装 `build/native-payload-p1-e298fdb/`，在一次性无网络 Linux 构建未签名开发包 `build/native-packages-p1-e298fdb/ai-device-native-client_0.2.2-1_amd64.deb`，109811944 字节，SHA256 `d28af6086edb846317624204e8774e56da707367ffdbc0611c7964bf641d781d`。

新包固定 Hash 验收脚本在干净容器验证新装、包内六模块和 bootstrap 四模块与源码一致，包内 helper 严格拒绝畸形屏障与嵌套 sender 类型漂移、合法旧/新版本通过，批准/退休 fixture、默认派发拒绝及重装保护通过（`20261028-000546-80a530.log`）。独立真实隔离 systemd 使用新包完成普通用户基线后，启动实际 guard 并模拟响应丢失；对该容器自己的 intent 注入 `true` 屏障、对 sender 嵌套 intent 注入 `2.0`，两者拒绝恢复且不生成 recovery；复原原测试字节后缺 returncode 拒绝、匹配回执显式恢复、同 ID 不重放且 MainPID 稳定。输出 `SYSTEMD_INVALID_BARRIER_AND_RECEIPT_REFUSED`、`SYSTEMD_RECEIPT_UNKNOWN_RECOVERY_OK`，日志 `20261028-000646-b9741d.log`；容器已退出。

本验收仅覆盖隔离装包和 systemd 指定故障，不证明真实设备、宿主断电、受控监督/实际处理者、invocation 身份或成功升级。生产派发仍默认关闭，旧证据不迁移；RPM 缺离线 rpmbuild。

准确 `e298fdb` DEB 另在独立无网络 systemd 容器执行 `start-guard-pause`：验证生产 job 门禁拒绝后，核实测试 daemon 与 guard 身份，暂停 guard 并仅注入该 daemon 异常退出；新 daemon 启动后缺回执仍拒绝恢复和新 start，systemd job 已终结时包 EX 仍冲突；guard 恢复并持久产生匹配回执后显式确认运行，同 ID 不重放。日志 `20261028-000914-d66f1b.log`，测试容器已退出。此为 start sender guard 的锁生命周期，不证明受控候选监督器或生产处理者身份。


## 151. start/recovery 的 systemd 查询严格解析

`Systemd.query` 先前用字典推导忽略畸形行并覆盖重复字段；六个负向测试复现重复、缺失、额外、畸形、过长和非文本输出未稳定拒绝。现明确 `--all --no-pager`，有界输出且仅接受 ActiveState/SubState/MainPID/Job/User/Group/FragmentPath/ExecStart 各一次；空 Job 合法，其余格式或字段缺失拒绝。helper/update/job 284 通过、1 跳过；完整 Device 1143 通过、4 跳过，ruff/diff 检查通过。

`accept-native-helper-observation.py` 使用准确 e298fdb DEB 的解释器、显式路径核对当前源码，在一次性 systemd 容器验证真实已完成 unit 的八字段和空 Job（`20261028-001450-353d05.log`）。初次探测发现真实不存在 unit 即使 `--all` 仍缺 ExecStart（`20261028-001346-bc62c1.log`），严格解析正确拒绝；未放宽。该实验不代表新包验收，e298fdb 包不含本次修复。严格解析不能证明同一 systemd invocation 在观察与写恢复记录之间未变化；生产派发、监督器和身份闭环仍未完成。


## 152. 7c06c81 准确 DEB 新装与 systemd 恢复

干净检查点 `7c06c81c1d9e20d31a77673b2c8b62432728c295` 离线构建三包，Device wheel SHA256 `627b6c86112a5482aaba716ad641d9b175fe7383e47be259b3a08d39dc25b7d4`；六个关键模块逐字节匹配 Git（`20261028-002311-16258d.log`）。锁定输入在一次性无网络 amd64/Rosetta Linux 构建未签名开发 DEB：`build/native-packages-p1-7c06c81/ai-device-native-client_0.2.2-1_amd64.deb`，109816578 字节，SHA256 `b4c6e300620ac92768c5ae636d62c2429533e32c0dbb9dbfb6c930550bee49a1`（`20261028-002325-150ea0.log`），不覆盖旧成果。

干净容器校验包摘要、安装并比对主 runtime 六模块及 bootstrap 四模块与该源码，批准/退休负向 fixture 与禁止覆盖门禁通过（`20261028-002429-cde6e6.log`）。另一个无网络独立 systemd 容器重复核摘要、安装和六模块比对，普通 UID12345 安装、helper 注册和启动、同 ID 不重放、非 owner 拒绝、真实 sender 完成后丢响应，以及畸形屏障/回执拒绝、修复 fixture 后显式恢复且 PID 不变和仅一次启动通过（`20261028-002451-c5f757.log`）。包内 `-I -B` 代码对真实运行 unit 返回八字段并对不存在 unit 拒绝（`20261028-002511-d1dea6.log`）；实验容器已停止（`20261028-002517-2d3e43.log`）。

这些证据仅覆盖隔离 Linux amd64/Rosetta 开发包和所述测试；不是生产监督器、实际消息处理者或同一 invocation 的事务绑定证明，也不覆盖真正系统重启、中心、升级和真实设备。RPM 尚缺离线构建工具；生产派发仍默认关闭，包覆盖保护未改变。


## 153. job 恢复回执的类型敏感重入

`recover_job` 已有 `.job-recovery.json` 时原以 Python 对象相等判断，`retry_allowed: 0` 会与 `false` 混同并被接受；定向回归先失败复现（`20261028-002714-634ed8.log`）。现使用既有 `_same_json` 对完整回执做类型敏感比较，分别验证 `retry_allowed: 0`、`fence_retained: 1` 拒绝，原有证据字节和包动作次数不变，恢复合法回执仍可幂等重入。修复后定向通过（`20261028-002730-a0e658.log`）、ruff 和 diff 检查通过，完整源码回归 1143 通过、4 跳过（`20261028-002746-8918c5.log`）。第152节准确 DEB 不包含此修复，须另构建验证；该测试是在已有底层 recovery 记录的重入场景，不证明所有失败情况下比较发生在全部持久化副作用之前，也不解决观察/提交竞态或生产身份绑定。


## 154. 准确 job 回执修复包与真实 systemd 隔离验收

从干净 `40cb25b5498b9c2cc4d748c4c9787c48daa40525` 离线构建三包，Device wheel SHA256 `2ec8f073908912174b553b61986e715ca994711ee0cc927d11c39e40b5accaa9`；六核心模块与 Git 逐字节一致（`20261028-003018-fb4c70.log`）。无网络 amd64/Rosetta 一次性构建未签名 DEB：`build/native-packages-p1-40cb25b/ai-device-native-client_0.2.2-1_amd64.deb`，109817092 字节，SHA256 `06d99960ef6228b226c9dddb41a511e57dbeeea2fd0ca78c825a9e55112af42a`（`20261028-003028-8aa96c.log`）。干净安装比对主 runtime 六模块、bootstrap 四模块、批准及退休 fixture 与重装保护通过（`20261028-003116-d32b23.log`）。

另一无网络 systemd 容器核验 DEB 与 wheel 摘要；脚本先证明生产派发默认拒绝、runner 0 次且无 intent，再仅在本次测试进程 monkeypatch gate 运行独立 job。真实旧版 inert 测试包作为源包，候选包实际 preinst 拒绝覆盖，active 未变、outcome unknown；缺 sender 拒绝，补回原 sender 后显式对账，类型漂移 `retry_allowed=0` 与 `fence_retained=1` 均拒绝且该测试台账文件字节不变、包动作未增加，恢复原回执后合法重入。日志 `20261028-003137-5fb3e6.log`；测试容器已停止（`20261028-003208-2e7e52.log`）。fixture 先解压 DEB 并重装准确 wheel，故该 job 验收须与独立干净安装的包内模块核验合并理解；不代表真实升级成功、生产 gate 打开或身份/观察竞态闭环。RPM、实机和宿主重启仍未验收。


## 155. 孤立 job 恢复回执前置拒绝

`recover_job` 原先调用 `Updates.recover` 后才校验 job-recovery；当 job-recovery 已存在而底层 recovery 缺失时，会先新建底层 recovery 再拒绝不匹配。定向 fixture 先复现（`20261028-003353-05a707.log`）。现仅在携带 job intent 的恢复路径、实例锁与 package EX 内，先检查既有 job-recovery：缺底层 recovery 即拒绝；两者均存在时根据实际 recovery 摘要、状态及固定字段类型敏感核对后才继续，原记录不写入。测试覆盖孤立回执、两种布尔/数字漂移、全部台账文件字节和包动作次数、首次恢复及合法重入；测试数据由隔离 fixture 建立并清理。定向、ruff、shell 语法与 diff 检查通过，完整源码回归 1143 通过、4 跳过（`20261028-003455-e35e8d.log`）。验收脚本已增加孤立回执真实隔离测试，但本节源码尚未构建新包、该脚本尚未以新代码执行。该检查不提供 systemd 观察与恢复的原子性，不开放派发，也不证明处理者身份。


## 156. 孤立回执前置拒绝的准确制品验证

干净 `1c3fb38dd0b8532f792c948394e1ca072d236fa2` 离线构建三包，Device wheel SHA256 `cf43cebede8824e08607094bdc43ef939b5561eb014ebab28943be81bc6536d8`，六个关键模块逐字节匹配 Git（`20261028-003649-de464a.log`）。一次性无网络 Linux amd64/Rosetta 构建未签名 DEB：`build/native-packages-p1-1c3fb38/ai-device-native-client_0.2.2-1_amd64.deb`，109820584 字节，SHA256 `43323671e97174928c4b5b22ace2981716af5c8f2093b027005ab35055d647ac`（`20261028-003700-d70a03.log`）。独立干净安装校验主 runtime 六模块及 bootstrap 四模块、批准/退休 fixture 和覆盖保护通过（`20261028-003746-dc5023.log`）。

另一个无网络 systemd 容器核对准确 DEB 与 wheel，生产 gate 原样拒绝且 runner 0、无 intent；仅测试进程替换 gate 后真实 job 被包保护拒绝，保持 unknown、active 不变。注入孤立 job-recovery 而无底层 recovery，`ORPHAN_JOB_RECOVERY_REFUSED_BEFORE_WRITE`，所有已有测试台账字节不变，无额外包动作；随后移除仅属于该临时 fixture 的回执，正常显式对账及两种类型漂移拒绝通过（`20261028-003808-617ab4.log`）。该 job fixture 解包 DEB 后重装同批准确 wheel；独立新装的包内模块核验见上。容器已停止（`20261028-003848-4dc057.log`）。不能外推为生产 dispatch、更新成功、systemd 观察与恢复原子化、受控处理者身份、真实机器或 RPM 已验收。


## 157. 通用恢复入口不绕过 job 台账

复现：已存在 job intent 的事务调用通用 `Updates.recover` 时，原实现不要求 job 绑定即可写底层 recovery（`20261028-004052-2f1f63.log`），绕过 job 专用入口的 systemd 观察和意图核验。现于实例锁与 package EX 内，发现本事务 job intent 或 job-recovery 路径（包括悬空符号链接）且调用未提供 job intent 摘要时拒绝，保留原台账；不影响无 job 的直接更新恢复。定向覆盖通用入口拒绝、悬空标记、无新增证据、包动作不增加、合法 job 对账和原有非 job 恢复；相关测试 113 项通过，完整源码回归 1143 通过、4 跳过（`20261028-004131-2864d6.log`）。真实隔离验收脚本已加入入口负例，尚待新包验证。

此门禁不把调用者提供摘要当认证，也不消除 `recover_job` 的 systemd 观察与持久化之间竞态；已获 root 权限的可信本机代码可调用内部 API，生产派发仍默认关闭，不能视为生产身份绑定。


## 158. 通用恢复入口修复的准确制品验收

干净 `cf18a80e09152a30c0262d387e8eb7ad182d4214` 离线构建三包，Device wheel SHA256 `22d43231e25f34855820629c09b8ef1f50a14f370e91b4c6923b152ad7652dbb`，六核心模块逐字节匹配 Git（`20261028-004319-919e0d.log`）。无网络 amd64/Rosetta Linux 构建 DEB：`build/native-packages-p1-cf18a80/ai-device-native-client_0.2.2-1_amd64.deb`，109817244 字节，SHA256 `723f8ed833d6e841ba78775ccdb0a48af765bdf830cea22d8b7fa2c34e01f9ee`（`20261028-004330-8047e8.log`），为未签名开发制品。独立干净安装核对主 runtime 六模块、bootstrap 四模块及覆盖保护通过（`20261028-004417-ec8669.log`）。

另一无网络 systemd 容器核对 DEB 与 wheel，先验证生产 gate 拒绝且零 runner/intent，再仅测试进程替换 gate；候选被原包保护拒绝，保持 unknown 和 active 不变。`GENERIC_RECOVERY_JOB_INTENT_REFUSED`、`ORPHAN_JOB_RECOVERY_REFUSED_BEFORE_WRITE`、`JOB_RECOVERY_TYPE_DRIFT_REFUSED` 均通过，测试台账字节未变，合法显式对账仍幂等（`20261028-004438-361db2.log`）。fixture 解包准确 DEB 并重装同批 wheel；包内无源码安装核对单独见上。容器已停止（`20261028-004517-1bd48c.log`）。仅覆盖该隔离路径，不证明生产 job 派发、升级成功、实际处理者身份、观察与恢复原子化、RPM 或目标设备。


## 159. 真实 systemd 恢复观察竞态复现

在一次性无网络 amd64/Rosetta systemd 容器，以准确 cf18a80 DEB 和 wheel（第158节摘要）运行 `accept-native-update-job.sh` 的 `observation-race` 模式。原生产 gate 先拒绝且零 runner/intent；仅测试进程替换 gate，测试注入有限 `ExecStartPre` 延迟。job 首次执行经原包保护拒绝并产出 sender/outcome，等待真实 unit 到达 active/exited、MainPID=0、无 Job。测试 runner 先捕获真实终态，再仅重启该隔离 fixture unit，确认同一 unit 进入 activating 后，才把旧输出返回给 `recover_job`。该调用仍写出 `.recovery.json` 和 `.job-recovery.json`：`STALE_TERMINAL_OBSERVATION_WROTE_RECOVERY_WHILE_UNIT_ACTIVATING`（`20261028-004746-90e33f.log`）；active 不变且恢复回执仍保留 fence、禁止重试。测试容器已停止（`20261028-004832-919dbe.log`）。

此实验确证终态观察与恢复写入不是原子操作；不证明包操作重复、真实管理员违规或生产 dispatch 已开放。测试通过主动重启同一隔离 transient unit 构造窗口，不能外推到任意 systemd/宿主故障。两次观察仍不能填平最终检查到提交的窗口；必须设计和验证可信受控 worker、持久完成证据及同一 invocation 的协调与锁生命周期，不以本探针或 pidfd 自报当生产身份。当前 job 恢复功能仍存在此已复现限制，生产派发保持关闭，不能称 P1 完成。


## 160. 独立进程封闭锁协议原型

`probe-native-job-seal-lock.py` 是无网络一次性 Linux amd64/Rosetta 容器的 fixture，使用准确 cf18a80 DEB 包内的实例非阻塞锁、package EX 非阻塞锁与 `_save_new` 持久 no-replace 发布。子进程实际持两锁并通过管道发屏障，父进程分别用独立描述符尝试实例锁和包锁，均观察实际冲突而不是仅判断调度暂停。恢复先持锁并保存 fixture 封闭标记后，另一独立 fixture worker 重新取得锁、看到标记并拒绝创建动作文件（`20261028-005358-f9cf66.log`）；worker 先持锁创建动作文件后，恢复进程取得锁、重读动作并标注封闭状态（`20261028-005401-cfbe89.log`）。各场景独立全新容器，脚本 ruff 通过。

该结果只证明被测参与者、所述两次顺序和实际 flock 冲突；锁争用时现有接口是立即拒绝而非自动等待。测试封闭/动作文件不是产品 worker、持久完成凭证或恢复策略，不证明全入口参与、旧 snapshot、sender/进程树、锁泄漏、fsync 断电、systemd invocation 或受控身份。未改变产品、既有事务或生产派发；第159节过期观察竞态仍未修复。


## 161. 未证明的 job 恢复写入默认关闭

第159节真实 systemd 已复现过期终态观察仍写恢复证据，受控 worker 和持久终态协议尚未实现。现新增统一 job 恢复默认拒绝门禁：`recover_job` 在 unit 观察之前拒绝，带 job 摘要的 `Updates.recover` 也在进入事务前拒绝，普通无摘要恢复仍拒绝已有 job 台账；`observe_job` 仍只读，无 job 的普通恢复保持原行为。原 intent、sender、已有 recovery 和围栏不修改、不清除。默认门禁在源码测试确认两个入口拒绝、未读取观察、台账字节和包操作次数不变；旧对账内部回归仅以测试进程替身继续覆盖，不代表产品授权。隔离 systemd 脚本先检查真实默认门禁，再明确使用临时替身测试历史逻辑。完整源码 1143 通过、4 跳过（`20261028-005855-0977c4.log`），ruff 与 shell 语法通过。准确新包与隔离 systemd 实验尚待执行。

这是暂时收紧能力，不能修复旧证据、证明旧 snapshot 接受新门禁、完成生产身份/终态协议或改变派发默认拒绝。旧测试 helper 仍由管理员手动退休，不自动迁移。详见 `native/HELPER.md` 与交接设计。


## 162. job 恢复默认拒绝的准确包验收

从干净 `19b7065a4d8a00693d5c5205e2a447394975af29` 离线构建 wheel，Device SHA256 `922241377dadebb26e7ab70128ef25dec252b981c299813b36bbb837e59b0a77`，六核心模块逐字节对应 Git（`20261028-010058-867dae.log`）。无网络 amd64/Rosetta Linux 构建未签名 DEB：`build/native-packages-p1-19b7065/ai-device-native-client_0.2.2-1_amd64.deb`，109820702 字节，SHA256 `067e364ba671ebdd5986e9846696631e31ff22ba05fe549da2e67d026eb18359`（`20261028-010115-f7a987.log`）。独立干净安装比对主 runtime 六模块、bootstrap 四模块及覆盖保护通过（`20261028-010202-c9c491.log`）。

独立无网络 systemd 容器核对 DEB/wheel，先验证生产派发 gate 拒绝且无 intent，测试替身派发后候选仍被包保护拒绝并保持 unknown。对于实际 job 测试台账，未替换的 `recover_job` 和底层带 job 摘要 `Updates.recover` 均拒绝，未执行 systemd 观察、无文件改写或新增且 active 不变：`PRODUCTION_JOB_RECOVERY_GATE_REFUSED_WITHOUT_WRITES`。之后明确输出 `DISPOSABLE_RECOVERY_FENCE_SUBSTITUTE`，仅测试进程内替换门禁以回归旧路径；其结果不表示生产恢复许可（`20261028-010228-881319.log`）。fixture 解包 DEB 后重装同批 wheel，与独立包内安装核验分开；容器已停止（`20261028-010309-b70a9e.log`）。旧 snapshot、生产 worker 身份、invocation/恢复协调、RPM 和真实设备仍未验证。


## 163. 验证候选 helper 拒绝响应的逐消息发送者

使用准确 `19b7065` DEB 的独立 bootstrap，在无网络一次性 Linux amd64/Rosetta 容器运行扩展 `probe-native-verified-parent-exit.py`。父持双锁，子加载验证过的候选内存字节并调用实际 `_serve`；客户端在一次拒绝请求上检查内核 `SCM_CREDENTIALS`，响应发送 PID 与受控 fixture 子进程一致；随后父退出触发测试 watchdog，子退出、双锁释放，stale socket 保留。`VERIFIED_HELPER_REFUSAL_RESPONSE_SENDER_MATCHES_FIXTURE_CHILD` 与 `VERIFIED_PARENT_EXIT_PIDFD_FIXTURE_OK`（`20261028-010539-fe692b.log`），ruff 通过。

仅覆盖单次拒绝响应与该 fixture，不能证明生产 systemd invocation、消息执行者对副作用的因果归属或检查后活性，未开放 dispatch/job 恢复。详细边界见 `docs/native-helper-peer-protocol-review.md`。


## 164. Debian 包 sender 直接子进程退出不证明进程树退出

`probe-native-package-descendant.py` 在无网络一次性 Linux amd64/Rosetta 容器安装准确 `19b7065` DEB，仅容器内将 `/usr/bin/dpkg` 替换为有限寿命 fixture；通过包内真实 `Debian.apply`、独立 snapshot guard 和 package EX 运行该替身，不执行真实包更新。直接替身进程退出后，guard 已发布 `sender_reaped`，其后代仍运行，且包锁可再次取得：`PACKAGE_DIRECT_CHILD_RECEIPT_WITH_DESCENDANT_ALIVE_AND_LOCK_FREE`（`20261028-010851-03d7d6.log`）。fixture 后代自然有限退出，容器随实验结束。此证明所测守卫只等待直接进程，不声称真实 dpkg 必然留下后代。

当前恢复新增临时默认拒绝：Debian 适配器或含 `runtime_snapshot_sha256` 的包事务，不以旧直接子进程回执推导安全恢复；既有记录及围栏不改。真实 job 恢复仍先由独立 job 默认拒绝门禁阻断；测试替身只用于验证既有逻辑。源码回归 1143 通过、4 跳过（`20261028-011109-b2fdfc.log`），新包及真实隔离验收待完成。后续需要版本化 subreaper/进程树回执、退出到 ECHILD、崩溃和锁生命周期验证，旧回执不可升级，生产派发保持关闭。


## 165. 包进程树恢复门禁的准确 DEB 隔离验收

从干净检查点 `af0d8b54aa8e0af94730340ac154a0c8c93f9a19` 全新目录离线构建 wheel、payload 和未签名开发 DEB。Device wheel SHA256 `12fb0548d1c03661a9b0f846f9d65a944a7b49f293639a2327d6601061a5bc9f`，六核心模块逐字节匹配 Git（`20261028-011924-978d15.log`）。DEB `build/native-packages-p1-af0d8b5/ai-device-native-client_0.2.2-1_amd64.deb`，109817922 字节，SHA256 `5faae0ec5f2787398718e15411307c4c8c3cf588ad429360b349524122b507a1`（`20261028-011836-1eacb4.log`）。

无网络一次性 Linux 容器干净安装，对照源码核验主 runtime 六模块及 bootstrap 四模块，重装保护拒绝（`20261028-011939-22fe15.log`）。独立隔离 systemd 实验观察 `PRODUCTION_JOB_RECOVERY_GATE_REFUSED_WITHOUT_WRITES` 与 `PRODUCTION_PACKAGE_TREE_GATE_REFUSED_WITHOUT_WRITES`；随后只在测试进程替换两个门禁以回归旧内部逻辑，真实候选包钩子仍拒绝，active 不变（`20261028-011958-113071.log`）。此 job fixture 解包 DEB 并重装同批 wheel，不是干净安装或真实升级成功证据；专属测试容器已停止（`20261028-012035-c00eb3.log`）。P1 未完成；生产派发、job 恢复及未经证明的包树恢复仍默认拒绝，旧 snapshot 不自动获得新门禁。


## 166. 包进程树候选 guard 与崩溃、持久化边界

先在准确 `af0d8b5` DEB 的一次性无网络 Linux 容器，以包内准备阶段 `TREE_GUARD` 和真实 package lease 做独立 fixture：正常情况下脱离会话的后代结束并被回收后才有回执；guard 被仅针对该 fixture 的 SIGKILL 杀死时，后代仍存活、锁已释放、没有回执（`20261028-012338-b19c06.log`）。这是机制实验，不是包事务生产实现。

新增未接入生产的 `native_package_sender.py` 候选：固定 `/usr/bin/dpkg --install`、`debian-package-tree-subreaper-v1` intent、版本化回执、subreaper 与 ECHILD、文件及目录 fsync；已有回执和协议不匹配在调用 dpkg 前拒绝。准确 DEB 安装后仅将候选源码作为只读实验输入，容器内把 dpkg 换成 fixture：后代被回收前不发布回执、guard 崩溃无回执且后代存活时锁已释放、协议漂移和已存在回执拒绝包动作（`20261028-012653-67a9a4.log`）。同一实验使用测试专属代码注入目录 fsync 失败，link 后回执可能可见，进程失败，应视为持久性 unknown，不能据文件存在授权恢复。该探针未安装或运行真实包更新，candidate 尚不在此 DEB 中。

候选未绑定正式 apply intent、worker、快照及现有只读观察者；旧 snapshot/回执不自动升级。需解决失败后持久性、进程树真实覆盖、生产调用防重放及受控身份/协调，方可考虑接入；生产派发、job 恢复与未经证明的包恢复仍默认拒绝。以上实验不代表完成 P1 或实机验收。


本轮 Mac 完整回归 `1143 passed, 10 skipped`，其中新增六项 Linux-only 单测按平台跳过（`20261028-012754-943825.log`）；以准确 DEB 的隔离 Linux Python 和只读本地纯 Python pytest 依赖再运行新增六项，`6 passed`（`20261028-012919-658d5a.log`）。该结果不替代真实包事务、崩溃持久化或生产集成验收。

自审将候选 guard 的 package log 同步移至后代完全回收之后，避免后代还在写时提前 fsync；隔离实验断言后代日志内容且其余故障边界复验通过（`20261028-013007-02a617.log`）。最终 Mac 回归 `1143 passed, 10 skipped`（`20261028-013023-23b033.log`），Linux 新增测试 `6 passed`（`20261028-013109-b9ff1c.log`）。


## 167. 候选回执结构及类型敏感绑定

对未接入的包树候选增加纯只读 `valid_tree_receipt`：要求外部提供预期 intent 和准确协议、固定完整回执字段及真实 int 返回码，并对嵌套 JSON 做类型敏感比较；`true/1`、`false/0`、意图漂移、未知/缺失字段、旧 sender 回执及非有限数字均拒绝。外部必须从独立受控事务获取预期 intent，不得用回执自报值反证自身；结构匹配不证明来源、目录 fsync 成功或恢复授权。

真实隔离 fixture 对 guard 所产生的回执验证正例及意图不匹配负例，同时复验 guard 崩溃/可见回执目录 fsync 失败等边界（`20261028-013326-a2d144.log`）；Mac 完整回归 1144 通过、10 跳过（`20261028-013348-bf291c.log`），隔离 Linux 候选单测 7 通过（`20261028-013435-a44cc3.log`）。候选仍未连接生产调用，派发及恢复默认拒绝不变。


## 168. job 只读观察的嵌套证据类型敏感比较

审查旧协议发现只读 `observe_job` 使用 Python 字典相等比较 apply intent 及 sender/outcome 内的 intent，会将 JSON `true` 与 `1` 视为相同。以人工构造的数值版本事务作边界 fixture，sender 和 outcome 嵌套版本改为 `true` 后仍返回 `package_outcome_recorded`；两项先失败复现（`20261028-013633-1002c2.log`）。这只证明该只读诊断误认类型一致，不证明真实合法版本类型、重复包动作或生产恢复已发生。

在只读观察中用类型敏感 JSON 比较 apply intent、sender/outcome 中完整 intent 和成功包观察，不修改旧记录、不扩大回执或启用恢复。覆盖四条类型漂移、合法正例和拒绝前后文件不变；相关回归 117 通过（`20261028-013724-f13e1f.log`），完整 1148 通过、10 跳过（`20261028-013733-ed8b57.log`）。旧冻结 snapshot 不自动获得修复；此前 `af0d8b5` DEB 不包含该修复，准确新包待验证。生产 job 派发和恢复门禁不变。


## 169. job 观察修复的准确 DEB 验收

以干净 `ef7fa12d64e22b332002c1bcfda0c002ccb3e5c9` 离线新构建 wheel 与未签名开发 DEB。Device wheel SHA256 `10847294a0390761b47908ea30f17e01e5ded92497064ce5f72598c53755e401`；七核心模块含新增候选 sender 与 job 修复均逐字节匹配 Git（`20261028-013920-42d1e1.log`）。DEB `build/native-packages-p1-ef7fa12/ai-device-native-client_0.2.2-1_amd64.deb`，109815438 字节，SHA256 `5a981f88c8795f8bda3f8824724e526e6c56c43db0f9c69b10060d13e7402327`（`20261028-013921-573623.log`）。

独立无网络容器干净安装、主 runtime 六模块/bootstrap 四模块与源码比对及重装保护通过（`20261028-014015-c8b3d6.log`）。在独立干净安装的包内解释器中确认 job 模块路径并运行四条诊断类型漂移负例：4 通过（`20261028-014102-3d9d3d.log`）。另在隔离 systemd 中双恢复门禁拒绝且无写入，测试替身之后才运行旧内部逻辑，包钩子仍拒绝、active 不变（`20261028-014018-b0dc5e.log`）；该 fixture 解包后重装同批 wheel，不代表真实升级成功。专属 systemd 容器已停止（`20261028-014105-bc9fe4.log`）。候选 guard 虽含在 wheel 中仍未接入生产；P1 未完成。


## 170. 候选包树 guard 的孤立日志拒绝

使用准确 `ef7fa12` DEB 干净安装，在运行实验前逐字节比对容器只读候选代码与包内模块；仅容器内替换 dpkg 为测试 fixture。对无回执但已有 package log 的普通文件与悬空符号链接，`open('xb')` 均在 fixture 包动作前拒绝，保留原文件或链接且不生成回执；同时重新验证协议漂移、后代收口、既有回执、guard 崩溃及目录 fsync 未知边界（`20261028-014259-45c960.log`）。

这是 guard 本地行为，不证明旧 worker 遵守本协议、持久 intent 的单次派发或所有失败状态均可安全重试；生产候选仍未接入，原默认拒绝和保留 unknown 不变。


## 171. dormant job 快照补充锁模块身份校验

现有 dormant dispatcher 只对 `native_update.py`、`native_update_job.py` 和 `native_helper.py` 比较快照与当前模块字节，未覆盖 helper 实际导入、控制实例互斥的 `management_lock.py`。仅在测试中替换生产派发默认拒绝门禁、虚构快照锁模块新旧摘要差异后，原实现仍可创建 job intent（`20261028-014603-9041b1.log`）；不代表真实生产派发已开放或真实旧快照已运行。

新建 job intent 前增加锁模块精确摘要比较，漂移时在发布 intent 和调用 runner 前拒绝；保留既有 intent 原有只读/不重放行为，不自动修改旧快照。定向回归 118 通过（`20261028-014656-919acb.log`），完整 1149 通过、10 跳过（`20261028-014706-3cc067.log`）。此四模块校验只是必要子集，`native_bootstrap`、`native_candidate` 等其余依赖、包树候选和完整 worker 版本封闭仍需明确，不能据此证明身份或开放生产派发；原恢复门禁不变。准确新包尚未构建。


## 172. 快照锁模块校验的准确 DEB 验收

从干净 `a9e3bdc8bdf1d49e3074f5fb2dc7cb2dbc89f5c5` 离线构建新 wheel、payload 与未签名开发 DEB。Device wheel SHA256 `5728f6489e2507e3835dfb7605b106e6a3b0a3f1ed71104ff9ee3eafcc9724ff`；七核心模块逐字节匹配 Git（`20261028-014854-5d7469.log`）。DEB `build/native-packages-p1-a9e3bdc/ai-device-native-client_0.2.2-1_amd64.deb`，109817516 字节，SHA256 `aeea9832291d111a44bac128b887fe33796ede9704098e20d47fb37786d8bc09`（`20261028-014855-e16a00.log`）。

干净无网络容器安装、主 runtime 六模块/bootstrap 四模块与源码比对及重装保护通过（`20261028-014957-57d84b.log`）。包内 job 模块运行锁快照漂移测试通过（`20261028-015048-f94048.log`），其中测试明确替换生产派发门禁，实际 runner 未被调用；初次测试因 macOS 测试依赖目录优先于包内 Linux `pydantic_core` 而导入失败（`20261028-015001-32bdb9.log`），修正路径优先级后使用包内 Linux 原生依赖重跑。

独立隔离 systemd 验证生产派发以及两个恢复门禁均拒绝，后续仅测试进程替身执行旧逻辑，包钩子仍拒绝且 active 不变（`20261028-015004-334220.log`、`20261028-015100-7313fd.log`）；fixture 解包后重装同批 wheel，并非真实升级成功。专属容器已停止。P1 未完成，其他 worker 依赖闭包、准确调用身份和新包树协议仍未完成。


## 173. dormant job 的本地 Python 源码集合校验

在保留原四个关键模块校验外，新 job intent 创建前增加当前 `device_runtime` 和 snapshot 中递归 `.py` 相对路径集合及 SHA256 比较；拒绝缺失、额外、字节变化、Python 文件或子目录符号链接及不可读目录，忽略 `__pycache__`。该校验只约束本地 Python 源码，不保证运行期间代码身份、不证明第三方依赖或解释器一致性，也不保证 `.pyc` 不被读取；`-B` 不等于忽略已有字节码。旧 snapshot 和已存在 intent 不自动升级或重新派发，生产派发和恢复门禁不变。

测试覆盖源码集合内容、增删和链接漂移；另用测试门禁替身验证 `native_candidate.py` 漂移在创建 job intent 和调用 runner 之前拒绝，相关回归 125 通过（`20261028-015401-5bb72b.log`）。在安装准确 `a9e3bdc` DEB 的隔离 Linux 容器中，通过其现有 snapshot 实现复制完整运行时；以只读挂载候选新 job 代码检查实际源码树一致，并在仅容器内更改快照候选模块后拒绝（`20261028-015511-39d311.log`）。这不是新代码的准确包或真实 job 验收。源码完整回归 1156 通过、10 跳过（`20261028-015425-37b8cc.log`）；新包待构建。生产可用前仍需明确有效加载字节、可执行依赖和可信启动身份，不以源码集合一致替代。


## 174. worker 源码集合校验的准确 DEB 验收

从干净 `14dd76f04b51e6bed8a0e3f0d07771fa52b9de0a` 离线构建新 wheel、payload 和未签名 DEB。Device wheel SHA256 `56a5ae29565c485517f48f915c2bc48d0de43c918dd993f84fd7cd44d91ce68d`；wheel 全部 33 个 `device_runtime` Python 文件的集合与字节均匹配 Git（`20261028-015632-799314.log`）。DEB `build/native-packages-p1-14dd76f/ai-device-native-client_0.2.2-1_amd64.deb`，109821804 字节，SHA256 `8f709286fe12c947fc294cb4e2d526ee505018723ffbe51f40a5e4f9dc0dcc36`（`20261028-015633-173e6e.log`）。

无网络干净安装、核心模块比较与重装保护通过（`20261028-015736-f09626.log`）；使用包内 job 模块、包内 Linux 原生依赖和只读测试依赖运行源码树七项测试通过（`20261028-015739-844ac4.log`），其中 dispatcher 负例仅测试中替换生产门禁。另一个隔离 systemd fixture 验证生产派发、job 和包树恢复门禁均拒绝，测试替身之后才回归旧逻辑；实际包钩子拒绝，active 不变（`20261028-015743-2d0a8a.log`、`20261028-015819-255f31.log`）。fixture 解包后重装同批 wheel，不代表真实升级成功；专属容器已停止。源码集合比较不证明 `.pyc`、第三方依赖或处理者身份，P1 未完成。


## 175. worker 现有字节码边界与快照一致性

以准确 `14dd76f` DEB 内 Python 在无网络一次性 Linux 容器实验：构造相同大小、相同时间戳的源码漂移，以及 unchecked-hash 字节码，`-I -B` 两种情况均读取既有 `.pyc` 并执行其内容；干净安装的 `device_runtime` 有 33 个 `.pyc`（`20261028-015915-9a8e22.log`）。这是隔离测试，不声称正式包已有恶意或错误字节码；证实第173节仅对 `.py` 的比较不足以限定快照加载内容。

将 dormant job 快照比较扩展为 `device_runtime` 下 `.py` 与 `.pyc` 相对路径集合和字节摘要，包含 `__pycache__`，拒绝文件及目录链接、缺失、额外和漂移。比较结果仅说明快照与当前安装文件一致，不证明两者与源码语义一致、不证明第三方依赖及真实 worker 身份；不改变既有 intent，派发和恢复默认拒绝。定向 129 项通过（`20261028-020028-757093.log`）；完整 1160 通过、10 跳过（`20261028-020050-b52e84.log`）。以准确 `14dd76f` 包内真实 runtime 创建独立快照，并加载只读候选新代码，验证 33 项字节码正例及容器内修改一项后拒绝（`20261028-020141-412e44.log`）；这不是包含新代码的新包验收，新包待构建。


## 176. worker 字节码一致性检查的准确 DEB 验收

从干净 `0fde77aad6c25a77d92f37d4ae3403806cb534c7` 离线构建 wheel、payload 和未签名 DEB。Device wheel SHA256 `c33bc9d11b585361791588867dc448685748799cbc0ecaa51b579385174ebe14`，全部 33 个 `device_runtime` 源码文件逐字节匹配 Git（`20261028-020314-290ae9.log`）。DEB `build/native-packages-p1-0fde77a/ai-device-native-client_0.2.2-1_amd64.deb`，109821082 字节，SHA256 `3f0c6b8601b0ddcfe8bec46929131642c4b2f66d88a5f71431aa01143e3327d6`（`20261028-020316-d30938.log`）。

隔离干净安装、核心源码比对及重装保护通过（`20261028-020439-917934.log`）；使用包内 job 代码与包内 Linux 原生依赖运行源码和字节码集合相关 11 项通过（`20261028-020442-5d7672.log`），dispatcher 负例仅在测试中替换生产派发门禁。独立隔离 systemd 实验中生产派发及两个恢复门禁仍拒绝、无 intent/恢复写入，随后仅测试进程替身执行旧逻辑；原包保护拒绝、active 不变（`20261028-020446-1036a5.log`、`20261028-020524-cf0e2e.log`），专属容器已停止。systemd fixture 解包后重装同批 wheel，不是生产升级验收。字节码集合一致不证明实际执行内容或第三方依赖，P1 继续未完成。


## 177. 真实 helper 只读查询响应发送者隔离实验

在准确 `0fde77a` DEB 的无网络隔离 systemd 容器干净安装、创建普通用户测试实例并通过实际 `register` 注册后，启动真实 helper；对同一 Unix socket 调用实际 `query`，在只读 `scoped_helper_unit_peer` 内以 `SO_PASSCRED`/`recvmsg` 核对单一完整 SCM_CREDENTIALS、unit peer PID 与 root UID，验证真实查询响应成功、实例和操作 ID 正确、无启动意图且 scope 明示不能派发。输出 `REAL_HELPER_QUERY_RESPONSE_CREDENTIALS_MATCH_UNIT_PEER` 与 `QUERY_ONLY_NO_START_INTENT_AND_NO_DISPATCH_AUTHORIZATION`（`20261028-021218-dc102b.log`）。

此实验只证明一次只读查询及响应发送者与观察到的 unit peer 一致；不证明消息处理者与任意不可逆动作的因果绑定、实际加载代码身份、观察后仍存活或原子 invocation 绑定。无真实设备或升级动作，生产派发和恢复默认拒绝不变。测试容器仅属本次实验并已停止。


## 178. 包树候选回执与外部 systemd 工作的边界

在一次性无网络隔离 systemd 容器中，以本工作区未接入生产的 `TREE_GUARD` 和容器内替身 dpkg 做负例：替身通过 `systemd-run` 创建等待释放的独立 unit；guard 正常退出并发布绑定 intent 的 `package_process_tree_reaped` 回执后，unit MainPID 仍存活，且 `/proc` 显示其父进程为 PID 1。输出 `TREE_RECEIPT_EXISTS_WHILE_EXTERNAL_SYSTEMD_WORKER_ALIVE`、`EXTERNAL_WORKER_NOT_ADOPTED_BY_SUBREAPER_NO_RECOVERY_AUTHORIZATION`（`20261028-021913-27e95b.log`）；释放 fixture 后自然结束，专用容器已停止。

此实验只验证外部委派超出 subreaper 收养范围；替身不是实际 dpkg/真实包钩子，不证明当前包有外部委派或发生真实升级。候选回执不能单独作为所有包相关工作已终结或恢复许可的证据；应审计受支持包及钩子的实际外部工作并设计可信绑定与终态证明。生产派发和恢复仍默认拒绝，旧 snapshot 不自动获得约束。

准确 `0fde77a` DEB 经 SHA 校验后只读提取控制目录（`20261028-022022-7b3604.log`），仅包含 `control`、`preinst`、`prerm`；两个钩子均为路径拒绝检查，无直接 systemd 调用。该结论仅针对该开发包自身控制文件，不覆盖宿主 dpkg 配置、其他包触发器、旧已安装包的钩子或未来候选，不能据此启用恢复。


## 179. 匹配 unit 的响应发送者不证明动作执行者

准确 `0fde77a` DEB 在无网络隔离 systemd 容器中干净安装；fixture 以独立 unit 占用测试专用 helper unit 名，主进程接收固定请求、fork 子进程写入 fixture 文件并等待其退出，再由主进程发送响应。使用准确包内 `scoped_helper_unit_peer` 做前后 unit/pidfd 只读观察，以 `SO_PASSCRED` 检查响应 sender PID 与 unit peer 相等，同时读取 fixture 动作 PID，确认动作 PID 不同；输出 `MATCHING_UNIT_RESPONSE_WITH_DISTINCT_FIXTURE_ACTION_PROCESS` 和 `RESPONSE_CREDENTIALS_DO_NOT_PROVE_ACTION_PROVENANCE`（`20261028-022213-9e93a8.log`）。专用 fixture 自行结束，容器停止。

该例不是实际 helper 或真实升级行为，只是严格限制第177节一次真实只读 query 的结论：响应来源匹配不能单独证明业务副作用实际处理者，更不能建立受控 invocation／worker／guard 的原子绑定。仍须设计受控进程树、请求和终态协调并覆盖故障与旧版本；生产派发与恢复门禁保持关闭。


## 180. 当前源码的 job worker 新动作默认拒绝

检查发现新 job 派发虽默认拒绝，但已有 job intent 仍可进入 `Updates.apply(..., job_intent_sha256=...)`；使用有效 intent、测试替身包管理器及快照校验替身的回归先失败（`20261028-022447-0bd298.log`），说明当前源码未对该入口默认拒绝，**不证明真实旧 job 已执行或真实包动作发生**。

当前源码在实例锁→package EX→service lease 内、close 标记检查后、新 apply intent 创建之前，对带 job 摘要的新动作调用 `_require_proven_job_execution_fence` 并默认拒绝。原 job intent/围栏保留，不删除、不重试；已有 apply intent 仍走原只读观察，直接 apply 保持原有行为。worker 内部旧校验测试通过显式测试替身绕过该新门禁以覆盖原逻辑；真实门禁测试不替换它。定向 131 项及 ruff 通过（`20261028-022540-d34e9e.log`），完整 1162 项通过、10 项跳过（`20261028-022548-5959a9.log`）。

此收紧不影响已冻结的旧 worker snapshot、已启动进程或真实 package guard，不证明 worker 身份、完整进程树或 invocation 绑定；旧环境仍须管理员维护窗口手动退休。真实准确新包验收尚未完成，派发和恢复继续默认拒绝，包覆盖保护不放宽。


## 181. job worker 新门禁的准确 DEB 与 systemd 验收

从干净 `8b52ab92f77d6847cd13cc251a394211e952f02b` 源码用锁定离线输入构建未签名开发 DEB：`build/native-packages-p1-8b52ab9/ai-device-native-client_0.2.2-1_amd64.deb`，109822010 字节，SHA256 `f082342286f9b50f15eae29dc1a049fcdef161400fc85f693dbbc095b6070bc6`，Device wheel SHA256 `2388a714f3e09cd2f0062f7439bbfd6c842ad8dd511f5f7f22f3d15c07689524`；wheel 33 个 Python 源码文件与提交逐字节一致（`20261028-022837-31e513.log`）。

无网络容器干净安装该 DEB，比较主 runtime 六模块及 bootstrap 四模块，原地重装被包保护拒绝；独立干净安装后使用包内 runtime 运行新门禁及已有观察测试，3 项通过（`20261028-022853-9d33ab.log`）。无网络隔离 systemd 中采用旧版 inert source fixture，解包新 DEB 并重装同批 wheel、创建普通用户实例、登记批准和独立 snapshot；原生产新派发拒绝且不创建 intent，再人为建立固定测试 job intent，由 systemd 调用 snapshot 实际 worker，确认 stderr 为新门禁拒绝，job intent 不变，无 apply/sender/outcome，active 不变（`20261028-023043-b42855.log`）。该 systemd fixture 不是干净安装或真实升级证据。

首次 systemd 实验仅确认失败但 journal 无输出，不能确认原因（`20261028-023007-ea69ad.log`）；随后在新容器为测试单元指定专用 stderr 文件才完成复验。两个专属容器已停止，不触及真实服务。旧 snapshot、在途 worker 与正式发布仍未验证；RPM 未构建，生产派发和恢复门禁不开放。

验收脚本已保留为 `scripts/accept-native-worker-default-deny-8b52ab9.sh`，固定准确包 SHA 并在全新隔离容器重跑通过（`20261028-023139-b2d484.log`）；仍是解包加重装 wheel 的 fixture，不扩大上述证据范围。


## 182. 关闭与退休目录同步未知时保留 job 围栏

使用真实 `_save_new` 在目标 hardlink 后注入目录 fsync 失败：`close_before_apply` 返回失败但 close 文件可见，随后旧退休路径读取该文件并发布 retired，退休目录 fsync 也失败而文件可见；旧 `_job_retired` 仍将完整结构判为已退休（先失败 `20261028-023534-545001.log`）。这是受控故障注入，不是断电实验，也不证明真实包或服务动作发生；但证明可见文件不能当作两个持久发布已成功的依据。首次注入因命中更早目录操作而未到目标，不作为复现证据。

当前源码在新的崩溃安全退休协议证明前默认拒绝 `retire_closed_job`，不产生新退休证据；`_pending_package_jobs` 不再根据既有退休记录跳过 job intent，因此当前 helper 的 start 和直接 apply 继续被原围栏阻断，所有旧记录保留。保留 `_job_retired` 的原结构校验仅供诊断和旧路径测试，不代表持久性或授权。隔离故障测试显式替换退休门禁以重现旧发布行为，验证可见退休文件结构虽正确，start 与新 ID 包动作仍被挡住、原证据不变；真实门禁无替换测试验证不写入。旧退休内部测试仅用测试替身继续覆盖历史逻辑。定向 304 通过1跳过（`20261028-023654-ee451d.log`），新故障与门禁测试2项通过（`20261028-023726-cb0c8d.log`）；完整1164通过10跳过（`20261028-023734-b16e63.log`），ruff通过。

关闭记录与退休记录仍可能处于持久化未知，不得自动删除、清理或按存在放行。该收紧仅覆盖更新后的 helper 和调用方，不自动约束旧 bootstrap、旧 snapshot 或运行中的进程；管理员维护窗口和旧实例手动退休前提不变。准确新包及真实隔离 systemd 验收尚未进行，派发／恢复默认拒绝继续保持。


## 183. 退休未知围栏的准确 DEB 与真实 helper 验收

以 `70d1ef20dd4c53282b313f86613ae5c8733e4d78` 干净源码离线构建未签名 DEB：`build/native-packages-p1-70d1ef2/ai-device-native-client_0.2.2-1_amd64.deb`，109822192 字节，SHA256 `40b0755e0a47f343a100e21edc29d9d0ab6ad59283e41aa9fc4e54cb61d6bed6`；Device wheel SHA `839cc9d8e33756adcdc9a8156f069758bf5ab1727edf0c0100054f28004e1d77`，33 Python 源文件与 commit 逐字节一致。无网络干净容器安装、主 runtime 六模块和 bootstrap 四模块 cmp、覆盖保护通过（`20261028-024027-9cf35a.log`）；包内 runtime 对退休门禁、关闭/退休两次目录 fsync 故障及 worker 门禁共3项通过（`20261028-024043-56b705.log`）。故障测试明确只在测试里替换退休门禁以复现旧发布，生产门禁未替换。

无网络隔离 systemd 中干净安装新包、普通用户安装并注册实例、启动真实 helper，构造结构完整的旧 intent/close/retired 测试记录；结构校验为真但仍处于待处理集合。真实 helper 直接入口确认 `package transaction blocks start`，socket 返回拒绝，原三份证据不变、无启动 intent，Device unit inactive/MainPID 0（`20261028-024222-89eb45.log`，脚本 `scripts/accept-native-retired-job-fence-70d1ef2.sh`）。首次实验误解 query 返回封装，在请求 start 前失败（`20261028-024143-49064c.log`），修正后全新容器重验通过，两容器均停止。

这是新包 helper 的测试，不能约束旧 helper、旧 snapshot 或在途进程；不证明崩溃后的文件系统持久性或旧 worker 已退休。RPM 尚未构建，生产派发、恢复与退休放行仍禁用。


## 184. 旧 bootstrap 不自动获得退休围栏

在一次性无网络 Linux 容器中干净安装旧准确 `8b52ab9` DEB，使用该包独立 bootstrap 的真实 `native_helper`，在 root 所有、祖先不可写的 `/run/old-retirement-test` 生成结构完整的 intent/close/retired；旧 `_job_retired` 为真且 `_pending_package_jobs` 返回空（`20261028-024334-3af117.log`）。因此第182节新围栏不能追溯覆盖旧 bootstrap，升级和退休须继续遵循管理员维护窗口，不得把新包测试当旧进程证明。首次以 `/tmp` fixture 运行因其祖先可写导致可信检查失败（`20261028-024309-5497a2.log`），不是有效旧行为证据。该实验只读判断隔离记录，不启动服务或执行包操作。


## 185. 旧 start sender 屏障不能授权后续动作

当前实现的旧启动 intent 缺失 `execution_barrier` 或使用版本1时，`resolve-running` 可在无 sender 回执下仅凭当前运行观察与锁空闲写 `confirmed_running`；已有旧恢复记录也能被新 start 和 package apply 消费。针对两种旧 intent、无回执和已有恢复记录的四项测试修复前均失败（`20261028-024646-89ed24.log`），测试使用适配器替身，不证明实际设备已出现重复动作。旧 sender guard 异常退出时子进程可能仍存活，单靠锁空闲无法确认完成。

当前源码只允许 execution barrier 严格整数2的 intent 进入新 resolve-running/resolve-stopped；消费已有恢复记录以授权新 start 或 package apply 时也检查该屏障，旧记录保留，可由 query/reconcile 只读查看，不授权新动作。关闭前的旧 helper 或 snapshot 不自动获得这一门禁，不自动删除或迁移旧记录；需要管理员在维护窗口核实并按后续协议处置。版本2回执仅证明直接 sender 已回收，不宣称完整进程树或 systemd 外部工作已终结。helper/update/job 定向310通过1跳过（`20261028-024742-e26cad.log`）；完整1170通过10跳过，ruff通过（`20261028-024806-1591d3.log`）。准确新包验收待完成。


## 186. 旧启动屏障的准确 DEB 及真实 helper 验收

从 `85657189cae7d78fc2157255477350735f76604f` 干净源码离线构建未签名 DEB：`build/native-packages-p1-8565718/ai-device-native-client_0.2.2-1_amd64.deb`，109823294字节，SHA256 `f37cf12b960824e46ae7de38ac71b8027d384016aa1469d07b165ce8fd7b0eec`；Device wheel SHA `6251721d38dbdb83bcbeb75fc3844d5927d6fcc8ab168ad891137d094349120a`。wheel 33 Python源文件与 commit 一致；干净安装、主 runtime 六模块及 bootstrap 四模块核对和原地覆盖保护通过（`20261028-025037-bc0231.log`）。包内7项旧屏障/包事务测试以普通用户通过（`20261028-025121-1042ac.log`）。首次 root 运行 helper 测试因夹具目录所有者不符导致4项失败、3项通过（`20261028-025052-0bfda8.log`），未当作产品通过。

无网络隔离 systemd 中干净安装并注册普通用户测试实例，启动真实 helper，构造旧 barrier 1 intent 和 unknown result：直接 resolve-running/resolve-stopped 均因旧 sender 未证明而拒绝、不创建 recovery；构造历史 recovery 后，真实 socket query 可读，直接 start 指明旧 sender 拒绝，socket start 返回拒绝，原记录不变、无新启动 intent、测试 Device unit inactive/MainPID 0（`20261028-025204-cf692e.log`，脚本 `scripts/accept-native-legacy-start-fence-8565718.sh`）。容器已停止。实验没有启动测试 Device，不证明真实旧 sender 或宿主故障已处理；旧 helper 和旧 snapshot 不自动获得修复，RPM 未构建。


## 187. start sender 回执不证明后代退出，未知恢复默认拒绝

准确 `8565718` DEB 在无网络隔离 Linux 容器中运行包内真实 `Systemd.start` guard；固定 `systemctl` 替身启动一个有限寿命后代后退出。观察到真实 `sender_reaped` 回执已发布、execution/package 锁可重新取得时该后代仍活着，释放后能继续写 fixture（`20261028-025504-fa28ee.log`，`scripts/probe-native-start-sender-descendant.py`）。此替身不证明实际 systemctl 一定产生后代，也未调用真实 Device 或 systemd；它证明直接子进程回执与锁空闲不能单独证明 sender 全部后代完成。

原版本2直接回执可驱动 resolve-running 和 resolve-stopped 写确认，两个针对该默认拒绝的回归修复前失败（`20261028-025536-0ab86a.log`）。当前源码在已验证 execution_barrier 2 后，默认拒绝新的 start 恢复确认，并禁止已存在版本2恢复记录授权新的 start 或包 apply；query/reconcile 保持只读，原 intent、sender 和 recovery 保留。旧 barrier 仍按第185节拒绝。现有恢复机制内部测试明确使用 `legacy_start_recovery_mechanics` 替身，不代表生产开放；无替身测试覆盖两类 resolve、已有 recovery 的 start 和包 apply 均被拒绝。定向314通过1跳过及ruff通过（`20261028-025756-29c729.log`），完整1174通过10跳过（`20261028-025810-d1fb21.log`）。首次调整后旧机制测试58项失败，随后仅对明确列出的用例启用测试替身；不把替身当产品门禁。

该收紧不处理已记录 `started`/`already_active` 的其他准入，也不证明真实 systemctl 后代、外部 systemd 工作、旧 helper/snapshot 或崩溃安全终态；这些仍待审计。旧记录与旧进程不自动迁移、清理。准确新包验收待完成。


## 188. 已记录 started 的后续动作也需 sender 树证明

第187节之后复核发现 `Updates.apply` 对旧 start 结果为 `started` 时绕过恢复检查，仅凭服务停止及包锁空闲即可继续；测试替身修复前未拒绝（`20261028-025956-e2ba78.log`）。真实 sender guard 只回收直接子进程，第187节证明替身后代可在回执和锁释放后继续动作，因此当前源码在已有 `started` 台账时，也对新 start 和包 apply 默认调用未证明的 sender 树门禁，拒绝新动作，不写新意图；原结果仍可只读查询。`already_active` 分支没有执行 sender，保持原行为；本修复不是对其全部故障场景的结论。

定向316通过1跳过（`20261028-030052-8f519e.log`），完整1176通过10跳过、ruff通过（`20261028-030106-847ed3.log`）。这是一项明确的可用性收紧：直到可信进程树和外部动作终态协议完成，存在 `started` 台账的后续新启动/包更新可能保持阻塞；不自行删除台账或绕过。旧 helper/snapshot 不自动获得新门禁，真实 systemctl 后代与宿主故障未验证，准确新包验收待完成。


## 189. sender 树门禁的准确 DEB 及真实 helper 验收

从 `09486d1319cfac4b9cb9d85292d041906e4c161d` 干净源码离线构建未签名 DEB：`build/native-packages-p1-09486d1/ai-device-native-client_0.2.2-1_amd64.deb`，109820404字节，SHA256 `156ca247130f6a0808b9cd82cb4a4d062b4d255de08c7f5c5ed312e2c72a3792`；Device wheel SHA `64f6beea3c6f3ff9cbb2d0dfc71b1074f6befb0825b4b424a4759ff4a9b07db9`，33 Python 源与提交逐字节一致。干净安装、主 runtime六模块及 bootstrap四模块比对、原地覆盖保护通过（`20261028-030335-72d419.log`）；以普通用户运行包内6项 sender树门禁测试通过（`20261028-030353-5c925d.log`）。

无网络隔离 systemd 干净安装并注册普通用户实例，启动真实 helper，构造版本2 unknown intent 和直接 sender 回执：直接与 socket 的 resolve-running/resolve-stopped 拒绝且不写 recovery；构造历史 recovery 后 query 可读，但 direct/socket 新 start 拒绝；替换测试 result 为 `started` 时 direct 新 start 也拒绝，原测试 result 恢复，无新 intent，Device unit inactive/MainPID 0（`20261028-030439-9a90cc.log`，脚本 `scripts/accept-native-sender-tree-fence-09486d1.sh`）。这是构造台账，不是在真实 systemctl 制造后代或完整恢复验收；仅替身实验说明直接 sender 回执不覆盖后代。隔离容器已停止，RPM 未构建，旧 helper/snapshot 不受新源码自动保护。


## 190. 未接入生产的 start sender 子孙回收候选

新增 `native_start_sender.py`：候选协议 `systemd-start-tree-subreaper-v1` 在固定 `/usr/bin/systemctl start` 前启用 Linux subreaper，直接 sender 完成或超时后继续等待收养后代直至 ECHILD，再以文件 fsync、无覆盖 link 和目录 fsync 写版本化回执；回执结构使用类型敏感完整 intent 比较。候选不被当前生产调用；调用者仍须独立保障固定 runtime、intent 与执行/包锁的持有，不能将结构匹配当作来源、持久性或恢复授权。守卫持锁时间不保证有界；guard 异常退出不会终止后代。

`probe-native-start-tree-candidate.py` 在无网络一次性 Linux PID1 容器中替换容器内 systemctl 为惰性 fixture，验证旧协议与已有文件/悬挂符号链接在动作前拒绝、后代活动时无回执且继承的 lease 持续被守卫持有、正常退出后才发布回执；杀死该测试守卫后，锁空闲但后代仍活且无回执；测试专用缩短超时显示直接 sender 超时后仍等待后代；在 link 后注入目录 fsync 故障时回执可见但终态未知（`20261028-031552-3b5947.log`）。不涉及真实 systemctl、Device 或掉电，不证明外部 systemd 工作结束。完整 Device 回归 `1177 passed, 16 skipped`（macOS，6 个新增 Linux 单测跳过，Linux fixture 独立覆盖对应拒绝）；ruff 通过。旧 helper/snapshot 和现有 unknown/started/包更新门禁均不改变，P1 未完成。


## 191. start sender 树完成不覆盖 systemd unit 生命周期

使用第190节未接入候选，在无网络一次性隔离 systemd 容器启动独立惰性 fixture unit：guard 返回并发布 sender-tree 回执时，unit 的 MainPID 大于1、InvocationID 非空、进程存在且 PPid 为1；fixture 释放后自然完成，容器停止（`20261028-031711-6d34bf.log`，`scripts/probe-native-start-tree-external-systemd.py`）。这验证 sender 的收养树不等于 systemd 管理的 unit 或其外部工作，不能依据 sender 回执单独断言 unit 终态或授权恢复。start 成功后服务继续运行本身是预期行为；实验未启动真实 Device，也不证明业务故障或其安全恢复方式。真实 invocation 绑定、并发重启/排队 job 与崩溃持久化仍须解决，生产默认拒绝不变。


## 192. 受控 worker 占用与外部工作隔离模型

新增纯测试模型 `tests/test_native_worker_coordination_fixture.py`，使用 spawn 独立进程、非阻塞独占 flock、排他创建 intent、文件和目录 fsync，测试同一 lease 的两个合作 worker：首个写入 intent 并持锁时，竞争者返回 busy；正常完成、intent 后进程退出及副作用后进程退出三种情况下，释放锁后竞争者仍因原 intent 返回 occupied，不重放动作。第三方独立 fixture 不参与该锁与意图，证明 worker 在 intent 后退出、锁释放及后续 worker 被阻止时，外部工作仍能继续并产生自身副作用。4 项测试重复三次通过（`20261028-032009-32f729.log`），完整回归 `1181 passed, 16 skipped`（`20261028-032013-f63087.log`），ruff 通过。

这些是当前宿主 macOS 的合作进程模型，不是生产 worker、真实 systemd、断电或原子恢复证明，也不验证受控 handler、实际 invocation 身份、真实外部动作、任意系统参与者、记录与作用的原子提交或目录 fsync 失败；记录存在不等于持久性已证明。实验不修改生产代码、门禁或旧环境，不允许删除 intent 以消除阻塞。需要单独设计与外部工作事实源和受控 invocation 关联的可验证终态与崩溃恢复，且所有相关入口必须共同参与。


## 193. 既有更新准备记录重用的 JSON 类型校验

`Updates.prepare` 对已存在 prepared 记录原先使用 Python 字典相等比较，将 JSON `true` 与整数 `1` 视为相等。隔离 fixture 在现有记录将 `schema_version` 改成 `true` 后重新调用真实 prepare 入口，原代码未拒绝（`20261028-032343-933860.log`）；这不证明正常管理流程会自动产生该记录，也不代表包已执行。

修复为使用既有 `_same_json` 类型敏感比较，拒绝已有记录与当前准备事实的类型漂移；记录原样保留，不触发包动作，完全一致的记录仍可重用。新增 schema 布尔漂移、嵌套数值/布尔漂移和正常重用三项回归。更新与 job 定向 `140 passed`（`20261028-032422-04f920.log`），完整回归 `1184 passed, 16 skipped`（`20261028-032432-44e351.log`），ruff 及 diff 检查通过。仅覆盖准备记录重用；不声称所有其他比较已审核，未修改生产派发、恢复及包保护门禁，未构建新包，旧快照不自动获得修复。


## 194. 登记恢复的标记与身份记录类型敏感校验

`register(..., recover=True)` 对管理标记和现有登记记录原先使用 Python 普通字典比较，测试中将嵌套 baseline 的 schema 或 GID `1` 改为 `true`、并对标记变化重新计算登记中的 management SHA；以及将登记记录的 schema 或 GID 改为 `true`，四种情况下真实恢复入口均未拒绝（`20261028-032829-004aeb.log`）。这些是本机隔离 fixture，账户、信任检查和管理锁采用替身，不证明正常系统自动产生异常记录。

改为对标记和登记 JSON 使用现有 `_same_json` 类型敏感比较；systemd unit 文本仍按原文比较，锁顺序、信任检查及已有无覆盖发布未变。四项漂移均拒绝且原有文件字节不变；合法记录保持无写重用；登记冲突且 unit 缺失时不补建 unit。helper 定向 `184 passed, 1 skipped`（`20261028-032859-c92117.log`）；新增的第六项测试后登记定向 `6 passed`（`20261028-033017-a29085.log`）；第六项加入前完整回归 `1189 passed, 16 skipped`（`20261028-032914-de2fcf.log`）；ruff 和 diff 检查通过。未构建新包或执行真实登记；旧 helper/bootstrap/snapshot 不自动获得该修复，生产派发、恢复和包保护门禁不变。


## 195. 更新恢复证据与只读 job 描述的类型敏感比较

隔离测试先复现两处普通字典比较的类型混同：非 Debian 测试适配器已生成恢复记录后，将 `reapplied:false` 改为数字 `0`，原 `Updates.recover` 将旧证据当作相同；构造异常 prepared 包元数据版本数字 `1`、相应 approval 和 apply intent 摘要均重新绑定后，将 apply 目标版本设为 `true`，原只读 `describe_job` 接受该类型不一致（`20261028-033310-63db70.log`、`20261028-033230-e58022.log`）。后者是构造异常证据，不证明正常包元数据会出现数字版本或发生包动作。

两处均改用 `_same_json`；漂移证据拒绝且文件不变，合法恢复记录仍可重用且不重放包操作，job 描述仍只读且不派发。更新/job 定向 `143 passed`（`20261028-033346-8e83cc.log`），完整 `1193 passed, 16 skipped`（`20261028-033357-bc9c83.log`），ruff 与 diff 检查通过。生产 Debian、job 恢复门禁未放开；未构建新包，旧 snapshot 不自动更新。bootstrap 回执、已有 outcome 等其他比较仅发现线索，尚未判断其字段和可达性，不声称全量审计。


## 196. worker runtime 快照类型校验

隔离 fixture 构造已有 runtime snapshot，将 `schema_version:1` 改为 `true`，或将一字节文件 inventory 的 `size:1` 改为 `true`；原 `validate_snapshot` 均接受（`20261028-033543-9a8607.log`）。改为要求 schema 为真实整数1，并对完整 inventory 使用类型敏感 JSON 比较；两个异常均拒绝且不覆盖 snapshot，正常快照回归保留。bootstrap/update/job 定向 `178 passed`（`20261028-033613-fb8b4c.log`），ruff 和 diff 检查通过。

首次完整回归 `1194 passed, 1 failed, 16 skipped`，失败为 `test_client_stop_restart_is_unknown_and_never_reexecutes` 的状态仍 `running`（`20261028-033622-9a7d29.log`）；该测试单独复跑通过（`20261028-033720-c1ff9d.log`），完整重跑 `1195 passed, 16 skipped`（`20261028-033728-7c831e.log`）。该测试等待 peer 移除后立即断言 unknown，可能存在时序敏感性，但根因未证实，本次未修改 WebSocket。新源码的校验不自动更新旧 snapshot，不证明受控运行身份、系统掉电安全或外部 job 终态；未构建新包，现有生产门禁不变。bootstrap 发布回执等其他比较尚未审完。


## 197. helper 候选发布回执的类型敏感重用与读取

隔离 fixture 通过实际 `publish_helper_candidate` 创建快照和候选回执（候选自检用测试替身），随后分别将 `schema_version:1` 改为 `true` 或 `activated:false` 改为 `0`。原发布重用与只读 `read_helper_candidate` 两条入口的四个组合均未拒绝（`20261028-034035-3fa5f6.log`）。两条入口改用 `_same_json` 类型敏感比较；异常回执拒绝且原字节不变，合法发布重用测试仍通过，候选不自动激活。bootstrap/job 定向 `87 passed`（`20261028-034053-cc5fbc.log`），完整 `1199 passed, 16 skipped`（`20261028-034101-9a760d.log`），ruff 与 diff 检查通过。

这是隔离 fixture，不证明正式 bootstrap 已更新或受控 handler/进程身份完成；旧 snapshot 仍需按管理员维护协议处理，不自动迁移。本轮未构建新包、未放开生产派发、恢复或包保护；第196节 WebSocket 单次失败根因仍未确认。


## 198. Debian guard outcome 不匹配后的占用保留

核查当前标准 Debian `apply_intent`：prepared/runtime snapshot 摘要和 dpkg-deb 的 Package/Version/Architecture 均由字符串构成；尚未发现正常路径中可触达的布尔/整数混同，未仅凭假设修改该比较。新增 `Debian` 子类隔离 fixture：在适配器执行时写入错误目标版本的 outcome，实际 `Updates.apply` 报 `package guard outcome intent differs`，已发布 apply intent 和 outcome 保留；同 ID 再调用只返回观察，不再次调用包适配器，相关文件字节不变。测试不运行 dpkg、真实 guard 或 systemd；重复调用的观察不证明 outcome 可信或事务安全完成。

更新/job 定向 `144 passed`（`20261028-034450-b06a4b.log`）；完整 `1200 passed, 16 skipped`（`20261028-034500-99d4aa.log`）；ruff 和 diff 检查通过。本项只新增测试，不更改生产门禁，未构建新包；旧快照和在途动作不自动受保护。外部工作终态、真实 handler/invocation 绑定以及其他持久证据比较仍未完成审计。


## 199. root 连接创建者与请求发送者的身份边界

`probe-native-request-credential-transfer.py` 在无网络一次性 Linux 容器中创建 Unix listener，root 连接后 fork 并使子进程降权至 UID/GID 12345，子进程使用继承的连接 FD 发送请求。服务端 `SO_PEERCRED` 仍是 root 连接创建者；启用 `SO_PASSCRED` 的 `recvmsg` 得到子进程实际 PID/UID/GID（`20261028-034855-407d7e.log`）。先前 socketpair 形式亦通过，但正式证据以 listener/connect 为准。脚本限 root Linux PID1 和特定测试环境变量，运行后 `--rm`，不启动 helper、systemd 或 Device。

反例依赖主动继承 FD，不证明真实客户端会跨身份转交，也不证明现有生产普通动作漏洞。它明确未来 root-only 交接不能单独把连接级 peer UID 当成每条消息发送者或实际执行者；逐消息凭据、分帧、可转交连接能力和 handler/invocation 绑定须另行设计。未修改生产认证、新派发或恢复门禁；无新包、无真实设备操作。


## 200. 逐段发送者凭据及单帧候选隔离验证

新增未接入产品的 `scripts/probe-native-request-frame-credentials.py`。Linux 一次性容器的 Unix stream 接收端在发送前启用 `SO_PASSCRED`，每次 `recvmsg` 要求唯一完整 `SCM_CREDENTIALS`、相同 PID/UID/GID 且 UID 0，限定单个换行帧和最大长度。通过真实 socket 及 fork fixture 验证同一 root 发送者分段可接收；中途降权、非 root、超长、同次读取多帧及断连未完成均拒绝。模拟缺失/畸形/截断控制信息均拒绝；真实 `SCM_RIGHTS` 注入拒绝且检查已接收的 FD 未泄漏（`20261028-035229-0fac9c.log`），ruff 通过。

这是受控接收端实验，不是生产接口、端到端授权或身份认证完成。实验用 socketpair；上一节真实 listener/connect 仅验证连接创建者与发送者差异。生产设计还需验证监听启用时机、超时、分帧与 JSON、发送进程存活及 PID 复用、受控 handler 代码/动作和 systemd invocation 与 worker/外部工作终态；凭据不证明执行者或持久性。异常 SCM 权限和其他内核版本未全面验证。本轮未重跑完整回归、未构建新包，生产派发/恢复/包保护门禁不变。


## 201. 真实 Unix listener 的预排队凭据与请求 deadline

新增未接入生产的 `probe-native-request-listener-credentials.py`，在无网络一次性 Linux PID1 容器用真实 Unix listener/connect：listener 在 bind/listen 前设置 `SO_PASSCRED`，子进程发送数据后父进程才 accept；accepted socket 开关为1，root 预排队请求获得子进程正确凭据。非 root、超长、同次读取多帧、半帧断连及读超时均拒绝（`20261028-035540-4135e1.log`）。

实验还验证单次 socket timeout 不保证整帧限时：两次间隔0.15秒的分片均短于0.25秒，但整帧超过0.25秒仍成功（`20261028-035605-066c93.log`）。在未接入的 `receive_root_frame` 增加可选 monotonic 绝对 deadline，从读取起限定整帧时间，同样慢速输入被拒（`20261028-035641-acb564.log`）；原逐段凭据、异常控制及FD清理实验复跑通过（`20261028-035655-fcdbcb.log`）。ruff通过，容器 `--rm`；不涉及生产 listener、真实 handler、动作、systemd 或设备。deadline 不覆盖 accept 前排队和业务执行，仍须设计整体预算、身份/动作/invocation 绑定、并发和崩溃终态。本轮未重跑完整回归、未构建新包，生产门禁不变。


## 202. helper 请求整帧读取总时限

现有 `_serve` 串行处理连接，每次 `recv` 的5秒超时会随分片重新计时。本机真实 socketpair、假 listener 和假 handler 的回归中，间隔0.5秒发送空白，超过5秒后再发送合法请求，原代码仍调用 handler（`20261028-040019-f76122.log`）。实验未启动真实服务或 Device。

修改生产 `_serve`：accept 后以 monotonic 设定5秒整帧读取截止时间，每次读取使用剩余时间，完整帧后再次检查 deadline；超时进入既有通用拒绝响应，快速合法请求仍调用 handler，响应写入保留原5秒超时。该时限不包括 accept 队列、handler 执行或响应完成，不改变连接级 `SO_PEERCRED`、动作白名单、更新授权及派发/恢复门禁，也不构成逐消息身份绑定。helper/bootstrap 定向 `220 passed, 1 skipped`（`20261028-040148-29a285.log`），完整 `1202 passed, 16 skipped`（`20261028-040208-2e3198.log`），ruff通过。测试使用 macOS socketpair 与假的 peer/监听器；未做真实 Linux helper 系统验收、未构建新包，旧 helper/snapshot 不自动更新。


## 203. 准确包真实 helper 读取截止与拒绝写入时限

从干净提交 `1a58f6d75684532dab33eb4e18ebcb5486a56895` 离线构建未签名 DEB `build/native-packages-p1-1a58f6d/ai-device-native-client_0.2.2-1_amd64.deb`，109834934 字节，SHA256 `e801ab2cdcfb3a592c4cc829c62b137cc5c7c3bef89b1998949d7653faf369b3`。Device wheel SHA256 `29b71db7112bf916697fcfc6f74671b0f333bd25d481513f7d37ea9826293279`，34 个 Python 模块逐字节匹配冻结提交（`20261028-041123-eb61e8.log`）。构建首次尝试因容器工具不支持单文件挂载失败，换只读目录挂载后成功（`20261028-040956-3aa08e.log`）。

以该准确包在无网络 amd64 隔离 systemd 容器中干净安装、普通用户安装实例并启动实际 helper；包内 bootstrap 和 runtime 的 helper 字节均匹配 wheel。慢速未完成帧约 5.004 秒得到拒绝，前后正常 query 成功，Device unit inactive、MainPID 0（`20261028-041213-a9b773.log`，脚本 `scripts/accept-native-helper-deadline-1a58f6d.sh`）；容器已停止。该实验只测请求读取时限和正常响应，不证明宿主重启、逐消息认证、动作身份或更新恢复。

本轮额外检查发现：超时拒绝路径发送响应时仍继承最后一次 recv 的剩余超时，可能缩短原 5 秒写入期限；socketpair 包装观察超时时先失败，慢帧实际为约 0.46 秒（`20261028-041319-13230d.log`）。因此发送所有响应前重新设置 5 秒写入超时，成功路径执行 handler 前的设置保留；不保证网络压力下响应必达，也不扩大读取 deadline。定向 `220 passed, 1 skipped`（`20261028-041401-de6943.log`），完整 `1202 passed, 16 skipped`（`20261028-041419-5ffdf8.log`），ruff 与脚本语法检查通过。上述准确 DEB 早于此后一行修复，不能把其验收当作新代码的包内验收；旧 helper/snapshot 不自动更新。


## 204. 响应写入修复后的准确包验收

从干净提交 `121c5e926ce5438cc2e73e724bf58be89655449c` 离线构建未签名 DEB `build/native-packages-p1-121c5e9/ai-device-native-client_0.2.2-1_amd64.deb`，109835950 字节，SHA256 `05d057cad34fd885b530b660fb152e92004678781bcdd2c980c56f4e30b25ea6`；Device wheel SHA256 `c94514170ce13a09488571b9ae5d9a1c11e1c434025c45f7eab9bc98e4677674`，34 个 Python 模块逐字节匹配冻结提交（`20261028-041615-8ee521.log`）。

无网络 amd64 隔离 systemd 容器干净安装准确包并以普通用户安装实例，真实 bootstrap/helper 启动后校验 bootstrap、runtime helper 文件均与 wheel 相同；缓慢未完成帧在 5.008 秒获得拒绝，前后正常 query 成功，Device unit inactive、MainPID 0（`20261028-041725-a2cf7f.log`，`scripts/accept-native-helper-deadline-121c5e9.sh`）。容器已停止。此实验未模拟响应背压，写入 5 秒设置由源码与 socketpair 定向测试覆盖；不能声称响应必达或验证真实设备、宿主重启、动作身份、外部 job 终态。旧包/旧 helper 不会自动更新；派发、恢复及覆盖保护仍关闭或维持原门禁，P1 未完成。


## 205. 新准确包生产 job 围栏回归

在独立无网络 amd64 systemd 容器中，以第204节 `121c5e9` 准确 DEB（SHA256 `05d057cad34fd885b530b660fb152e92004678781bcdd2c980c56f4e30b25ea6`）及同批 wheel 复测：惰性旧包夹具登记于隔离 dpkg，解包新制品，不安装真实更新；包内 helper/update/job 三模块与 wheel 字节一致。生产新派发在写 job intent 和运行 systemd 之前拒绝；人工构造的固定 snapshot worker 在真实隔离 systemd 中拒绝新动作，intent 字节和 active 不变，不创建 apply intent、sender 或 outcome（`20261028-041937-775869.log`；`scripts/accept-native-worker-default-deny-121c5e9.sh`）。这不是新包的干净升级、运行身份可信绑定或外部终态验收。两个本轮 fixture 容器已停止，未解除门禁。

首次复用旧验收脚本时，在隔离容器中离线调用了 pip 重装 wheel（`20261028-041852-bd5c0b.log`），不符合本项目 uv 工具链约束；后续改成直接核对已打包模块并在全新容器重跑，通过结果以无重装的上述日志为准。进一步核查发现现有 `build-native-linux.sh` 内部也调用 pip 离线安装 wheel，因此第203、204节包构建同样存在工具链合规缺口；制品身份和隔离测试记录仍是实际事实，但构建流程需改用 uv 并重新构建验证，不能将其表述为工具链合规完成。


## 206. 原生 Linux 构建器切换锁定 uv

构建器不再用 CPython `-m pip` 安装 wheel；使用 payload 中已由 `tool-lock.json` 验证的 uv，空环境、独立临时 HOME/TMPDIR、`--offline --no-cache --no-config --system`，显式指定解压的 CPython、`--no-index --no-deps --link-mode copy` 与组件及依赖 wheel 列表。空环境避免 UV 变量和用户配置污染，不修改原有包 preflight/覆盖保护。README 与构建器约束测试同步；定向 14 通过（`20261028-042229-bb04af.log`），完整 1203 通过、16 跳过（`20261028-042242-209baf.log`），ruff 与 shell 语法通过。

初版 uv 在隔离 Linux 离线试建成功（`20261028-042107-3a3873.log`），但该试包先于最终空环境修改且复用旧 payload，不是准确新提交制品。最终构建器的准确制品、安装落点、依赖和 helper 验收需在提交后单独验证；无 RPM 或真实宿主验收。


## 207. 锁定 uv 构建的准确 DEB 隔离验收

从干净提交 `0a348ec12c0ed8e7bc88b25e5eb5091af836640a` 用第206节最终构建器在无网络 amd64 Linux 容器离线构建未签名 DEB `build/native-packages-p1-0a348ec/ai-device-native-client_0.2.2-1_amd64.deb`，105251986 字节，SHA256 `f9bba80be6356441c1f8c3fcc7b9c1be36695e090c959ef09a8d7a15b1cb8f58`。Device wheel SHA256 `c94514170ce13a09488571b9ae5d9a1c11e1c434025c45f7eab9bc98e4677674`，34 个 Python 模块逐字节匹配提交；包构建使用 payload 锁定 uv，无 pip 调用（`20261028-042415-ff0a08.log`）。

在独立无网络 systemd 容器干净安装该 DEB，包内 uv 检查指定 Python 3.13.17 环境的 17 包依赖兼容；普通用户安装实例，bootstrap/runtime helper 均与准确 wheel 匹配。真实 helper 对慢速未完成帧 5.008 秒拒绝，前后 query 成功；Device unit inactive/MainPID 0，原地覆盖拒绝且 helper 文件字节未变（`20261028-042516-deaf0e.log`，`scripts/accept-native-helper-deadline-0a348ec.sh`）。另在新的隔离容器使用惰性旧包和新包解包 fixture，核对关键模块字节，生产派发在 intent/runner 前拒绝、真实 systemd snapshot worker 不写 apply intent/sender/outcome 且 active 未变（`20261028-042547-1bbdeb.log`，`scripts/accept-native-worker-default-deny-0a348ec.sh`）。两容器已停止。

这些证据不证明 RPM、真实宿主/设备、响应背压、受控动作身份、包更新与外部 job 终态或崩溃恢复；惰性旧包 fixture 不是实际升级。旧运行实例不自动更新，所有既定 default-deny 与包覆盖保护维持，P1 未完成。

后续核查：上述三份工具输出文件在接续时已不可读（`20261028-073027-886d40.log`）；验收结果依据此前会话中实际返回的工具输出，本次未重跑，准确 DEB 摘要已重新核对。不要将日志文件名当作当前可读取的独立证据。


## 208. 关闭与迟到 worker 的跨进程包锁竞争

新增 `test_close_and_late_worker_use_same_package_lease_across_processes`：独立 spawn 子进程实际调用 `close_before_apply`，在持有生产 package EX 锁并到达关闭标记发布前同步暂停；父进程实际调用 `Updates.apply`，确认由于包锁竞争拒绝且无关闭/apply intent。子进程被释放后走真实 `_save_new` 发布关闭标记；父进程迟到 apply 因已关闭拒绝，原 job intent 与 active 字节不变，无包适配器动作、apply intent、sender 或 outcome。管理身份、snapshot 验证和包适配器为测试替身，实例 lease 也为替身；实际测试的包锁是 OS 跨进程 flock，未运行真实 systemd/设备/包动作。

初版使用 fork 的完整回归通过但报告多线程 fork 警告（`20261028-073259-f5717c.log`），已改 spawn 并显式建立子进程替身；单项连续 5 次通过及更新/job 定向 145 通过（`20261028-073423-12cd9f.log`），完整 1204 通过、16 跳过无该警告（`20261028-073436-7689e7.log`），ruff通过。正常关闭与迟到拒绝不证明目录 fsync 故障的持久性、旧 snapshot 兼容、真实身份、外部 job 终态或崩溃恢复；生产派发、worker 与恢复默认拒绝不变。


## 209. 关闭标记重试重新同步目录

对现有 `close_before_apply` 增补故障回归：首次 `_save_new` 已完成文件 fsync 和 hardlink，但父目录 fsync 注入失败，标记可见；重试时继续对目标目录的 fsync 注入失败，旧实现仍直接返回 `closed_before_apply`，测试先失败（`20261028-073746-21be63.log`）。这表明重试仅看见匹配字节不能确认首次目录同步成功，不表示实际掉电已发生。

修复后，在原实例事务和 package EX 锁内，既有关闭标记通过类型敏感内容核对后再次 fsync 其父目录；同步失败传播异常，不覆盖标记或删除 intent；同步恢复时可返回原关闭结果。初次发布仍走原有文件 fsync、no-replace link、目录 fsync 路径。定向 145 通过（`20261028-073804-14db39.log`），完整 1204 通过、16 跳过（`20261028-073818-bf894a.log`），ruff 和 diff 检查通过。测试模拟文件系统错误，未模拟实际掉电或证明任意已存在文件的数据持久性；旧 snapshot 不自动获得修复。退休、worker、派发及恢复仍维持原有默认拒绝，不能据此释放围栏；真实受控身份、外部 job 和崩溃恢复仍未完成。


## 210. 准确 DEB 的关闭重试故障验收

从干净提交 `5728183ddb9c6640eb5c817a6bcbe118e65c3914` 用锁定 uv 和无网络 amd64 Linux 构建未签名 DEB `build/native-packages-p1-5728183/ai-device-native-client_0.2.2-1_amd64.deb`，105251468 字节，SHA256 `f53e4f3b03efc70ea6fdafe710ab44af7b1c8e8d95b82d1429310e37c8f0fca0`；Device wheel SHA256 `04535852600937c8c307553f12edb95de18a5791333c05811d1f5501edc867d1`，34 个 Python 模块匹配提交（`20261028-074022-66af5a.log`）。

无网络隔离 systemd、惰性旧包夹具和新 DEB 解包环境中，包内 helper/update/job 模块匹配 wheel；生产 dispatch 在创建 intent 和 runner 前拒绝，真实 systemd snapshot worker 拒绝新动作且不写 apply intent。使用实际包内 `close_before_apply` 和 snapshot，注入目标目录 fsync 错误，初次发布及再次重试均抛错，不报告成功；恢复 fsync 后重试成功，原 job intent 和 active 字节不变，未写 apply intent（`20261028-074132-8260a7.log`，`scripts/accept-native-close-fsync-5728183.sh`）。一次性容器结束后已停止并删除，其他容器未动（`20261028-074156-4532ee.log`）。

这不是干净安装、真实升级、实际掉电、可信运行身份或外部 job 终态证明；目录同步被故障注入，未验证硬件持久性。生产派发、退休、worker 和恢复门禁不变，旧 snapshot 不自动获得修复，P1 未完成。


## 211. 新建实例安装失败诊断

共用 `install_instance` 原先把 root 创建及后续所有 `OSError` 都包装为“目标不能已存在”，导致准备过程发生权限错误或磁盘满也被误报；注入 errno 13/28 的 `_prepare` 故障先复现（`20261028-074957-6b6769.log`）。现仅在 root.mkdir 本身的 `FileExistsError` 报目标已存在；目录创建其他错误报告 errno；目录已创建后 I/O 故障报告 errno 和 partial instance retained，保留异常链与现场，不清理、不覆盖、不自动重试。测试覆盖权限、空间、已有实例保护与证据保留；安装及原生安装定向 75 通过（`20261028-075033-76a564.log`），完整 1208 通过、16 跳过（`20261028-075044-966090.log`），ruff 通过。

这只修正诊断，不实现中断安装恢复、自动重建或空间预留，也不保证所有非 OSError 均有该提示。产物尚未重新构建，现有包和运行实例不自动更新，P1 保持未完成。


## 212. 准确包安装失败诊断验收

从干净提交 `2437e2062d871851d56eb537748df4cf16346987` 离线构建未签名 DEB `build/native-packages-p1-2437e20/ai-device-native-client_0.2.2-1_amd64.deb`，105251424 字节，SHA256 `3cae4ae3ea54a84e080ef44198413289ebc6ada4afe42db934366d896b51198b`；Device wheel SHA256 `1f2220b78ba734c6d6d5f1a1b63c97f58080c005ca8338df64d7a71e9e94fe6f`，34 个 Python 模块逐字节匹配提交（`20261028-075246-beabe4.log`）。

无网络 amd64 一次性 Linux 容器以 root 安装准确包，包内 uv 检查 17 项依赖，普通 UID 12345 从包内 Python 运行 `native_install.install_payload`，核对安装模块与准确 wheel 字节一致。对 `_prepare` 注入 errno 13 和 28：错误报告 errno、部分实例保留，再调用拒绝已有实例且证据不变；恢复正常路径后普通用户干净安装成功，重复安装拒绝且 active 不变（`20261028-075403-76ac6c.log`；`scripts/accept-native-install-errors-2437e20.sh`）。容器 `--rm`，随后核对已回收（`20261028-075417-be9f56.log`）。

故障是注入而非实际磁盘耗尽；测试直接调用包内函数而非独立 CLI 子进程，未验证真实主机、systemd、安装恢复或重建，旧包不自动更新。生产 job/恢复/覆盖门禁不变，P1 未完成。


## 213. job 观察拒绝孤立执行证据

`observe_job` 原先在 apply intent 缺失而 sender 或 outcome 存在时，仍只返回 `apply_intent_present=false`，遗漏矛盾证据；用实际观察入口构造两种孤立文件，先复现均未报错（`20261028-075830-f00758.log`）。现检测此冲突并报 `job execution evidence without apply intent`，保留文件不重放；apply intent 使用 `lexists`，不再把悬空链接误认为完全缺失。只读诊断仍不授权重试或释放围栏，不证明证据来源、运行身份、外部 job 终态或可恢复性。

更新/job 定向 147 通过（`20261028-075907-ad1f48.log`），完整 1210 通过、16 跳过（`20261028-075919-082716.log`），ruff 与 diff 检查通过。新代码尚未构建准确包，旧 helper/snapshot 不自动更新；生产派发、worker、退休和恢复门禁不变，P1 未完成。


## 214. 原生实例安装前的 bundle 复制空间下界

新实例安装在创建目录之前，根据已校验的离线 bundle 清单求各文件大小之和，检查目标最近已有目录所在文件系统可用空间；不足或查询失败时拒绝且不创建实例。悬空链接、非目录祖先、源文件消失亦拒绝，不更改已有文件。构建清单与复制文件一致是前提；该值仅为复制 bundle 的必要下界，不包括 venv 解压、文件系统开销与配额，不预留空间，也不解决并发消耗或挂载变化；实际安装仍可能失败且保留现场。

测试先复现不足或查询失败仍进入安装核心（`20261028-080933-5f700f.log`）。定向安装与共享安装测试 79 通过（`20261028-080958-15767b.log`，增加三个异常用例前）；完整 1217 通过、16 跳过（`20261028-081030-96d790.log`），ruff 与 diff 检查通过。此切片不改变覆盖保护、不构成安装中断恢复或正式签名空间要求；新准确包验收需另行记录，旧包不会自动更新。


## 215. 空间下界检查的准确 DEB 隔离验收

从干净代码检查点 `9c0b2181d0739eced340d39c19edc4d599fc56b0` 离线构建未签名 DEB `build/native-packages-p1-9c0b218/ai-device-native-client_0.2.2-1_amd64.deb`，105251238 字节，SHA256 `1ea705f62d275f87ce63a67ba9ef8a8930c0b56fbd339a978e4ea4478564b6c3`；Device wheel SHA256 `a62687a7c1d9aeb73f7e1cfe4c17633019fa27c56d78a92aeb69ec4f888fec2f`，34 个 Python 模块与提交逐字节一致（`20261028-081340-1398f2.log`）。

无网络一次性 Linux 容器干净安装 DEB，包内 uv 检查 17 项依赖，普通 UID 12345 核对安装模块与 wheel 字节一致；在包内实际 `install_payload` 注入可用空间 0 或查询抛错，均在缺失父目录创建前拒绝；恢复真实查询后干净安装成功，重复安装拒绝且 active 不变（`20261028-081410-923792.log`，脚本 `scripts/accept-native-install-space-9c0b218.sh`）。构建及验收容器均 `--rm`，核对已回收（`20261028-081426-da3185.log`）。

空间不足与查询异常是函数级注入，非真实磁盘满或配额测试；未验 CLI 子进程或真实主机。预检只是 bundle 复制大小必要下界，不保证完整安装空间或恢复。旧包不自动更新，生产门禁不变，P1 尚未完成。


## 216. job 派发锁内准备与外部调用分离

将 `dispatch_job` 的实例事务内准备提取至私有 `_prepare_dispatch_locked`：复核已批准事务与既有同 ID intent，保留生产新派发默认拒绝；实际持包锁时检查冲突、建立 snapshot 与 durable intent 并形成固定参数。原公开入口在退出实例事务后才调用 systemd-run，既有响应丢失 unknown 与同 ID 不重派行为不变。私有入口内部再次检查 Linux 管理员与 Debian 适配器，不持有或验证调用者的实例锁，未接入 helper 消息；不能凭返回参数授权或证明执行。

新增定向测试检查默认拒绝不写 intent、不获取包锁，隔离替身允许下锁内准备不重取实例事务、不启动外部进程、已有 intent 不重派；使用生产 `_transaction`、`Helper._lease` 和 `package_lease` 的真实独立文件描述符，验证 intent 保存时实例与包锁均拒绝竞争，事务退出后可重新取得（`20261028-082016-6c70ee.log`，更新/job 定向 150 通过）。既有派发测试额外跟踪实例→包锁顺序，验证 runner 仅在两锁释放后调用。完整 1220 通过、16 跳过（`20261028-082041-cb2c75.log`），ruff 与 diff 检查通过。

真实锁测试为同进程独立 open，绑定、批准、snapshot 与生产门禁使用替身；不证明跨进程、实际 systemd、持锁身份、受控 handler/消息/invocation 或崩溃恢复。新准确包尚未构建验收，旧 helper 和 snapshot 不自动更新；生产派发、worker、恢复与覆盖保护不变，P1 未完成。


## 217. 锁内准备拆分的准确 DEB 默认门禁验收

从干净源码检查点 `513fb1d792aa43f4d1548d1b3dcde9904e7751b4` 离线构建未签名 DEB `build/native-packages-p1-513fb1d/ai-device-native-client_0.2.2-1_amd64.deb`，105242314 字节，SHA256 `7cba514a7db82e694373bd239dac821029092443c16e21ea22e1aeb8d6c94cdc`；Device wheel SHA256 `6a47513dd0562622d27075ad2c1d5848019c48531593abf7b39f8e355d191fac`，34 个 Python 模块与提交逐字节一致（`20261028-082339-7cc198.log`）。

无网络隔离 systemd 容器安装惰性旧包夹具并解包准确 DEB，普通用户创建实例、登记、准备和批准；包内 worker、update、helper 模块与 wheel 字节一致。未修改生产门禁时，实际 `dispatch_job` 对新 ID 在写 intent 或调用 runner 前拒绝；人工保存匹配 intent 后同 ID 返回只观察、不重派；以该 snapshot 在真实隔离 systemd 中执行 worker，新动作默认拒绝，intent 和 active 不变且不创建 apply intent/sender/outcome（`20261028-082437-ac97b5.log`；`scripts/accept-native-worker-default-deny-513fb1d.sh`）。构建与验收专用容器均已回收（`20261028-082459-f25563.log`）。

这是旧包惰性夹具加新 DEB 解包，不是干净包安装或实际升级；人工 intent 不证明真实派发、受控 handler、逐消息身份、invocation、worker 进程树或崩溃恢复。生产门禁及覆盖保护不变，旧包与 snapshot 不自动更新，P1 未完成。


## 218. 已持实例锁时的规范更新事务初始化

将 `Updates._transaction` 中持锁后的 ID 校验、注册绑定读取、可信 updates 目录检查、registry 目录 fsync 与长度前缀 stem 计算提取为私有 `_transaction_locked`；原事务保留锁前 ID 校验、取得同一 helper 实例锁并调用该函数。未来受控 handler 已持该锁时可复用同一初始化，而不是重取锁或接收消息自报的绑定/路径；该函数不检查调用者确实持锁，也不授予动作权限，当前未接入新 handler。

测试验证真实 helper 实例锁持有期间普通事务和锁内初始化返回相同绑定及无歧义 stem，锁不会重入；非法 ID 和 registry fsync 注入失败拒绝，不写 job intent。另一项测试在真实实例锁下串联 `_transaction_locked` 与 `_prepare_dispatch_locked`，并在 intent 保存时用独立 open 验证实例及包锁均冲突；绑定、准备、snapshot 和生产门禁使用替身，仅证明受测文件锁及流程。更新/job 定向 154 通过（`20261028-082658-c9d687.log`，串联调整前）；完整 1224 通过、16 跳过（`20261028-082724-16f6bf.log`），ruff 与 diff 检查通过。

未证明跨进程运行身份、消息/handler 绑定、外部 job 终态或崩溃恢复；生产默认拒绝不变。新代码准确包尚未构建，旧包与冻结 snapshot 不自动更新，P1 未完成。


## 219. 规范锁内事务的准确 DEB 隔离验收

从干净源码检查点 `e3ecbb65ccfd6760bb0d6243e852d4a00d218971` 离线构建未签名 DEB `build/native-packages-p1-e3ecbb6/ai-device-native-client_0.2.2-1_amd64.deb`，105245718 字节，SHA256 `93043dfd7ed26ac38ba22f52f8320d5041e882daeada149fd81ef2240376fddd`；Device wheel SHA256 `8951d8b93c77fec5f3730395ce91500b654200d00eab7141518506bff59e56f5`，34 个 Python 模块与提交逐字节一致（`20261028-083038-b1390f.log`）。

无网络隔离 systemd 容器安装惰性旧包夹具并解包准确 DEB，普通用户创建实例并登记；包内 update、job、helper 与 wheel 一致。真实 `_transaction` 持实例租约下，`_transaction_locked` 返回相同绑定与 stem，独立重取实例锁被拒绝；正常准备批准后，新生产 dispatch 默认拒绝且不创建 intent，人工匹配 intent 同 ID 只观察不重派；真实 systemd 启动 snapshot worker 被默认拒绝且无 apply/sender/outcome（`20261028-083105-6d8131.log`，`scripts/accept-native-worker-default-deny-e3ecbb6.sh`）。构建和验收容器已删除（`20261028-083131-3456bc.log`）。

这是惰性旧包加新包解包的隔离夹具，不是干净安装或实际升级；同进程实例锁竞争不证明受控 handler、消息来源、生产 invocation、跨进程锁交接、外部进程树或崩溃恢复。门禁与覆盖保护不变，P1 未完成。


## 220. 未接入生产的 root 消息逐段凭据接收组件

新增私有 `native_helper._receive_root_credential_frame`：要求 Linux 和已启用的 `SO_PASSCRED`，逐段 `recvmsg` 检查唯一完整内核凭据、同一 root PID/UID/GID、有界单行帧及单调时钟整帧 deadline；缺失、混合、截断、未知控制数据、超长、多帧或异常拒绝。拒绝附带 `SCM_RIGHTS` 时关闭已交付 FD，某次关闭失败仍尝试余下 FD 并拒绝。调用者负责在所有结果后关闭连接；当前开关检查不能证明数据排队前已启用，后续 listener 必须显式保证。

模拟测试覆盖分段、混合凭据、非 root、无/重复/畸形凭据、截断、大小、deadline、关闭失败和 FD 清理；定向 194 通过、1 跳过（`20261028-083538-3fb2e3.log`，最后三个补充用例前）；完整 1240 通过、16 跳过（`20261028-083650-a9805e.log`），ruff 与 diff 检查通过。新组件尚未接入 `_serve`，不解析动作、不调用 handler、不验证代码或 invocation、不授予派发权限；实际 Linux socket 和准确包验证另待记录。旧 helper/snapshot 不自动更新，生产门禁不变。


## 221. 逐消息凭据组件的准确 DEB Linux 验收

从干净源码检查点 `4a2542c8f836e41e15d658c76b84edb82e0bf739` 离线构建未签名 DEB `build/native-packages-p1-4a2542c/ai-device-native-client_0.2.2-1_amd64.deb`，105258734 字节，SHA256 `935787550b08eb79bd1c328557eae0b2f30340a64d838fa2444bcccaafe44a91`；Device wheel SHA256 `b6ee26507368edf382dc2c3ae55ea19854ddb2b02b27609d1dd3592c743efc6b`，34 个 Python 模块与提交逐字节一致（`20261028-083952-2b3674.log`）。

无网络一次性 Linux amd64/Rosetta 容器干净安装准确 DEB，包内 uv 检查 17 项依赖，`native_helper.py` 与 wheel 一致；对包内 `_receive_root_credential_frame` 使用真实 Unix listener/socketpair：listener 在 connect 前启用 `SO_PASSCRED`，接收 accept 前排队的 root 消息及正确内核 PID/UID/GID；子进程两段之间降权的混合帧拒绝；真实 `SCM_RIGHTS` 单 FD 和 80 FD 截断均拒绝且 `/proc/self/fd` 数量无净增；部分帧按有限 deadline 拒绝（`20261028-084040-b545d9.log`，`scripts/accept-native-credential-frame-4a2542c.sh`）。构建及验收容器均已删除（`20261028-084053-a08f58.log`）。

这是独立接收组件测试，未接入生产 listener/handler，不证明当前服务逐消息认证、受控监督或 invocation 身份，也不证明业务动作及持久事务绑定。生产 job/worker/恢复门禁不变，旧包和运行中 helper 不自动更新，P1 未完成。


## 222. 未接入生产的逐凭据只读 query 接线

新增私有 `_serve_credential_query_once`，复用逐段 root 凭据接收，仅解析三个严格字段且 action 必须为 `query`、ID 合法，拒绝 JSON 重复字段、其他动作及额外字段。合法请求调用现有 `Helper.handle` 查询，一次响应后关闭连接；失败只返回固定拒绝，响应丢失不重调 handler。该入口未接入生产 `_serve`、不允许 start/job/recovery，不能把来源 root 解释为监督器、执行者或 invocation 身份；listener 须在消息排队前启用 `SO_PASSCRED`。

测试覆盖合法 query、重复字段、start、错误 ID、额外字段、格式错误、凭据失败、响应丢失和连接关闭；定向 206 通过、1 跳过（`20261028-084411-8c6b41.log`，响应丢失测试前），完整 1250 通过、16 跳过（`20261028-084440-c73ae3.log`），ruff 与 diff 检查通过。测试为 handler 替身；真实包、socket 与实际 systemd query 验证另待记录。生产协议和所有门禁不变，P1 未完成。


## 223. 凭据只读 query 的准确 DEB 隔离 systemd 验收

从干净源码检查点 `55dca56afac866688d32fb0047a559eb517b78c8` 离线构建未签名 DEB `build/native-packages-p1-55dca56/ai-device-native-client_0.2.2-1_amd64.deb`，105240936 字节，SHA256 `fad5a3ccfdf05478918a42f75cf3e2b22877e62aff23f416c0539a8df9d73b00`；Device wheel SHA256 `a2bc777dc9839cd3fb6e924be263fc1baaba38ef9f6d486ee4f69cc242d00a08`，34 个 Python 模块与提交逐字节一致（`20261028-084754-244a53.log`）。

无网络隔离 systemd 容器干净安装准确 DEB，包内 uv 检查 17 项依赖，普通用户创建实例并登记；独立 bootstrap helper 与 wheel 字节相同。手动创建启用 SO_PASSCRED 的真实 Unix listener，以 root 消息触发私有 `_serve_credential_query_once`，实际调用 `Helper.handle` 和 systemd 查询，返回 inactive；start 和重复 action 字段被 handler 前拒绝、无 start intent，Device 仍 inactive/MainPID 0（`20261028-084846-52d399.log`，`scripts/accept-native-credential-query-55dca56.sh`）。构建及验收容器均已删除（`20261028-084903-241e80.log`）。

该 listener/入口由验收脚本直接调用，并非生产 `_serve`；未证明 supervisor、处理者身份、invocation、状态变更动作或外部 job 终态与恢复。生产 job/worker/恢复门禁和包保护不变，旧环境不自动更新，P1 未完成。


## 224. 未接入生产的受控子进程握手观察

新增私有 `native_bootstrap._observe_child_handshake`：要求 Linux、已启用 `SO_PASSCRED` 的 SEQPACKET 和 32 字节挑战，对单条记录核对内核 PID/UID、挑战、截断及 deadline，接收前后观察 pidfd；拒绝附带描述符并关闭已交付 FD，关闭失败仍处理余下描述符。成功仅返回 `observed_not_authorized`，不创建进程、不获取 pidfd、不发挑战、不调用 handler、不授权 job。

调用方仍须证明 PID 与 pidfd 属于受控直接子进程、监听在数据入队前启用凭据、挑战随机且不复用，并承担锁交接、子进程生命周期及失败清理；存活检查后仍可立即退出。当前没有生产调用点。模拟定向 53 通过（`20261028-085358-291115.log`，补充异常测试前），完整 1269 通过、16 跳过（`20261028-085428-c0892b.log`），ruff 与 diff 检查通过。准确包真实 Linux 父子进程验证另待完成；生产默认拒绝不变，P1 未完成。


## 225. 子进程握手观察准确 DEB Linux 验收

从干净源码检查点 `e1ffb44c08b58bcfe4bebe17eec06bc14f4b8154` 离线构建未签名 DEB `build/native-packages-p1-e1ffb44/ai-device-native-client_0.2.2-1_amd64.deb`，105261666 字节，SHA256 `1e59690850fed8db58fe8c3784fadbd60368afcfb577fbbb67db5f1d59691813`；Device wheel SHA256 `0838a0a73a145d7bdb0009dec00f2a2379278937cd4184a968129b10d48e33b3`，34 个 Python 模块与提交逐字节一致（`20261028-085744-b8ec23.log`）。

无网络一次性 Linux amd64/Rosetta 容器干净安装准确包，包内 uv 检查 17 项依赖；独立 bootstrap 模块与 wheel 一致。测试脚本自行 fork 有限寿命直接子进程、获取 pidfd 并通过已启用 SO_PASSCRED 的 SEQPACKET 发送随机挑战；包内观察组件接受正确记录但仅返回 observed_not_authorized，对错误挑战、后代发送、子进程提前退出、超时与附带 FD 均拒绝；进程均 wait 回收，FD 数量无净增（`20261028-085827-ff06b0.log`，`scripts/accept-native-child-handshake-e1ffb44.sh`）。构建和验收容器均已删除（`20261028-085844-9e9d3a.log`）。

这是测试脚本自行管理 PID/pidfd、进程与通道的局部观察，不是生产监督器或锁交接；不证明就绪后存活、handler、消息动作、invocation 或外部 job 故障恢复，未开放任何生产门禁。旧包和 snapshot 不自动更新，P1 未完成。


## 226. daemon 双锁内部所有权接口

新增内部 `_daemon_lease_pair`，按既有目录→文件顺序获取并在上下文中返回两个描述符；原 `_daemon_lease` 继续只返回文件 FD，调用方与锁序不变。作用域正常或异常结束时两个 FD 关闭；文件锁获取失败时释放目录锁。新增 3 项回归包括调用方异常、部分获取失败、实际 fork 后父作用域退出而子保留两把锁及子退出后重新获取；Device 完整回归 `1272 passed, 16 skipped`（`20261028-090632-e18f6b.log`），修正测试静态写法后专项及 ruff 通过（`20261028-090808-bd82a6.log`）。

一次性无网络 Linux amd64/Rosetta 容器使用 e1ffb44 DEB 并在容器内覆盖当前 `native_helper.py`，在 root 私有目录验证真实源码的双锁 fork 持有及释放（`20261028-090826-ee9c7e.log`）；这是源码覆盖实验而非准确新包验收，容器已删除。首次在 `/tmp` 因可信祖先检查正确拒绝，未进入 fork 阶段（`20261028-090805-6c551c.log`）。

该接口并非监督器；持有 FD 或 inode 匹配不证明锁仍持有，任一共享 OFD 的 `LOCK_UN` 会影响其他持有者，后代继承也会拖延释放。调用方不得手动关闭/解锁，须控制所有 fork 继承，并依赖已确认的稳定 registry 管理前提。未接入受控启动、消息授权或任何新生产动作，P1 未完成。


## 227. 双锁准确 DEB 隔离验收

以干净检查点 `dbd1a200e0c44e8acb2719b083180b9a3a80964c` 离线构建 wheels 和 payload（`20261028-091157-e30c64.log`），Device wheel SHA256 `8f4c66ef643804a47bf1705ec3f8d8f8fac9026abe4be3fdb570cc2976d051e0`；34 个 Python 模块与该提交逐字节一致（`20261028-091316-413ef2.log`）。无网络构建未签名 DEB `build/native-packages-p1-dbd1a20/ai-device-native-client_0.2.2-1_amd64.deb`，105245404 字节，SHA256 `b700bcf4289ec853804db6d46c8d8ab3d0dff7964195adaba2fddb82abfdbd0f`（`20261028-091207-3400d6.log`）。

一次性离线 Linux amd64/Rosetta 容器干净安装该包，包内 uv 检查 17 项依赖，独立 bootstrap 的 helper 源码与准确 wheel 一致；真实 fork 验证父作用域退出后子进程继续持有目录锁与文件锁，子进程退出后重新获取（`scripts/accept-native-daemon-pair-dbd1a20.sh`，`20261028-091312-f72912.log`）。构建及验收容器均已删除。

该有限夹具不执行生产监督、启动、消息授权或异常后代治理；共享 FD 不证明锁仍持有，不改变稳定 registry 维护前提，生产派发和恢复门禁不变。旧包和冻结 snapshot 不自动更新，P1 未完成。


## 228. 已校验模块加载作用域

将 `run_verified_helper_candidate` 原有的三个内存源码模块创建、编译和 `sys.modules` 恢复抽成内部 `_verified_helper_modules` 上下文；公开入口仍先调用原 `_verified_helper_bytes` 校验，再进入作用域并调用候选 `serve`，不改变加锁或服务逻辑。新增候选模块编译执行失败及调用方失败时恢复原模块的回归；已有候选路径替换后仍执行已校验字节的测试继续通过。bootstrap 与握手定向 60 通过（`20261028-091511-bcac69.log`），完整 `1274 passed, 16 skipped`（`20261028-091520-aeac99.log`），ruff 通过（`20261028-091512-c5efa0.log`）。

内部作用域不校验传入字节，调用者须先以原权威检查取得并持有验证结果；它修改进程全局模块表，须单线程独占使用，不能用作外部可调用入口、身份或授权证明。本步骤没有 fork、监督、锁交接或门禁放行，尚未完成该检查点准确包的真实候选加载验证；P1 未完成。


## 229. 候选模块作用域准确 DEB 验收

以干净源码检查点 `bcb6ec500dbf6dbd6e6aadc7839e7813677f2a40` 离线生成 Device wheel SHA256 `15c355aa8addb7f450820a7858807dc8321ff9f2318dcd68b72df073c8a4d665`（`20261028-091715-d22eec.log`），34 个 Python 模块与提交逐字节一致（`20261028-091843-cb8fa9.log`）。无网络生成未签名 DEB `build/native-packages-p1-bcb6ec5/ai-device-native-client_0.2.2-1_amd64.deb`，105258776 字节，SHA256 `d45bff032c4f5b985b706f7978edd54b2970b2247bf1b43db134516e0ccca7b7`（`20261028-091723-029a4b.log`）。

一次性离线 Linux amd64/Rosetta 容器干净安装准确包，包内 uv 检查 17 项依赖，独立 bootstrap 与 helper 模块均匹配 wheel；发布实际候选后，错误固定摘要拒绝且没有创建 socket，正确摘要通过原同进程入口执行实际候选，socket peer PID 与测试启动的子进程匹配，畸形请求拒绝；测试子进程终止并回收，构建与验收容器已删除（`scripts/accept-native-bootstrap-scope-bcb6ec5.sh`，`20261028-091833-60c489.log`）。

该验证限于旧同进程入口，没有证明受控监督、实际消息处理者、持锁交接或动作授权，不开放生产门禁；P1 未完成。


## 230. 凭据帧意外 FD 的原子 close-on-exec

未接入生产的 `_receive_root_credential_frame` 现要求 Linux `MSG_CMSG_CLOEXEC`，在 `recvmsg` 接收 SCM_RIGHTS 时原子设置 close-on-exec；缺少该常量时直接拒绝，不退回接收后设置。原有意外描述符全部关闭、异常时继续清理及拒绝行为不变。测试在修改前确认新契约失败（`20261028-092156-82ce7c.log`），helper 及帧定向 `210 passed, 1 skipped`（`20261028-092216-c1fabb.log`），完整回归 `1276 passed, 16 skipped`（`20261028-092255-51fda8.log`），ruff 通过。

一次性离线 Linux amd64/Rosetta 容器安装旧 bcb6ec5 包并仅在容器内覆盖当前源码，真实 SCM_RIGHTS 接收后、拒绝关闭前观察 FD_CLOEXEC，并启动 close_fds=False 的测试子进程核实 exec 后无法访问该 FD；接收器拒绝并无 FD 净增（`scripts/accept-native-credential-cloexec-source.sh`、`20261028-092252-7e86c0.log`）。测试容器已删除；此为源码覆盖实验，非准确新包验收。

CLOEXEC 不阻止 fork 后、exec 前的继承，也不证明非并发代码、直接子进程、消息处理者、持续锁或动作授权；现有生产 listener 不使用该候选接收器，生产门禁未改变。P1 未完成。


## 231. 未接生产的逐消息凭据查询监听器

新增内部 `_serve_credential_queries`：仅 Linux root、受稳定 registry 管理前提约束，持现有 daemon 双锁，在绑定 socket 前启用 SO_PASSCRED，以 0600 权限监听并对每个连接调用既有一次性 root 凭据查询处理器；只允许 query，不接入 CLI、生产 daemon、start、更新或恢复。测试覆盖开锁、设置凭据、bind/listen、异常关闭及释放的次序和平台拒绝。定向 `212 passed, 1 skipped`（`20261028-092734-e8896e.log`），完整 `1278 passed, 16 skipped`（`20261028-092834-c9157b.log`），ruff 通过（`20261028-092737-e8cd45.log`）。

一次性离线 Linux amd64/Rosetta 容器安装旧 bcb6ec5 包并覆盖当前 helper 源码，真实执行新监听循环：缺登记的 query、start 和重复 action 均返回固定拒绝、每次连接关闭，监听期间目录锁与文件锁均冲突，测试进程退出后可重新获取（`scripts/accept-native-query-listener-source.sh`、`20261028-092829-a6c6cf.log`）；容器已删除。该实验未注册实例或运行 systemd，不验证成功查询，也不是准确新包验收。

监听器仍不证明受控监督、处理者代码、当前 invocation 或消息动作授权；旧生产 listener 和所有默认拒绝门禁不变。受控直接子进程创建、锁交接、故障回收和外部 job 终态仍未完成，P1 未完成。


## 232. 内部凭据查询监听器准确包及真实实例验收

以干净检查点 `2cd3c17baf64453c98bfd0f017883316ec88f472` 离线生成 Device wheel（SHA256 `c1218ed62b82428a735877684c826b596bafe81e3da0005479365ec84f368725`，`20261028-093147-0d059d.log`），34 个 Python 模块与提交逐字节一致（`20261028-093338-bd7d27.log`）。无网络构建未签名 DEB `build/native-packages-p1-2cd3c17/ai-device-native-client_0.2.2-1_amd64.deb`，105266880 字节，SHA256 `36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4`（`20261028-093204-1f655d.log`）。

一次性无网络 Linux amd64/Rosetta systemd 容器干净安装包并通过包内 uv 检查 17 项依赖；普通用户安装实例并真实登记测试 unit；测试专属子进程调用包内内部 `_serve_credential_queries`，真实 root socket 查询返回该实例 inactive，start 和重复 action 拒绝，设备 unit 保持 inactive/MainPID 0、无 start intent；监听期间双锁冲突，退出后可重新取得（`scripts/accept-native-query-listener-2cd3c17.sh`、`20261028-093357-b1c49e.log`）。构建及验收容器已删除并核实（`20261028-093428-7dd357.log`）。

这是直接调用未接生产的内部监听器，不是受控启动、handler 身份、invocation、更新事务或失败恢复验收；没有实际启动 Device 服务。旧包和冻结 snapshot 不自动更新，生产门禁不变，P1 未完成。


## 233. 继承双锁的内部查询循环

将 `_serve_credential_queries` 内的监听循环抽取为 `_serve_credential_queries_locked`，原入口仍在现有 daemon 双锁内调用，原 Linux/root、凭据能力、可信路径和 query-only 约束不变。新私有循环明确要求调用者在整个生命周期持有两把锁，但不能验证该前提，不是公开入口、监督或授权。新增测试验证循环不重复取得 daemon 锁；helper 定向 `213 passed, 1 skipped`（`20261028-094003-702d7f.log`），完整 `1279 passed, 16 skipped`（`20261028-094101-d75b4d.log`），ruff 通过。

一次性离线 Linux amd64/Rosetta 容器安装旧 2cd3c17 DEB，容器内覆盖当前 helper 源码；测试父进程持两锁 fork 子进程后退出自身租约作用域，子进程继承双锁并运行新私有查询循环；观察两个锁各自冲突、start 请求拒绝，测试子进程退出回收后两锁可重新获取（`scripts/accept-native-query-inherited-lock-source.sh`、`20261028-094056-f01145.log`），容器已删除。这是源码覆盖实验，不是准确新包、成功实例查询或系统级监督验收。

该接口不证明 FD 仍持锁，不阻止后代继承或共享 OFD 显式解锁，也未完成 pidfd 门闩、父子死亡处理、消息处理者和 invocation 绑定；生产门禁不变，P1 未完成。


## 234. 直接子进程 pidfd 失败回收原语

新增未接生产的 `_reap_direct_child_pidfd`：Linux 下先以 `waitid(P_PIDFD, WEXITED|WNOHANG|WNOWAIT)` 验证 pidfd 对应尚未回收的直接子进程；若仍运行，以 `pidfd_send_signal(SIGKILL)` 定向该进程，再以非阻塞 `waitid` 在绝对期限内回收。已退出与信号前退出竞态均继续回收；非子进程、能力缺失、非法参数、信号错误及超时拒绝，不向裸 PID 或进程组发信号。调用方必须排除其他 waiter、持有 pidfd 并持续持有必要锁直至回收成功；该函数不负责获取或释放锁。

修改前测试因入口/依赖尚缺失败（`20261028-094552-499682.log`）；握手及回收定向 `30 passed`（`20261028-094842-ca15a1.log`），完整回归在追加三个定向失败用例之前为 `1287 passed, 16 skipped`（`20261028-094720-70d853.log`），ruff 通过。一次性无网络 Linux amd64/Rosetta 容器安装旧 2cd3c17 DEB 并覆盖当前 bootstrap 源码，真实验证非子进程拒绝、存活和已退出直接子进程 pidfd 回收，父进程持双锁直到回收完成且作用域结束后可重新取得（`scripts/accept-native-child-reap-source.sh`、`20261028-094716-d2993f.log`）；容器已删除，非准确新包验收。

此原语不创建子进程，不处理父进程死亡、后代、外部 systemd job、不可中断内核态或锁漂移，也不授权启动/恢复；超时意味着未知而非成功。生产门禁保持关闭，P1 未完成。


## 235. 候选字节与门闩的隔离集成夹具

一次性无网络 Linux amd64/Rosetta 容器安装旧 2cd3c17 DEB，仅在容器内覆盖当前 bootstrap 和 helper 源码。夹具发布实际候选，父进程执行 `_verified_helper_bytes` 获取已验证内存字节，持 daemon 双锁创建私有 SEQPACKET 和直接子进程；获取子进程 pidfd，以随机挑战及内核凭据通过 `_observe_child_handshake`，并断言收到 GO 前 socket 不存在。发送 GO 后，子进程从继承字节进入 `_verified_helper_modules` 并运行 `_serve_credential_queries_locked`；两把锁均冲突，start 请求拒绝，父进程通过 `_reap_direct_child_pidfd` 回收直接子进程，锁作用域结束后可重取。第二个子进程在握手后遇父端关闭而未收到 GO，正常退出并由 pidfd 回收，未创建 socket（`scripts/accept-native-gated-candidate-source.sh`、`20261028-095313-3c2e13.log`）；容器已删除。首次运行因夹具 PID 条件错误在安装前退出（`20261028-095204-d3bd7c.log`），修正后通过。

这是源码覆盖的测试夹具，fork、门闩和清理均由夹具自行实现，不是生产监督器或准确新包验收；未覆盖父进程死亡、信号窗口、额外后代、监听器存活期约束、实际 handler 与 invocation 绑定或 systemd 外部 job。握手仅为观察，生产门禁与既有包保护不变，P1 未完成。


## 236. 受控候选查询子进程侧门闩

将第235节夹具中的子进程侧门闩逻辑提取到内部 `_gated_candidate_query_child`：按绝对 deadline 接收 32 字节挑战、回复 READY、等待准确 GO；失败或通道异常关闭控制 socket，成功关闭控制 socket 后才将调用方提供的已验证字节载入 `_verified_helper_modules` 并调用 `_serve_credential_queries_locked`。只提供 query-only 路径，无 CLI 或生产调用点；调用方必须保证直接子进程、已验证字节及双锁，函数本身不证明这些前提或父身份。

新增无效挑战、关闭/错误/超长 GO、deadline 和正确路径测试，验证失败不加载候选；握手、回收和门闩定向 `37 passed`（`20261028-095640-c310f0.log`），完整 `1297 passed, 16 skipped`（`20261028-095715-fa692f.log`），ruff 通过。原隔离门闩夹具改为调用实际新函数，在一次性无网络 Linux amd64/Rosetta 容器以旧 2cd3c17 包覆盖当前源码，验证放行后 query-only 和 start 拒绝、关闭门闩后无 socket、pidfd 回收及双锁释放（`20261028-095707-41f080.log`）；容器已删除，非准确新包验收。

父进程 fork、信号处理、异常回收和运行期监督仍由夹具实现；父死亡、后代、外部 job、invocation 与动作绑定未验证，不能把子进程侧门闩作为认证或授权。生产入口和默认拒绝不变，P1 未完成。


## 237. 未接生产的 query-only 父进程监督候选

新增内部 `_supervise_candidate_queries`：要求 Linux root、固定候选摘要、单线程及兼容信号状态；在候选校验前屏蔽 SIGTERM/SIGINT，持 daemon 双锁，检查 pending stop 后 fork，子进程执行内部 query 门闩；父进程获取子 pidfd、发随机挑战并验证逐记录凭据与存活，再检查 pending stop 后放行。运行中同步等待终止信号或观察子进程退出，finally 在锁作用域内以 pidfd 回收直接子进程。pidfd 获取失败则关闭门闩并有界等待，不向裸 PID 发信号；该实现无生产调用点，不授权动作。

首次隔离运行因 Python 默认 SIGINT handler 被误判而拒绝（`20261028-100438-063705.log`），修正为允许 Python 默认 handler 或系统默认，仍拒绝其他 handler。一次性无网络 Linux amd64/Rosetta 容器安装旧 2cd3c17 DEB 并覆盖当前源码，实际验证候选字节、双锁、门闩、start 拒绝、SIGTERM 后直接子进程回收及锁释放；注入校验阶段 SIGTERM，确认未 fork 且无 socket（`scripts/accept-native-query-supervisor-source.sh`、`20261028-100550-13cb66.log`）；容器已删除。完整回归 `1299 passed, 16 skipped`（`20261028-100601-bb4df7.log`），定向 `39 passed`（`20261028-100248-dccfbc.log`），ruff 通过。此为源码覆盖，非准确新包验收。

**不可接生产的剩余风险**：父进程 SIGKILL 或崩溃不会由此候选收口子进程；子进程或后代可继承锁；回收超时后当前函数抛错并离开父侧锁作用域，不证明遗留进程或锁安全；未覆盖 fork→pidfd 窗口全部故障、SIGINT、竞争 waiter、异常后代、systemd invocation 和外部 job。不能凭该候选授权恢复或新动作，生产门禁和包保护不变，P1 未完成。


## 238. query-only 子进程观察父进程死亡

第237节未接生产的 query-only 监督候选在 fork 前以 `pidfd_open(getpid())` 固定父进程，子进程门闩前启动内部 pidfd 观察线程；父进程已死亡或观察线程无法启动时不进入候选，观察线程看到父 pidfd 就绪或自身观察异常则以 `_exit(1)` 结束该子进程。父侧正常完成后关闭自身 pidfd；该逻辑不接入生产入口、不授权任何动作。

先以一次性无网络 Linux amd64/Rosetta 容器实际杀死夹具创建的监督父进程，复现查询子进程仍存活（`20261028-101023-e138fb.log`）；修改后相同隔离实验确认直接子进程退出、双锁可重新获取（`scripts/accept-native-supervisor-parent-death-source.sh`、`20261028-101129-ea215c.log`）。正常 SIGTERM 及验证期间终止无 fork 路径回归通过（`20261028-101140-e98856.log`），容器均已删除。完整回归 `1299 passed, 16 skipped`（`20261028-101150-169e10.log`，在新增观察线程前置失败测试之前）；补充定向 `22 passed`（`20261028-101310-4d18dc.log`），ruff 通过。容器使用旧 2cd3c17 DEB 覆盖当前源码，不是准确新包验收。

父死亡观察是异步的，不能证明死亡瞬间没有动作或外部副作用；不覆盖后代、外部 systemd job、解释器挂起/不可中断内核态，也不证明 SIGKILL 后进程回收者及所有 FD 生命周期。第237节回收超时离开父锁作用域等风险仍存在；旧包不自动更新，生产门禁关闭，P1 未完成。


## 239. 监督候选 fork 窗口与回收失败故障注入

在一次性无网络 Linux amd64/Rosetta 容器，使用旧 2cd3c17 DEB 并覆盖 f14bfcc 源码，真实 fork 返回后注入 SIGTERM、以及单独注入子 pidfd 获取失败；两种情况下均记录直接子进程已回收、未创建监听 socket、双锁可重新取得。正常 SIGTERM 与校验阶段停止的回归亦通过（`scripts/accept-native-query-supervisor-source.sh`、`20261028-101650-ab1bb5.log`）。此为源码覆盖、限定所测时序，不是生产监督或准确包验收。

新增隔离验收脚本 `scripts/accept-native-supervisor-reap-failure-source.sh`：在真实 query-only 监督候选运行期间注入 `_reap_direct_child_pidfd` 失败，要求回收状态未知时父进程继续持有两锁并在许可后完成回收。当前实现实际退出，测试按预期失败（`20261028-101740-52aef7.log`）；尚未修复，不能声称锁保持或完整回收。测试只运行专属一次性容器，容器由 `--rm` 回收。

后续应先设计并实现回收未知时的锁所有权与诊断、恢复和退出契约，再重跑该回归；不得通过移除断言、提前放锁或清理 intent 规避问题。父死亡、后代及外部 job 的全生命周期仍未证明，生产门禁保持关闭，P1 未完成。


## 240. 直接子回收未知时监督父进程持锁重试

query-only、未接生产的监督候选在直接子回收结果未知时，不再抛出后离开双锁作用域：保留父侧租约和 pidfd，持续尝试；无子 pidfd 的门闩路径亦等待 `waitpid` 实际回收，不向裸 PID 发信号。第一次未知向 stderr 输出有界固定诊断；后续即使回收成功，也以失败而非正常停止退出。未知可能永久存在，因此可能无限期持锁，需要后续管理员诊断与处置设计，不能把 ECHILD 或锁本身当作完整进程树证明。控制通道或自身 pidfd 关闭异常也不能跳过直接子回收。SIGKILL、宿主故障、后代及外部 job 不在本保证内。

隔离旧 2cd3c17 DEB＋源码覆盖：注入自身 pidfd 关闭异常和直接子 pidfd 回收故障，先使直接子退出并用 pidfd 验证退出，再验证父监督存活、两锁均竞争失败；再发 SIGTERM、解除注入后验证真实回收回执、非零退出和双锁可重取（`20261028-102924-732bcb.log`）。正常终止及 fork/pidfd 窗口回归通过（`20261028-102614-873655.log`；该结果在最终清理顺序调整前）。最终源码完整回归 1305 passed / 16 skipped，ruff 通过（`20261028-102935-2e0003.log`）。测试容器已回收；这是限定源码实验，尚非准确新包、完整树/job 或生产授权验收。补充两种故障路径的最终源码验证（`20261028-103132-02e29d.log`）：子 pidfd 回收未知，以及子 pidfd 获取失败后门闩子进程 `waitpid` 回收未知。两种场景均先确认子退出、重复终止信号后父侧双锁仍被持有，并在恢复后取得回收回执；门闩场景未创建 listener。生产门禁保持关闭。


## 241. 查询处理进程异常退出后的查询子进程

一次性无网络 Linux 容器中，旧 2cd3c17 DEB 加载当前 `native_helper` 源码，运行实际 `Systemd.query`，将 `systemctl` 替换为有限寿命、可显式释放的测试程序。测试父进程持有真实 daemon 双锁并创建 Unix listener；替身启动后通过 pidfd SIGKILL 该父进程，确认父进程退出后替身仍存活，而双锁可重取；替身进程 FD 检查未发现 socket。最后显式释放替身并观察 pidfd 退出，专属容器已删除。脚本 `scripts/accept-native-query-subprocess-orphan-source.sh`，日志 `20261028-103429-bc81b2.log`。

该实验直接调用真实 `Systemd.query`，不是完整受控监督候选、实际绑定实例或真实 systemd；只证明替身时序下直接回收处理进程、锁可重取不等于查询子进程终止，不证明真实 systemctl 有同样长期存活，不证明后代、外部 job 或生产影响。需要先设计对查询子进程和后代的可验证持锁收口，覆盖子进程启动窗口、handler/监督者异常退出、pidfd/回收错误和外部委派；不可把本实验或局部 pidfd 回收用于开启 dispatch/recovery。


## 242. 查询后代 subreaper 限定收养实验

一次性 Linux 容器中先设置并读取 Linux child subreaper 标志，独立监督 fixture 持有 daemon 双锁，启动调用真实 `Systemd.query` 的 handler，测试替身启动后通过 pidfd 结束 handler。`waitpid(-1,WNOHANG)` 表明被收养子仍存活，竞争进程不能取得双锁；显式让替身自然退出后由监督 fixture 实际回收该 PID，再观察 ECHILD 并退出锁作用域，之后双锁可重取。旧 2cd3c17 DEB 加源码覆盖，`scripts/accept-native-query-subreaper-source.sh`，日志 `20261028-103601-1a0f00.log`，测试容器已删除。

此为单受控替身、非完整 `_supervise_candidate_queries` 的可行性实验，不证明多代并发、fork 竞态、稳定身份强制终止、回收故障、监督者自身崩溃、外部 job 或持久状态；未接入生产，不授权启动和恢复。候选及其必要限制见 `docs/native-helper-dispatch-handoff-design.md` 的“查询监督的后代收口候选”。


## 243. 未接生产查询监督候选的收养后代持锁

私有 `_supervise_candidate_queries` 在已持 daemon 双锁、fork handler 前检查调用进程无既有可等待子进程，拒绝已有子进程及已启用 subreaper；设置 Linux child subreaper 并核验，完成后恢复。依赖既有单线程、默认 SIGCHLD 和独占 waiter 的专用进程前提。直接 handler 先经原有 pidfd / 门闩回收路径，随后在同一锁作用域等待收养的后代自然退出并逐个回收，直到 ECHILD；等待错误不能作为完成证明，沿用未知时持锁重试。未知、不可中断或不退出的后代可能无限持锁；不得向裸 PID 发信号。

隔离 Linux 测试用真实 `_supervise_candidate_queries`，但覆写 query loop 以调用真实 `Systemd.query` 和有限寿命替身：停止 handler 后替身仍存活时两锁拒绝竞争，释放替身并收养回收后监督退出、双锁可重取；独立验证既有子进程拒绝且没有误回收（`scripts/accept-native-query-supervisor-descendant-source.sh`，`20261028-104137-df088b.log`）。旧 2cd3c17 DEB 加源码覆盖；两条直接子未知回收故障（`20261028-103922-14dce9.log`）及 fork/pidfd 窗口（`20261028-104243-f769a0.log`）回归通过。完整测试 1308 passed / 16 skipped，ruff 通过（`20261028-104147-0b9b99.log`）；测试容器已删除。

以上只覆盖单一受控查询替身自然结束，不证明实际完整 handler、并发多代、其他 waiter、不可中断进程、监督父 SIGKILL、宿主故障或 systemd 外部委派；没有持久 unknown/管理员恢复契约，无法授权生产 start、job、recovery 或包更新。不得把 ECHILD、pidfd 或双锁空闲单独当作整体动作收口。准确新包、独立评审和 P1 验收均尚未完成。


## 244. 将候选验证纳入监督收养与持锁范围

第243节候选先调用 `_verified_helper_bytes`，其自检实际通过独立 Python 解释器导入候选模块，若导入派生后代并退出，原顺序可能在设置 subreaper 前产生逃逸。现在先获取双锁、检查专用进程并启用 subreaper，再执行验证；无论验证成功还是抛错，都先在锁内回收已收养后代，成功路径在后代全部回收后才启动 handler。验证遗留进程不退出或回收未知时可能无限占锁；默认拒绝不变。

隔离源码覆盖测试在验证包装中启动有限寿命子进程后，分别完成实际验证与注入验证失败：子进程存活时双锁拒绝竞争，handler 未启动；显式释放后仅成功路径启动 handler，失败路径非零退出且无 listener（`scripts/accept-native-query-supervisor-descendant-source.sh`，`20261028-104619-790ab3.log`）。首次运行夹具嵌套代码换行错误导致子进程未就绪，修正后通过（`20261028-104501-44523e.log`）。验证期 SIGTERM、fork/pidfd 窗口通过（`20261028-104737-8c0e43.log`）；完整回归 1308 passed / 16 skipped，ruff 通过（`20261028-104642-be96f2.log`）。专属容器已删除。

这是受控进程与源码覆盖实验，未证明候选导入主动外部委派、任意多代或监督父崩溃后的持久恢复；生产门禁仍关闭，不代表准确新包、真实 handler 或完整 P1 验收。


## 245. 真实查询 handler 与两代替身的监督故障

一次性无网络容器中用普通用户完成离线实例安装并真实注册，运行 `_supervise_candidate_queries` 的真实私有 query listener，经 Unix socket 和逐消息凭据发送实际 query，保留 `Helper.handle`、实例绑定与 `Systemd.query` 路径，仅将 systemctl 可执行路径替换为有限寿命两代测试程序。监督停止时 handler 退出，两代替身仍存活，父监督继续持双锁；自然释放后回收并退出，未创建 start intent（`20261028-105020-3cb0ca.log`）。

追加对 `_wait_adopted_query_children` 的测试注入：后代退出前后均持续报告未知，父监督继续持双锁；解除注入后回收并以非零退出，双锁才可重取（`scripts/accept-native-query-handler-tree-source.sh`，`20261028-105126-2744bd.log`）。旧 2cd3c17 DEB 加源码覆盖，专属容器已删除；未启动真实 Device 或 systemd。此实验不证明真实 systemctl 的外部委派、父监督被强杀/宿主崩溃后的持久 unknown、不可中断后代或完整生产 handler 身份，不解除生产门禁。


## 246. 查询监督父进程 SIGKILL 的锁与后代反例

在第245节真实注册、实际 query handler 加两代有限寿命 systemctl 替身的隔离场景中，对监督父进程发送 pidfd SIGKILL：handler 父死亡观察线程使 handler 退出，但两代替身仍存活，同时原 daemon 双锁可被重新取得。随后显式释放替身并用 pidfd 确认退出，专属容器已删除（`scripts/accept-native-query-handler-tree-source.sh` 的 `DEVICE_SUPERVISOR_MODE=crash`，`20261028-105304-b78b28.log`）。旧 DEB 加源码覆盖，未执行真实 systemctl 或 Device。

因此第243—245节仅保护存活的监督者，锁空闲、直接 handler 已退出或 subreaper 标志均不能证明树已停止；监督者崩溃时后代收养者变化，必须设计持久未知围栏、所有相关入口一致拒绝及管理员可核验恢复，且考虑 fsync 发布不确定和宿主重启。现有生产 start/job/recovery/包围栏保持关闭；不能自动删除标记或靠杀未知 PID 解锁。此实验不证明实际 systemctl 派生同类后代或发生真实生产故障。


## 247. 查询中间后代先退出的收养时序

扩展第245节真实 query handler 与两代测试替身场景：监督停止并回收 handler 后，通过已持 pidfd 仅终止明确的中间替身，确认孙进程仍存活；通过 `/proc/<grandchild>/status` 观察其 PPid 已变为监督者，双锁仍不可重取。显式释放孙进程后观察退出，监督正常结束、双锁重取（`DEVICE_SUPERVISOR_MODE=intermediate-exit`，`20261028-105641-cf9bd4.log`）。一次性无网络容器已删除；真实 systemctl、外部 job、父监督崩溃恢复及持久围栏不在实验范围，生产门禁不变。


## 248. 监督持久意图原语与跨锁反例

真实 query handler 的隔离替身运行时，daemon 双锁与 package EX 可同时取得（`20261028-110016-8c5d3f.log`）；两类锁不天然互斥。该测试不执行包动作，不证明现有生产事务可越过其他门禁；但不能据 daemon 双锁推断包事务被封闭。

新增未接生产、未接入查询监督候选的 `_publish_query_supervisor_intent` 原语：调用方必须自行持有全部参与者的共同租约，固定文件名 no-replace 发布绑定 run ID 和 snapshot digest 的意图，使用既有文件 fsync、硬链接和目录 fsync 路径；已存在、坏文件、断链符号链接或发布不确定均拒绝，不自动删除、退休或恢复。该函数不验证实际持锁，不提供跨入口保护、发布后可运行证明或持续身份。Linux 源码覆盖旧包的两进程竞争仅一方成功，目录 fsync 注入失败后可见意图保留且不覆盖（`scripts/accept-native-query-intent-source.sh`，`20261028-110233-5c6c6e.log`）；注入不是掉电持久证明，容器已删除。

完整回归首次 1315 passed / 16 skipped / 1 failed，失败为安装 runner 子进程就绪超时（`20261028-110302-8ab0bd.log`），单项重跑通过（`20261028-110414-663094.log`），完整重跑 1316 passed / 16 skipped（`20261028-110425-223a52.log`）；首次超时根因未知。ruff 通过。下一步须设计与 package SH/EX、daemon 及所有旧入口一致的锁序、意图观察和安全终态；不可仅把本原语接到单个入口后声称已形成持久围栏，生产门禁继续关闭。


## 249. 查询监督候选与包锁的运行期互斥

仅未接生产的 query-only 监督候选在 daemon 双锁之后尝试取得共享 package lease，持有至候选验证、handler 和收养后代回收结束；包锁为非阻塞竞争，已有 package EX 时拒绝启动，不运行验证或 fork。真实 query handler 源码覆盖旧包的隔离测试确认：监督运行期间 package EX 拒绝，中间后代退出但孙进程存活期间仍保持，完整收口后可重取；包 EX 先持有时验证前拒绝（`20261028-110716-5b9172.log`、`20261028-110812-3128c1.log`）。pidfd 与无 pidfd 两条直接子回收未知路径期间也均保持包共享锁（`20261028-111003-688123.log`）。完整回归 1316 passed / 16 skipped，ruff 通过（`20261028-110832-b443d9.log`），专属容器已删除。

这只证明参与同一 registry/package lock 的协作进程在监督者存活时互斥；SIGKILL 父监督后 handler 退出、后代仍存活时 daemon 锁及 package EX 均可重取（同 `20261028-110716-5b9172.log`）。锁描述符在 fork 阶段仍会被继承，exec/CLOEXEC 不证明整个 FD 生命周期；未证明旧 helper、冻结 snapshot 或原生包钩子参与同一协议。持久意图原语仍未接入，未知恢复/终态未实现，不能解除任何生产门禁。


## 250. 私有查询监督候选启动前发布持久意图

未接生产的 `_supervise_candidate_queries` 取得 daemon 双锁及共享 package lease、检查专用 subreaper 条件后，在候选字节验证和任何 handler fork 前，使用第248节原语发布固定名称、绑定随机 run ID 和 snapshot digest 的 no-replace 意图。发布失败或未知时不运行验证/handler。候选**任何退出均不自动退休意图**；正常停止也会阻止同一 registry 再次启动此候选。没有人工恢复入口；不能删除意图绕过。

隔离真实 query handler + 两代替身：监督父 SIGKILL 后后代仍存活、daemon 与 package 锁可重取，但意图保留，同候选在验证前拒绝重启（`20261028-111205-b67ac8.log`）。实际监督入口注入文件 fsync、目录 fsync 故障时均不验证或启动，目录 fsync 失败留下的可见意图阻止重启（`20261028-111508-5f7fb8.log`）；正常停止不退休，fork 窗口回归通过（`20261028-111546-c12541.log`）。测试场景改为各自独立 registry，不删除意图复用状态；完整 1316 passed / 16 skipped，ruff 通过（`20261028-111559-9f4c61.log`），专属容器已删除。注入和进程 SIGKILL 均不证明真实掉电后目录持久性。

此意图仅由私有候选自身消费；现有生产 daemon、安装/更新、包钩子和旧冻结 snapshot 未受该意图保护。发布后无持久终态和恢复契约，且旧代码可绕过，故不能声称跨入口围栏或恢复完成，既有生产 start/job/recovery/包保护保持关闭。下一步须在共同 package 锁及管理员维护前提下逐入口设计和验证守卫，审计旧快照，另行设计 durable 终态和管理员处置。


## 251. 监督意图发布与当前源码的部分入口互斥

发现第250节监督候选及 start 都以 package SH 操作时，start 可能在检查之后、意图发布之前执行；现调整为监督候选在 daemon 双锁后以 package EX 发布意图，发布成功后降为 SH 并持续持有。即便锁转换存在竞争间隙，参与协议的新代码在取得 package 锁后必须先检查已发布意图，存在任何文件或断链符号链接均按未知拒绝。start 在其包 SH 内、新 intent 发布和调用 sender 前检查；register/recover-registration 在包 SH 内、创建 management lock 前检查；新包 apply 与 recover 在包 EX 内检查。已有 apply intent 的只读观察及 query 保持可读，未绕开既有拒绝门禁。

隔离 Linux 受控交错确认：发布方持 EX 期间 SH 获取失败；发布后降 SH，参与方持 SH 检查到意图并拒绝；反向持 SH 时发布方 EX 获取失败（`20261028-112601-8583c9.log`）。真实注册的 query 监督父 SIGKILL 后，两代替身仍存活，当前源码 helper 使用受控只读 adapter 的新 start 被拒绝，未写 start intent、未调用 start（`20261028-112413-a76aa3.log`）。单元测试覆盖登记拒绝前不创建管理锁、apply/recover 不增加事务记录及包动作、只读 query 保留；完整 1321 passed / 16 skipped、ruff 通过（`20261028-112437-ab1ac3.log`）；专属容器已删除。锁转换和注入试验不证明掉电持久性。

此为**部分入口的源码收紧**：旧 helper/bootstrap、旧冻结 snapshot 不自动接入，普通生产 daemon 启动、其他更新/部署/退休路径和原生包钩子仍须逐项审计及覆盖；当前意图无 durable 终态和管理员恢复入口，正常停止也阻断后续动作。不得删除意图绕过，不以本阶段为跨入口闭环或生产许可，原有 start/job/recovery/包门禁保持关闭。


## 252. 新源码 daemon、部署、发布和 socket 恢复的未知意图围栏

当前源码普通 `serve` 按 daemon 双锁→package SH 获取，锁内拒绝任意可见 query-supervisor intent；释放 package SH 后持续持 daemon 双锁运行，遵守同一 daemon 锁协议的监督者不能在此期间发布意图。`recover_socket` 按相同顺序持有双锁与 package SH，检查意图后贯穿 socket 核验、unlink 和目录 fsync；有未知意图时原 socket 保留。`publish_managed_helper_candidate` 在 package EX 内、任何候选写入前检查意图。`deploy_helper` 对新旧两种 unit 在 package EX 内检查意图，并将锁持至 unit 的 no-replace 发布及目录 fsync；候选部署使用已有锁文件，新建普通登记目录只在可信父目录下建立，不意味着启动或授权。断链符号链接同样视为未知，检查不解析或退休记录。

定向测试 342 passed / 1 skipped；最新完整回归 1334 passed / 16 skipped，ruff 通过（`20261028-113858-b8c4b7.log`）。一次性隔离 Linux 使用旧 DEB 加当前源码覆盖、真实 socket 和跨进程文件锁，确认拒绝四个入口、socket/意图保留、daemon 持锁时另一进程无法获取监督者锁（`20261028-113853-116f32.log`）；服务监听循环为替身，未启动 Device/systemd，容器按授权删除。首次新增测试因缺导入失败、部署重构测试因缺局部变量失败，修正后重跑通过。

此措施仅覆盖当前源码参与者，非完整生产封锁：`run_verified_helper_candidate` 在进入冻结候选自身 `serve` 前先执行候选验证，旧 snapshot 中的 `serve` 不会自动获得新检查；验证导入可派生后代，不能凭 daemon 锁空闲或 socket 不存在证明树已收口。已经运行的旧进程、包钩子、其他退休/更新入口、持久终态与管理员恢复仍需审计。DEB/RPM 覆盖保护和所有原有拒绝门禁不放宽；不得删除意图绕过。


## 253. 包安装和卸载钩子拒绝断链路径

审计发现 DEB preflight 对 instances 与 manager、DEB prerm 对实例目录/manager/helper unit 仅用 `-e`，断链 symlink 可能被误判为不存在；RPM preun 同样仅用 `posix.stat`。现对这些既有保护目标增加 `-L` 或 `posix.readlink` 拒绝；只收紧缺口，不赋予更新、卸载或恢复许可。固定版本 payload/bootstrap 子路径也显式检查符号链接。旧已构建包不自动更新。

从构建脚本提取 DEB preflight/prerm 的定向测试 23 passed，覆盖空路径、文件和断链 symlink；RPM 为源码断言，未执行 Lua/RPM。一次性隔离 Linux 容器运行提取的脚本并核对断链条目保留（`20261028-114307-088350.log`），未执行真实包安装卸载，容器已删除。完整回归 1345 passed / 16 skipped，ruff 通过（`20261028-114316-6d7aa0.log`）。未构建新 DEB/RPM，实际包管理器事务、RPM 运行行为和目标系统验证仍待完成；不得视为覆盖全部监督意图入口。


## 254. 监督器锁故障实验与候选自检调用链更正

使用真实私有 `_supervise_candidate_queries` 进行隔离故障注入：另一进程持 package SH 时，监督器获取 EX 失败，未发布意图、未执行验证或 fork；注入 EX→SH 降锁失败时已发布意图仍存在，验证和 fork 均未执行（`20261028-114604-3b249d.log`）。测试对验证/fork 设置替身哨兵，未运行真实 handler；结果不证明崩溃持久或后代收口。一次性容器已删除。

**更正第244、252节以及相关历史记录中对验证调用链的表述**：当前 `_verified_helper_bytes` 和 `read_helper_candidate` 只读取并验证快照与文件，未调用候选解释器；第244节后代实验是在 `_verified_helper_bytes` 上注入派生子进程的替身，证明的是注入场景下的收养，不证明该函数的实际现行实现会派生进程。实际的 `inspect_helper_candidate` 在 `publish_helper_candidate` 中用候选解释器执行导入自检；`_verified_helper_modules` 则在候选启动时执行候选模块字节，可产生副作用和子进程。

重要剩余缺口：`publish_managed_helper_candidate` 虽在包 EX 内拒绝已有 query 监督意图，但其自身启动的自检没有新的持久监督意图；自检父进程退出/崩溃与锁释放不能证明后代收口。旧 candidate 的模块执行和 `serve` 仍没有完整锁交接与跨崩溃围栏。`docs/native-query-supervisor-recovery-design.md` 记录了覆盖表、监督单元、代际、完整本地/外部树、持久终态与管理员处置的待设计条件；不是实现或恢复授权。任何上述缺口均不得通过删 intent、锁空闲或人工猜测放行，生产门禁保持关闭。


## 255. 候选发布和启动默认拒绝未证明进程树

真实 `publish_managed_helper_candidate` 的隔离反例使用受控候选 helper 模块在导入自检时 fork 有限寿命后代；发布成功返回后后代仍活、包 EX 可重取且没有监督意图（`20261028-115003-948b25.log`）。实验不是断电证明，也不暗示可信现有模块必定会这样做；但证明自检退出与包锁释放不足以覆盖后代。

当前源码新增两个永拒门禁：受管候选发布在包锁内及现有意图检查后、复制/自检前拒绝；候选启动在验证和执行模块前拒绝。候选 unit 部署在写 unit/运行目录前调用相同启动门禁拒绝，避免部署一个必定不能启动的服务；普通非候选部署不因该候选门禁关闭。底层测试原语不构成授权，原有历史路径测试仅在显式 monkeypatch 门禁时验证机制，不能视为生产放行；现有旧 bootstrap、在途进程、冻结 snapshot 和历史制品不会自动被新门禁修复。

隔离 Linux 旧 DEB 加源码覆盖确认三个入口在任何候选代码/目标 unit 写入前拒绝；另外显式测试替身绕过发布门禁复现历史后代反例并自行收口（`20261028-115511-bae687.log`）。首次容器脚本漏导入 `sys` 未完成第三项，修正后重跑通过；容器均已删除。定向测试 261 passed / 1 skipped，完整 1348 passed / 16 skipped、ruff 通过（`20261028-115353-71247d.log`）。未构建新包，第三方审核及 P1 验收尚未完成；监督终态、管理员恢复、旧版本维护窗口和其他入口仍须完成。

现有一些历史隔离验收脚本使用受管发布准备候选，新门禁生效后将直接拒绝，不能将它们当作本次重跑通过；后续如需继续这些实验，应在各自严格隔离夹具中明确标注测试替身及范围，并重跑相应脚本，不添加生产可配置的绕过开关。


## 256. 受门禁影响的部分私有监督验收夹具

四个严格一次性 Linux 夹具的候选准备步骤显式以测试替身绕过受管发布默认门禁，范围仅该测试进程；不提供产品绕过选项。实际重跑 `accept-native-query-supervisor-source.sh`，验证正常停止不退休、验证期间停止与 fork/pidfd 故障回收（`20261028-115759-0f119e.log`）；重跑 `accept-native-query-handler-tree-source.sh` 的 crash 模式，观察监督父 SIGKILL 后后代仍活且锁可取，但当前源码新 start 和同候选重入因持久意图拒绝（`20261028-115816-c210d2.log`）。均为旧 DEB 源码覆盖、受控替身，并非真实 Device 服务或 systemd 外部 job 验收，两个一次性容器已删除。

`accept-native-query-supervisor-descendant-source.sh` 和 `accept-native-supervisor-reap-failure-source.sh` 只调整了同样的夹具准备替身，尚未重新执行；其他使用受管候选发布或旧候选启动的历史脚本也尚未逐项调整、重跑。最新完整产品回归仍为第255节的 1348 passed / 16 skipped。不得将夹具替身当成生产授权，也不改变持久未知或其他关闭门禁。


## 257. 重启后补验两个监督故障夹具

服务恢复后核实 `ses-14204` 非私有、唯一 execution 指向任务、任务停止原因检查开关开启；原 Worktree 干净且 HEAD 与已保存检查点一致。补跑第256节中两个已调整但未执行的隔离夹具：`query-supervisor-descendant-source` 注入验证子进程的 success 和 fail 两种模式均通过（`20261028-120254-eb945d.log`、`20261028-120338-6aa333.log`）；`supervisor-reap-failure-source` 的 pidfd 和 gated 两种模式均通过（`20261028-120309-8a62ab.log`、`20261028-120352-384c15.log`）。四个一次性容器均删除并核对不存在。

候选准备是明确标识的测试替身；验证阶段的派生子进程也为注入替身，现行 `_verified_helper_bytes` 只读文件。查询循环使用替代 worker，不代表实际外部 systemd job、持久终态、真实服务或掉电安全。其他依赖受管发布的历史脚本尚未全部调整或重跑，需按各自隔离约束逐个处理；生产候选门禁保持关闭。


## 258. 新 start 初始查询前检查监督未知意图

审计发现 `Helper.handle(start)` 原先在监督意图检查前先调用 `_snapshot`；真实 `Systemd.query` 会启动 `systemctl show` 子进程，即使之后拒绝 start 也已派生查询。现对没有既有同 ID intent 的新 start，在实例锁内取得 package SH、先检查 query 监督意图，再持锁执行初始查询；已有同 ID 的原操作观察仍按只读路径返回，不因本次收紧重放动作。原有发布 start intent 前的第二次包锁检查保留，不能将初次检查视为跨阶段授权。query/reconcile 保持原只读语义。

定向测试 302 passed / 1 skipped；隔离 Linux 在真实登记后用受控适配器验证初始 start 查询期间 package EX 拒绝，并在真实私有监督 SIGKILL 后确认新 start 在调用查询和 sender 前拒绝（`20261028-120642-babec1.log`）；无真实 Device 服务启动，测试容器已删除。完整回归 1350 passed / 16 skipped、ruff 通过（`20261028-120655-1c3500.log`）。

此收紧只在已发布意图时阻止新 start 的初始查询，并在查询调用期间阻止遵守包锁协议的意图发布；查询返回或超时后，其后代是否退出仍未证明，query/reconcile 和既有 ID 观察也可能产生查询子进程。旧 helper/snapshot 不自动生效；不得将锁空闲或本次检查作为进程树收口、恢复或生产授权，所有默认门禁保持关闭。


## 259. 更新准备和批准检查监督未知意图

审计发现 `prepare`、`prepare_signed` 与 `approve` 可创建更新目录、认证临时数据或准备/批准台账，普通准备还会执行包元数据查询，但此前不消费 query 监督意图。现三者在实例管理锁后取得 package SH，先拒绝任何可见监督意图，再初始化事务目录并在持锁期间完成各自写入；签名准备与后续 `prepare` 分两次取得锁，后者重新检查，不把首次通过当作持续授权。原 apply/recover 的包 EX 拒绝及既有 apply intent 只读观察路径不变。

定向 308 passed / 1 skipped；完整回归 1356 passed / 16 skipped，ruff 通过（`20261028-121122-aa8add.log`）。隔离 Linux 使用旧 DEB 完整运行时加当前源码覆盖及包适配器替身，确认断链意图在创建 updates 目录和元数据调用前拒绝、准备期间 EX 获取失败、批准在意图存在时不写记录（`20261028-121314-347f07.log`）；首次实验误用只含 helper 的独立 bootstrap 导致 ImportError，改为完整运行时后重跑通过，容器均已删除。无实际包更新、真实 Device 服务或外部进程树证明。

该锁只覆盖当前源码和调用期间；元数据工具若派生后代，在调用返回或超时后仍需完整监督与终态协议。旧版本/冻结 snapshot 不自动生效，已存在的无意图运行进程、其他入口及管理员恢复仍需审计，生产门禁不开放。

随后补充签名准备两阶段之间插入监督意图的回归：第二次 `prepare` 重新检查并拒绝，不产生对应准备或批准记录；`test_native_update.py` 共 110 passed、ruff 通过（`20261028-121412-68efed.log`）。上述完整 1356 项运行在添加此单项测试之前，产品代码未变化。


## 260. 新 job 派发在包元数据查询之前默认拒绝

`_prepare_dispatch_locked` 此前先调用 `_prepared`（可能启动包元数据工具）再检查新 job 的默认拒绝门禁。现对无既有 job intent 的请求先执行原有永拒门禁，再读取准备信息；存在断链或其他 symlink 的 intent 明确拒绝，避免把无效条目当作既有操作绕过。已有有效 job intent 的观察仍按既有准备校验和无重放路径处理。此改动只提前拒绝，不放开派发、恢复、退休或包操作。

job/update 定向 164 passed；完整回归 1358 passed / 16 skipped、ruff 通过（`20261028-121705-b5c2c7.log`）。隔离 Linux 旧包加源码覆盖，在受控 manager 下确认新 job 于元数据调用和 intent 写入前拒绝（`20261028-121701-6a2951.log`）；未调用 systemd-run、未启动真实 Device 或包事务，容器已删除。调用方在进入该私有函数前的 `_transaction` 仍可能创建 updates 目录和检查绑定，此验证不声称其完全无文件副作用；旧 worker/snapshot 不自动更新。


## 261. 新 job 拒绝前不初始化更新事务

第260节的调用方在默认拒绝前会创建 updates 目录。现新增长期受实例锁保护的 dispatch 预检查事务：准确判断该 ID 的 job intent 不存在时先调用既有默认拒绝门禁，只有既有 intent 的观察或测试明确替身放行才初始化事务；原私有函数保留再次检查以防调用方绕过和交错。已存在的 intent 继续走既有核验和不重放逻辑，异常 symlink 仍拒绝。实例管理锁本身可能创建实例锁文件，因此不声称整个调用零文件副作用。

隔离 Linux 用真实 `Updates` 和受控 helper，确认新 job 在 binding、updates 目录和 runner 前拒绝（`20261028-122040-1f79f9.log`）；未运行真实 systemd job，容器已删除。完整 1359 passed / 16 skipped、ruff 通过（`20261028-122044-911fb4.log`）；首次定向测试通过但 ruff 报新增测试导入分组，修正后完整重跑通过。更改只收紧默认拒绝，不能替代旧版本、并发进程树及持久终态协议。


## 262. 新 apply 与 recover 在包元数据查询前检查监督意图

新 apply 和 recover 原先在获取最终 package EX 前调用 `_prepared`，其中包含包元数据子进程，即使之后因监督未知意图拒绝也已执行查询。现对这两条变更路径在实例事务内先持 package SH 检查意图并完成准备校验，随后释放 SH、仍在原 package EX 内再次检查后才执行变更；已有 apply intent 的观察分支保持原只读语义。测试覆盖预校验与 EX 之间出现意图时二次检查拒绝、无 apply intent 和包动作；recover 在意图已存在时不调用元数据。

定向 165 passed；隔离 Linux 旧 DEB 源码覆盖确认意图存在时新 apply 在元数据和 apply intent 前拒绝（`20261028-122514-0f99a6.log`），包适配器为测试替身，容器已删除；完整 1360 passed / 16 skipped、ruff 通过（`20261028-122518-698b9e.log`）。当前 `_transaction` 在这些检查前可能创建 updates 目录及 fsync，实例锁文件也可能创建，不能声称整个调用零文件副作用。reconcile 和既有 intent 观察仍会初始化事务目录及调用包查询，不能把逻辑只读等同于无文件或进程副作用；这些查询后代、旧快照和 durable 恢复仍未闭环。生产门禁未开放。


## 263. reconcile 不初始化缺失的更新事务目录

`reconcile` 保留实例租约和绑定校验，但不再调用会创建 `updates` 并 fsync registry 的事务初始化路径；目录缺失明确拒绝，已有目录须通过原管理员可信路径校验。已准备事务仍执行原有元数据和已安装包查询，返回原观察结构；实例锁可能创建锁文件，查询本身可能派生后代，这不是完全无副作用或进程树收口证明。已有 apply intent 观察仍沿原事务初始化路径，待单独处理。

定向 168 项通过；完整 1362 passed / 16 skipped 与 ruff 通过（`20261028-123322-35bb40.log`）。隔离 Linux 在真实路径信任检查下验证缺失及符号链接目录拒绝且未初始化（`20261028-123316-b14b74.log`）；helper 和包适配器为测试替身，容器已删除，无真实 Device 服务或包更新。候选、恢复和生产门禁不变，旧包与冻结快照不自动获得该修正。


## 264. 既有 apply 观察避免事务初始化

`apply` 在原实例租约内先识别已有 apply intent，已有记录走只读取可信事务目录的路径，不再执行 mkdir 或 registry fsync；新操作仍沿原事务初始化及预校验、package EX 复核与持久意图流程。已识别的 intent 在观察前消失则拒绝，不转换为新事务；registry/祖先稳定仍是管理员维护前提，检查不是对恶意管理员或未协调旧进程的原子防护。未知监督意图存在时保留原只读观察，不删除标记，不重放包动作。

定向 170 项通过；完整 1364 passed / 16 skipped、ruff 通过（`20261028-123635-d16822.log`）。隔离 Linux 以包和 helper 测试替身确认已有意图观察不初始化目录、不 fsync、不重放，仍执行 metadata/installed 查询（`20261028-123631-bba921.log`），容器已删除。实例锁文件可能创建，元数据和安装状态真实查询可能派生后代；测试未证明查询树收口、旧代码覆盖或生产恢复安全，门禁不变。


## 265. 不可信 apply 意图在包查询前拒绝

原 `exists()` 对断链符号链接返回 false，可能将已有未知条目误判为新 apply 并在后续门禁前执行 metadata。新源码在实例租约内以 `lexists` 识别已有意图，观察前验证可信普通文件；断链、符号链接、目录和 FIFO 均拒绝且保留。新事务初始化期间意图出现则拒绝，不继续执行预校验。记录消失仍拒绝；registry 等稳定性依赖管理员维护约束，不声称保护不协调的旧进程或任意文件替换。

定向 175 项通过；完整 1369 passed / 16 skipped、ruff 通过（`20261028-124020-f90409.log`）。隔离 Linux 真实路径校验下，断链、指向现有文件的符号链接及 FIFO 意图均在包查询前拒绝（`20261028-124016-54e307.log`），使用 helper/包测试替身，容器已删除。合法意图的观察仍可能执行包查询；metadata 与 installed 子进程的完整后代收口仍未证明，现有生产及恢复门禁不变，旧包和冻结快照不自动更新。


## 266. 更新观察不把异常证据当缺失或普通文件

`_observation` 对 apply intent、outcome、recovery 使用 `lexists` 区分缺失与异常目录项，已有条目须经可信路径及普通文件校验；断链、链接、目录、FIFO 等保留并拒绝，不打开 FIFO 或调用后续 installed 查询。合法文件的观察结构不变；错误不代表事务已完成或恢复获授权。已有调用方在进入 `_observation` 前仍可能执行 `_prepared` 的 metadata 查询，不能声称所有查询前拒绝。检查和读取间依赖可信管理员维护稳定目录，不是 OS 沙箱。

定向 187 项通过；完整 1381 passed / 16 skipped、ruff 通过（`20261028-124307-443bbd.log`）。隔离 Linux 使用真实可信路径检查对三类证据分别验证断链、符号链接和 FIFO 拒绝（`20261028-124302-4fd495.log`）；该实验直接调用观察函数，包及 helper 为替身，容器已删除。旧冻结代码、真实包查询后代和持久终态不受该局部修正完整覆盖，生产与恢复门禁保持关闭。


## 267. 包查询进程树与超时边界审计

盘点 `Debian.metadata`、`installed` 以及包 guard 查询调用与锁/后续写入，记录于 `docs/native-query-supervisor-recovery-design.md`。准备阶段在 package SH、变更阶段部分在 EX，观察可能无 package 租约；锁与监督意图检查不能证明查询的后代退出或外部 job 收口。

隔离 Linux/Python 3.13 使用有限寿命 fork 替身验证：直接子进程等待或先退出两种情况下，`subprocess.run` 捕获管道的 0.25 秒超时均约 0.26 秒返回，后代随后仍运行并自然结束（`20261028-124618-f9e097.log`）。首次误以为 timeout 会等待管道后代，测试以约 0.26 秒返回否定该假设（`20261028-124547-1fbe31.log`），已修正实验与记录；这不是实际 dpkg 派生后代的证明。专属容器自动删除并验证不存在。未修改产品代码或重跑完整回归，最近完整结果仍为第266节 1381 passed / 16 skipped。生产、恢复和退休门禁保持关闭。


## 268. 更新预校验拒绝非普通准备与批准记录

`_prepared` 在既有可信路径检查后、读取批准 JSON 或计算 prepared 摘要前，要求 prepared 与 approved 均为普通文件，避免 FIFO 等特殊文件令读取阻塞或目录被误作记录。apply、reconcile、recover 的共同预校验因此在 metadata 查询前拒绝这些异常；文件检查与后续读取仍依赖管理员维护 registry 及祖先稳定，不是对恶意并发替换的 OS 沙箱。记录不删除，合法行为和生产门禁不变。

定向 199 项通过；完整 1393 passed / 16 skipped、ruff 通过（`20261028-124844-755aeb.log`）。隔离 Linux 在真实路径检查下验证 prepared FIFO、approved 目录在 metadata 前拒绝（`20261028-124836-676aa3.log`），包与 helper 为替身，容器已删除。未构建新包或证明真实查询后代收口；旧包和冻结 snapshot 不自动更新。


## 269. job 证据在 unit 查询前校验并拒绝异常文件

`native_update_job` 增加统一证据读取：job intent、关闭回执、apply intent、outcome、sender 与 job recovery 回执均以 `lexists` 区分缺失，已有条目须可信且为普通文件，断链/链接/目录/FIFO 保留并拒绝。`observe_job` 在执行 systemd 状态查询前完成这些校验；outcome 存在但 sender 缺失时明确拒绝，不再把它当作未执行。job intent 缺失现返回明确错误而非底层 `FileNotFoundError`。该顺序只覆盖本入口的读取，不把状态查询当作执行收口，也不放行重试、退休或恢复。

定向 212 项通过；完整 1406 passed / 16 skipped、ruff 通过（`20261028-125942-00a8f8.log`）。隔离 Linux 验证断链 outcome 与目录 sender 在 unit 查询前拒绝（`20261028-125927-5c1111.log`）；隔离中首次夹具身份字段不全、后代写入未显式关闭文件分别导致断言失败（`20261028-125703-c29a98`、`20261028-125902-a295c1`），已修正夹具并重跑，不据此声称产品行为已验证。真实 systemd 外部 job、旧快照和持久终态恢复仍未闭环，生产门禁不变。


## 270. 外部 job 与旧快照入口覆盖审计

外部 job 链：`dispatch_job` 由 `_require_proven_job_fence` 默认拒绝，拒绝发生在 `_prepare_dispatch_locked` 的元数据查询与 `updates` 目录初始化之前；`describe_job` 只产出不可派发的 unit 候选，`ConditionPathExists` 门禁文件从不创建；`observe_job` 先校验证据再做 unit 查询；`recover_job` 与 `retire_closed_job` 仍分别由 recovery/retirement 默认拒绝阻挡。系统命令共四处：`dispatch_job` 的 `systemd-run`、`observe_job` 的 `systemctl show`、`Updates.Systemd.query/cancel_job` 与向 `upgrade_supervisor` 的 `systemctl` 调用；后两者属旧升级器；但 `upgrader` 与 `upgrade_supervisor` 的全部 7 个入口都由 `@legacy_operation` 包裹，受管实例（存在 `.native-management.json`）会在执行 `systemctl` 前被 `management_lease` 直接拒绝，`native_prepare._binding` 另有 `_trusted_marker` 校验标记为 root 所有、非符号链接、无组/其他写权限。故旧路径不是无围栏，而是靠受管标记拒绝；旧包未部署该标记的未受管实例仍可走旧升级器。

旧快照链：`run_verified_helper_candidate` 首行即调用 `_require_proven_candidate_launch_fence`（永抛），字节校验与 `exec(compile(...))` 均在其后不可达；`publish_managed_helper_candidate` 同样先默认拒绝；`deploy_helper` 候选路径另有固定 bootstrap 检查；普通部署的 bootstrap 已由既有 `_trusted` 与 `is_file` 覆盖符号链接、目录与不可信路径，本轮测试确认该既有行为，未新增围栏（早先添加的重复检查已撤回）。

本轮实际收紧仅两处：`read_helper_candidate` 的 `candidate.json` 要求可信普通文件，避免 FIFO 阻塞；`describe_job`、close 前置检查与 `observe_job` 的 prepared/approved 统一改用 `_read_job_evidence`。定向 bootstrap 47 项、job 74 项通过；完整 1417 passed / 16 skipped、ruff 通过（`20261028-133859-936ff6.log`）。

未闭环：`upgrade_supervisor` 旧路径仍可直接调用 `systemctl` 启动/停止真实 unit，无新意图检查；旧 helper、旧 bootstrap、冻结 snapshot 与在途进程不自动更新；外部 systemd job 的完成、取消与宿主重启后终态无持久证据；`standard` 单元部署后的实际运行身份绑定仍未验证。生产、恢复、退休门禁不变。


## 271. 旧升级器围栏与受管标记核实

第270节曾把 `upgrade_supervisor` 的 `systemctl` 调用记为“不受新 job 围栏约束”，本轮核实后判定该表述不准确，已在原节更正。实际事实：

- `upgrader` 3 处、`upgrade_supervisor` 4 处入口全部由 `@legacy_operation` 装饰，进入即取 `management_lease(root)`。
- 该租约在受管实例（存在 `.native-management.json`）上抛 `ValueError("native-managed instance requires the native lifecycle manager")`，发生在任何 `systemctl` 调用之前。
- `native_preparation_lease` 只用 `is_file()` 粗判标记，但 `native_prepare._binding` 随后用 `_trusted_marker` 要求普通文件、root 所有、无组/其他写权限，并校验 schema2 绑定、身份、unit 与 active 摘要，因此不构成绕过。

仍存在的真实缺口：未部署受管标记的旧实例可以正常走旧升级器启停真实 unit，这部分不受新协议约束，只能靠维护窗口人工迁移；旧包本身也不会因新源码而更新。本轮未调用任何真实 systemd unit，未在真实设备验证该拒绝路径，完整回归未因本次文档更正而变化（仍为 1417 passed / 16 skipped）。


## 272. 真实 systemd job 终态探测脚本（已编写，未执行）

新增 `scripts/probe-native-job-terminal-states.py`，在一次性 systemd 容器内用当前只读 `observe_job` 覆盖三种此前无证据的终态：`/bin/false` 失败 unit、排队 job 被 `systemctl cancel`、unit 被清理后 `LoadState=not-found`；每种都断言 `retry_allowed=false` 且 `fence_retained=true`。脚本强制 `DEVICE_NATIVE_DISPOSABLE_PROBE=yes`、root、PID1 为 systemd，并在 `/opt/ai-device/instances` 存在时拒绝运行，避免触碰已安装实例。全部 unit 为惰性 fixture，不执行真实包事务。

**本轮未执行**：脚本尚未在容器中运行，任何终态结论都还没有实际证据，不能据此声称 systemd 终态已验证。上一轮已确认可用的镜像 `device-qual-systemd-amd64-ses13838:probe` 默认 `Cmd` 为 `/sbin/init`。接续第一步应为：只读挂载 `packages/device-runtime/src/device_runtime` 到 `/source`，无网络运行该脚本，把 `TERMINAL_STATE_SUMMARY` 与 `PROBE_OK` 作为证据记录，然后才更新第270/271节的未闭环项。若脚本失败，应先修正探测而非修改产品判定。


## 273. 真实 systemd job 终态实测

在一次性 amd64 systemd255/cgroupv2 容器（`--cap-add ALL --rosetta`、`--network none`、PID1=`systemd`、`system-is-running=degraded`）内运行 `scripts/probe-native-job-terminal-states.py`，只读导入 `/pkg/device_runtime`（当前源码）并对真实 unit 调用 `observe_job`。容器已精确 `stop`+`delete`，`container list --all` 复核不存在。日志 `20261028-141740-edcac3.log`。

实测事实（`TERMINAL_STATE_SUMMARY` + `PROBE_OK`）：

| 场景 | systemctl show | observe_job.state |
| --- | --- | --- |
| oneshot `/bin/false` 失败 | loaded/failed, Result=exit-code, MainPID=0, Job空 | `job_failed_requires_review` |
| oneshot 运行中，存在排队 job（Job=720） | loaded/activating, SubState=start, MainPID=620 | `job_in_progress` |
| `systemctl cancel <job>` 之后 | loaded/**activating**, MainPID=620 仍在, Job空 | `job_in_progress` |
| 再 `systemctl stop` | loaded/failed, Result=signal, MainPID=0 | `job_failed_requires_review` |
| `clean` 后 unit 消失 | **not-found**/inactive, MainPID=0 | `dispatch_unknown` |
| systemctl 不可用（runner 抛 OSError） | 无观察 | `dispatch_unknown` |

所有六个场景 `retry_allowed=false`、`fence_retained=true`。

### 重要否定结论
- **`systemctl cancel` 不是终态**：它只清 Job 号，已 fork 的 ExecStart 进程继续运行，unit 仍 `activating`。把 cancel 当 failed 会误判为可进入 review/恢复路径，当前解析器没有这个错误。
- **unit 消失不会变成失败或成功**，仍停留在 `dispatch_unknown`，正确保持保守。
- `MainPID=0`＋`Result=success` 同时可能出现在 `active/exited`（Recording）与 `not-found`，单看这两个字段不区分终态，必须结合 `LoadState`。

### 仍然成立的边界（未因本轮而改变）
- 以上是**同一 systemd 实例内的瞬时观察**，不是宿主重启后的持久终态证据。
- 未覆盖崩溃中途（systemd 自身重启、机器掉电）后的 InvocationID 变化与 job 台账对齐。
- `job_in_progress`/`job_failed_requires_review` 都不授予 retry、不释放围栏、不触发退休；生产 `dispatch`/`recover`/`retire` 门禁保持不变。
- 探测中曾出现 `systemctl run`（不存在该子命令）、缺 `--no-block`、同名 failed unit 复用三次夹具错误，均已修正脚本，不是产品缺陷。


## 274. InvocationID 语义实测：不能作为「终态属于本次派发」的持久判据

在一次性 amd64 systemd255/cgroupv2 容器内只做只读探测，全部 unit 为惰性 fixture。容器已 `stop`+`delete` 并复核不存在。日志 `20261028-145943`、`150015`、`150042`。

### 实测事实

| 场景 | InvocationID | 说明 |
| --- | --- | --- |
| oneshot 失败（`loaded/failed`） | 保留，32hex | **唯一稳定留存终态的场景** |
| 成功后 `stop` | 变为空，`LoadState` 退化为 `not-found` | 停止即销毁代际 |
| `clean` 后 | 空，`not-found` | 代际随之消失 |
| `clean` 后重新派发同名 unit | 全新 32hex（`c0eab163…`） | 同名可复用，代际不延续 |
| `reset-failed` 后重新派发同名 unit | 全新 32hex（`1db1494e…`） | 同上 |
| unit 运行中两次观察 | 完全一致 | 活跃期稳定 |
| 从未启动过的 unit 名 | 空 | 与「已停止」不可区分 |

结论：**InvocationID 只在 unit 处于 `failed` 或活跃状态时存在**。一旦 `stop` 成功退出或 `clean`，代际标识即被 systemd 丢弃，且与「从未派发」在字段上不可区分。因此它无法承担「跨重启/跨停止后仍能证明终态归属」的持久判据职责。

### 派发侧两个已确认的缺陷

1. **`dispatch_job` 的 docstring 与实际行为不符。** 文档写「without waiting for execution」，但 `_prepare_dispatch_locked` 构造的 argv 没有 `--no-block`。实测同一 flag 集合对 6 秒 ExecStart **阻塞 6.1 秒**才返回。生产 worker 是真实包事务，`TimeoutStartSec=infinity`，派发将一直阻塞到包事务结束；而 `dispatch_job` 的 `runner` 用 `timeout=30`，长事务会在 30 秒后抛 `TimeoutExpired`，被归为 `dispatch_unknown`，但此时 unit 可能仍在正常运行。
2. **InvocationID 在派发时确实可得**：`systemd-run` 把 `invocation ID: <32hex>` 写进 stderr，且与 `systemctl show` 一致（实测 match=true）。加 `--no-block` 后 0.03 秒返回，ID 依然可读。也就是说修复路径存在，但当前代码没有采集它。

### 未做与边界

- 未修改任何产品代码；上述两点只是实测结论，修复涉及派发语义与超时契约，需单独设计与验收。
- 未验证 systemd 自身重启（`daemon-reexec`/崩溃）后的 InvocationID 变化，也未验证宿主重启。
- 现有 8 项 dispatch 测试全部使用替身 `runner`，没有任何测试断言阻塞语义或真实 systemd 返回，因此这两个缺陷不会被当前测试发现。

### 环境异常（必须记录）

`device-qual-systemd-amd64-ses13838:probe` 与 `device-qual-systemd-ses13838:probe` 在本轮中途**从本机消失**：第 273 节运行时仍可用，随后 `image list` 只剩 redis/pgvector，`inspect` 报 `image not found`。本轮改用按 `ubuntu:24.04 + systemd` 重建的等价镜像 `device-qual-systemd-amd64-probe:rebuild`（systemd 255，与原镜像版本一致，PID1=systemd、`is-system-running=degraded`）。重建镜像与已丢失的原镜像**不是同一制品**，后续涉及镜像身份的结论需注明使用哪一个。原镜像丢失原因未知，不能排除是被外部清理。


## 275. 修复：dispatch 不再阻塞于包事务

### 问题
`_prepare_dispatch_locked` 生成的 argv 缺少 `--no-block`。oneshot 加 `TimeoutStartSec=infinity` 时，`systemd-run` 会一直等到 ExecStart 结束才返回，而 `dispatch_job` 的 runner 用 `timeout=30`。真实包事务远超 30 秒，于是：派发在 30 秒抛 `TimeoutExpired` → 记为 `dispatch_unknown`，而 unit 仍在正常执行。这是把「执行中」误报成「结果未知」。

### 改动
`native_update_job.py`：
- argv 增加 `--no-block`，位置在 `--unit=` 之前。
- 单独捕获 `subprocess.TimeoutExpired`。加 `--no-block` 后正常路径应立即返回，超时只代表「systemd 未确认本次提交」，不再代表「已提交的执行被放弃」。
- 更新 `dispatch_job` docstring，写明 `--no-block` 的原因与边界。

未改动：`dispatch` 的永拒门禁、`observe`/`recover`/`retire` 的任何判定、`package_execution_confirmed` 恒为 false 的语义。

### 验证
- 单测 `test_dispatch_records_once_without_replay` 增加断言：argv 含 `--no-block` 且位于 `--unit=` 之前。
- 新增 `test_dispatch_never_waits_for_the_package_transaction`：断言 runner 收到 `--no-block`，且 timeout 仍为 30（仅约束提交调用，不约束事务）。
- 真实 systemd 实测（一次性容器，已删除）：8 秒 ExecStart → 派发 **0.04s** 返回，回执 `dispatch_accepted`，同时 unit 处于 `activating/MainPID=79`；`/bin/false` 场景派发 0.06s 返回、2 秒后 unit 为 `failed`。日志 `20261028-154234`。
- 完整回归 **1425 passed, 16 skipped**（`20261028-154254`），ruff 通过。

### 仍然存在的边界
- 「accepted」依旧只表示 systemd 接受了作业，不表示包事务成功，也不表示 unit 已观察。`package_execution_confirmed` 保持 false。
- 派发成功后 unit 立刻崩溃、或 systemd 在接受后重启，本轮未覆盖，仍依赖后续 `observe`。
- 修复不改变「派发失去响应即 durable unknown 且不自动重投」的既有契约。


## 276. systemd 探测镜像的构建配方已入库

### 原因
原镜像 `device-qual-systemd-amd64-ses13838:probe`（及 `device-qual-systemd-ses13838:probe`）已被用户误删。此前仓库只记录了镜像名，没有构建配方，导致无法复现，只能临时手搓等价镜像。

### 现在可复现
新增 `scripts/containers/systemd-probe/Containerfile`，配方固定为 `ubuntu:24.04` + `systemd 255.4` + `python3` + `dbus`，并预 mask 容器内无意义的 unit。构建与运行方式写在文件头注释里。

```
container build --platform linux/amd64 \
  -t device-qual-systemd-amd64:probe \
  -f scripts/containers/systemd-probe/Containerfile \
  scripts/containers/systemd-probe
```

产物：`device-qual-systemd-amd64:probe`，manifest list `sha256:7b5d57b39d1f93e2b4d7e2fa4cbffb11e2205db953477eee935ea63090819213`，amd64 层 digest `7b5d57b39d1f`。

### 验证
在一次性容器中确认：`PID1=systemd`、`systemd 255 (255.4-1ubuntu8.18)`、`is-system-running=degraded`、`/sbin/init` 存在。随后实跑两个既有探测脚本：
- `probe-dispatch-no-block-fix.py` → `PROBE_OK`（8 秒 ExecStart 派发 0.04s 返回）
- `probe-native-job-terminal-states.py` → `PROBE_OK`，六类终态与第 273 节结论完全一致

容器已 `stop`+`delete` 并复核不存在。

### 两个操作要点（曾踩坑）
1. **不要把命令直接传给 `container run`**：那会替换 PID1 为 `/bin/sh`，systemd 根本不启动。必须 `--detach` 后用 `container exec` 进入。
2. **必须 `--cap-add ALL`**：默认能力集下 systemd 启动即退出。

### 关系说明
`device-qual-systemd-amd64-probe:rebuild`（第 274 节临时重建）与本次的 `device-qual-systemd-amd64:probe` 均为不复现原镜像的重建品，但功能等价已由上述探测脚本验证。原镜像未被恢复，其历史证据（第 273 节及更早）仍指向已不存在的制品。


## 277. 实施 run-start 归属证据（设计稿第 1—3 步）

按 `docs/native-job-run-attribution-design.md` 实施前三步，第 4 步（管理员显式对账入口）按设计建议暂不实施。

### 新增证据
`native_update_job.py` 新增 `_read_boot_id` / `_boot_id` / `_record_run_start` / `_read_run_start`。

worker 在 `main()` 中**先写** `.job-run-start.json`，再做任何 apply 相关工作：

```json
{"schema_version":1,"state":"job_run_started","job_intent_sha256":"<64hex>",
 "boot_id":"<uuid>","pid":<int>,"started_at_ms":<int>}
```

- 用既有 `helper._save_new`（fsync → hardlink no-replace → fsync dir）写一次，永不覆盖。
- 已存在即 `duplicate start refused`，保护「同一次派发只启动一次」。
- 写入前校验当前 job-intent 摘要与派发侧传入的 `--job-intent-sha256` 一致，防止证据在半路被换掉。
- 不触碰 job-intent 的封闭键集（5 处精确比对），因此既有 `describe`/`close`/`retire` 全部不受影响。

### observe_job 状态映射
`run_start` 缺失时**保持既有映射不变**（这是实施中纠正的设计错误，见下）。`run_start` 存在且绑定一致时：

| 观察 | 状态 |
| --- | --- |
| unit failed | `job_failed_requires_review` |
| unit 活跃 | `job_in_progress` |
| unit not-found | `job_completed_no_unit_evidence` |
| systemctl 不可达 | `job_in_progress_unverified` |
| 无 apply-intent（任意上述情形） | `job_started_without_mutation` |
| boot_id 与当前不同 | 追加 `prior_boot_execution=true` |

全部 `retry_allowed=false`、`fence_retained=true`；`recover_job` 前置条件未改，新状态均不进入其成功路径。

### 实施中发现并修正的两个问题

1. **设计稿 4.3 有错。** 初稿写「run_start 缺失 → 一律 dispatch_unknown」，但现有实现本来就把 `unit failed` 判为 `job_failed_requires_review`（第 273 节实测）。按初稿实现会让**所有历史事务静默降级**为 unknown，属回归。实现改为保持既有映射，只把 not-found 与不可达两类从 unknown 中分离。已在设计稿就地订正。
2. **`main()` 的 import 顺序是真缺陷。** 原 `from .native_update import Updates` 在 run-start 之前；容器实测中因镜像缺 `packaging` 直接抛 `ModuleNotFoundError`，run-start 根本没写。已把该 import 移到 run-start 之后，并补回归测试锁住顺序。

### 验证
- 单测 **95 项** job 模块（含 run-start 记录/拒绝/形状/boot_id/特殊文件/状态映射/import 顺序）。
- 完整回归 **1455 passed, 16 skipped**（`20261028-164627`），ruff 通过。
- 真实 systemd 容器端到端：worker 在 apply 前被 SIGKILL → `run_start_written=true`、`apply_intent_written=false`、状态 `job_started_without_mutation`、`retry_allowed=false`、`fence_retained=true`；重复启动被拒。日志 `20261028-164456`。容器已删除并复核不存在。

### 仍然成立的边界
- run-start 只证明「开始过」，不证明包变更成功，也不证明进程正常退出。
- 没有 run-start 的历史事务不会得到新结论，仍按旧映射处理，不回填。
- `prior_boot_execution=true` 时观察仅供参考，不构成收口依据。
- 未实现管理员显式对账入口；`job_completed_no_unit_evidence` 等新状态目前只读。


## 278. 精简第 277 节：移除无消费方的状态与字段

第 277 节首版引入了三个新状态和两个记录字段，事后核查发现它们**没有任何生产消费方**（`rg` 全仓只有定义与自证测试）：

- `job_in_progress_unverified`
- `job_started_without_mutation`
- `prior_boot_execution`
- 记录字段 `pid`、`started_at_ms`

这些是设计的想象产物，不是需求。已全部移除。

### 现在的最小实现
- run-start 记录只保留 `schema_version` / `state` / `job_intent_sha256` / `boot_id` 四个字段。
- 观察侧只增加一条规则：`run_start` 存在且 `state` 仍为 `dispatch_unknown` 时，改为 `job_completed_no_unit_evidence`。
- 其余映射与第 273 节实测的既有行为完全一致，不做任何扩展。

### 净变化（相对第 276 节提交）
- 产品代码 `native_update_job.py`：**−26 行**（8 增 34 删）
- 测试：+20 / −53

即最终实现比引入前更短。

### 复验
- 完整回归 **1452 passed, 16 skipped**（`20261028-165736`），ruff 通过。
- 真实 systemd：worker 在 apply 前被杀 → `run_start_written=true`、`apply_intent_written=false`、`state=job_completed_no_unit_evidence`、`retry_allowed=false`、`fence_retained=true`；重复启动仍被拒。日志 `20261028-165843`。容器已删除并复核不存在。

### 保留的复杂度及其理由
- `_boot_id` 的格式校验：boot_id 参与「记录是否来自本次启动」的判断，格式错误必须拒绝而非猜测。
- duplicate-start 拒绝：`_save_new` 的 no-replace 语义天然提供，几乎零成本，且防止同一次派发被启动两次。
- intent 摘要复核：防止 run-start 与 task intent 错配，是这条证据成立的前提，不能省。


## 279. 修复：`null` 证据被当作「不存在」

### 问题
`_read_job_evidence` 对不存在的文件返回 `None`，但文件内容为 JSON `null` 时 `json.loads` 同样返回 `None`。两者因此无法区分。

实测（root 容器，`20261028-170329`）：

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 无 apply-intent 文件 | `apply_intent_present=false` | 不变 |
| apply-intent 内容为 `null` | **被接受**，`apply_intent_present=false` | `JobError: job evidence is null` |
| sender 为 `null` 且无 apply-intent | 被孤儿检查拒绝 | 不变 |

即：一个损坏或被篡改的 apply-intent 会被静默降级成「还没写」，而「没有 apply-intent」与「有但坏了」在语义上差别很大——前者是尚未开始，后者是证据被破坏。

### 修复
`_read_job_evidence` 在解析后显式拒绝 `None`，区分「文件不存在」与「内容是 null」。改动 4 行。

### 验证
- 新增 `test_null_evidence_is_not_treated_as_absent`、`test_missing_evidence_still_reads_as_none`（后者锁住缺失路径仍返回 None，防止修过头）。
- 容器复验：null 证据被拒，真缺失行为不变（`20261028-170520`）。
- 完整回归 **1454 passed, 16 skipped**，ruff 通过。

### 影响面
只影响已有的 job 证据读取路径，不新增状态、字段或门禁。所有生产永拒保持原样。


## 280. 修复：畸形 prepared 证据抛出裸 KeyError/TypeError

### 复现
`observe_job` 在 apply-intent 存在时直接索引 `prepared["metadata"]["Package"]`，但 prepared 只经过「是普通文件 + 摘要匹配」校验，没有结构校验。用合法 JSON、错误结构构造五种输入，全部抛出非 `JobError` 异常（`20261028-171625`）：

| prepared 内容 | 原异常 |
| --- | --- |
| `{"foo":1}` | `KeyError: 'metadata'` |
| `{"metadata":"string"}` | `TypeError: string indices must be integers` |
| `{"metadata":null}` | `TypeError: 'NoneType' object is not subscriptable` |
| `{"metadata":{"Package":"f"}}` | `KeyError: 'Version'` |
| `[1,2,3]` | `TypeError: list indices must be integers` |

影响：`recover_job` 只捕获 `JobError`，这些异常会以原始堆栈穿透到调用方，而不是被当作证据问题拒绝。

### 修复
新增 `_prepared_field(prepared, key)`：校验 prepared 是对象、`metadata` 是对象、键存在，否则抛 `JobError`。两处索引点改用它。

### 修正过程中的一次自伤
首版还加了 `isinstance(value, str)` 校验，导致 4 个既有测试失败——它们的 prepared 里 `Version` 是整数 `2`，而原代码接受任意 JSON 值。**这属于改变既有语义**，已改回：`_prepared_field` 只收敛异常类型，不改值域，并补 `test_prepared_field_values_pass_through_unchanged` 锁住这一点。

### 验证
- 容器复验：五种输入全部变为 `JobError`（`20261028-172000`）。
- 完整回归 **1460 passed, 16 skipped**，ruff 通过。

### 边界
这只保证异常类型一致，不改变任何判定结果：prepared 结构正常时行为与之前完全相同，畸形时仍被拒绝，且拒绝发生在任何包动作之前。


## 281. 修复：其它 JSON 载体非对象时抛裸 AttributeError

### 复现
第 280 节只修了 prepared 的 `["metadata"][...]`。但 `observe_job` 还对 intent、outcome、sender 调用 `.get()`，这些载体同样只经「普通文件 + JSON 解析」校验。构造合法 JSON 的三类畸形（`20261028-172751`、`20261028-172825`）：

| 载体 | 内容 | 原异常 |
| --- | --- | --- |
| job-intent | `[1,2]` | `AttributeError: 'list' object has no attribute 'get'` |
| outcome | `[1,2]` | 同上 |
| outcome | `"text"` | 同上 |
| sender | `[1,2]` | 同上 |

`outcome-is-list` 在没有合法 sender 时先撞上「lacks sender evidence」，说明原顺序掩盖了类型问题；补上合法 sender 后才暴露出 `outcome.get` 的 AttributeError。

### 修复
在 `observe_job` 中对 intent、apply-intent、outcome、sender 四处加 `isinstance(..., dict)` 检查，非对象抛 `JobError`。仅校验容器类型，不校验内部字段值域。

### 已核查的其它读取点（不需要改）
- `close_before_apply`：已有 `isinstance(intent, dict)`。
- `_prepare_dispatch_locked`：用 `intent != expected` 整体比较，不依赖 `.get`。
- `marker` / `job-recovery.json`：只用 `_same_json`，接受任意 JSON 值。
- `describe_job`：同样有裸索引，但**无生产调用方**（`rg` 只命中自身与测试），暂不改，避免无消费方的扩散。

### 验证
- 容器复验：五例全部 `JobError`（`20261028-172842`）。
- 新增参数化测试覆盖 intent/outcome/sender 三载体。
- 完整回归 **1463 passed, 16 skipped**，ruff 通过（`20261028-173011`）。

### 边界
未做全局 schema 校验，也未验证字段级类型（如 `unit` 为 int 时仍会走到「identity differs」而非类型错误）。本轮只保证「非对象」不再逃出 `JobError` 契约。


## 282. 修复：run-start 把「查不到」误报为「已完成」

### 复现
`observe_job` 里只要有 run-start 证据，就把 `dispatch_unknown` 一律改写为 `job_completed_no_unit_evidence`。但 `dispatch_unknown` 混着两类完全不同的情况：**unit 确实消失了**，和**根本没查成功**。

容器实测五种输入，全部坍缩成同一状态（`20261028-174339`）：

| 情形 | 有 run-start | 无 run-start |
| --- | --- | --- |
| systemctl 不存在（`OSError`） | `job_completed_no_unit_evidence` | `dispatch_unknown` |
| systemctl 超时 | 同上 | `dispatch_unknown` |
| 输出无法解析 | 同上 | `dispatch_unknown` |
| unit 仍 loaded 但 inactive | 同上 | `dispatch_unknown` |
| unit 确实 not-found | 同上 | `dispatch_unknown` |

只有最后一行是真正的可归因完成。前四行是「查询失败」，却被报告成「已完成」——**把观测不到说成了观测结果**。

### 修复
改写条件增加 unit 证据必须是 `LoadState == "not-found"`，并在改写时写入 `completion_basis: "unit_not_found_after_run_start"`，让「凭什么说完成」可核对，而不是只看状态名猜。

### 验证
- 容器复验：仅 `unit_not_found` 改写，其余四项维持 `dispatch_unknown`（`20261028-175258`）。
- 新增参数化测试覆盖四种对照。
- 完整回归 **1467 passed, 16 skipped**，ruff 通过（`20261028-175336`）。

### 边界
- 这条改写仍然**不授予 retry**，fence 保留；它只影响状态描述，不改变任何重放许可。
- `not-found` 只说明「现在查不到该 unit」，不证明包事务成功、不证明 unit 曾属于这次 dispatch、也不证明 cgroup 后代已收口。`completion_basis` 的命名刻意限定在「unit 不在了」这一事实，而非「事务完成」。
- 未区分「从未创建」与「创建后卸载」——两者都表现为 not-found，本轮不做进一步收敛。

### 修正记录
首次测试写法把 `_unit(NOT_FOUND)`（本身返回 runner）又包了一层 lambda，导致 runner 收到函数对象而非 stdout，报 `AttributeError`。已改为直接传 `_unit(NOT_FOUND)`。


## 283. 修复：FIFO 证据导致 worker 启动无限阻塞

### 复现
`_record_run_start` 在 worker 启动前检查 `helper._trusted(intent)`，但 `_trusted` 只拒绝符号链接、非 root 属主和组可写，**不拒绝 FIFO**；随后 `_digest` 直接 `read_bytes()`。root 属主的 FIFO 若无人写入，读取便永久阻塞。

容器实测（`20261028-175621`）：

| 输入 | 结果 |
| --- | --- |
| 普通文件、摘要不符 | 立即 `JobError`（子进程 exit 21） |
| root 属主 FIFO | **阻塞满 5 秒**，被 SIGKILL（exit null） |

这段代码在 `main()` 里位于 `from .native_update import Updates` 之前，所以阻塞发生在任何包动作之前，但也不会留下 run-start 证据——整个 job 就这么静默挂住。

### 修复
`helper._digest` 增加 `S_ISREG` 检查，非普通文件抛 `HelperError`。这个函数被 91 处调用，全部目标都是证据或制品文件，在入口统一拦一次比逐点加检查更可靠。`_record_run_start` 随后把 `HelperError` 转成 `JobError`，与模块的异常契约一致。

### 验证
- 容器复验：FIFO 从阻塞改为立即 `JobError`（`20261028-175922`）；`_digest` 对普通文件正常、对 FIFO 拒绝。
- 完整回归 **1468 passed, 17 skipped**，ruff 通过（`20261028-175926`）。

### 边界
- 只覆盖 `_digest` 这条读路径。`native_update.py` 里仍有多处直接 `read_text()` / `read_bytes()`，其中 job 证据文件先经 `_trusted` + `_read_job_evidence` 的普通文件检查，但其它路径（如 `.recovery.json`、`active.json`）未逐一验证是否都有前置普通文件检查，本轮未全部审计。
- 属主与权限检查不变；这里只堵「非普通文件」，不改变可信管理员模型。
- 容器测试需 root，本地非 root 环境该用例跳过，行为由容器验证兜底。

### 测试自伤记录
新增的 fork 测试最初在非 root 下跑，`_trusted` 先以「administrator-owned non-writable path」拒绝，暴露的是权限门禁而非 FIFO 缺陷。已加 `os.geteuid() == 0` 跳过条件，并在容器中以 root 复验真实路径。


## 284. 修复：prepare_signed / trusted_keys 读取普通用户可控文件时阻塞

### 审计结果
上一轮只堵了 `_digest`。本轮逐个检查 `native_update.py` 的 19 处直接 `read_text()`/`read_bytes()`：

| 位置 | 前置校验 | 结论 |
| --- | --- | --- |
| `_verify_job_intent` | `_trusted` + 后续 `_digest` | `_digest` 已要求普通文件 |
| `_observation.read` | `_trusted` + `S_ISREG` | 已安全 |
| `_read_runtime_receipt` | `O_NOFOLLOW` + `fstat` + `S_ISREG` + 属主 | 已安全，且有读取上限 |
| `installation.json`、`approved`、`recovery` 等 | 均在 root 属主 registry 下或经 `_digest` | 已安全 |
| **`prepare_signed` 的 `active.json`** | **无** | **缺陷** |
| **CLI `trusted_keys`** | 仅 `_trusted`（不拒绝 FIFO） | **缺陷** |

关键是权限不对称：`active.json` 位于**实例属主（普通用户）**的 root 目录下，而 `prepare_signed` 以 root 运行。普通用户可把它换成 FIFO，root helper 随即阻塞。

### 复现
用既有 `signed_update` fixture 把 `active.json` 换成 FIFO，在子进程中调用 `prepare_signed`（`20261028-180519`）：**阻塞满 5 秒被 SIGKILL**，而非返回错误。修复后同一用例立即 `UpdateError`。

### 修复
新增 `_read_regular(path, error)`：先 `lstat` 验 `S_ISREG`，再读。`prepare_signed` 的 `active.json` 与 CLI 的 `trusted_keys` 改用它。

### 验证
- 单测：FIFO 立即拒绝（`1 passed, 0.07s`），另有 `_read_regular` 正反例。
- 容器真 root 验证（`20261028-180910`）：uid 0 读取 uid 1000 目录下的 FIFO 被拒，普通文件正常。这一条验证了权限不对称本身——root 确实能 stat 并到达属主的 FIFO，只有类型检查在拦。
- 完整回归 **1471 passed, 17 skipped**，ruff 通过（`20261028-180940`）。

### 边界与自伤记录
- 单测用 `monkeypatch` 把 `geteuid` 设为 0、`_trusted` 置空，实际以 uid 501 运行。它证明阻塞行为，**不证明真 root 下的权限路径**；后者由容器另行验证。二者不可互相替代。
- 容器镜像缺 `packaging` 与 `pydantic`，`native_update` 无法导入。我一度想用自写同名函数「复现」，那等于测自己写的代码，已放弃；最终改为验证真实 `_digest` 与权限不对称事实，并如实标注 `_read_regular` 只由单测覆盖。
- 仍有一处未覆盖：`native_prepare.py` 的 `active.json` 读取要求非 root 且目录属主是自己，风险低但同样没有 `S_ISREG` 前置检查，本轮未改。


## 285. 收口：native_prepare 的 active.json / instance.json 读取

### 复现
第 284 节标注的遗留项。`_binding` 里 `active.json` 是裸 `read_bytes()`，`instance.json` 同样。用既有 fixture 把 `active.json` 换成 FIFO，子进程调用 `_binding`：**阻塞满 5 秒被 SIGKILL**（`20261028-185806`）。

与 284 的差别：这里运行身份是实例属主本人（`_binding` 明确拒绝 root），所以**不构成跨权限边界**。但仍是无限阻塞——FIFO 无写入方时 `read_bytes()` 永不返回，准备流程静默挂住。

### 修复
`native_prepare.py` 增加 `_read_regular(path, error)`，与 `native_update.py` 同名同形：先 `lstat` 验 `S_ISREG`，再读。`instance.json` 与 `active.json` 两处改用它。

### 同一函数内已核查无需改的位置
| 读取 | 前置 | 结论 |
| --- | --- | --- |
| `marker_path` | `_trusted_marker`（含 `S_ISREG` + root 属主） | 已安全 |
| `root / "run"` 等 digest | `helper._digest`（283 已要求普通文件） | 已安全 |
| `instance.json`、`active.json` | 无 | **本次修复** |

### 验证
- FIFO 从阻塞改为立即 `PreparationError`。
- 新增 `_read_regular` 正反例、`instance.json` FIFO 用例。
- 完整回归 **1474 passed, 17 skipped**，ruff 通过（`20261028-185857`）。

### 边界
- `_binding` 的其余读取（`retire_preparation`、候选回执等）在 `native_prepare.py` 中还有若干裸读，多位于 `native-candidates` 目录、由本进程此前写入，本轮未逐一加检查。
- 新增两个 fork 测试带来 2 条 `DeprecationWarning`（多线程进程 fork）。测试在子进程内只做纯读取，未继承锁，但这是已知的测试侧风险，不是产品缺陷。
- 修复只改变「非普通文件」的失败方式，不改变任何合法路径的判断结果。


## 286. 修复：登记身份校验读取普通用户可控证据

用户指定开始 P1 的「干净安装与身份绑定」。审计该项时先查现有实现：`_register` 已完成账户解析、属主匹配、标记发布与工件不覆盖，本轮不在其上加新协议，只修其中已复现的缺陷。

### 复现
`_register` 以 root 运行，却从实例属主（普通用户）目录读取三个文件：

| 读取 | 前置检查 | 是否拦 FIFO |
| --- | --- | --- |
| `instance.json` | 无 | 否 |
| `active.json` | 无 | 否 |
| `run`（runner） | `is_symlink` + `os.access(X_OK)` | **否** |

前两个是裸 `read_text()`。第三个尤其隐蔽：**FIFO 可以被 chmod +x，`X_OK` 会通过**，于是「可执行文件」的判断放行了一个永远不会返回的读取。

容器实测（`20261028-191942`）：root 对 uid 1000 的 FIFO `access_x_ok=true` 而 `is_regular=false`。

单测复现（`20261028-191656`）：`instance.json` 与 `active.json` 各换 FIFO，`_register` 均**阻塞满 5 秒被 SIGKILL**。

### 修复
- 新增 `helper._read_regular(path, error)`：先 `lstat` 验 `S_ISREG`，再读。`instance.json`、`active.json` 改用它。
- runner 增加 `stat.S_ISREG(runner.lstat().st_mode)` 检查，与既有的 `is_symlink`、`X_OK` 并列。

三个函数（`native_helper`、`native_update`、`native_prepare`）现在各有一份同形 `_read_regular`。**没有抽成公共函数**：分属不同模块、错误类型不同（`HelperError` / `UpdateError` / `PreparationError`），强行共享会把错误语义搅在一起，收益不抵成本。

### 验证
- 单测：三个载体换 FIFO 后均立即 `HelperError`。
- 容器真 root：FIFO 被拒，同时证实 `X_OK` 不排除 FIFO。
- 完整回归 **1477 passed, 17 skipped**，ruff 通过（`20261028-191959`）。

### 边界
- 本轮只修「读取阻塞与类型混淆」，**不表示 P1 的身份绑定已验收通过**。需求文档该条还要求运行用户、权限、实例与服务绑定的端到端准确，以及真实包安装后的实际身份；这些未在本轮验证。
- `_register` 其余读取（`marker_path` 经 `_trusted_marker`、`unit_path` 经 `_trusted`）已核查具备普通文件校验。
- 容器镜像缺 `pydantic`/`packaging`，验证脚本只导入 `native_helper`，不覆盖 `Updates` 相关路径。


## 287. 身份绑定端到端验收（真实包 + 真实登记）

### 制品
从提交 `76c4060` 构建，未签名开发包：

| 项 | 值 |
| --- | --- |
| Device wheel | `1b21bad1ce29771d50b06e6a1c6bb7225d79973df121f84f3873c7b892378a67` |
| Python 模块 | 34 个，与源码逐字节一致（本轮核对，0 处不符） |
| DEB | `build/native-packages-p1-76c4060/ai-device-native-client_0.2.2-1_amd64.deb` |
| DEB SHA256 | `7a427bebe4aa15357c5d4ab8fbf8abac9889a6488121adaf1aa525bca58f6208` |
| DEB 大小 | 105277098 字节 |

wheel 由 `uv build` 生成；payload 沿用 `ef7fa12` 的锁定依赖与运行时归档，仅替换 device wheel 并同步 `bundle/manifest.json` 与 `build-inputs.json` 中的 device 摘要。

### 验收脚本
`scripts/accept-native-identity-binding-76c4060.sh`，一次性无网络 amd64 容器，PID1 独立。

流程：`dpkg -i` 装包 → 普通用户 uid 12345 用包内解释器建实例 → root 用包内 bootstrap 登记 → 核对工件身份 → 重复登记必须被拒。

### 结果（`20261028-194332`）

| 检查 | 结果 |
| --- | --- |
| 实例属主 == 指定账户 | ✅ uid 12345 |
| 登记记录 uid == 账户 | ✅ 且 > 0 |
| marker 绑定 uid == 账户 | ✅ schema_version 2 |
| 登记记录 root 路径 | ✅ `/home/nativedev/instance` |
| 凭证环境文件 0600 | ✅ |
| registry 属主 root、非 world-writable | ✅ |
| unit 文件存在、属主 root、普通文件 | ✅ |
| 重复登记 | ✅ 拒绝（`native-managed instance requires the native lifecycle manager`）|

安装侧另证：`install_payload` 拒绝 root 执行、要求包内解释器路径即 shebang、拒绝已存在实例。

### 一个我自己的错误
首版断言用 `mode & 0o022` 判「不可写」，把 0775 的 **group 可写**误判成世界可写，报出 `instance_not_world_writable: false`。实例目录 0775 且 group 就是账户自身组，这是合理的。已改为分别报告 world/group 位，并保留 `instance_mode` 原值。

### 结论与边界
需求 P1 的「运行用户、权限、实例和服务绑定准确」在**包安装 + 登记**链路上成立。

但这一条**仍未整体判定通过**：
- 只验证了 `.deb`；`.rpm` 未验。
- 服务未启动（`service_started: false`、`enabled: false`），unit 只核对文件身份，**未验证 systemd 实际以该用户身份运行**。
- 重复登记实际被生命周期管理器门禁拦下，而非登记逻辑自身判重——两者都拒绝，但原因不同，不能混为一谈。


## 288. systemd 运行时身份验收

### 目的
第 287 节只核对了 unit 文件的身份声明，服务未启动。本轮在隔离 systemd PID1 容器中真实启动 unit，核对进程的实际运行身份。

脚本 `scripts/accept-native-unit-runtime-identity-76c4060.sh`，用同一 DEB（`7a427beb…`）。

### 过程中发现：服务首次启动失败
只写 `PRIVATE` 与 `DEVICE_SERVER_URL` 时，unit 进入 `activating/auto-restart`，`ExecMainStatus=2`。手工以该用户执行 `./run client` 得到：

```
device-runtime: ValueError: configuration or runtime unavailable; see installation and state-directory requirements
```

根因：`cli.py` 的 client 分支要求 `--token-env` 指定的环境变量（默认 `DEVICE_RUNTIME_TOKEN`）存在且长度 ≥ 16，否则 `ValueError`。而 `installation.py` 构造 client argv 时不传 `--token-env`，unit 只挂 `EnvironmentFile`。**EnvironmentFile 里没有该 token 就启动失败。**

这不是身份绑定缺陷，而是「未配对时服务无法启动」的预期边界——P2 才做在线配对。但要注意两点：
1. `cli.py` 把该 `ValueError` 吞成泛化文案「configuration or runtime unavailable」，**管理员拿不到「缺 token」这个具体原因**。
2. 自动重启会让失败表现为无限 `auto-restart` 循环，日志还拿不到（无 journal），排查成本高。

在 EnvironmentFile 内补 `DEVICE_RUNTIME_TOKEN`（32 位）后，服务 `active/running`。**本轮未改产品代码**：这是配对流程缺失，不是本阶段应修的门禁。

### 验收结果（`20261028-200015`，干净容器)

| 检查 | 结果 |
| --- | --- |
| `ActiveState` / `SubState` | ✅ `active` / `running` |
| `MainPID` | ✅ 188 |
| unit 声明 `User` / `Group` | ✅ 数值 12345 / 12345 |
| `/proc/<pid>` 实际 uid / gid | ✅ 12345 / 12345 |
| 进程非 root | ✅ |
| `WorkingDirectory` 与实际 cwd | ✅ 均为 `/home/nativedev/instance` |
| `NoNewPrivileges` | ✅ 1 |
| `ControlGroup` | ✅ `/system.slice/ai-device-client-dev1.service` |

补充组 `100 12345`，`Uid`/`Gid` 四个字段（real/effective/saved/fs）全为 12345。

### 结论与边界
「systemd 以注册用户身份运行 unit」在 `.deb` + 真实登记 + 真实启动链路上成立，且是**进程实测**，不是读 unit 文件推断。

仍未覆盖：
- `.rpm` 的同一链路。
- 配对完成后带真实凭证的长期运行行为。
- 重启后自启（`enabled: false`，未做 `enable` 验收）。
- 泛化错误文案问题已记录但未修。


## 289. unit 启用与重启自启验收

### 目的
第 288 节服务虽运行，但 `enabled=false`，自启未验。这是需求 P1「干净机器可靠安装和本机启动」的直接验收项。

脚本 `scripts/accept-native-unit-enable-reboot-76c4060.sh`，两阶段：阶段 1 安装/登记/enable/启动；阶段 2 在**容器真实 stop+start 之后**核对自启。

### 前提事实
产品代码的 `register` 只写 unit 文件，`_register` 返回恒为 `enabled: false`、`started: false`，不执行 `systemctl enable`。所以 enable 是管理员显式步骤，脚本里单独执行，未改产品行为。

### 阶段 1（`20261028-200945`）
`systemctl is-enabled` 初次为 `disabled` → `systemctl enable` 创建 multi-user.target.wants 符号链接 → 变 `enabled`；启动后 `active/running`，MainPID 203，User/Group 12345。

### 阶段 2（`20261028-201053`）
容器 stop 后 start（新 PID1，`is-system-running=degraded`），**未执行任何 start 命令**，unit 自行恢复：

| 检查 | 结果 |
| --- | --- |
| UnitFileState | `enabled` |
| ActiveState / SubState | `active` / `running` |
| MainPID | 38（新启动，与阶段 1 的 203 不同）|
| /proc uid | 12345，非 root |
| cwd | `/home/nativedev/instance` |

MainPID 变化证明是重启后的新进程，不是旧进程残留。

### 结论与边界
「安装 → 登记 → enable → 重启自启 → 以注册用户运行」在 `.deb` 链路上成立。

未覆盖：`.rpm` 同一链路；`uninstall` 是否清除 enable 符号链接；真实设备重启（仅容器 PID1 重启）。


## 290. RPM 链路：构建、安装、登记、启用与重启自启

第 289 节只覆盖 `.deb`。本节补齐 `.rpm`，使两条包链路有同等证据。

### 构建
源码 `76c4060` 的 payload 与 `.deb` 完全同源。首次在 Rocky 9 容器用 `build-native-linux.sh rpm` 构建，产出：

| 项 | 值 |
| --- | --- |
| 文件 | `build/native-packages-p1-76c4060/ai-device-native-client-0.2.2-1.x86_64.rpm` |
| 大小 | 106423810 |
| SHA256 | `2c2b2b790f0f9f40b59a4d40b5513a66ff8b40c747ebb310042fe8b91923944d` |

`rpm2cpio` 解出的 device wheel 与 `.deb` 内一致（`1b21bad1...`），确认同源。`rpm -qp --requires` 仅五项 rpmlib 能力（`AutoReqProv: no`），`--scripts` 显示 `%pretrans`/`%preun` Lua 守卫与 DEB 的 preinst/prerm 同意图。

**边界**：构建镜像 `rockylinux:9` 无 `rpmbuild`，需 `dnf install rpm-build`（联网，一次性容器，与历史 v24 同法）。宿主与既有镜像均无离线 rpmbuild，因此**这不是离线构建**。新增 `scripts/containers/rocky-systemd-probe/Containerfile` 供后续复用。

### 阶段 1（`20261028-202432`）
`rpm -ivh --nodeps` → 普通用户 UID12345 建实例 → root 登记（`enabled:false`）→ `enable` 建 wants 符号链接 → `active/running`，MainPID 127。

首次运行失败于 `adduser`：脚本沿用 Debian 语法，Rocky 只有 `useradd`。属验收脚本移植错误，非产品缺陷，已改为 `useradd --uid 12345 --user-group --create-home`。

### 阶段 2（`20261028-202534`）
容器 stop+start（新 PID1），**未发 start 命令**：

| 检查 | 结果 |
| --- | --- |
| UnitFileState / ActiveState / SubState | `enabled` / `active` / `running` |
| MainPID | 23（≠ 阶段 1 的 127）|
| /proc uid | 12345，非 root |
| cwd | `/home/nativedev/instance` |

### 进程子树补充（回应第 288 节的缺口）
第 288 节只验证了 MainPID 包装器。本次在 RPM 环境核对了整棵子树：

- PID 23 = `run` 包装器，uid real/eff/saved/fs = 12345
- PID 31 = 真正 client `python -m device_runtime.cli client --state-dir .../state/client --server-url wss://center.invalid --device-id dev1`，四个 uid 同为 12345

即实际加载代码的 client 进程本身也是非 root 运行，而不只是外层包装器。

`exe` 读为 `/run/rosetta/rosetta`，是 amd64 容器在 Rosetta 下运行的结果，非产品异常。

### 关于 `release` 值不同
RPM 安装得到 `release: 0.2.2-9323288...`，DEB 是 `0.2.2-3c9bf8b...`。查 `installation.py:327` 为 `manifest["release_version"] + "-" + uuid.uuid4().hex`，**每次安装随机生成**，两次 DEB 安装之间同样不同。不是 RPM/DEB 差异，也不影响身份绑定（绑定依据是 `bundle_sha256`）。

### 结论与边界
`.rpm` 与 `.deb` 在隔离容器中达成同等结论：安装 → 登记 → enable → 重启自启 → 以注册用户运行，且 client 子树身份正确。

未覆盖：RPM `--nodeps`（不作依赖验收）；实机与真实宿主重启；真实凭证联网运行；RPM 离线构建。

`uninstall` 与 enable 符号链接的关系在第 291 节跟进，并发现两个真实卸载缺陷。


## 291. 卸载缺陷：运行中实例被删 payload、卸载不彻底

第 290 节完成后转向卸载验收，发现两个真实缺陷，均已复现、修复、回归。

### 缺陷一：运行中的已登记实例被 `rpm -e` 直接删除 payload

复现（修复前，一次性 Rocky 容器）：安装 → 普通用户建实例 → root 登记 → `systemctl start`（`active`）→ 直接 `rpm -e`。结果 `rc=0`，`/opt/ai-device/packages/0.2.2-native1` 被删除，而服务仍显示 `active`。随后 `systemctl restart` 失败：

```
Failed to execute /home/nativedev/instance/run: No such file or directory
Main process exited, code=exited, status=203/EXEC
```

原因：`run` 脚本的 shebang 指向包内解释器，payload 被删后实例彻底不可启动，而 `active.json` 仍指向已消失的 release。

根因：`%preun` 守卫检查的是旧布局路径 `/opt/ai-device/instances`、`/var/lib/ai-device/manager`，而当前实际登记在 `/var/lib/ai-device/native`（`register` 的 registry 是参数，无内建默认值），实例根在普通用户家目录。**守卫路径与实际布局完全不匹配，等于没有守卫。** DEB 的 `prerm` 同一组路径，缺陷相同。

修复：两包守卫都补上 `/var/lib/ai-device/native`，并增加"存在 `ai-device-client-*.service` 登记 unit 即拒绝"这一不依赖路径约定的判据。

### 缺陷二：卸载不彻底，且导致后续无法安装

修复守卫后又发现：卸载成功后 `packages/0.2.2-native1` 残留 **43 个 `.pyc`**（`runtime/lib/python3.13/{json,re,pathlib,collections,encodings}/__pycache__`），bootstrap 也残留。这些是包内解释器运行时写出的字节码缓存，不在包清单内。

后果有两层：
1. 目录非空，`%pretrans` 的 `posix.stat('/opt/ai-device/packages')` 命中，判为"existing native installation"，**之后任何安装都被拒绝**（实测 `install skipped`）。
2. rpm 卸载留下空父目录同理——已由第 290 节的 `%dir` 修复解决。

修复分三处：
- 构建完成后 `find ... -type d -name __pycache__ -prune -exec rm -rf {} +`，交付干净 payload（DEB 同样受益）。
- DEB `prerm` 用 `find` 清理运行期缓存。
- RPM `%preun` 用 Lua `drop_caches` 递归清理（无 `find` 可用）。

修复后 rpm 清单不再含 pyc（`rpm -qlp | grep -c pycache` 为 0），卸载无 warning。

### 验收

`scripts/accept-native-uninstall-guard-76c4060.sh`，**每步真实断言**，失败即非零退出：

| 场景 | 期望 | DEB | RPM |
| --- | --- | --- | --- |
| 干净主机卸载 | 允许 | 通过 | 通过 |
| 卸载后布局目录 | 不留空目录 | 通过 | 通过 |
| 干净卸载后重装 | 允许 | 通过 | 通过 |
| 已登记且运行中卸载 | 拒绝 | 通过 | 通过 |
| 拒绝后 payload/实例/运行态 | 完好 | 通过 | 通过 |
| 移除登记后卸载 | 允许 | 通过 | 通过 |
| 卸载后实例数据 | 保留 | 通过 | 通过 |
| 卸载后包目录残留文件 | 无 | 通过 | 通过 |
| 卸载后再次安装 | 允许 | 通过 | 通过 |

日志：DEB `20261028-210031`、RPM `20261028-210050`（均为 r6 制品的干净容器）。

### 制品
`build/native-packages-p1-76c4060-r6/`，源码 `76c4060`：

| 包 | SHA256 |
| --- | --- |
| `.deb` | `d04bf54d9f5012fe9f318e70683fcc5932c2a6362fa08131173a81694db57e49` |
| `.rpm` | `1d3766db5b02954539e215425b2068656b2c8c3ae44a8eaff0d4faf1ec6c7b64` |

（第 290 节的 r1 制品保留未删，其 `2c2b2b79...`/`7a427beb...` 不含本节修复。）

回归：**1485 passed, 17 skipped, 5 warnings**，Ruff 通过。新增 4 个测试锁定守卫语义与缓存清理。

### 边界
- 守卫是"存在登记 unit 即拒绝"，仍是粗粒度；没有正式的 unregister 命令，验收脚本用删除 registry 记录模拟管理员移除，**不代表产品已提供该入口**。
- DEB 卸载仍会产生 `directory not empty so not removed` 之外的正常清理；不证明所有运行时产物都被枚举。
- 未验证：卸载与升级并发、helper 自身 unit 的卸载、实机与真实包管理器策略差异。
- 不调查恶意 root 场景；守卫目的是一致性与数据保留，不是安全边界。
