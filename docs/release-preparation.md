# D1 本地发布准备

日期：2026-10-22。状态：本地制品准备及验证完成；外部权限和发布授权未收口，D1整体发布准入仍有阻塞。没有推送、创建远程tag/release、上传PyPI或切换宿主。

## 首发候选

| 包 | 候选版本 | 依赖/说明 |
| --- | --- | --- |
| ai-device-bash | 0.2.0 | 保留mcp_server_bash及mcp-server-bash入口 |
| ai-device-fs | 0.2.0 | 保留mcp_server_fs、mcp-server-fs和main入口 |
| ai-device | 0.2.2 | client精确依赖上述两个0.2.0包 |

沿用现有候选版本，不作额外版本重置；注册表查询三项目均404（仅2026-10-22观察），不等于名称预留、账号归属或未来仍可发布。

三包已补Homepage、Repository、Issues链接，MIT元数据与LICENSE保留。源码来源和许可技术检查见阶段C记录及THIRD_PARTY_NOTICES.md。依赖许可清单不是逐行第三方代码溯源或法律审计。

## 第三方许可补充资料（后续更新）

准确版本的许可正文、固定源码 commit、wheel 哈希和再分发义务索引见 [第三方许可补充资料](third-party-licenses/README.md)。本次补齐 dnspython 2.9.0 / fastmcp-slim 3.4.11 正文缺项，并补充 certifi 2026.10.19 的 MPL 正文和源码定位；不改写历史环境清单，不自动随 bundle 分发，不代表全部依赖法律审计完成。三处导入代码的权利归属及 MIT 公开授权仍须另行确认。

本次只改仓库资料、通知引用及其校验，不改三包源码、构建配置或 bundle 协议，不构建或发布。下述 D1 及远程状态属于当时记录，不应用来覆盖后续运行事实。

## 上传后的证明文件与核验边界

`pypa/gh-action-pypi-publish` 会在当前包目录生成 `<准确制品文件名>.publish.attestation`。上传前的 `release_artifacts.py check` 仍严格要求三包六制品，拒绝额外文件。上传后的 `public --group <包组>` 仅容许该组 manifest 已列制品对应的普通、非符号链接证明文件；证明文件不作为制品、不加入公开文件集合，也不在这里验证其签名。缺失或篡改制品、无对应制品的证明、其他组证明及任意杂文件仍会阻断核验。公网准确文件集合、yanked 状态、元数据哈希及实际下载哈希仍须核对。

首次正式运行 `39883225536` 的 Bash 0.2.0 已上传，但旧校验因这些新增证明文件报 `release file set mismatch`，FS/Device 已跳过。本地修复不等于恢复发布授权；禁止重跑整组或重复上传 Bash，恢复前须核对现状并另行明确方案与授权。

## 准确制品及证据

- 六制品在被忽略的dist/，名称、SHA256及大小由 `evidence/candidate-manifest.json` 固定。
- 该清单包含包源码哈希、锁文件和配套依赖；清单中的来源commit早于本次修改，以文件哈希补足，不将早期commit当作最终版本。
- 元数据变更后重新构建三包，六制品twine check全部PASSED。
- 重新执行sdist隔离重建、基础包和独立入口探针、FS3.10制品行为、Device制品581测试（42.37秒）及临时标准安装生命周期，均通过；证据已刷新绑定此次候选。
- 原始本地日志为artifacts/d1-build.log、d1-metadata-check.log、d1-verify-artifacts.log、d1-verify-delivery.log、d1-capture.log。固定结果见docs/evidence。
- 之后任一包内容、依赖或元数据改变都须重建并更新哈希、相关验证与证据；禁止将旧报告套到新制品。正式发布前再次核实clean commit与文件哈希。

## 建议的发布执行契约（尚未执行）

