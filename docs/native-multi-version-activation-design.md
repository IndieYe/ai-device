# 多版本激活协议（设计稿）

> 状态：草案，未实现。基于 `e3034c9` 的实际代码与需求文档
> `/Users/kongkongye/data/project/ai-assistant/doc/requirements/device-native-installation-pairing-and-remote-upgrade.md` §5.4、§7.5。

## 1. 必须先解决的冲突

### 1.1 事实

`installation.py` 在**安装时**把解释器绝对路径写进 `run` 的 shebang：

```python
private_python = root / "releases" / active["release"] / "runtime" / "bin" / "python3.13"
runner_content = f"#!{private_python}\n" + RUNNER.split("\n", 1)[1]
```

`run` 是稳定入口（systemd `ExecStart` 指向它）。因此：

- `run` 的 shebang 已经钉死某个具体 release
- 改 `active.json` **不会**改变解释器
- 真正切版本必须重写 `run`，而 `run` 正在被运行中的服务使用

需求 §5.4 要求：「稳定启动入口 —— 服务始终指向稳定入口；不要求升级后重配路径」。
当前实现只满足"路径不变"，不满足"切换后生效"。

### 1.2 选项对比

| 方案 | 做法 | 优点 | 代价 |
| --- | --- | --- | --- |
| **E1 固定 shell 启动器** | `run` 改为 `#!/bin/sh`，读取 `active.json` 后 exec 对应 release 的解释器 | 真正与 release 解耦；切换只改 JSON | 引入 shell 解析 JSON 的复杂度，或依赖系统 python3 |
| **E2 多层 indirection** | `run` shebang 指向 `root/current/bin/python3.13`（符号链接） | 语义清晰 | 需要原子切换符号链接；`lstat` 拒绝 symlink 的现有校验要改；符号链接本身成为新的信任边界 |
| **E3 重写 run** | 切版本时原子替换 `run` | 改动最小 | 运行中进程仍用旧 shebang；替换与执行竞争；不满足"无需重配"的稳定性预期 |

**倾向 E1**：唯一能在写 `active.json` 后立即生效、且不引入 symlink 信任面的方案。

但 E1 有个硬约束：`run` 必须能在**没有 intact venv** 时工作（rebuild 场景已明确要求 stdlib-only）。
因此 shell 或系统 python 都不可靠。更稳的是：`run` 保持 `#!/bin/sh`，用纯 shell 提取 release id，
再 `exec` 该 release 的解释器执行真正的 RUNNER。

### 1.3 E1 的提取可行性（已实测）

`_json` 当前以 `indent=2` 多行写入。实测 `sed -n 's/.*"release"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'`
在多行格式下能正确提取，且不会误匹配出现在**值**里的 `release` 字样（因为要求键带引号+冒号）。

但这是「当前排版恰好成立」，不是明确契约：任何人改 `_json` 的 indent 或键名都会静默破坏入口。
因此 E1 落地时需要二选一：

- **E1a** 新增独立的单行指针文件（如 `current`，内容仅 release id），`active.json` 保持元数据角色；
  入口只需 `read`，没有解析。推荐。
- **E1b** 给 `active.json` 格式加固定契约并加测试锁定，任何排版变更立即红。

E1a 更简单且把「入口读什么」与「元数据长什么样」解耦，代价是多一个需要同步写入的文件。
同步写入必须与 `active.json` 在同一次 `_lock` 内完成，且 `current` 写入失败不得留下半状态。

## 2. 状态模型

```
root/
  instance.json          # 身份，跨版本不变
  active.json            # {release, roles, bundle_sha256, ...}
  previous.json          # 新增：上一个已知良好 release（回滚用）
  releases/<release>/    # 每个版本完整闭包：runtime + venv + bundle
  run                    # 稳定入口（E1 后为 shell 启动器）
  .installation.lock     # SH 由服务持有，EX 由切换持有
```

`active.json` 原子写入（已有 `_json` 用临时文件 + rename）。

## 3. 激活状态机

```
IDLE
  └─ prepare      校验制品、解包、建 venv、自检 → 写入 releases/<new>/
       ├─ 失败 → IDLE（active 不变，新目录保留供诊断）
       └─ 成功 → PREPARED（active 仍指旧版本，服务无感）

PREPARED
  └─ activate     取 EX 锁 → 写 previous.json → 原子写 active.json → 放锁
       ├─ 取锁失败 → 服务在跑，需先 stop（不强制）
       └─ 成功 → ACTIVATED（下一次 start 用新版本）

ACTIVATED
  └─ verify       启动 + 健康检查
       ├─ 失败 → ROLLBACK
       └─ 成功 → IDLE（新版本成为已知良好）

ROLLBACK
  └─ 写回 previous.json → active.json → 重启
     并标记该版本不可用，不自动重试
```

关键点：**prepare 与 activate 分离**。prepare 期间服务不受影响；activate 是极短的锁内原子操作。

## 4. 与现有代码的衔接

| 现有能力 | 复用方式 |
| --- | --- |
| `rebuild_instance` | 已有"准备新 release → 最后原子写 active.json"骨架，正是 prepare+activate |
| `_prepare` | 直接复用，但**必须补传 `runtime_source`**，否则新 release 退回包依赖（A 方案白做） |
| `_lock` | 复用，但需区分 prepare（EX 过久阻塞服务恢复）与 activate（短 EX） |
| `RUNNER` | 逻辑不变，改为由 shell 启动器选择解释器后执行 |
| `lifecycle._check_release` | 增加 previous.json 校验 |

## 5. 必须守住的边界

1. **软件回退 ≠ 数据回退**（需求 §7.5）。回滚程序不代表 state 兼容，激活旧版本前要检查 state 版本。
2. **不自动清理旧 release**。保留数量需要单独策略，本轮不做。
3. **绕过 `run` 启动的进程不受锁保护**。RUNNER 的 `pass_fds` 只覆盖经 `run` 的进程；直接执行 `releases/*/client/venv/bin/python` 的进程无法被 EX 锁发现。这是已知边界，需在文档写明。
4. **激活失败不自动重试**，避免在坏版本上循环。
5. **`previous.json` 只保留一层**，多代回滚需要额外台账，本轮不做。

## 6. 验收清单（实现后需逐项实测）

- [ ] prepare 失败时 active 不变、服务不受影响
- [ ] activate 在服务运行时被 EX 锁拒绝，且无副作用
- [ ] activate 成功后重启，实际运行的是新 release 的解释器（核对 `/proc/<pid>/maps`）
- [ ] 新 release 仍自带 runtime，卸包后可用（A 方案不回退）
- [ ] verify 失败触发回滚，回滚后服务可用
- [ ] 回滚不删除新版本目录
- [ ] previous.json 缺失/损坏时的行为
- [ ] state 版本不兼容时拒绝激活旧版本
- [ ] 绕过 run 直接启动的进程不被误认为安全

## 7. 未决问题（需你决定）

1. **入口策略选 E1/E2/E3 哪个**——这是根本分歧点，影响后续所有实现。
2. **是否允许 activate 时自动 stop/start 服务**，还是必须人工介入。
3. **旧 release 保留几个**，超出后是自动清理还是仅报告。
4. **state 兼容性如何判定**：用显式版本号，还是拒绝一切跨版本回滚。
