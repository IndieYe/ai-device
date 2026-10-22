# ai-device 独立仓库与三包迁移需求

- 文档状态：需求基线，待实施；不代表源码迁移、发布或运行验收已完成。
- 创建日期：2026-10-22。
- 目标仓库：`git@github.com:IndieYe/ai-device.git`。
- 本机目标目录：`~/data/project/ai-device`。
- 当前授权：创建需求文档。后续代码导入、依赖切换、发布和部署不由本文自动授权。

## 1. 背景与目标

Device 当前位于 AI 助手仓库中，但其客户端、中心、CLI 和 MCP 已具备相对独立的实现。客户端使用两个独立 Bash/FS 包的执行核心；开发和交付仍依赖准确的私有 wheel。需要将三份源码集中到独立仓库，使其他 coding agent 无需获取 AI 助手源码或个人运行环境即可使用。

### 1.1 已确认的决策

1. 使用一个 GitHub 仓库，维护 Bash、FS、Device Runtime 三个独立 Python 包。
2. Bash/FS 保留独立安装和复用能力，各自只有一份权威实现；Device 调用其 Python 核心接口，不复制执行实现。
3. CLI、MCP 是通用 Agent 接入入口；各平台插件是薄适配，不重复实现执行、查询与设备通信。
4. AI 助手保留自身专属插件、界面和宿主适配，作为新项目的消费者。
5. 首轮以保留行为、独立构建和依赖闭环为目标，不同时进行执行逻辑重构、协议改版或线上升级。
6. 新仓库名称为 `ai-device`；最终 Python distribution 名称尚未确定，不能从仓库名推导。

### 1.2 成功目标

- 三包源码、测试及必要资源在新仓库内完整可维护。
- 在不能访问 AI 助手源码、旧源码仓库、个人配置和私有 wheel 缓存的干净环境中，能够构建、安装和验证三包。
- Bash/FS 可独立使用；Device 的 client/server/ops/mcp 各角色按声明安装。
- AI 助手依赖版本化制品或明确固定来源，不要求相邻目录存在源码副本。
- 迁移后旧位置不再与新仓库并行维护同一份实现。

### 1.3 非目标

- 新增设备能力、修改现有执行协议、重新设计任务持久化或权限体系。
- 为所有 coding agent 同时发布市场插件。
- 自动迁移设备状态、凭证、运行目录或历史任务。
- 自动部署、停止或重启真实服务。
- 自动删除旧仓库、历史、Worktree 或运行环境。
- 在没有证据时扩大 Windows、Linux、macOS 的平台或角色支持声明。

## 2. 事实基线与证据边界

以下为迁移前只读核实结果。实施开始前须复查工作区和提交；路径与历史记录不是永久有效的运行态证明。

| 包 | 来源路径 | 已核实基线 |
| --- | --- | --- |
| Bash | `~/data/project/mcp-server-bash` | `c95a13d050e67bac2f75c8c51f83b3d0e52e2a5a` |
| FS | `~/data/project/mcp-server-fs` | `e0f470e500d970e787e0a96062e403c45f757057` |
| Device | `~/data/project/ai-assistant/packages/device-runtime` | AI 助手 HEAD `1cf2551f191312bf2a7d132bf8e678920e5e6705` 下该目录，与候选提交 `d33fb68a6a8d7d678ee1d27383437c67f7c1ceec` 无差异 |

核实时三个来源工作区均干净；Bash、FS 各自本地分支领先对应 origin 3 个提交，不能假定远程已有全部来源代码。

### 2.1 Bash/FS 制品对应

准确制品来源目录：`~/.device-runtime/staging/ses13742-4f6cd7926/macos-client-bundle/wheels`。此路径仅是历史证据定位，不得成为新项目构建依赖。