1. 采用人工触发、逐包核对的发布方式，不因push自动发布。
2. 推荐GitHub Actions + PyPI Trusted Publishing；已准备本地release.yml，尚未推送或运行；Bash绑定已登记，剩余绑定未完成（见下表）。
3. 配置时必须固定仓库IndieYe/ai-device、workflow文件、受保护environment及PyPI项目/发布者关系，并确认审批者；不要把Token写进仓库、参数、消息或日志。
4. 准确授权后，先发布Bash与FS，分别公开查询/下载核对哈希，再发布Device；三包tag建议分别命名，不创建浮动发布身份。
5. 上传只使用清单中准确文件，不使用可能包含额外旧版本的无约束glob。发布前检查名称/版本仍可用、元数据和最终提交。
6. 任一包失败或结果未知先对账，不覆盖同名同版本；未完成组合不进入D3/D4。
7. 上传成功不等于验收完成。D3须仅从正式注册表下载/安装并验证，无workspace或私有wheel帮助；D4再独立实施宿主切换。

## 必须确认的外部门禁

- PyPI账号/组织归属、三个项目的创建/发布权限：未核实，不读取个人凭证推断。
- 发布方式：Trusted Publishing已部分配置；Bash Environment/待发布者已登记，FS与Device未完成，实际OIDC仍未验证。
- GitHub推送、tag/release、PyPI发布的准确授权：本次均不包含。
- 对外公布平台范围：沿用阶段C限制，macOS arm64本地验证不代表Linux/Windows完整交付支持。
- 正式公开发布前最终来源/许可证审查仍需确认；依赖元数据和声明文件完整不替代该决策。

因此本轮可称“D1本地准备完成”，不能称“已具备注册表发布权限”或“D1所有外部门禁通过”。下一步在冻结本地提交上审核发布清单，集中确认上述外部事项后另行授权D2。


## 本地发布工作流（未推送／未运行）

入口：`.github/workflows/release.yml`。仅 workflow_dispatch，main 分支、准确40位 expected_commit 必须匹配；publish 默认 false。不会因 push 自动上传，也不会创建 Git tag 或 GitHub Release。

### 配置前置条件

由有权限的管理者在 GitHub 创建 `pypi` Environment，限定 main 分支，并配置 required reviewers（套餐须支持）、尽可能禁止自批；如果仓库不支持有效审批，不能把仅有 Environment 名称当成审批保护，应先调整方案。仓库普通写权限、人工布尔值和Environment名称均不代替本次发布授权。

PyPI Pending Publisher 的唯一键为owner、repository、workflow、environment，不含项目名，三个待创建项目不能同时使用相同绑定。owner均为 `IndieYe`、repository均为 `ai-device`、workflow均为 `release.yml`；按下表区分Environment：

| 项目 | 上传job | Environment | 当前外部状态 |
| --- | --- | --- | --- |
| ai-device-bash | publish-bash | pypi | 已创建Environment并登记Pending Publisher |
| ai-device-fs | publish-fs | pypi-fs | 尚未创建/登记 |
| ai-device | publish-device | pypi-device | 尚未创建/登记 |

`pypi`已配置审批者IndieYe、允许自批、禁止管理员绕过、仅main；两个新增Environment须采用同样规则，但本轮只修改本地文件，不执行外部配置。已有项目须由项目所有者配置发布者。不得填写Token。Pending不预留名称；首次使用后转换为正式发布者，正式项目允许共享发布者，本方案不依赖发布中途重绑。

