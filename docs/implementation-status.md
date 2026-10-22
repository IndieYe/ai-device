# 三包导入实施状态

日期：2026-10-22。当前为本地导入检查点，不是最终迁移验收或正式发布。

## 已确认与授权

- 用户确认一个仓库三个包、MIT、来源标注导入、不迁历史。
- Distribution 采用 ai-device-bash、ai-device-fs、ai-device；2026-10-22 PyPI JSON 查询均 404，不代表预留或发布权限。
- Python import 和命令保持原有契约。版本暂沿用来源值 Bash/FS 0.2.0、Device 0.2.2，首次正式发布版本另定。
- 新仓从导入起是唯一修改源；本轮旧仓无修改。允许本地提交，不推送、不发包、不切换宿主、不部署。

## 已完成

- 导入147个来源文件（不含三个旧 uv.lock）；逐文件来源和原始 SHA256 见 source-manifest.json。附加导入 Device 通用文档与技能。
- 建立 workspace、根锁文件、三包MIT元数据和许可文件、独立制品验证脚本与CI。
- Bash/FS Python执行实现保持来源字节，未重构；FS一个测试文件仅去除来源已有尾随空白。Device调整distribution及制品识别，保留import、命令和执行协议。
- Bash/FS MCP依赖增加 `<4`：独立安装曾解析FastMCP 4.1.3，Bash导入旧ToolResult路径失败；锁定开发环境3.4.11没有该问题。限制兼容主版本后独立导入通过。
- FS Inspector命令名保留mcp-server-fs，不随distribution改名。

## 本地验证

平台：macOS arm64。workspace Python 3.13.12。

| 验证 | 结果 |
| --- | --- |
| Bash完整源码测试 | 176 passed |
| FS完整源码测试 | 116 passed；首次Inspector命令误改已修正并复跑 |
| Device完整源码测试 | 581 passed |
| 原/新测试收集 | 176/116/581一致；Device参数节点仅distribution改名，标准化后完全一致 |
| 三包sdist/wheel构建 | 成功 |
| 隔离解压sdist重建wheel | 三包成功，不读取旧仓 |
| Bash/FS base及MCP独立venv导入 | Python3.13成功，base无FastMCP |
| 安装wheel后Device完整测试 | 581 passed，模块来源指向独立venv |
| FS Python3.10.20 base/MCP安装导入 | 成功；MCP create_mcp可创建 |
| FS Python3.10完整测试 | 修正mock模块定位后116 passed；Python3.13复跑116 passed |
| FS Python3.10独立wheel行为 | base 14 passed/3 deselected；MCP 114 passed/2 deselected（源码构建与Inspector用例不在制品行为范围） |
| Ruff | 新制品脚本及Device源码/测试通过 |
| uv lock --check | 通过 |
| CI远程运行 | 未运行，未推送 |

FS3.10首次20个失败已定位为unittest.mock对同名导出函数和子模块的解析差异。两个测试文件改用importlib显式获取模块后patch.object；断言和运行实现不变。3.10及3.13全套均116通过。独立FS wheel在3.10运行base和MCP行为套件通过，排除仅适用于源码构建和Inspector脚本的用例。最新Device wheel回归581 passed（41.61秒）。

原始输出位于本机被忽略的 artifacts/：test-bash.log、test-fs.log、test-device-runtime.log、collection-comparison.json、build.log、verify-artifacts.log、artifact-verification.json、fs-python310-base.log、fs-python310-mcp.log、fs310-tests.log。不要将可变本地输出当作长期发布证据。

## 阶段结论与剩余门禁

- 阶段B：三包导入、workspace及回归已完成，本地检查点已提交。
- 阶段C：macOS arm64本地限定范围的最小角色、真实标准安装生命周期和独立制品验收已补齐；详见 [阶段C验收](stage-c-acceptance.md)。Linux/Windows不在本轮实际安装验证范围，CI远程未运行。
- 已补真实临时实例install/inspect、稳定runner中心/客户端/MCP、prepare/activate/rebuild、rollback/backup/restore，以及租约、损坏制品、无效release和平台/角色拒绝。
- 已保存第三方许可元数据与声明正文、三包源码/制品对应及组合证据；不等于法律审计或已获发布权限。
- D1本地准备完成；D2正式发布及后续宿主切换、E/F未开始：无发布、宿主切换、旧源码移除或真实设备升级。

## 接续入口

后续先阅读stage-c-acceptance.md和docs/evidence中的固定候选证据；如需独立审核，冻结本地提交后限定审查。正式版本、注册表权限和发布授权仍需收口。不得推送、发包或修改AI助手/真实服务。

## D1接续状态

本地发布准备已完成，见release-preparation.md。三包补项目链接后重建六制品，twine check通过；准确候选独立制品581测试和临时生命周期重跑通过，固定证据已刷新。PyPI归属、发布者配置、正式许可确认及远程发布授权未收口；未推送、不发布、不改宿主。后续先审核冻结清单及确认外部门禁，不能直接开始上传。

## 本地发布工作流准备

已准备人工触发、默认不上传的release.yml、准确制品整理/公共哈希核验工具及回归。未推送、未运行Actions；之后已创建GitHub pypi Environment及Bash Pending Publisher，但FS复用相同绑定被PyPI拒绝，外部门禁未全部通过。详细配置与部分失败对账见release-preparation.md。本地16项回归及stage/check通过，ruff通过；actionlint因Go代理网络超时未完成，仅完成YAML解析和关键结构断言，远程语义待验证。

## Pending绑定规则整改

保留release.yml及Bash的pypi绑定；三个上传job串行，FS/Device分别绑定pypi-fs/pypi-device，每个job核验同run准确制品后仅发布本包，公共哈希通过再放行下游。新增工作流结构与Bash语法回归；本轮仅本地改动，未创建剩余Environment/发布者、不推送不发布。旧单job独审结论不覆盖此次调整，远程Actions/OIDC仍未验证。

本次调整验证：新增6项工作流结构/语法用例与既有16项共22项通过（5.58秒），ruff与diff check通过。初次生成的结构截断错误由回归发现并修正。actionlint仍未完成，未重跑制品构建/交付或远程发布；包源码和六制品未改。
