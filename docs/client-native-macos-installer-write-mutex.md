# PKG 写入期互斥：让运行路径不进入写入窗口

> 版本：3（2026-11-09）｜状态：结构已离线验证；**真实 PKG 安装与升级已实测通过**，见第四节第 4 条

> 版本 2 修正：上一版称写入期“已消除”、并称结构“已实现”。独立审核
> （ses-14548）证明当时的 postinstall 调用了一个二进制不接受的动词，即真实
> 安装不可能成功；因此“已实现”不成立。本版按代码实际能力重写。

> 版本 3 修正：版本 2 第三行称“当前 inspection 包的 preinstall 仍直接 `exit 1`，
> 所以上述路径尚未在真机上跑通”。这句已过时。`exit 1` 属于
> `build-client-nopayload-inspection.py` 的专用禁安装产物，不是 shipping 包；
> `build-client-pkg.py` 产出的包已在 macminim6 完成四项真机验收，postinstall 与
> `RENAME_SWAP` 已多次实际执行。

## 一、问题

需求 §154 曾指出：`preinstall` 与 `postinstall` 是**两个独立进程**，脚本持有的锁随脚本退出而释放，
所以 `postinstall` 再取锁**无法覆盖**两者之间那段真实的文件写入期。

这个判断是对的，而且它否掉了一整类方案：任何"两个脚本各持一次锁"的做法都留有窗口。
更根本的是，安装器的 payload 写入**不是我们控制的代码**——它在 preinstall 之后、postinstall 之前，
以安装器自己的节奏写文件。想要"锁住写入期"，等于要求安装器配合我们的锁协议，而它不会。

## 二、结论：让写入期不存在

既然无法锁住写入期，就让**运行路径在整个安装过程中不被写入**。

```
安装器写 payload          →  /opt/ai-device-client-stage     （永远不会被执行的路径）
postinstall 一次 renamex_np →  /opt/ai-device-client          （运行路径，只变这一次）
```

运行路径的变化只有一个原子 `rename`。两个脚本之间不存在对运行路径的**文件写入**，
所以“postinstall 需要跨越 preinstall 持锁”这个问题不再成立。

但这不等于互斥已经被证明：

- 它只保证“payload 写入不会落在运行路径上”，不保证“两个安装器不会同时安装”；
- 它不保证“发布的树是正确的预期版本”——清单校验不在该入口内；
- 动词漂移曾使整条链在真实安装时必然失败（exit 64），该问题已修，
  但只有真实安装才能最终确认，而这尚未发生。

准确说法是：**运行路径的写入窗口被移除，剩余风险转移到并发发布与制品校验。**

## 三、为什么这不等于"安装期间服务可用"

必须说清楚边界，否则会读成过度承诺。

- 客户端**可以**在安装期间继续运行，因为它的程序树没有被改写。这是移除写入窗口的直接收益。
  但注意：这描述的是该设计的意图，尚未在真机验证。
- 但**升级**仍然需要停旧实例：新程序要接管，旧实例不能边跑边被替换。
  这个停止顺序由升级协调器负责（方案 B，见 `client-macos-upgrade-recovery-contract.md`），
  与本文的安装器原子发布是两件事，不要混为一谈。
- 首次安装时没有旧实例，此时原子发布的收益最直接。

## 四、实现

### 4.1 原生原语

`native/client-macos-program-commit.h`，`commit_staged_program(stage, program, owner)`：

| 结果 | 含义 |
|---|---|
| `PROGRAM_COMMIT_OK` 0 | 重命名成功 |
| `PROGRAM_COMMIT_REFUSED` 1 | 前置检查拒绝，未做任何改动 |
| `PROGRAM_COMMIT_UNKNOWN` 2 | 重命名调用失败，无法区分"未发生"与"已发生但未验证" |

拒绝条件（负向门禁，不是正向证明）：

- stage 不存在、不是目录、owner 不符、对 group/other 可写；
- 运行路径的父目录不是合法的 bound directory；
- stage 与 program 相同，或互为祖先/后代（避免自我交换或摧毁运行树）；
- 运行路径存在但不是目录；
- 交换前重新校验父目录（防止检查期间被换成更宽权限的目录）。

**它不做什么**：不构建、不修复、不用外部清单校验、不注册、不启停、不回滚、**不取管理锁**。
不取锁是刻意的——取锁会让读者以为并发安装器已被排除，而这个入口无法验证这一点。

### 4.2 入口

`native/client-macos-commit-main.c` → 树内 `bin/ai-device-client-commit`：
root 身份检查 → 一次调用 → 输出 token（`PROGRAM_COMMITTED_V1` / `PROGRAM_COMMIT_REFUSED_V1` / `PROGRAM_COMMIT_UNKNOWN_V1`）。
参数错误 exit 64，非 root exit 70，且**在身份检查处就返回，不会触达原语**。

