# stage 路径唯一化小方案

> 版本：2（2026-11-05）｜状态：**已实施，离线验证通过，未提交**
> 关联：`doc/client-macos-real-install-verification-plan.md` §9.2（C1/C2）
>
> 本文描述改动方式与边界。§10 记录实施结果。
> **未做真实 PKG 构建与安装**，真实安装验证归到验证方案的 S1。

## 1. 要解决的问题

`/opt/ai-device-client-stage` 是固定单一路径。安装器把 payload **原地写入**该目录
（`build-client-pkg.py:21` 注释：`The installer writes its payload in place`）。

commit 使用 `RENAME_SWAP`，旧树回到 stage 路径。cleanup 当前被禁用，旧树留在原地。

于是第二次安装的实际行为是：

```
installer 把新 payload 合并写入含旧树的 stage
  → commit 一次 RENAME_SWAP
  → 混合树成为 live 程序
```

安装器不会删除 payload 中不存在的文件，因此**旧版本有、新版本已删除的文件会残留在
live 程序树里**。commit 的检查是负向门禁（owner / 权限 / 目录类型），不校验清单，
所以它发现不了这一点。

这不是"少一个清理功能"，是重装与升级的主路径会静默产出错误结果。

## 2. 核心思路

stage 路径按构建唯一化：

```
/opt/ai-device-client-stage
  → /opt/ai-device-client-stage-<包版本>-<build_id[:12]>
```

目标：让每次安装尽可能写入一个全新空目录，把正确性从“运行时删除旧树”换成
“路径结构”；前者是四轮评审都没做对的时序控制。

**但它不是无条件的。** 路径只在包版本或源码变化时才变化，因此：

| 安装序列 | 结果 |
|---|---|
| A→B 升级 | 路径不同，安全 |
| 同版本第 1 次重装 | 路径不存在，安全 |
| **同版本第 2 次重装** | **路径含旧树，仍会混树** |

完整推导见 §3.3 “已知限制：同版本重装两次后会再次混树”。

一句话：本方案把混树从“每次重装都发生”缩减到“同版本反复重装才发生”，
**缩小了范围，没有消除问题**。

## 3. 改动边界

### 3.1 不需要改的地方（已核实）

| 位置 | 结论 | 依据 |
|---|---|---|
| `managedPaths()` | **不含 stage 键** | `client-macos-identity.m` 只定义 program / management / directory / runtime_lock / start_gate / config / plist / service |
| commit 原语 | **stage 由 argv 传入** | `client-macos-program-commit-entry.h:28`、`client-macos-commit-main.c:78` 均用 `argv[2]` |
| commit 校验逻辑 | **不关心路径拼写** | 只查目录、owner、权限位、与 program 的 resolved 重叠 |
| cleanup 原语 | 同样接 argv | 禁用状态不变，无需动 |

这一点是整个方案成立的基础：**C 侧对 stage 路径无任何硬编码**。

### 3.2 需要改的地方

| # | 文件 | 改动 |
|---|---|---|
| 1 | `scripts/build-client-pkg.py` | `STAGE_PATH` 由常量改为按 `build_id` 生成 |
| 2 | `scripts/build-client-pkg.py` | `build()` 已接受 `stage` 参数（第 161 行），保持参数化，默认值改为由 build_id 推导 |
| 3 | `scripts/build-client-pkg.py` | `POSTINSTALL` 的 `{stage}` 占位符已存在，无需改模板 |
| 4 | `tests/test_client_pkg_build.py:165` | 断言 `"/opt/ai-device-client-stage" in postinstall` 改为匹配新前缀 |
| 5 | `doc/requirements/client-native-install-upgrade.md` §154 | 更新 stage 路径表述 |

改动集中在 1 个构建脚本 + 1 条测试断言 + 1 段需求文档。原生代码零改动。

### 3.3 build_id 的来源（已核实）

**不需要新建标识，已有权威定义。**

`scripts/build-client-tree.py:73-102` 的 `write_client_identity()` 生成并写入
`<tree>/client-identity.json`：

```python
identity = {"version": versions[0], "build_id": "sha256:" + digest.hexdigest()}
```

其中 `digest` 是 `runtime/lib/python3.13/site-packages/device_runtime` 下全部 `*.py`
按排序、长度前缀拼接后的 SHA-256。该文件随 tree 进入 payload。