依据：[PyPI首发说明](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)、[正式发布者说明](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)、[Warehouse唯一约束](https://github.com/pypi/warehouse/blob/b4d058739869132dea89c9fd2c39740267ddbc1b/warehouse/oidc/models/github.py#L379-L389)。

### 执行与准确制品

- 先以 publish=false 人工准备（推送和运行需另获授权）。macOS runner重新构建并运行三包源码、定向回归、twine、sdist重建/独立制品、真实临时交付生命周期；不复用本地旧C报告。
- 验证通过后保存同一次run的 `pypi-release-<sha>` Artifact：六制品、manifest.json、源码commit和两份准确报告；保留14天。到期不上传，重新准备并审核。
- prepare产出的manifest是新候选，必须审核，不能仅凭本地D1旧哈希视作已批准。每次重新运行都可能生成不同文件，应重新核对。
- 正式执行需另获授权并使用publish=true；prepare仍会重新构建。审批者必须在各上传job的Environment等待期间核对本次run的commit、清单及报告，才批准。先前publish=false运行的产物不会被自动复用。
- 三个串行上传job各自持有id-token: write，且不重新构建。prepare → publish-bash → publish-fs → publish-device，每个job均下载本run Artifact后核验六文件集合、哈希和source commit，仅上传本包；每包后查询PyPI元数据并实际下载核对SHA256。仅404公共查询有限等待，不自动重试上传。
- workflow concurrency串行化本仓发布工作流，不是PyPI全局锁。相同版本不设skip-existing。

### 部分失败与重跑

如果Bash或FS已成功而后续失败，禁止不经对账就重跑工作流或失败job：重复上传已成功包会遇到已存在版本，流程有意停止，不把跳过当成功。先人工对账并记录每个已公开文件哈希，另行审核授权限定剩余包的恢复操作；当前工作流不提供自动恢复模式。结果未知同样先对账。

静态检查和本地mock测试不证明GitHub Actions远程可运行、三个Environment均已受保护或OIDC/PyPI权限有效。正式注册表安装组合验收仍归D3，公开下载哈希检查不能代替它。远程验证或实际发布前建议独立审核本次工作流增量。

三个上传job各自经过Environment审批，需要逐包批准。上游公共下载核验失败时，下游依赖默认success条件停止；不使用always绕过失败。剩余两个Environment与发布者待另行授权配置。原单job独审PASS不覆盖本次增量，调整后需限定复核。

## 固定候选的 FS / Device 恢复流程（实现不等于获准发布）

`recover-release.yml` 只用于首次正式 run `39883225536` 的剩余两包。
默认 `publish=false`，仅手动、仅本仓 main；与原发布共用 `pypi-release` 并发组。
它从原 Artifact `12471078590` 下载 ZIP，校验固定 ZIP SHA256、六制品准确
manifest/hash、两个原验证报告 hash，以及原来源提交 `718385bc9d3514a700a6e5f042c813d4e37704dc`。
不重建、不重传 Bash，不接受其他 run/candidate。过期或不一致立即停止，不自动换来源。
`expected_commit` 则要求准确的、经审核的**恢复流程提交**等于运行 `GITHUB_SHA`；
原制品来源与恢复代码来源分别记录，不能混写。

预检先对账 Bash 公网准确文件/hash/下载字节及未 yanked 状态，再确认 FS 0.2.0、
Device 0.2.2 均不存在；只有准确版本 API 的 404 表示不存在，限流、网络错误和其他状态失败。
正式开启 `publish=true` 仍须本次明确发布授权。FS 与 Device 使用 `pypi-fs`、
`pypi-device` 独立审批；每 job 重新下载原 ZIP 并在审批后复查前提。
FS 上传和公网对账、Python 3.10 干净安装通过后，Device 才能审批上传；
Device 复查 Bash/FS 已发布内容和自身不存在，然后上传并完成公网对账与干净安装烟测。
干净安装只访问 PyPI，不从 checkout 或候选 wheel 安装；help 不启动真实服务。
这些烟测不等于生产部署或全部角色功能验收，既有原候选动态验证证据仍需保留。

### 执行前必须落实的外部门禁

- **新 workflow 文件名不同**：PyPI FS / Device Trusted Publisher 必须对应
  `IndieYe/ai-device`、`recover-release.yml` 及各自 Environment；不能沿用旧
  `release.yml` 声明而假定可上传。新增/调整需另行授权，本次实现不修改外部配置。
- 保留两个 Environment 的人工 reviewer、main 限制；不批准 Bash Environment。
- 先推送经审核代码并运行 `publish=false` 禁发布预检，审核其准确提交、原候选及公网结果；
  然后另行授权 `publish=true` 并逐包审批。布尔开关不是用户授权。
- 不重跑原正式 run 或其失败上传 job；不使用 skip-existing、自动上传重试或删除/yank。

上传后失败或结果未知先对账。若 FS 已成功存在，即使字节一致，本流程下次预检也会停止；
不得盲目重跑本流程，应另定仅 Device 的恢复方案并授权。发现已存在但不一致、Artifact
不可验证或公网异常时硬停止。此流程不打 tag、不创建 Release、不部署或更新生产设备。
