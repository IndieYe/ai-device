# ai-device

面向 AI agent 的设备访问与执行工具。客户端主动连接中心，agent 通过 CLI 或 MCP 执行命令、操作文件和查询结果。

## 三个包

| 目录 | Distribution | Python import | 命令 |
| --- | --- | --- | --- |
| packages/bash | ai-device-bash | mcp_server_bash | mcp-server-bash |
| packages/fs | ai-device-fs | mcp_server_fs | mcp-server-fs |
| packages/device-runtime | ai-device | device_runtime | device-runtime / device-mcp |

目前仅本地构建，未发布到 PyPI。名称查询未发现已发布项目，不代表名称预留。版本沿用导入基线，首次正式发布版本另定。不要安装同名旧包代替本仓准确制品。

## 开发

```bash
uv sync --python 3.13 --locked --all-packages --all-extras --group dev
(cd packages/bash && ../../.venv/bin/python -m pytest -q)
(cd packages/fs && ../../.venv/bin/python -m pytest -q)
(cd packages/device-runtime && ../../.venv/bin/python -m pytest -q)
uv build --all-packages
uv run --no-sync python scripts/verify_artifacts.py --fs-python 3.10
```

三包测试分别运行，避免配置及测试包互相污染。`verify_artifacts.py` 从 sdist 在临时目录重建 wheel、使用独立 venv 检查 base/MCP 包，并在源码目录之外对安装制品运行 Device 测试。测试不连接真实设备。

Bash/FS 的 MCP 服务使用各自的 `[mcp]` extra；Device extras 为 client、server、ops、mcp。FS 核心保留 Python >=3.10 声明，workspace 开发环境使用 >=3.13；具体验证范围见实施记录，不代表全平台支持承诺。

## 文档

- [需求和验收](doc/requirements/ai-device-monorepo-migration.md)
- [实施状态与验证边界](docs/implementation-status.md)
- [阶段C验收矩阵](docs/stage-c-acceptance.md)
- [D1本地发布准备与外部门禁](docs/release-preparation.md)
- [来源文件清单](docs/source-manifest.json)
- [Device 使用](packages/device-runtime/README.md)
- [Bash 使用](packages/bash/README.md)
- [FS 使用](packages/fs/README.md)
- [Agent 操作技能](skills/device-operations/SKILL.md)

MIT。第三方依赖按各自许可使用；完整第三方声明与对外发布审查仍须在发布前收口。当前 CI 配置不包含发布步骤；未推送前没有远程 CI 运行结果。
