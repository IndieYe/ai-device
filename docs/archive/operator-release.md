# 本机 Operator 稳定入口与发布清单

状态：实现资产，尚未部署。适用本机 public-center MCP 和 ops，不管理远程中心、客户端、服务或数据库。

## 最终布局

```text
~/.device-runtime/operators/public-center/
  launch.py                 # operator/launch.py，调用方式不变：launch.py mcp
  runtime.py                # 两个入口共用的标准库解析器
  config.json               # server_url；旧 python 字段不再作为新入口事实源
  credentials.json          # admin_token；不放进 release
  active-release.json       # 单个绑定文件，一次指定两个角色
  releases/<release_id>/
    release.json
    wheels/*.whl
    mcp/venv/bin/python
    ops/venv/bin/python
~/.local/bin/device-ops       # operator/device-ops
```

release_id 只接受字母、数字、点、短横线、下划线，首字符为字母或数字，最多128字符。建议版本＋制品哈希前缀；不能只依名字判断身份。

`active-release.json` 契约：

```json
{"schema_version": 1, "release_id": "0.2.2-<hash-prefix>", "manifest_sha256": "<release.json原始字节SHA256>"}
```

绑定文件、config和credentials必须是当前用户拥有的0600普通文件。根目录建议0700。release环境在最终路径新建，不能移动已有venv。

两个入口都解析同一绑定，不回退到config.python、staging或candidate。release.json必须与绑定中的摘要相同；角色解释器固定为对应`<role>/venv/bin/python`。这不是OS安全隔离或恶意本地代码防护。venv解释器自身允许链接到真实Python。

入口清理继承的Python路径及Device凭证/连接变量，然后从私有profile注入凭证与固定中心地址。两角色均使用`-I -m`。ops拒绝连接覆盖参数及其缩写，本地参数边界由候选角色环境中的真实CLI解析器只读解析；支持REMAINDER远程argv（有无显式`--`均可），远程参数不作本地help或连接覆盖解析。解析子进程不调用run_operations，不联网；参数通过stdin传递，输出仅为脱敏分类，失败关闭。ops help不读取凭证且不继承Device凭证。失败统一退出2，避免输出配置、异常和凭证。绑定不可用也不能靠help绕过。

## 清单生成（未来准备阶段使用，不是本轮部署指令）

在已经准备好的最终release目录上：

```text
uv run --no-sync python scripts/operator_manifest.py <release-directory>
```

工具不安装、不连接中心、不写active绑定、不复制凭证、不修改现有profile。仅运行所指定候选环境的Python，以`-I -B`获取解释器版本、平台、架构、已安装distribution清单和Device的Python源码摘要；探针无网络或业务模块导入。Device源码集合通过实际扫描安装包目录获得，不依赖RECORD枚举，额外未登记`.py`同样参与比较；editable布局明确拒绝。候选环境必须可信，命令不是执行不可信解释器的沙箱。

release.json schema_version=1，包含release_id、roles、wheels。roles记录角色相对解释器路径、Python版本、平台、架构、依赖名称版本和Device源码摘要。wheels记录文件相对路径、大小和SHA256。必须恰有一个ai-device wheel，两角色ai-device版本一致，且已安装Device全部`.py`文件与wheel对应源码精确匹配。无wheel、版本/源码不一致、解释器探针失败即拒绝生成。

**验证边界**：这证明记录时Device Python源码与所保留wheel相符，不证明全部依赖由所保留wheel安装，也不替代完整依赖锁、RECORD/签名审核或功能验收。依赖清单不是完整可恢复bundle；release不是数据备份。清单生成后不允许继续修改环境；启动只验证清单摘要和入口可用性，不每次重算全部安装文件。环境被修改须重新准备、验收和生成新release，不能覆盖旧清单。

生成器在同目录写完整0600临时文件并fsync，再以硬链接无覆盖发布release.json；已有清单会拒绝。文件系统须支持同目录硬链接。失败不留下半个公开清单；目录项断电持久性不作为事务保证。无active绑定写入器，也不自动扫描或选取“最新”版本。

## 部署门禁（本轮不执行）

1. 固定准确wheel、依赖和最终路径，分别准备两个环境。
2. 生成清单并隔离验收，记录准确manifest哈希。
3. 另获正式切换授权：部署入口与runtime.py，原子更新active绑定。凭证原地保留。
4. 新ops调用即使用新环境；已有MCP进程仍使用旧路径，须通过宿主支持的连接重建或另行授权的安全重启切换。
5. 核对新MCP进程路径、工具发现、版本与只读目录查询；核对ops只读查询和连接覆盖拒绝行为。清单/健康检查不能替代真实调用链验收。
6. 旧目录仍保留。另查所有配置、角色和进程引用，列精确清理名单，再取得删除授权。

本阶段没有修改安装生命周期、发行版本、数据库、正式配置、运行凭证或服务；没有自动切换、回退或删除命令。
