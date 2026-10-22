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
2. 推荐GitHub Actions + PyPI Trusted Publishing；尚未创建发布workflow或配置发布者，避免未授权开启上传路径。
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
