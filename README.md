# ai-device

面向 AI agent 的设备访问与执行工具。客户端主动连接中心，agent 通过 CLI 或 MCP 执行命令、操作文件和查询结果。

## 三个包

| 目录 | Distribution | Python import | 命令 |
| --- | --- | --- | --- |
| packages/bash | ai-device-bash | mcp_server_bash | mcp-server-bash |
| packages/fs | ai-device-fs | mcp_server_fs | mcp-server-fs |
| packages/device-runtime | ai-device | device_runtime | device-runtime / device-mcp |

三个包已发布到 PyPI：ai-device-bash 0.2.0、ai-device-fs 0.2.0、ai-device 0.2.2。仓库中的 `ai-device` 的 `client` extra 必须精确 pin 另两个包各自的版本，版本号不必相同；持久状态格式按 `lifecycle.AUDITED_STATE_FORMATS` 审计，只有变更数据格式时才需登记新格式号。`tests/test_release_version_consistency.py` 会在漏改时报错。不要安装同名旧包代替本仓准确制品。

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

### 客户端安装升级

- [Linux 客户端安装升级指南](docs/client-native-linux-guide.md)（Ubuntu/Debian amd64 DEB、RHEL 系 x86_64 RPM，systemd）
- [macOS 客户端安装升级指南](docs/client-native-macos-guide.md)（Apple Silicon arm64，PKG/launchd）
- [旧路径退役与保留清单](docs/client-native-legacy-inventory.md)
- [旧路径删除核对记录](docs/client-native-legacy-deletion-review.md)
- [Linux 客户端开发记录与验收证据](docs/client-native-linux-development.md)
- [macOS 客户端开发记录](docs/client-native-macos-development.md)

### 其他

- [macOS Device 用户级部署、迁移与验收](docs/macos-ai-workstation-setup-and-verification.md)
- [macOS 可选配套工具：tmux、Harness 与管理员配置](docs/macos-optional-tools.md)（非 Device 必需依赖）

- [需求和验收](doc/requirements/ai-device-monorepo-migration.md)
- [实施状态与验证边界](docs/implementation-status.md)
- [阶段C验收矩阵](docs/stage-c-acceptance.md)
- [D1本地发布准备与外部门禁](docs/release-preparation.md)
- [来源文件清单](docs/source-manifest.json)
- [第三方许可补充资料与再分发边界](docs/third-party-licenses/README.md)
- [Device 使用](packages/device-runtime/README.md)
- [Bash 使用](packages/bash/README.md)
- [FS 使用](packages/fs/README.md)
- [Agent 操作技能](skills/device-operations/SKILL.md)

MIT。第三方依赖按各自许可使用；完整第三方声明与对外发布审查仍须在发布前收口。`test.yml` 在 push/PR 运行测试与构建，不包含上传步骤；PyPI 发布由 `release.yml` 手动触发并逐包经过 Environment 审批，不会因 push 自动发布。详见 [D1本地发布准备](docs/release-preparation.md)。

- [本机 Operator 稳定入口与发布清单（待部署）](docs/operator-release.md)