| 制品 | SHA256 | 核对结果 |
| --- | --- | --- |
| `mcp_server_bash-0.2.0-py3-none-any.whl` | `24231647d6da796b2c3bc16dac305331b9499828f83beccdc3f687f32d1f15c9` | 13 个包文件与对应源码逐字节一致；无遗漏的源码 Python 文件 |
| `mcp_server_fs-0.2.0-py3-none-any.whl` | `6feb24c2e2d78d7e1b779918aae0f813a25b01746ada1b784b45035c6a894f0f` | 33 个包文件与对应源码逐字节一致；无遗漏的源码 Python 文件 |

AI 助手 Device 包 `local-wheels/` 中两份制品与上述制品一致。包文件比对不等于重建得到相同 wheel，也不证明全部第三方许可已核查。

### 2.2 验证结论的限制

- Device 旧基线曾有 581 项测试通过，是历史证据，不能当作迁移后结果。
- 当前源码核实未重新运行三包测试、安装、构建或远端命令。
- 未发现 Bash/FS 跟踪文件中的 LICENSE/NOTICE/COPYING；AI 助手根和 Device 包根亦未找到许可证文件，仍需正式许可核查。
- 同级其他项目的根 `pyproject.toml`、`package.json` 未发现额外直接依赖，不等于全机无其他消费者。
- 当前真实服务的运行成功不替代新仓制品、安装器或平台支持验收。

## 3. 目标架构

```text
Agent / 用户工具
  ├─ Device CLI
  ├─ Device MCP
  └─ 平台薄适配
         │ HTTP
         ▼
     Device 中心
         ▲
         │ 客户端主动建立 WebSocket
         ▼
     Device 客户端
       ├─ Bash Python 核心
       └─ FS Python 核心
```

客户端主动连接中心，减少设备接受入站连接的要求。Agent 配置中心接入，不必逐设备配置网络连接；每台客户端仍需独立安装和配置身份。

### 3.1 三包职责与依赖

| 包 | 职责 | 边界 |
| --- | --- | --- |
| Bash | 命令实际执行、输出捕获、临时资源和进程清理；独立 MCP 入口 | 不依赖 Device 或 FS |
| FS | 文件读写编辑、patch、图片读取及辅助处理；独立 MCP 入口 | 不依赖 Device 或 Bash |
| Device Runtime | 连接、设备寻址、协议、操作日志、任务编排、并发、恢复、CLI/MCP、安装交付 | client 依赖 Bash/FS；其他角色不应无故强制安装执行依赖 |

Device 的任务调度、活动槽位及操作幂等继续由 Device 管理，Bash 核心负责实际执行。不得因迁移引入两个互相竞争的任务状态事实源。

### 3.2 推荐目录

```text
ai-device/
  packages/
    bash/
      pyproject.toml
      src/mcp_server_bash/
      tests/
    fs/
      pyproject.toml
      mcp_server_fs/
      main.py
      tests/
    device-runtime/
      pyproject.toml
      src/device_runtime/
      tests/
      scripts/
      examples/
  tests/integration/
  skills/device-operations/
  docs/
  scripts/
  doc/requirements/
  pyproject.toml
  uv.lock
  .github/workflows/
  README.md
  LICENSE
```

FS 首轮保留现有 flat layout，避免为统一目录风格额外调整打包。若后续改 src layout，须独立验证 wheel、入口及测试，不是本次必做项。只有真正跨包的测试进入根 integration 目录，不机械搬走所有包内测试。

## 4. 三包迁移清单

### 4.1 Bash

来源仓库共 40 个跟踪文件，其中测试目录有 17 个 Python 文件（含辅助文件，不等于测试用例数）。

**迁入：**

- `src/mcp_server_bash/` 完整源码，包括 core、models、backends、shell、output_capture、artifacts、tracking、config、server、cli、模块入口。
- `tests/` 全部测试和辅助文件，保留核心不安装 MCP 依赖时的验证。
- `pyproject.toml` 的包定义、构建规则、命令和依赖。
- `README.md`、`docs/usage-guide.md`。

**整理后迁入：**

