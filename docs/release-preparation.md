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

## 准确制品及证据

- 六制品在被忽略的dist/，名称、SHA256及大小由 `evidence/candidate-manifest.json` 固定。
- 该清单包含包源码哈希、锁文件和配套依赖；清单中的来源commit早于本次修改，以文件哈希补足，不将早期commit当作最终版本。
- 元数据变更后重新构建三包，六制品twine check全部PASSED。
- 重新执行sdist隔离重建、基础包和独立入口探针、FS3.10制品行为、Device制品581测试（42.37秒）及临时标准安装生命周期，均通过；证据已刷新绑定此次候选。
- 原始本地日志为artifacts/d1-build.log、d1-metadata-check.log、d1-verify-artifacts.log、d1-verify-delivery.log、d1-capture.log。固定结果见docs/evidence。
- 之后任一包内容、依赖或元数据改变都须重建并更新哈希、相关验证与证据；禁止将旧报告套到新制品。正式发布前再次核实clean commit与文件哈希。

## 建议的发布执行契约（尚未执行）

1. 采用人工触发、逐包核对的发布方式，不因push自动发布。
2. 推荐GitHub Actions + PyPI Trusted Publishing；已准备本地release.yml，但尚未推送、运行或配置发布者。
3. 配置时必须固定仓库IndieYe/ai-device、workflow文件、受保护environment及PyPI项目/发布者关系，并确认审批者；不要把Token写进仓库、参数、消息或日志。
4. 准确授权后，先发布Bash与FS，分别公开查询/下载核对哈希，再发布Device；三包tag建议分别命名，不创建浮动发布身份。
5. 上传只使用清单中准确文件，不使用可能包含额外旧版本的无约束glob。发布前检查名称/版本仍可用、元数据和最终提交。
6. 任一包失败或结果未知先对账，不覆盖同名同版本；未完成组合不进入D3/D4。
7. 上传成功不等于验收完成。D3须仅从正式注册表下载/安装并验证，无workspace或私有wheel帮助；D4再独立实施宿主切换。

## 必须确认的外部门禁

- PyPI账号/组织归属、三个项目的创建/发布权限：未核实，不读取个人凭证推断。
- 发布方式：上述Trusted Publishing为推荐，尚未配置；需确认environment/审批与注册表发布者。
- GitHub推送、tag/release、PyPI发布的准确授权：本次均不包含。
- 对外公布平台范围：沿用阶段C限制，macOS arm64本地验证不代表Linux/Windows完整交付支持。
- 正式公开发布前最终来源/许可证审查仍需确认；依赖元数据和声明文件完整不替代该决策。

因此本轮可称“D1本地准备完成”，不能称“已具备注册表发布权限”或“D1所有外部门禁通过”。下一步在冻结本地提交上审核发布清单，集中确认上述外部事项后另行授权D2。


## 本地发布工作流（未推送／未运行）

入口：`.github/workflows/release.yml`。仅 workflow_dispatch，main 分支、准确40位 expected_commit 必须匹配；publish 默认 false。不会因 push 自动上传，也不会创建 Git tag 或 GitHub Release。

### 配置前置条件

由有权限的管理者在 GitHub 创建 `pypi` Environment，限定 main 分支，并配置 required reviewers（套餐须支持）、尽可能禁止自批；如果仓库不支持有效审批，不能把仅有 Environment 名称当成审批保护，应先调整方案。仓库普通写权限、人工布尔值和Environment名称均不代替本次发布授权。

在PyPI为三个项目各自登记Pending Trusted Publisher：owner `IndieYe`、repository `ai-device`、workflow `release.yml`、environment `pypi`。已有项目须由项目所有者配置发布者。不得填写Token；权限和绑定仍由管理者确认，本轮不执行外部配置。

### 执行与准确制品

- 先以 publish=false 人工准备（推送和运行需另获授权）。macOS runner重新构建并运行三包源码、定向回归、twine、sdist重建/独立制品、真实临时交付生命周期；不复用本地旧C报告。
- 验证通过后保存同一次run的 `pypi-release-<sha>` Artifact：六制品、manifest.json、源码commit和两份准确报告；保留14天。到期不上传，重新准备并审核。
- prepare产出的manifest是新候选，必须审核，不能仅凭本地D1旧哈希视作已批准。每次重新运行都可能生成不同文件，应重新核对。
- 正式执行需另获授权并使用publish=true；prepare仍会重新构建。审批者必须在上传job的Environment等待期间核对本次run的commit、清单及报告，才批准。先前publish=false运行的产物不会被自动复用。
- 只有上传job持有id-token: write，且不重新构建。下载本run Artifact后核验六文件集合、哈希和source commit，再顺序上传Bash、FS、Device；每包后查询PyPI元数据并实际下载核对SHA256。仅404公共查询有限等待，不自动重试上传。
- workflow concurrency串行化本仓发布工作流，不是PyPI全局锁。相同版本不设skip-existing。

### 部分失败与重跑

如果Bash或FS已成功而后续失败，禁止直接重跑整个上传job：从第一包再次上传会遇到已存在版本，流程有意停止，不把跳过当成功。先人工对账并记录每个已公开文件哈希，另行审核授权限定剩余包的恢复操作；当前工作流不提供自动恢复模式。结果未知同样先对账。

静态检查和本地mock测试不证明GitHub Actions远程可运行、Environment已受保护或OIDC/PyPI权限有效。正式注册表安装组合验收仍归D3，公开下载哈希检查不能代替它。远程验证或实际发布前建议独立审核本次工作流增量。
