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
| FS Python3.10完整测试 | **20 failed, 96 passed，未关闭** |
| Ruff | 新制品脚本及Device源码/测试通过 |
| uv lock --check | 通过 |
| CI远程运行 | 未运行，未推送 |

FS3.10失败中已观察到unittest.mock对`mcp_server_fs.core.<操作>.<helper>`的解析落在同名导出函数上，抛AttributeError；尚未逐项完成根因核验和修正。不得据安装/导入成功宣称Python3.10全部支持验收通过。该次测试从新仓运行，不属于独立wheel完整FS测试证明。

原始输出位于本机被忽略的 artifacts/：test-bash.log、test-fs.log、test-device-runtime.log、collection-comparison.json、build.log、verify-artifacts.log、artifact-verification.json、fs-python310-base.log、fs-python310-mcp.log、fs310-tests.log。不要将可变本地输出当作长期发布证据。

## 阶段结论与剩余门禁

- 阶段B：导入、workspace及包内3.13回归已建立，本地提交作为检查点；仍须整体自审后最终收口。
- 阶段C：**部分完成，未通过最终门禁**。优先处理FS3.10测试失败，核实最低支持边界；补实际行为与独立FS制品测试。
- 安装生命周期已有Device回归覆盖，但尚未形成保留命令×平台×角色的完整新制品验收矩阵，不能宣称所有实际安装/prepare/activate/rebuild路径均已动态验收。
- Device各角色独立最小依赖安装、完整三包来源/依赖/制品发布组合记录、第三方许可审查仍需收口。
- 导入文档仍含历史交付说明；需要继续核对命令示例、路径、历史支持声明，尤其Linux各角色及CentOS交付，不能直接据旧文档宣称新包已支持。
- 阶段D/E/F未开始：无发布、宿主切换、旧源码移除或真实设备升级。

## 接续入口

先阅读需求第6.1—6.6节和AC-17—AC-22。下一步定位FS3.10 mock解析问题，复核基线同环境行为并做最小测试适配；完成后重跑3.10/3.13及独立制品验证，再补生命周期和角色矩阵、文档与组合证据。没有新授权不得推送、发包或修改AI助手/真实服务。