- `.env.example`：确认仅含示例，不含真实配置。
- `.gitignore`、`.python-version`：与仓库级规则合并。
- `docs/bash-execution-modes-requirements-design.md`、`docs/core-test-evidence.md`、`docs/remote-host-design.md`：标记需求、历史设计和历史证据，不能全部冒充现行契约。
- `uv.lock`：用于依赖对照，切换 workspace 后不保留多个含义不清的活跃锁文件。

**当前契约：** distribution `mcp-server-bash`，import `mcp_server_bash`，命令 `mcp-server-bash`；Python `>=3.13`，基础依赖 psutil，MCP 为可选依赖。

### 4.2 FS

来源仓库共 52 个跟踪文件，其中测试目录有 9 个 Python 文件（含辅助文件）。

**迁入：**

- `mcp_server_fs/` 完整源码：core、patch、tools、图片/路径/I/O/结果辅助、配置、server、cli 和模块入口。
- `tests/` 全部测试及辅助文件。
- `main.py`：当前被显式打包，承担兼容导入/启动入口；首轮保留，不因拆仓擅自删除。
- `pyproject.toml`、`README.md`、`docs/usage-guide.md`。

**整理后迁入：**

- `.env.example`、`.gitignore`。
- `scripts/inspector.sh`：修正路径并验证。
- `docs/READ_IMAGE_END_TO_END_WORK_PLAN.md`、`docs/REMOTE_HOST_DESIGN.md`：标记历史性质。
- 原 `uv.lock`：作为依赖对照，按 workspace 规则合并。

**当前契约：** distribution `mcp-server-fs`，import `mcp_server_fs`，命令 `mcp-server-fs`；Python `>=3.10`，基础依赖 Pillow，MCP 为可选依赖。不得仅因 workspace 使用 Python 3.13 就无依据提高 FS 独立包最低版本；若现有依赖实际上限制可用版本，应验证后修正文档或明确调整支持范围。

### 4.3 Device Runtime

来源目录共 58 个跟踪文件：18 个源码、25 个测试 Python 文件及其余交付资产。

**迁入：**

- `src/device_runtime/` 全部源码：client、server、protocol、service、http_client、executor、tasks、journal、limits、CLI/MCP、版本、依赖、bundle、installation、lifecycle。
- `tests/` 全部测试和 fixtures。
- `scripts/` 五个构建/验收脚本。
- `examples/` 六个示例文件。
- 包 README、pyproject、忽略规则。

**从 AI 助手配套提取：**

- `doc/docs/device-runtime.md`。
- `doc/docs/device-delivery.md`。
- `doc/docs/device-linux-client.md`。
- `.ai-assistant/skills/device-operations/` 的通用内容；清除宿主专属能力发现依赖，保留授权、设备核实、原 ID 对账和完成判定规则。

**迁移适配：**

- client 对 Bash/FS 改为 workspace 开发来源；对外制品使用明确版本依赖。
- 移除 `local-wheels/` 作为开发必需来源的假设；历史准确制品保留在原位置，不复制为源码事实源。
- 检查 bundle、installation、测试中的 distribution 名、wheel 名和元数据识别。
- 修正示例中写死的 `0.2.0` wheel、文档相对路径和旧仓库路径。
- `device-runtime`、`device-mcp` 和 Python import 首轮原则上保留；新 distribution 名待确认。

## 5. 宿主和外部消费者

### 5.1 AI 助手保留项

- `plugins/device/`：设备目录、连接设置、凭证代理和宿主 App 适配。
- `backend/src/integrations/device_runtime_host.py`。
- `backend/src/cli_runtime.py`、`backend/src/web/app.py` 的可选挂载入口。
- `backend/tests/test_device_runtime_*.py` 中宿主、Agent、ServerPool、生命周期集成验证。

内嵌中心挂载是否长期保留不在本轮重设计；首轮保留现有可选行为。

### 5.2 必须联动

- `backend/pyproject.toml` 的 Device 本地路径来源及对应锁文件。
- `plugins/device/pyproject.toml` 的 `../../packages/device-runtime` 来源及对应锁文件。
- `web/tests/e2e/fullstack_backend.py` 中的 FS 模块引用和 `web/TESTING.md`。
- 宿主文档、技能入口、安装指引与实际消费者配置。