**pkg 阶段可直接读取**：`build-client-pkg.py:177` 已在读同目录的 `build-manifest.json`，
且 payload 由 `copytree(tree, ...)` 生成，因此 `build()` 内读
`tree / "client-identity.json"` 无需新增任何构建输入。

#### 最终路径形态

```
/opt/ai-device-client-stage-<包版本>-<build_id 前 12 位 hex>
例：/opt/ai-device-client-stage-0.3.4.12-a453baca9c3a
```

- **版本用包版本，不是 identity 的 application 版本**。实施时纠正：identity 的
  `version` 是应用版本（`0.3.4`），跨包重建不变，单靠它无法让同源码不同包版本
  的路径区分开——那样混树风险会原样返回
- 去掉 `sha256:` 前缀：冒号虽在 APFS 合法，但出现在 shell 拼接、日志与 `ls`
  输出中会造成困扰
- 12 hex = 48 bit，单机自用场景碰撞风险可忽略
- 长度约 50 字符，远低于 `PATH_MAX`
- 与新程序路径的字符串校验兼容：`build-client-pkg.py` 的前缀排他判断不会被命中
  （`/opt/ai-device-client-stage-…` 不以 `/opt/ai-device-client/` 开头）

#### 构建期校验（已实施）

原方案写的是 `identity["version"] == version`，**这是错的**：

| 值 | 实例 | 含义 |
|---|---|---|
| 源码 `__version__` | `0.3.4` | 应用版本 |
| `client-identity.json` 的 `version` | `0.3.4` | 同源码 `__version__` |
| pkg `--version` 参数 | `0.3.4.12` | 包构建版本，四段数字 |

等值断言会让每次正常构建都 `ValueError`。实际实现改为**包含关系**：

```python
if version != application and not version.startswith(application + "."):
    raise ValueError(...)
```

即包版本必须等于应用版本，或以「应用版本 + `.`」开头（`0.3.4.12` 以 `0.3.4.` 开头）。

另加两道严格校验：

- **tree 无 `client-identity.json` 即拒绝**（不降级到弱路径名，否则恰好是
  「无法证明来源的树」共用 stage，重新引入同一 bug）
- **build_id 必须是 `sha256:` + 64 位十六进制**，否则拒绝；防止退化为空或短段

#### 已知限制：build_id 只覆盖 Python 源码

`digest` 的范围是 `site-packages/device_runtime` 下的 `*.py`，**不覆盖**：

- 原生二进制（launcher、commit entry、Python 解释器）
- 第三方 wheel（仅记录在 `build-manifest.json` 的 `application_wheels`）
- 运行时非 Python 文件

因此只改 native 代码、不改 Python 源码时，build_id 不变。
**但路径不会因此重复**——包版本段仍会变化，只要包版本递增。

剩余风险只剩：**同包版本 + 仅 native 变更**这一种组合。它需要人工刻意构造
（同一 `--version` 重打包含不同 native 代码），正常构建流程下不会出现。

处理方式：**接受，作为已知限制**（备选方案 A）。理由——`build_id` 是客户端身份
标识，可能参与 ready 精确匹配等逻辑，扩大其覆盖范围的影响面远超本次问题。

不在本方案处理：若后续要求 native 变更也必须区分 stage，需要单独评估。

#### 已知限制：同版本重装两次后会再次混树

这是本方案**最重要的边界**，也是最初设计时漏掉的场景。

##### 已查证：Installer 不会清理 stage 里的旧文件

`bom(5)`（本地手册原文）：

> The Mac OS X Installer uses a file system "bill of materials" to determine
> which files to **install, remove, or upgrade**.

BOM 逐条记录包自己 payload 里的每个路径。实际制品示例（`mac-fixed-pkg3`）：

```
./opt/ai-device-client/bin/ai-device-client-launcher  100755  51016
./opt/ai-device-client/build-manifest.json            100644  716
```

推论：**BOM 不包含的文件，Installer 不知道它们存在。**「remove」只针对
BOM 记录的路径，所以 stage 目录里「新版本已不包含」的旧文件删不掉，会原地保留。

准确地说，Installer 不是“主动合并”，而是“不清理”；结果与合并等价。

