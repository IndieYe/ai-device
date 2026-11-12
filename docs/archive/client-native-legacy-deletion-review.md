# 旧路径删除核对清单（供审核）

> 状态：**已执行第一步（删除 A+B），C 保留待定**。删除明细见第 8 节。
> 核对方法：对全仓 287 个 Python 文件做 AST 导入图分析（排除 `.venv`/`build`/缓存），
> 再逐项人工复核动态导入与字符串引用。不是按文件名前缀判断。
> 对应需求 §9「新旧路径替换与清理」与 §13.5 交付清单。

## 0. 结论摘要

| 项 | 状态 | 依据 |
| --- | --- | --- |
| A. `launchd_deploy.py` + 专属测试 | **已删除** | 导入图 0 引用，仅 `installation.py` 的 `instance launchd` 分支 + 1 份专属测试 |
| B. `installation.py` 的 `instance launchd` 子命令 | **已删除** | 全仓无任何脚本/文档/代码实际调用该子命令 |
| C. `upgrade_supervisor._runner_bound` 的 launchd 分支 | **保留**（待定） | 被 4 处 `@legacy_operation` 入口共用；Linux 侧同一函数也走这里 |
| D. `upgrade_supervisor` 整体 | **保留** | `upgrader.py` 有 5 处调用，共享状态机仍在使用 |
| E. `installation.py` / `lifecycle.py` / `bundle.py` / `upgrader.py` / `native_runtime.py` | **保留** | 均为共享模块，清单 3.1 已记录 |

一句话：**A+B 已删（约 380 行，实测 2199 passed / 20 skipped 全绿），C 看似同类但实际不能动。**

## 1. 项 A：`launchd_deploy.py`（230 行）

### 1.1 事实

| 项 | 值 |
| --- | --- |
| 文件 | `packages/device-runtime/src/device_runtime/launchd_deploy.py` |
| 行数 | 230 |
| 专属测试 | `packages/device-runtime/tests/test_launchd_deploy.py`（135 行） |
| AST 导入图中被引用次数 | **0** |

### 1.2 为什么判定是旧路径

它生成的是**旧布局**的 LaunchAgent，与当前交付的客户端完全不同：

| | `launchd_deploy` | 当前客户端 |
| --- | --- | --- |
| Label | `com.device-runtime.<instance>` | `local.ai-device.client` |
| 程序入口 | `<instance>/launchd/start.sh`（shell 包装） | `/opt/ai-device-client/bin/ai-device-client-launcher` |
| 凭证 | 独立 `client.env` 文件 | `~/Library/Application Support/ai-device-client/token` |
| 安装位置 | `~/Library/LaunchAgents/` | 同左（但内容与生命周期完全不同） |

当前客户端用 `client_macos_layout.py` / `client_macos_service_job.py`，不经过它。

### 1.3 唯一引用来源

```
packages/device-runtime/src/device_runtime/installation.py:902   from . import launchd_deploy
packages/device-runtime/tests/test_launchd_deploy.py:8           from device_runtime import launchd_deploy as deploy
```

除这两处外，全仓（含 `scripts/`、`operator/`、`examples/`、文档）无任何引用。

### 1.4 删除前提

必须先删 CLI 分支，否则 `installation.py` 会在 `instance launchd` 时 `ImportError`。

## 2. 项 B：`instance launchd` 子命令

### 2.1 涉及位置（`installation.py`）

| 行号 | 内容 |
| --- | --- |
| 849 | `add_launchd_parsers(actions)` 调用 |
| 852–878 | `def add_launchd_parsers(actions)`（约 27 行，定义 `install`/`uninstall`/`status`/`profile` 四个子动作） |
| 901–923 | `elif args.instance_action == "launchd":` 分支及 `handlers` 字典 |

### 2.2 无调用方证明

全仓搜索 `instance launchd`、`launchd install`、`launchd uninstall` 等实际调用形式，
**只找到定义，没有任何脚本或文档示例调用它**。它不在 DEB/RPM/PKG 的任何构建或验收路径上。

### 2.3 一个需要你确认的点

`installation.py` 的 `instance` 子命令**整体**仍被使用：

- `packages/device-runtime/scripts/accept-centos7.py` → `import install_instance, rebuild_instance`
- `packages/device-runtime/examples/linux-container/run-acceptance.py`
- `scripts/verify_delivery.py`
- `device_runtime/cli.py`（`device-runtime` 命令入口）

这些用的是 `instance install` / `instance rebuild`，**不是 `launchd`**。
所以删除范围必须精确到 `launchd` 子分支，不能碰 `install`/`rebuild`。

## 3. 项 C：`upgrade_supervisor._runner_bound`（**不建议删**）

这一项是重点，因为它看起来像同类旧代码，实际不是。

### 3.1 事实

```python
# packages/device-runtime/src/device_runtime/upgrade_supervisor.py:131-151
def _runner_bound(instance, snapshot):
    # 先解析 systemd 的 ExecStart（Linux 路径）
    ...
    # 再读 launchd plist（macOS 路径）
    path = instance / "launchd" / f"com.device-runtime.{instance.name}.plist"
    ...
    return job.get("ProgramArguments") == [str(instance / "run"), "client"]
```

### 3.2 不能删的原因

1. **同一个函数服务两种平台**。上半段解析 systemd `ExecStart` 是 Linux 路径，下半段才是 launchd。
   删掉 launchd 段只省约 12 行，但会让 `_runner_bound` 在 macOS 上对旧实例返回错误结论。
