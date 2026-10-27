# P1 原生运行时构建输入

本目录保存准确的构建输入锁，不包含运行时二进制或用户凭证。当前是 P1 的运行时准备子模块，**不是已经完成的 Device 原生安装包**。

## 构建锁

`runtime-lock.json` 固定 python-build-standalone release `20261021` 的 CPython `3.13.17` install_only_stripped 制品：

- macOS arm64：25,286,365 字节，SHA256 `8dc60610a82f1e73be7faf964383e02520f95f6e39592a5c37c3809f5a7b798e`。
- Linux x86_64：35,356,674 字节，SHA256 `f829adc9d888e76c8801b4799ed444188d35239fcae3a9808a99ec5cb0d8d92c`。

版本、大小及摘要在 2026-10-26 从上游 release API 获取；正式对外发布仍需由可信发布流程认证准确输入。模块不联网，不接受 latest 或不匹配的上游 release。

## 离线准备命令

先由构建流程将锁中的准确 URL 下载到构建输入目录，完成后在匹配的目标系统执行（路径替换为实际绝对路径）：

```bash
uv run --no-sync python -m device_runtime.native_runtime \
  --lock packages/device-runtime/native/runtime-lock.json \
  --archive build/native-inputs/cpython-linux-x86_64.tar.gz \
  --target linux-x86_64 \
  --destination build/native-runtime-linux
```

macOS 将 target 改为 `darwin-arm64`，使用对应归档。目的目录必须不存在；已存在目录拒绝覆盖。模块校验平台、大小和摘要，限制解包成员及预算，使用 Python data filter 拒绝逃逸链接，真实探测解释器版本及 ssl/sqlite3/ctypes。移动到另一个目录后再次探测，再在本地文件锁内发布到新目的目录。失败清理本次 staging，不修改旧实例、active、服务或用户数据。

该命令由开发／构建 Python 引导；终端用户的免 Python/uv 安装承诺要由后续包内 bootstrap 完成，不能把此命令当作最终用户安装方法。该模块是同步构建工具，不能直接在异步 HTTP 路径调用。

## 许可与完整分发清单

锁中 `license_url` 指向 python-build-standalone 构建项目的 MPL-2.0 许可，不等于 CPython 或整个分发的全部许可证。解释器、OpenSSL、SQLite、libffi、压缩库、pip 及其 vendored 库需要按准确平台制品形成完整再分发清单，并保留归档内 LICENSE 文件。

上游分发文档：`https://github.com/astral-sh/python-build-standalone/blob/20261021/docs/distributions.rst`。install_only 归档不是完整构建元数据的替代品；正式原生包产出前还须关联对应完整构建元数据／许可文件并完成第三方声明。当前不得宣称第三方许可已收口或据本锁直接发布。

## 包事务实验

`../scripts/probe-native-deb-gate.sh` 与 `probe-native-rpm-gate.sh` 只用于显式选择的一次性测试容器，探测 preinst/pretrans 的拒绝时序。实验只安装名称为 `ai-device-p1-transaction-probe` 的无服务 fixture 包，测试失败更新不覆盖旧文件、不改变旧版本，原版本重试可收敛。脚本要求 `DEVICE_NATIVE_DISPOSABLE_PROBE=yes` 和容器 root，不能在宿主或正式机器运行。

实验不等于正式 helper、Device 包构建、实例锁和签名发布已完成，也没有证明 systemd、登录／机器重启或中心升级。当前阶段状态和准确结果见仓库 `docs/native-lifecycle-implementation.md`。

## P1 Linux开发包主路径（进行中）

新增 `native_install.py` 调用现有 `install_instance`，不复制release生命周期。安装器必须由选定普通用户执行，拒绝root创建实例；包内自带runtime、uv及离线client依赖。`installation.install_instance(runner_python=...)` 在发布active前写入绝对解释器shebang，旧调用不传参时行为保持。

构建入口：`../scripts/assemble-native-payload.py` 从三包wheel、client锁定约束和依赖wheel目录组装bundle，验证 `tool-lock.json` 内uv准确文件名/大小/摘要；`../scripts/build-native-linux.sh` 在独立amd64 Linux构建环境使用包内锁定的 uv，隔离环境、无网络且无依赖解析地把显式 wheel 安装到指定运行时，产出DEB/RPM。运行时归档在执行前验证锁定大小和SHA256。包是**未签名开发试制品**，固定开发版本布局，尚不支持更新/卸载已存在实例。现已包含独立helper bootstrap及显式systemd部署入口，包安装本身不自启；普通用户启动、UID隔离及socket显式恢复已在隔离Linux验证，契约与未完成边界见 [HELPER.md](HELPER.md)。

开发包当前安装入口 `ai-device-native-install --root <new-instance> --instance-id <id> --server-url <https-center>` 必须由普通用户运行，父目录须事先授予该用户。它不启动服务、不配对、不要求输入长期Token。`--server-url` 目前只写入连接配置；实际配对由P2实现。

包更新在实例或原payload已存在时拒绝，包卸载在实例存在时拒绝，避免helper未实现时破坏现有环境。此拒绝行为不能当作已经完成“更新和卸载”；后续必须用准确事务和显式数据保留语义替换。直接实例安装失败仍保留现场，不承诺删除目录重试可恢复。

构建产物位于忽略目录 `build/native-packages-v5/`，不推送或分发。构建中source_commit仅指本地基线，dirty开发wheel以组件SHA256标识，不当作正式准确源码发布。第三方运行时许可证、签名清单和完整SBOM仍未收口，禁止据此开发包宣称正式交付。

`accept-native-package.sh` 只挂载包和验收脚本，不挂源码；以容器root安装系统包，再由UID12345的明确普通用户创建实例并检查离线依赖与数据保持。不等于真实systemd、自启或主机恢复验收。`accept-native-payload.sh` 为无包组装调试入口，使用当前v5本地fixture，不用于用户安装。