##### 安装序列推演

| 次序 | stage 路径状态 | commit 语义 | 结果 |
|---|---|---|---|
| 1 | 不存在，installer 新建 | `RENAME_EXCL`（program 不存在） | stage 被移走，路径消失 |
| 2 | 不存在，installer 新建 | `RENAME_SWAP` | **旧树留回 stage** |
| 3 | **含第 2 次的旧树** | installer 写入 payload，旧文件残留 | SWAP 发布**混合树** |

因此：

- **A→B 升级**（包版本变化）：路径不同 → 安全 ✓
- **同版本第 1 次重装**：路径不存在 → 安全 ✓
- **同版本第 2 次重装**：路径含旧树 → **混树** ✗

路径唯一化只对「包版本变化」生效。**同版本反复重装不在保护范围内。**

##### 一个尚未确认的缓冲

`build-client-pkg.py` 会读取 `lsbom` 输出并校验 `fields[2] == "0/0"`：

```python
if len(fields) < 3 or fields[2] != "0/0":
    raise ValueError("package payload ownership is not root:wheel")
```

如果混入的旧内容 uid ≠ 0，该检查会失败，混树以**安装报错**而非静默方式暴露。
但这是否真会发生取决于旧树的实际归属，**未经真机验证**，不得当作保证。

##### 为什么不在这里继续修

把 stage 路径做得更唯一（加时间戳、加随机数）会失去可复现性；改回「删除旧树」
又会回到四轮评审都没做对的运行时序控制。两者都不在本次范围。

**当前定位：已知限制，写入验收口径。** S4 场景按三连装设计，预期第 3 次失败。

## 4. 落地后验收口径的变化

| 项 | 变化 |
|---|---|
| **C2**（旧树不污染下次 payload） | **部分满足** —— 包版本变化时满足；同版本反复重装不满足（见 §3.3）。**不能标为“不再需要”** |
| **C1**（旧树被清除） | 从「正确性必需」降级为「正确性部分必需」：单一重装不需要，反复重装需要 |
| S4 同版本重装 | **仍适用，且预期第 3 次失败**。应按三连装设计，记录前两次成功与第三次的混树证据 |
| S5 A→B 升级 | **适用**，包版本变化，路径唯一化覆盖 |
| S12 并发安装 | **不受影响** —— 两个 installer 并发时，若包版本与 build_id 相同仍会写同一 stage。并发互斥是独立缺口，本方案不解决 |

#### S4 的测试方式需重写

原表述「验证即使旧 stage 残留，payload 也干净」**是错的**——新路径下
旧 stage 残留就是混树原因，不是可绕过的背景条件。正确设计：

```
1. 装 0.3.4.12          → 记录 live 程序树的完整文件列表
2. 同包重装（第 1 次）    → 预期成功；记录 stage 是否已含旧树
3. 同包重装（第 2 次）    → 预期失败或混树
   ├─ 若安装报 ownership 错误 → 混树以失败暴露（较好）
   └─ 若安装成功 → ls live 树，对比第 1 步列表，多出的文件即混树证据
```

第 3 步无论哪种结果都是有效证据，必须完整记录，不得因“预期失败”就跳过。

#### 阶段结论的影响

C2 从「已满足」降为「部分满足」，因此：

> macOS 阶段结论仍只能是「未完成交付」。stage 唯一化缩小了问题范围（A→B 已安全），
> 但没有消除它（同版本反复重装仍会混树）。

若验收要求覆盖「同版本反复重装」，则必须另做设计（协调器提交回执、或改需求为
「检测到残留即拒绝安装」）。这不再是“清理功能要不要做”的取舍，而是**必需能力**。

S12 这一点必须写清楚：**路径唯一化不解决并发**。两个同版本包同时安装仍会冲突。
那一项仍是未实现。

## 5. 代价

### 5.1 磁盘残留

每次安装留下一个旧 stage 目录（root 所有）。本机当前**无任何残留**：

```
/opt/                     只有 homebrew
/opt/ai-device-client*    No such file or directory
/Library/.../management   No such file or directory
```

因此第一次安装的基线是干净的，残留是纯未来问题。

**决定：本次不实现自动回收。** 理由：

