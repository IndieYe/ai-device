# 阶段 C：独立交付验收

日期：2026-10-22。范围为本地候选、隔离实例和制品验收；不代表正式发布或下游切换。

## 结论

阶段 C 的 macOS arm64 / Python3.13 本地验收路径已补齐；FS 最低版本 Python3.10 的核心/MCP 及制品行为已有独立验证。Linux/Windows 不在本轮实机验证范围，不能据此承诺三平台完整支持。CI 已配置但未推送、未远程运行。

## 可复跑命令

在仓库根目录：

```bash
uv sync --python 3.13 --locked --all-packages --all-extras --group dev
uv build --all-packages
uv run --no-sync python scripts/verify_artifacts.py --fs-python 3.10
# 下项仅在 macOS 执行，创建和收口自身临时测试进程
uv run --no-sync python scripts/verify_delivery.py
uv run --no-sync python scripts/capture_evidence.py
```

`verify_delivery.py` 不接入真实服务，不读取用户凭证，仅在临时目录构建 bundle、安装角色、启动 loopback 测试中心/客户端/MCP，并回收自身进程组。其他平台明确拒绝，不静默跳过后声称通过。

## 验收矩阵

| 范围 | 本轮结果 | 证据/限制 |
| --- | --- | --- |
| 三包源码测试 | Bash176、FS116、Device581通过 | 分包执行；不与制品测试混淆 |
| FS Python3.10 | 源码116通过；制品base14、MCP114通过 | 制品排除仅面向checkout的构建/Inspector用例 |
| sdist独立重建 | 三包成功 | 临时目录解压，无旧源码仓依赖 |
| 最终安装制品Device测试 | 581通过，45.51秒 | 独立venv和源码外测试目录；包括协议、安装语义和生命周期回归 |
| wheel源码一致性 | 三包所有Python模块匹配当前新仓文件 | candidate-manifest记录文件和wheel哈希；不是可重复二进制构建承诺 |
| 最小client | 独立安装实际导入、稳定runner连接中心通过 | 包含Bash/FS，不启用它们的MCP extra |
| 最小center | 独立安装、稳定runner HTTP认证查询通过 | 无Bash/FS |
| 最小mcp | 独立安装、稳定runner stdio初始化/12工具/设备列表通过 | 无Bash/FS |
| 最小ops | 独立安装通过 | 无fastapi、mcp、Bash/FS；完整操作由Device制品回归覆盖 |
| bundle构建/inspect | 真实候选wheel通过 | 三包来源和依赖pins显式记录 |
| instance install/inspect | 真实三角色安装和环境闭包验证通过 | 标准安装器，不是直接pip导入替代 |
| instance prepare/activate/rebuild | 真实临时实例通过 | prepare不切换active；activate后环境可用 |
| instance rollback/backup/restore | 真实临时实例通过 | 仅测试数据，不是生产备份/回退计划 |
| 运行中prepare | 拒绝且active不变 | 稳定runner持有真实共享租约 |
| 无效release/损坏wheel | 拒绝且active不变 | 仅篡改临时bundle |
| 平台/角色拒绝 | Linux bundle仅client，macOS装Linux center被拒绝 | 不证明Linux实机安装 |
| 依赖、metadata负向 | Device完整测试覆盖 | test_bundle/test_installation的隔离fixture及真实环境校验器；不得说全部均为真实制品安装故障注入 |
| 第三方许可 | 77个环境依赖有许可字段/分类或声明文件，无缺失项 | 自动元数据核查，不是完整法律意见 |
| 文档/示例 | 修复已发现失效链接、命令名及旧wheel文件名 | 历史平台记录明确标注，不视为新仓动态验证 |

## 平台与角色支持边界

- macOS arm64 / Python3.13.12：本轮真实wheel最小client/center/mcp/ops和标准bundle生命周期验证。
- FS Python3.10.20：本机base/MCP、源码回归和独立wheel行为验证。不是所有Python版本与CPU矩阵证明。
- Linux：核心已有代码和来源历史测试；当前标准bundle声明仅client。新仓Linux CI配置存在，远程未运行。本轮只动态验证目标声明/拒绝，不将其写成Linux实际安装支持证据。
- Linux center/mcp可有独立核心运行路径，但不属于当前标准bundle角色；不得混为完整交付支持。
- 原生Windows：不在当前标准bundle平台范围；WSL不得称为原生Windows。
- CentOS依赖离线bundle：保留历史说明及相关回归，未重建系统镜像或重新执行实机验收。

## 许可、来源与交付身份

- 本项目三包MIT，包内LICENSE进入sdist/wheel。
- 第三方声明正文原样保存，保留其尾随空白与RST标题分隔线；Git空白/冲突标记检查对此文件单独人工核对，其余文件正常检查。
- `source-manifest.json`记录原始来源；`evidence/candidate-manifest.json`记录新候选三包源码、制品哈希、锁文件、Python/平台与最小角色实际解析依赖。
- `evidence/third-party-inventory.json`为当前开发/验证环境依赖元数据；根`THIRD_PARTY_NOTICES.md`收集可获取的许可/声明正文。最小角色依赖子集见delivery报告，不把77个环境包都宣称为客户端必需。
- Device build_id仍仅表示Device源码指纹，不包含Bash/FS。源码commit可能早于未提交修订，候选以固定文件哈希和制品哈希补足，不凭commit单独推断全部内容。
- 源码声明扫描和依赖元数据清单未发现缺失许可项目；未进行第三方代码逐行溯源、法律审计或注册表权限验证。这些不应伪装为已完成。

## 后续与未授权操作

阶段D（正式发布/宿主切换）、E（旧源码退出维护）、F（真实环境升级）均未执行。GitHub push、PyPI发布、宿主依赖修改、旧目录删除及真实服务动作均需对应授权。对外发布前仍需冻结正式版本、注册表归属、完整发布组合与授权；不能直接把本地候选版本当作已发布版本。