迁移前须补查真实使用方，但不得为发现消费者无界读取个人凭证或完整敏感配置。已知本机运行环境不是本次修改目标。

### 5.3 不迁入

真实 `.env`、Token、密钥、设备身份、个人公网连接 profile、状态库、日志、输出、venv、缓存、dist/build、运维候选目录、个人绑定包装脚本和无关宿主基础设施。

## 6. Workspace、依赖与发布要求

1. 使用 uv 管理 Python 依赖、workspace、测试和构建；不使用其他包管理器绕过既定流程。
2. 根 workspace 统一开发解析；每包 pyproject 仍完整描述自身构建、运行依赖和入口。
3. 不允许 Bash/FS 反向依赖 Device，不新增无实际需要的 shared 包。
4. 保留 base 与 MCP extras 边界；base import 和核心使用不应隐式启动 MCP 或加载宿主。
5. 三包独立版本；仅有实际变化或依赖更新的包发布新版本。
6. 发布顺序为必要的 Bash/FS 版本在先、依赖它们的 Device 在后；建议 tag 使用包名前缀。
7. 不修改已发布同名同版本内容，不将公开同名包当作本项目准确制品的替代。
8. 发包前确认注册表名称归属和发布权限；包名、命令名、import 名是不同契约。
9. workspace 源码组合通过后，还必须使用独立 wheel 组合验证，防止 workspace 掩盖依赖声明错误。
10. 运行安装器仍须支持准确制品、依赖和元数据校验，不把开发 workspace 当作部署必需环境。
11. 新仓应补 CI；首轮发布前确定许可证及第三方声明，并确认进入 sdist/wheel。

### 6.1 Python 支持矩阵与独立安装

- workspace 的开发解释器范围与各包对外支持范围分别声明；不得以 workspace 统一解析成功证明所有包的最低 Python 版本成立。
- Bash、Device 当前要求 Python >=3.13；FS 当前声明 >=3.10。FS 必须在 workspace 外，使用其声明的最低版本及选定的代表版本独立安装和验证。
- FS base 与 MCP extras 分别核验最低可用版本、依赖解析、入口及核心行为；Bash/Device 也须按基础包、extras 和角色声明验证，不以全 extras 环境替代。
- 验证结果记录 Python、平台、安装组合及依赖版本。依赖导致某组合不成立时，明确修正依赖或支持声明；提高最低 Python 版本须作为显式支持范围变更确认，不得隐藏在搬仓中。

### 6.2 测试收集与制品导入隔离

- 三包包内测试分别运行，显式固定 pytest 配置、工作目录和测试范围；跨包测试另设入口，避免 conftest、同名 tests 包和 asyncio 配置互相污染。
- 迁移前后记录收集用例清单、数量、跳过及排除项，解释差异；源码文件数量不能代替用例数量。
- 制品测试在源码目录之外的独立安装环境运行，不设置指向 checkout 的 PYTHONPATH，不使用 workspace editable 安装。
- 检查实际模块来源（例如模块 `__file__` 和 distribution metadata），确认三包来自目标环境的安装制品，而非新旧 checkout 或个人环境。
- Bash 现有 conftest 会向 sys.path 注入 src；制品测试须使用独立测试入口或经过核查的隔离 harness，不可直接复用会重新引入源码的配置。其他测试辅助也须检查路径注入。
- 包内源码测试与 wheel 集成测试分别报告；任一方通过不能代替另一方。

### 6.3 sdist 到 wheel 的独立重建

每包必须完成以下链路：

1. 从冻结的新仓源码构建 sdist，并检查清单。
2. 在隔离临时目录解压 sdist，仅以解压内容构建 wheel；不可读取原 Git checkout、兄弟包源码、旧仓或个人私有制品。
3. 在独立环境安装由这些 sdist 重建的 wheel，验证包入口、资源和三包组合。