1. 第一次安装基线干净，无历史包袱
2. 固定 stage 每版本一个，量级可控（几十到几百 MB/版本）
3. 回收涉及 root 权限与删除操作，正是本项目历史上出问题的领域
   （`link/.` 删除 target 目录即属此类），过早实现会把「删一个 build_id 路径」
   变成新的安全面
4. `gc-stage` / `prune` / `stale stage` 在全仓零命中，没有任何现成基础可复用

**替代做法：人工回收指引（运维文档，非代码功能）**

```sh
ls -d /opt/ai-device-client-stage-*
# 逐个确认不是当前安装所用、且无进程引用后再删除：
# sudo rm -rf /opt/ai-device-client-stage-<旧的 version>-<build_id>
```

注意：旧 stage 属于 root，普通用户无法自行回收；需 sudo，且需逐次确认。

### 5.2 路径无界增长

stage 名带 build_id 后，`/opt` 下会逐步积累目录。这一点应在需求里写明，
避免日后被当作缺陷。人工回收是唯一手段，不设自动上限。

### 5.3 不在本方案范围的：per-pid stage 残留

存在**第二套** stage 机制，与 PKG 安装路径独立：

| | PKG 安装路径 | 原生安装器发布路径 |
|---|---|---|
| 入口 | `build-client-pkg.py` postinstall | `client-macos-installer.m` 的 `upgrade-registered-program` / `repair-registered-program` |
| 发布函数 | commit 动词 → `commit_staged_program` | `publish_first_program` / `publish_registered_target` |
| stage 路径 | `/opt/ai-device-client-stage`（固定，由 argv 传入） | `/opt/.ai-device-client-stage-<pid>`（per-pid，自行计算） |
| 谁写 | installer 原地写 payload | `copyfile` 现复制 |
| 旧树去向 | 留回固定 stage | 留回 `.…-<pid>` |

#### 调用链（已核实，不是实验代码）

```
publish_first_program
  ← publish_first_program_with_host_check  (first-host-publish.h:21)
  ← prepare_and_publish_first_program      (first-install.h:14)
  ← client-macos-first-entry.h:15
  ← client-macos-installer.m:19  #include

publish_registered_target
  ← upgrade_registered_bound_once  (registered-upgrade.h:92)
  ← repair-registered-program      (repair-entry.h:163)
  ← client-macos-installer.m:40  #include
```

`installer.m:52-58` 的动词分派实际生效：`observe-unregistered-program` /
`upgrade-registered-program` / `repair-registered-program` / commit。

该二进制确实被编译进当前制品：`.../expanded/Scripts/installer-inspection`。

**准确表述**：这是**活的生产代码路径**，不是实验机制；当前仅被 inspection 门禁
（preinstall `exit 1`）挡住无法执行。这一点上一版写成「尚未接入生产」是错的，已纠正。

（`writer-probe.c` 中的 `OFFLINE experiment only` 注释只适用于它自己的
`publish_guarded_tree` 实验入口，不代表上述生产函数。）

#### 不纳入本方案的理由

> `publish_first_program` / `publish_registered_target` 属于**原生安装器内部的完整
> 发布路径**，与 PKG postinstall → commit 是两个不同入口。stage 唯一化只改
> postinstall 传给 commit 的路径参数，不触及 installer 内部自行计算的 per-pid 路径。
> 把它们纳入意味着改原生发布逻辑，改动面性质完全改变。

这个理由不依赖「是否接生产」这个有争议的判断。

#### 已知缺口

`copyfile` 失败或校验失败时，`fail("...; stage retained")` 会保留整棵
`.…-<pid>` 树。全仓 `gc-stage` / `prune` / `stale stage` 零命中，无任何回收机制。
该缺口已登记到验证方案 §9.5.1。

## 6. 实施顺序建议

```
1. 确认 build_id 来源（§3.3）          ✅ 用 client-identity.json
2. 改 build-client-pkg.py 的 stage 推导  ✅ stage_for()
3. 改测试断言 + 补唯一性断言             ✅ 新增 5 条
4. 更新需求文档 §154                    ✅
5. 离线验证：构建测试与相关测试，不安装   ✅ 2042 passed
```

第 5 步不含真实安装，真实安装仍归到验证方案里的 S1，**尚未执行**。

## 7. 实施记录

### 7.1 实际改动

