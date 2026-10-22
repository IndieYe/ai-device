> 本文件是现有全局使用技能的本地化修订稿，供部署升级时同步；本轮未覆盖实际工作目录的技能。

---
name: fs-usage-guide
description: 指导如何高效、安全地使用 Filesystem MCP 的 fs 工具完成本地文件读写与编辑。用户提到“fs 工具怎么用”“read/write/edit/replace/insert 参数”“大文件分段读取”需要先预览 diff 再改文件时使用。
---

# FS 使用指引

## 执行位置

所有工具只操作服务所在机器的文件系统。工具的 SSH `host` 参数已删除；旧请求携带该字段（包括 null 或空字符串）会在文件操作开始前被拒绝。MCP SSE 监听地址 `--host` / `MCP_HOST` 不是 SSH 参数，仍然保留。

## 工具选择

| 场景 | 优先工具 |
|---|---|
| 读取局部内容或大文件分页 | `read_file` |
| 新建文件，或已明确知道完整目标内容 | `write_file` |
| 单文件一个或多个精确替换 | `edit_file` |
| 按唯一锚点、行号或文件末尾插入 | `insert_file` |
| 多文件原子修改，或新增、删除、移动文件 | `apply_patch` |
| 单文件存在大量重复结构 | 优先 `edit_file`，不要依赖弱上下文 patch |

单文件即使有多个修改点，也优先使用 `edit_file`；不要因为改动行数多、修改点多或看起来“复杂”，就默认使用 `apply_patch`。只有跨文件原子变更、文件结构操作，或修改天然适合用多个唯一 hunk 表达时，才优先使用 `apply_patch`。

## 常用参数

- `read_file(path, type="chars", offset=0, limit=null, direction="forward", max_chars=10000)`
- `write_file(path, content)`
- `edit_file(path, edits, dry_run=false, include_diff=false)`
- `insert_file(path, text, line=null, before=null, after=null, at_end=false, dry_run=false, include_diff=false)`
- `apply_patch(patch, dry_run=false)`

### 额外说明

- `read_file.type` 可选：`chars` / `lines`
- `insert_file` 的 `line` / `before` / `after` / `at_end` 必须四选一
- `insert_file.after` 是“插在匹配字符串之后”，不会自动跨过原有换行；如果想插到下一行，把换行包含进 `after`，或改用 `line` / `before`
- `edit_file` 按 `oldText -> newText` 顺序逐条替换，每条只应用一次；`oldText` 应覆盖足以唯一确定目标的完整局部片段
- `write_file` 更适合“我已经明确知道完整目标内容”的场景

## 推荐流程

1. 先用 `read_file` 读取目标位置的最新上下文
2. 单文件精确修改优先 `edit_file` / `insert_file`
3. 已知完整目标内容时优先 `write_file`
4. 跨文件原子变更或新增、删除、移动文件时使用 `apply_patch`
5. patch 的每个 hunk 都应包含稳定且唯一的上下文
6. 复杂、高风险、上下文可能不唯一，或用户要求预览时先用 `dry_run=true`
7. 非必要不要开 `include_diff=true`



## 大文件读取

推荐先小窗口读取，再按 `offset` 分页：

```json
{"path":"/var/log/app.log","type":"lines","offset":0,"limit":200}
```

建议：

- 先读较小范围
- 确认需要继续时再增加 `offset`
- 不要一开始就拉整个大文件
- 想看尾部时可结合 `direction="backward"`

## `dry_run` / `include_diff`

### 什么时候用 `dry_run`

- 多文件修改
- 删除、移动文件
- patch 上下文不够稳
- 用户要求先预览

### 什么时候用 `include_diff`

- 用户明确要看 diff
- 需要确认插入点或替换结果
- 配合 `dry_run=true` 做一次性检查

默认都尽量少开，避免增加返回体积。`dry_run=true` 可以提前发现 patch 无法唯一定位，但不会自动消除上下文歧义。

## `apply_patch` 关键规则

外层必须是：

```text
*** Begin Patch
...操作块...
*** End Patch
```

支持：

- `*** Add File: <path>`
- `*** Delete File: <path>`
- `*** Update File: <path>`
- `*** Move to: <new_path>`（仅在 `Update File` 中可用）

关键规则：

- `Add File` 块内每行都要以 `+` 开头
- `Delete File` 只有头部，不能跟正文
- hunk 以 `@@` 开头
- hunk 行前缀规则：
  - 空格：上下文
  - `-`：删除
  - `+`：新增
- `Update File` 使用 hunk 的旧内容与上下文定位目标，不是按行号盲目修改，也不会在歧义时猜选位置
- 上下文找不到或不能唯一定位时会失败
- patch 整体是原子的：任意一步失败，之前改动会回滚

### Hunk 上下文必须唯一

`apply_patch` 通过 hunk 中的旧内容和上下文定位修改位置。目标文件中存在多个候选位置时，会返回 `PATCH_CONTEXT_AMBIGUOUS`，而不是猜测修改其中一处。

编写 hunk 时：

- 不要只使用 `return`、空行、括号、通用赋值、重复 SQL 片段等弱上下文
- 上下文应包含方法签名、类名、唯一变量名、独特字段或相邻调用
- 每个 hunk 尽量只修改一个局部语义单元
- 修改前读取目标位置的最新内容；不要使用较早读取或其他修改前的旧快照生成 patch
- 同一文件有多个相似结构时，优先改用 `edit_file`，并让 `oldText` 覆盖足以确定目标的完整局部片段

上下文不是越多越好，关键是包含稳定且唯一的锚点；大段重复模板或 SQL 仍可能产生歧义。

示例：

```text
*** Begin Patch
*** Update File: /abs/path/app.py
@@
-host = "localhost"
+host = "0.0.0.0"
*** End Patch
```

## 编辑失败后的处理

### `PATCH_CONTEXT_AMBIGUOUS`

含义：某个 hunk 在目标文件中匹配到多个候选位置，工具无法安全决定修改哪一处。

处理顺序：

1. 不要原样重复调用同一个 patch
2. 重新读取目标位置附近的最新内容
3. 搜索旧文本在文件中的出现次数和位置
4. 为 hunk 增加方法签名、唯一字段等稳定上下文，或将大 hunk 拆小
5. 如果仍存在重复结构，改用 `edit_file`，以更完整的 `oldText` 精确描述目标局部
6. 修改成功后再继续处理后续位置

不要通过“选择第一个候选位置”绕过歧义，因为这可能静默修改错误代码。

### 上下文找不到

重新读取最新文件。补丁可能依据旧快照生成、目标已被其他修改改变，或相同改动已经落盘；不要原样盲目重试。

### 多 hunk 中途失败

`apply_patch` 是原子操作，失败后应按未修改的文件重新检查和生成补丁，不要假设前面的 hunk 已经落盘。

## 注意事项

- 修改前读取最新上下文，不要盲改
- 单文件精确修改优先轻量工具，不按“复杂度”机械选择 patch
- 如果目标是整文件覆盖，优先考虑 `write_file`
- 如果不需要 diff，就保持 `include_diff=false`

一句话准则：**先读取最新上下文；单文件精确修改优先 edit/insert，只有跨文件原子变更或文件结构操作才优先 patch；patch 的每个 hunk 必须能由稳定上下文唯一定位。**