允许从明确声明的来源获取构建及运行依赖；同批 Bash/FS 准确 wheel 可作为 Device 配套依赖输入，但须显式提供并记录，不得通过隐含缓存获得。重建须证明 README、构建配置、许可证及运行资源齐全，不要求不同构建时刻 wheel 二进制字节天然相同；比较内容和元数据差异并记录实际制品哈希。

### 6.4 完整交付组合身份

- 当前 Device build_id 仅指纹化 device_runtime 包内 Python 源码，不包含 Bash/FS；不得将其称为完整客户端或全部依赖的身份。
- 每个候选及正式发布组合须记录三包 distribution 名、版本、源码提交（含子目录）、sdist/wheel 文件与 SHA256、Python、平台以及实际依赖解析结果。
- Device 发布记录明确配套 Bash/FS 版本和验证结果；仅升级 Bash/FS 也必须形成新的组合记录，不能复用旧验证结论。
- 组合记录绑定固定制品，不使用会漂移的 latest 或浮动分支。不得含凭证、个人配置或设备敏感数据。
- 本轮不强制更改线上版本查询协议。优先以发布清单及验收报告补足追溯，Device 源码指纹继续按原语义展示。

### 6.5 安装交付生命周期

- 将保留的安装命令及适用平台/角色列成清单，在临时独立实例中验证准确 wheel/bundle 引导安装、稳定入口启动、诊断、prepare、activate、rebuild 等实际保留路径。
- 仅提供交付物和声明的依赖来源，不提供开发 checkout；验证改名后的 distribution、wheel、metadata、约束和依赖识别。
- 覆盖不匹配制品、角色、平台、元数据及依赖的拒绝路径，确认失败不意外推进 active 指针、不破坏既有临时实例，且可诊断、可接续。
- 代码存在不等于支持承诺；不拟保留的命令先明确变更决策，不能一边声明保留一边省略验收。
- 启停和切换仅涉及测试自行创建的隔离进程，不操作真实服务。此处不新增本次生产部署的备份、恢复或回退要求，也不修改生产状态。

### 6.6 部分发布失败与过渡期维护

- 按包独立记录“待构建、已构建、已验收、已发布、失败或结果未知”，发布状态附制品哈希与可核实的远程结果；只有三包所需组合均已验收并可获取，才标记该组合可供下游切换。
- Bash 已发布而 FS/Device 失败时，保留已发布事实，不宣称整组完成，不覆盖已发布同名同版本内容。修复后按实际变化发新版本或继续未完成步骤。
- 发布超时或结果未知，先核对注册表和制品，不盲目重复上传；公开下载的制品须核对身份并验证安装组合。
- AI 助手仅切换到完整验收过的组合；下游尚未切换时，不移除旧源码或提前宣布旧来源退出维护。
- 阶段 B 导入时明确每包过渡期唯一修改源。若必须修复旧使用方，记录改动、来源和同步情况，受控同步到另一副本并复核，不能两边各自演进。
- 旧来源可保留供未切换消费者使用，但不代表其仍与新仓并行拥有功能开发权；最终退出维护以消费者核查和阶段 E 门禁为准。

## 7. 行为保持与安全要求

- 不重新引入已删除的 Device 旧执行接口或别名；保留当前统一执行/查询契约。
- 普通请求、查询、活动任务三类限额保留现有默认 8 和正整数校验；不因迁移改为无限制或排队。
- pending、unknown、网络超时不视为失败无副作用；继续以原 operation ID 对账，不盲目重放。
- 保留任务取消、输出元数据、进程和资源清理、断线重连与请求去重行为。
- FS 保留原有编码、范围、截断、编辑、patch 和图片处理契约。
- 不引入宿主会话、Sandbox、AI 登录态或插件框架作为三包运行前提。
- 独立测试仅使用临时目录、测试身份和隔离端口；不访问生产状态或凭证。
- 开源前检查示例、文档、源码和拟提交文件中的敏感信息及第三方许可；不公开个人部署配置。

## 8. 分阶段实施与门禁

### 阶段 A：基线与决策收口

复查来源提交、工作区、制品对应和消费者；确定许可证、发布名称及支持范围，保存来源清单。