2. **被 4 处调用**：`upgrade_supervisor.py` 行 233、300、388、428，全部位于 `@legacy_operation` 装饰的入口内。
3. **有 3 份测试覆盖**（`test_upgrade_maintenance.py:995/1003` 及相邻），这些是共享回归，不是专属测试。
4. `upgrader.py` 有 5 处 import `upgrade_supervisor`（行 81、336、346、350、354）。

### 3.3 需要你决策的问题

`@legacy_operation` 的语义是：**受管实例**（存在 `.native-management.json`）会被直接拒绝，
但**旧包部署的未受管实例仍可走旧升级器**。这是需求里明确的保留场景。

也就是说，这段代码不是"忘了删"，而是"刻意留给旧实例的最后一条退路"。
是否要让旧实例彻底失去升级能力，是个产品决定，不是清理决定。**建议保持现状。**

## 4. 项 D/E：共享模块（清单已记录，复核一致）

| 模块 | 导入者数 | 结论 |
| --- | --- | --- |
| `installation.py` | 10（含 4 份测试、3 个脚本） | 保留，仅删 launchd 子分支 |
| `lifecycle.py` | 多 | 保留 |
| `bundle.py` | 多 | 保留 |
| `upgrader.py` | 多 | 保留 |
| `native_runtime.py` | 1（`installation.py`） | 保留 |
| `journal.py` / `tasks.py` / `maintenance.py` | — | 保留 |

## 5. 已确认真正删干净的部分（核对通过）

清单 `client-native-legacy-inventory.md` 第 2 节声明"已删除"的项，逐项核实**确实不存在**：

| 声明已删 | 核实结果 |
| --- | --- |
| `scripts/build-native-linux.sh` | 不存在 ✓ |
| `scripts/assemble-native-payload.py` | 不存在 ✓ |
| `scripts/render_upgrade_adoption.py` | 不存在 ✓ |
| 12 个旧 native Linux 运行模块 | `device_runtime/` 下无 `client_linux_*` ✓ |
| `templates/upgrade-stage1/` | 目录不存在 ✓ |
| `upgrade_supervisor.Systemd` 适配器 | 类定义不存在 ✓ |
| `accept-native-*` 验收脚本 | 不存在 ✓ |

**这一节可以信任。**

## 6. 建议执行方案（待你确认）

### 第一步：删 A + B

```
删除 packages/device-runtime/src/device_runtime/launchd_deploy.py
删除 packages/device-runtime/tests/test_launchd_deploy.py
修改 packages/device-runtime/src/device_runtime/installation.py:
  - 删除第 849 行 add_launchd_parsers(actions) 调用
  - 删除第 852–878 行 add_launchd_parsers 函数
  - 删除第 901–923 行 launchd 分支
```

预计减少约 380 行。验证要求：

- `cd backend 或 packages/device-runtime && uv run pytest`（全量）
- `uv run ruff check src tests`
- 确认 `device-runtime instance install/rebuild` 仍正常（跑 `accept-centos7` 相关测试或 `test_installation.py`）

### 第二步：不动 C/D/E，只更新文档

在 `client-native-legacy-inventory.md` 补一节，说明：

- `launchd_deploy` 与 `instance launchd` 已退役（本次删除）
- `upgrade_supervisor._runner_bound` 的 launchd 分支**刻意保留**，理由是旧未受管实例的升级退路
- 删除这一条要等"旧实例全部退役"这个前提成立

### 需要你决定的两件事

1. **是否执行第一步**（删 A+B，约 380 行）。
2. **C 段的定位**：是维持"旧未受管实例还能升级"的现状，还是打算让它们彻底不能升级？
   后者影响范围更大，需要单独评估，我建议不在本次做。

## 7. 风险提示

- 本次核对只覆盖 Python 侧。Mac 的 45 个 `native/*.h`、`.m`、`.c` 文件未做同等分析，
  它们由 `build-client-pkg.py` 编译进 PKG，引用关系是构建期决定的，文本搜索不足以判断。
- `operator/device-ops` 是独立工具，不在 `device_runtime` 导入图内，单独处理。
- 删除 A+B 后，若仍有旧实例依赖 `instance launchd` 注册，会失去该入口。
  按需求 §9「旧布局不自动迁移」和「旧测试设备由管理员明确重装」，这符合既定方向，
  但**应在指南或发布说明里留一句提示**。

## 8. 执行记录：第一步已完成

### 8.1 实际改动

```
删除 packages/device-runtime/src/device_runtime/launchd_deploy.py          (230 行)
删除 packages/device-runtime/tests/test_launchd_deploy.py                   (135 行)
修改 packages/device-runtime/src/device_runtime/installation.py             (-52 行)
  - 删除 add_launchd_parsers(actions) 调用
  - 删除 add_launchd_parsers() 函数定义
  - 删除 run_instance() 中 args.instance_action == "launchd" 分支
```

合计减少 417 行。

### 8.2 验证结果

| 验证项 | 结果 |
| --- | --- |
| 残留引用搜索（`launchd_deploy` / `launchd_action` / `add_launchd_parsers`） | 空，无残留 |
| `ast.parse` 语法检查 | 通过 |
| `uv run pytest -q`（device-runtime 全量） | **2199 passed, 20 skipped** |
| `uv run ruff check src tests` | All checks passed |
| `instance` 子命令冒烟 | 仍保留 `activate/backup/inspect/install/prepare/rebuild/restore/rollback`，`launchd` 已消失 |

测试总数由 2209 降至 2199，减少的 10 项即 `test_launchd_deploy.py` 的专属测试，
**不是覆盖率下降**。

### 8.3 未做的事

- 未动 C 项（`_runner_bound` 的 launchd 段），保持旧未受管实例的升级退路。
- 未动任何 native 头文件与 `build-client-pkg.py`。
- 未 push。
