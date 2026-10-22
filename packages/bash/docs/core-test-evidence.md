# 本地执行核心第一阶段交付与验证

## 边界

- Worktree：`ai-device-bash-core`；分支：`refactor/device-core-agt543`。
- 基线 / 当前 HEAD：`877b6fffb471808a70ad493f6a2eb09378b9ba0b`。本次成果仅在未暂存工作区，没有 add、commit、merge、push 或历史修改。
- 公共入口：`from mcp_server_bash.core import ExecutionRuntime`。该模块导出运行实例、请求/目标/受管文件模型和 stdin/script 文件准备函数。
- 保留 `shell.py` 的执行、输出捕获与超时逻辑；主要 diff 来自将已有函数缩进为实例方法。新增运行实例的关闭与事件循环约束，后台索引/保留定时器归属于实例，MCP 适配器通过 lifespan 关闭自己的实例。
- FastMCP 与适配器直接使用的 Pydantic 移至 `mcp` extra；默认依赖仅 `psutil`，`uv.lock` 已更新。README 与 MCP 使用说明已联动更新。
- 不涉及设备身份、设备网络协议、Windows、正式配置或真实服务。仅在该 Worktree 内写入源码与 `.tmp/` / `.env` 测试配置。

## 实际验证

验证平台：macOS / Darwin arm64，Python 3.13。Linux 代码路径保留，但本次未在 Linux 主机执行测试。

| 验证 | 通过 | 失败 | 错误 | 跳过 |
| --- | ---: | ---: | ---: | ---: |
| 不启用 `mcp` extra 的核心测试 | 29 | 0 | 0 | 0 |
| 完整核心 + MCP 回归 | 168 | 0 | 0 | 0 |

当前完整运行包含全部 139 项既有回归和 29 项新增核心测试；核心单独运行是同一批测试的依赖边界验证，不能重复计入总数。初版19/158后修复P1关闭循环等待并补2项回归（21/160）；限定复审确认P1关闭但发现P2启动前取消遗漏受管资源。P2已修复并补8项回归，当前29/168通过。原审核会话 ses-12225 在冻结 v3 上完成限定复审：37项直接回归通过，P2关闭、P1保持关闭。核心分离第一阶段已验收；设备服务、客户端及Device MCP不属于本阶段交付。

核心验证实际覆盖 argv 边界/环境变量、Shell 展开与管道、script 与 stdin 分离、语法失败不执行、内联/落盘输出限制、后台重复读取、两个实例隔离、关闭后的拒绝使用、前台取消、后台进程与受管文件清理、调用方异常、输出捕获异常、重复取消关闭及保留时长到期。

`test_no_mcp_install.py` 在全新的虚拟环境用 `uv sync --locked --no-default-groups --no-dev` 安装默认依赖，以 `python -I` 实际导入并运行 Shell/argv/script/stdin/后台查询；确认 FastMCP、MCP、Pydantic 均未安装，并验证 MCP CLI 给出安装 extra 的提示。不是仅检查 import 语句。

MCP 集成回归使用子进程中的真实内存 FastMCP Client，验证四个工具发现、参数拒绝、本地执行、后台结果重复读取、退出 lifespan 时的后台清理，以及重新开启 lifespan 时的新实例；没有开放监听端口或连接正式 MCP。

## 独立审核整改

独立审核 `ses-12225` 发现：外部关闭若等待调用者整条 Task，调用者的 `__aexit__`/`finally` 又等待同一次关闭，会形成循环等待，阻止后台 tracker 清理。主会话使用不执行命令、不写文件的隔离内存探针确认了问题。

`run_execution` 现在创建并跟踪运行实例拥有的内部执行 Task，关闭只等待内部执行，不等待调用者的后续退出逻辑。调用者取消仍传播到内部执行。新增 `test_concurrent_close.py` 分别覆盖外部关闭与 `async with` 退出、`finally` 再次关闭，并检查 tracker 确实关闭；探针在独立解释器中以父进程超时约束，不遗留真实命令进程。原取消移交测试的 background-yield 同步点调整为内部执行 Task，仍从调用者取消，并保留原资源回收断言。

启动前取消整改：内部执行包装器设置 `started` 标志；如果内部Task从未运行，外层finally使用请求有效artifact目录清理接受的受管文件。`test_prestart_cancellation.py` 覆盖 run/script、实例目录/调用级覆盖目录、取消调用者/内部Task共8项，修复前8项均因遗留文件失败，修复后通过；调用者非受管文件保持不变。原P1的内部Task所有权边界保留。该P2修复已通过原审核会话 `ses-12225` 限定复审，审核前后25文件及哈希均匹配 v3，实际执行37项通过。本轮主会话另复跑168项全量、29项核心及正确性Ruff通过；最新证据 `.tmp/core-final-v3-{full,only}.{log,xml}`。审核结果保存于代理工作区 `bash-p2-review-result.json`，定向测试证据保存于 `evidence/bash-p2-review/targeted.{log,xml}`。使用无缓存隔离安装核验实际安装的shell.py与v3一致，在无FastMCP/MCP/Pydantic/dotenv环境中FS写入→Bash argv读取→FS编辑/读取通过（Python3.14.3）。最后仅更新本证据文档的验收状态，运行源码及测试不变；最终交付清单v4相对已审核v3仅此文档改变。

## 可复现命令与证据

在 Worktree 根目录执行；测试 `.env` 禁用服务日志并将输出、受管文件与日志目录指定到该目录下 `.tmp/`，不覆盖正式配置：

```bash
mkdir -p .tmp
export TMPDIR="$PWD/.tmp" UV_CACHE_DIR="$PWD/.tmp/uv-cache"

uv run --locked --with pytest --with pytest-asyncio python -m pytest tests/core -q \
  --basetemp=.tmp/pytest-core-prestart-fix --junitxml=.tmp/core-prestart-fix-only.xml

uv run --locked --extra mcp --with pytest --with pytest-asyncio python -m pytest -q \
  --basetemp=.tmp/pytest-full-prestart-fix --junitxml=.tmp/core-prestart-fix-full.xml

uv run --locked --extra mcp --with ruff ruff check --isolated \
  --select E4,E7,E9,F --ignore E402 src tests

git diff --check
```

- 当前核心输出：`.tmp/core-prestart-fix-only.log` / `.tmp/core-prestart-fix-only.xml`（29项）。
- 当前完整输出：`.tmp/core-prestart-fix-full.log` / `.tmp/core-prestart-fix-full.xml`（168项）。
- `.tmp/core-close-fix-*` 记录P1整改后21/160测试；不是本次P2修复后的当前证据。
- 初版 `.tmp/core-final.*` / `.tmp/full-final.*` 仅记录整改前 19/158 验证，不作为最终通过证据。
- 正确性静态检查：`.tmp/ruff-final.log`，通过；E402 排除既有 MCP stub 安装后延迟导入的测试模式。
- `git diff --check`：通过。
- 另行尝试未隔离的环境继承 Ruff 风格规则，仍未通过：基线 166 项、当前 160 项（`.tmp/ruff-baseline.json` / `.tmp/ruff-current.json`）。仓库未配置该全局规则集；未为消除既有风格问题扩大重构范围，不能将此检查表述为通过。

`.tmp/` 与专用测试 `.env` 被 Git 忽略；证据留在此 Worktree，源代码、README 和测试未暂存，等待主会话最终复核。