门禁：三包来源明确，无未归属改动；实现所需决策已确认。尚未决定的发布事项可以保留为发布阻塞，不得擅自代替用户决定。

### 阶段 B：新仓源码导入

完整导入三包及必要资产，建立 workspace、锁文件与文档索引，仅调整路径和依赖来源；记录来源 commit 和原路径。

推荐使用带来源说明的导入提交，不默认复制 AI 助手全部历史。是否提取原 Git 历史属于另行选择。提交必须有适用授权。

门禁：新仓独立解析、导入和包内测试成立；测试入口、配置、收集数量及差异已记录，过渡期各包唯一修改源已明确。旧仓暂不删除；过渡期两份文件不是长期双向维护机制。

### 阶段 C：独立制品与集成验收

按第 6.1—6.5 节执行 Python/base/extras 矩阵、sdist 隔离重建 wheel、制品来源检查、三包组合测试和安装交付生命周期；建立 CI、文档、组合清单与平台矩阵。

门禁：不依赖旧仓和个人私有 wheel；制品测试未导入 checkout；安装和生命周期关键成功、失败、恢复路径均有结果。FS 低版本支持与 workspace 验证分别报告，未验证平台或组合明确标注，不发布为已支持。

### 阶段 D：发布与宿主依赖切换

按顺序发布必要制品；AI 助手改为明确版本依赖，更新锁文件和接入测试。未正式发布时可在授权范围使用准确本地制品或固定 Git commit 验证，不用浮动分支作为长期依赖。

门禁：目标组合所需制品已发布且身份核实，公开获取后的安装组合验证通过；宿主不需要旧 Device 源码目录，插件、内嵌挂载和相关 E2E 验证通过。部分发布失败按第 6.6 节收口，不推进下游切换。源码独立完成、各包发布和下游切换完成分开标记。

### 阶段 E：旧来源退出维护

确认消费者切换后，受控移除旧 Device 源码；Bash/FS 原仓库更新迁移说明，停止维护旧实现，保留历史。不得自动删除仓库、分支或 Worktree。

门禁：新仓成为唯一权威实现，无残留活跃本地路径依赖，无双份维护。

### 阶段 F：运行环境升级（独立任务）

发布与拆仓不要求线上立即升级。现有 0.2.2 可继续运行；新制品部署、服务动作和设备状态处理必须另行明确范围和授权，不作为前述阶段的隐含动作。

## 9. 验收矩阵

| 编号 | 验收项 | 必须取得的证据 |
| --- | --- | --- |
| AC-01 | 源码可追溯 | 来源 commit、文件清单、必要哈希和有解释的迁移差异 |
| AC-02 | 单一实现 | Bash/FS 核心只有一份；Device 无复制实现 |
| AC-03 | 独立构建 | 无旧仓、私人目录、local-wheels 前提时构建三包成功 |
| AC-04 | 包完整性 | sdist/wheel 含运行资源、入口、许可证；无敏感或运行数据 |
| AC-05 | 基础依赖隔离 | Bash/FS 不安装 MCP extras 的核心测试通过 |
| AC-06 | 独立入口 | Bash/FS MCP、模块入口、FS main.py 现有契约验证 |
| AC-07 | Device 角色 | client/server/ops/mcp 按声明安装并工作 |
| AC-08 | 完整链路 | 临时中心、客户端、CLI、stdio MCP 的执行和结果查询 |
| AC-09 | 生命周期 | 后台、取消、输出、清理、幂等、重连、恢复回归 |
| AC-10 | 并发 | 三类限额、独立查询额度、拒绝与释放恢复 |
| AC-11 | 文件能力 | 读写编辑、patch、范围、编码、截断、图片回归 |
| AC-12 | 制品组合 | 非 editable 独立安装，三包实际导入路径和 metadata 指向目标制品，base/extras/角色组合成立 |
| AC-13 | 平台声明 | 区分核心可运行、安装器支持、真实部署验收 |
| AC-14 | 宿主切换 | AI 助手依赖、插件、可选挂载和相关 E2E 验证 |
| AC-15 | 文档与示例 | 安装、使用、开发、发布步骤及链接可用，无过期硬编码 |
| AC-16 | 维护收口 | 过渡期唯一修改源及必要同步可追溯；下游切换后旧实现退出维护，消费者清单已核对 |
| AC-17 | Python 支持矩阵 | workspace 与独立包分开验证，FS base/MCP extras 最低版本及代表版本结果明确 |
| AC-18 | 测试收集隔离 | 三包配置入口、迁移前后收集/跳过清单及差异说明，无路径注入导致制品测试误测源码 |
| AC-19 | sdist 重建链路 | 仅从 sdist 解压内容构建 wheel 并安装验证，无 checkout 或隐含私有缓存依赖 |
| AC-20 | 交付组合身份 | 三包版本、来源提交、制品 SHA256、Python/平台及解析依赖完整，区别于 Device build_id |
| AC-21 | 安装交付生命周期 | 保留命令的平台/角色矩阵，准确制品引导与诊断/prepare/activate/rebuild 等路径及拒绝失败验证 |
| AC-22 | 部分发布收口 | 分包状态、发布核对和未知结果处理可追溯，未完整验收组合不切换下游、不覆盖已发布版本 |