| 文件 | 改动 |
|---|---|
| `scripts/build-client-pkg.py` | `STAGE_PATH` → `STAGE_ROOT`；新增 `stage_for()` 与 `read_client_identity()`；`build()` 的 `stage` 默认改 `None` 时推导 |
| `tests/test_client_pkg_build.py` | fixture 补 `client-identity.json`；新增 5 条测试；６处无关的 `"1"` 版本字面量改为 `"0.3.4.1"` |
| `doc/requirements/client-native-install-upgrade.md` §154 | stage 路径表述更新为推导形式 |

原生代码**零改动**，与 §3.1 的核实一致。

### 7.2 验证结果

```
pkg 构建测试：27 passed
相关测试族（pkg/nopayload/stage_cleanup/macos_packaged）：80 passed, 3 skipped
Device 全量：2042 passed, 20 skipped (238.58s)
ruff check src tests scripts：All checks passed
```

### 7.3 变异验证

| 变异 | 结果 |
|---|---|
| `stage_for()` 退回固定路径 | **5 failed**（含两条新唯一性测试） |
| identity 缺失改为宽松回退 | `test_tree_without_identity_is_refused` 捕获 |
| 去掉版本包含校验 | `test_package_version_must_extend_client_version` 捕获 |
| 去掉 digest 格式校验 | `test_identity_build_id_must_be_a_sha256_digest` 捕获 |

四项全部被对应测试捕获，还原后 27 项全绿。

### 7.4 未做的事

- 未构建真实 PKG（需 macOS arm64 且当前制品不含本轮全部源码）
- 未安装、未执行 postinstall、未碰 launchctl 与真实服务
- 未提交（待用户授权）
- S12 并发安装、per-pid 残留、C1 清理均不在本次范围
- **未验证同版本反复重装的实际行为**（§3.3）——只做了静态推导，真机未测
- **未验证 ownership 检查是否真能拦住混树**（§3.3 的“尚未确认的缓冲”）

### 7.5 后续发现（提交 0b6f9a7 之后）

已提交时只认识到“包版本唯一化”。之后核实 `bom(5)` 与真实 BOM 内容，
确认：

- Installer 根据 BOM 决定 install/remove/upgrade，**BOM 外的残留文件不会被打理**
- 因此同版本第 2 次重装仍会混树
- 本方案的价值从“消除混树”降为“把混树局限到同版本反复重装”

代码无需修改（推导逻辑本身正确），但**文档与验收口径必须改正**。
本次文档修改即为此项。

## 8. 本方案不解决的问题

| 缺口 | 状态 |
|---|---|
| 并发安装互斥 | 未解决，S12 仍预期失败 |
| 锁所有权证明 | 未解决，与 stage 路径无关 |
| 后代进程清理 | 未解决 |
| 旧 stage 空间回收 | 明确不在范围内 |
| cleanup 原语的授权缺陷 | **不修复，保持禁用** |

cleanup 那 844 行仍在代码里、仍禁用。本方案的立场是：**让它们不再必要**，
而不是把它们修好。是否最终删除实现，是另一个决定。

## 9. 需要决定的事

已定：

1. ~~§3.3 的 build_id 来源~~ → 复用 `client-identity.json`，已完成
2. ~~磁盘残留如何处理~~ → 本次不实现回收，只写人工回收指引（§5.1）
3. ~~版本语义~~ → 路径用包版本，identity 版本只做包含校验（§3.3）
4. ~~是否走独立评审~~ → **需要**，但限定了范围与产出（见下）

待定：

1. **是否提交本次文档修正**（§3.3/§4 新增内容）
2. **评审如何组织** —— 建议只问一个题：
   “这个推导在哪些安装序列下会产生重复路径？”
   要反例列表，不要「通过/不通过」结论；一轮不迭代
3. **cleanup 去留** —— 现在性质变了：同版本反复重装下，清理从
   “磁盘优化”回到“正确性部分必需”。需重新判断是实现清理还是改需求
   （检测到残留即拒绝安装）

## 10. 与既有授权的关系

- 本文经用户授权已实施：代码与文档改动均已完成
- **未**构建 PKG、**未**安装、**未**操作服务、**未** push
- 所有既有边界继续有效：不部署、无 sudo、制品的临时目录全保留
- 提交需单独授权