**为什么是独立二进制，而不是复用已有的两个入口**：

- `ai-device-client-launcher` **不能**用：它是固定 exec 包装器，忽略 argv，硬编码 exec
  `/opt/ai-device-client/runtime/bin/python3 -m device_runtime.client_macos_entry`，根本无法承载子命令。
- `client-macos-installer.m` **不能**放进 payload：它通过 `#include "resource-table.h"` 嵌入由
  **已完成的树**生成的清单，构成自举循环——payload 里要包含一个由 payload 自身内容编译出的产物。

所以这个入口保持最小、零外部清单依赖：它只重命名一个目录，不需要知道自己搬的是什么。

### 4.3 包结构

- payload 落在 `/opt/ai-device-client-stage`，构建脚本**拒绝**任何使 stage 与运行路径重叠的配置。
- `postinstall` 调一次 `bin/ai-device-client-commit`，把退出码原样写入 `/var/tmp/ai-device-client-commit-status`。
- 该状态文件是**报告**，不被任何逻辑读取来驱动行为。
- 失败不重试：退出码无法区分"未发生"和"已发生但未验证"，重试可能二次交换。

## 五、已知限制

1. **`RENAME_SWAP` 是交换，不是移动。** 旧树会留在 stage 路径。这需要调用方处理（清理或保留为回退证据），
   当前入口不删它——删除属于不可逆动作，不应由安装脚本顺手做。
2. **命令行参数可能出现在进程列表中。** 路径本身不是凭证，但这是可观察性事实。
3. **并发安装器未被排除。** 原子发布保证"要么旧树要么新树"，不保证两次并发安装的结果顺序。
   真正的串行化仍需安装器/包管理器层面或管理锁，而那需要能跨进程持锁的调用方。
   实测（macminim6）：并发安装两次一成一败，失败方报“脚本执行出错”而非明确的“已有安装器在运行”，
   `/opt` 下残留两个 stage 且程序树 `build_id` 属更早的包。即并发下状态会乱且失败原因不可读，
   仍是未闭合项。
4. **已实测（版本 3 修正）。** shipping PKG（`build-client-pkg.py`）已在 macminim6 完成
   A→B 升级、忙碌拒绝、卸载重装与登录生命周期四项真机验收，postinstall 与
   `RENAME_SWAP` 均实际执行过。inspection-only 包（`build-client-nopayload-inspection.py`）
   的 preinstall `exit 1` 是那个产物的设计行为，与本节结论无关。
   未覆盖：无配置的全新首装、多用户、并发。
5. **正向不可证。** 入口无法证明"stage 里就是期望的版本"，只有构建清单能证明；这里只做负向否决。

## 六、测试

`tests/test_client_macos_program_commit.py`（13 项，真实文件系统 + 真实 `renamex_np`）：

- 存在运行树时交换成功，且显式断言旧树留在 stage（承认副作用，不掩盖）；
- 运行树不存在时创建；
- stage 缺失、owner 不符、是文件时拒绝且运行树不变；
- stage 与运行路径相同 / 互相包含三种重叠形状均拒绝；
- 入口参数形状、非 root 拒绝、原语调用次数为 0。

**测试绕过 root 门禁的方式**：测试以当前用户运行，用相同 uid 构造 stage 与运行树，
直接调用原语并显式传入 owner；只有入口测试走真实 root 检查并断言其拒绝。
真实二进制端到端已确认 root 门禁生效（非 root 调用 exit 70 且两棵树均未变），
但**以 root 完成真实交换未验证**，因为本环境无 sudo。

`tests/test_client_pkg_build.py`（22 项）：payload 落在 stage 而非运行路径、
postinstall 存在且可执行、stage 与运行路径重叠配置被拒、postinstall 只调用一次且不重试。

新增的 `test_generated_postinstall_verb_matches_the_shipped_binary` 会**真实编译**
`client-macos-commit-main.c`，再用生成脚本里的动词调用它：匹配时停在 root 门禁
（70），旧动词 `commit-program` 返回 64。这修复了上一版“脚本断言自己写的文本、
入口测另一个未被打包的包装器”的自证问题。

**这个测试的边界**：它验证“生成脚本的动词”与“真实源码编译出的入口解析”契合，
但**没有执行真实 postinstall**、没有打包产物、没有 root 成功交换。`verb_from_header()`
是 Python 正则，不是 C 预处理器；头文件若被改为条件编译或重复定义，两侧解释可能不同。
所以准确说法是“当前值与解析一致且已被测试绑定”，而不是“结构上不可能漂移”。