测试报告需标明提交、平台、Python、依赖组合、执行命令、结果和未执行项。不得把静态检查、历史验收、容器验证、正式设备验证混为同一证明。

## 10. 待决事项与风险

| 事项 | 当前状态 | 最迟处理点 |
| --- | --- | --- |
| 许可证及第三方声明 | 未确定/未完整审查 | 对外公开和发布前 |
| 三包 distribution 名和注册表权限 | 未核实 | 包重命名及正式发布前 |
| 包版本与首次独立发布版本 | 未确定 | 发布元数据冻结前 |
| Git 历史处理 | 推荐来源标注导入，未执行 | 导入提交前 |
| FS 独立 Python 支持矩阵 | 声明 >=3.10，未重跑验证 | 独立发布验收前 |
| Linux 各角色和原生 Windows 支持 | 既有交付路径不完全一致 | 对外支持文档冻结前 |
| 其他本机/外部消费者 | 已知引用已定位，未全量覆盖 | 旧来源退出维护前 |
| 各 agent 市场插件 | 后续范围 | 通用 CLI/MCP 稳定后另立需求 |

主要风险：workspace 掩盖缺失依赖、改名破坏制品识别、复制遗漏运行资源、测试因重名或配置变化漏收集、只测试全 extras 而遗漏独立安装、来源版本混用、旧目录仍被消费者依赖、历史文档被误当成现行支持承诺。

## 11. 交付物与完成判定

必须形成：来源与迁移清单、三包源码与测试、workspace 和锁文件、独立构建/验收脚本、CI、许可证与依赖声明、安装/开发/发布文档、宿主切换改动、测试及未验证项报告、旧来源退出维护说明。补充交付 Python/base/extras 支持矩阵、测试收集对照和导入路径证据、sdist 重建记录、固定制品组合清单、安装生命周期矩阵、分包发布状态与过渡期唯一修改源记录。

分别报告“新仓独立可用”“制品已发布”“宿主已切换”“旧来源已退出维护”“真实环境已升级”；不使用一个“迁移完成”掩盖尚未完成的阶段。

### 11.1 本轮审核修订记录

本次仅修订需求，六项审核意见分别落实到第 6.1—6.6 节，并同步阶段 B/C/D 门禁及 AC-12、AC-16—AC-22。此处“落实”仅指文档要求补齐，不表示 Python 支持、制品构建、生命周期或发布失败路径已经运行验证。

**当前进度：用户已确认推荐方案并授权本地提交、不推送。三包已本地导入，workspace、CI配置和独立制品验证已建立；阶段C仍未完成（含FS Python3.10回归失败等门禁）。详细结果见 `../../docs/implementation-status.md`；正式发布、下游切换及运行环境升级均未开始。**
