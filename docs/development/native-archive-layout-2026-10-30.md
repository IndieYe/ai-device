# 归档归属布局：2026-10-30 真实制品验收记录

本文件记录一次**已执行完毕**的真实制品构建与验收，制品本身已被删除以回收空间。
记录目的是让后续会话能判断"哪份源码对应哪次验证"，以及在需要时重建同样的输入。

已删除不等于未验证：下表的命令与结果均在 `--arch amd64` 一次性容器内实际执行。

## 制品指纹

三个包均为未签名开发制品（`development_build: true`），不是发布件。

| 制品 | SHA256 | 字节 |
| --- | --- | --- |
| `ai-device-native-client_0.2.2-1_amd64.deb` | `107df8f531e6c887ed1716aa133c24fe1912df88bd53350dad6520450f2d0e8d` | 87849706 |
| `ai-device-native-client-0.2.2-1.x86_64.rpm` | `70a54eaae379da462593835665e039bc52b9a51a21bf1eb69275b669ae85c9b3` | 87862211 |
| `ai-device-native-client_0.2.2-2_amd64.deb` | `acf789860aa90b01dc86857950670122975a79588fdff22daef73548debd6ca0` | 87834362 |

-2 包与 -1 包使用同一 payload，只有 tree 名（`0.2.2-native2`）和包版本不同，用于验证升级路径。

## payload 输入

`build-inputs.json` 的 SHA256 为 `4b0643613b36465b2fc8d7295bae21012264636dc5f1ff1efd2de76178bef698`，内容：

| 项 | 值 |
| --- | --- |
| `source_commit` | `06ea4bde719fc51443b3967a3aa184904d99907c` |
| `uv_wheel` | `uv-0.13.5-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl` |
| `uv_sha256` | `fa8d26e08e34c29c0c4e820cf3181757086d547240b4f4d31ff1d8bd810ca946` |
| device wheel | `752123d368cd269c8454a828b86c45783cef89205f8f5ee5745639b7f6cffe3e` |
| bash wheel | `a1f82a1603abec0413bdb2ea651655223004f53b96409f2a3b88810d33276c6d` |
| fs wheel | `125508340a204364c4ec86c9700cbd88980be41f30501759116b171caaef6126` |

CPython 运行时归档由 `packages/device-runtime/native/runtime-lock.json` 固定，构建脚本在解包前校验：

- linux-x86_64：`f829adc9d888e76c8801b4799ed444188d35239fcae3a9808a99ec5cb0d8d92c`，35356674 字节

## 依赖闭包

glibc 2.17 目标下解析，全部为 cp313 x86_64 兼容 wheel：

```
annotated-types==0.8.0
packaging==26.3
pillow==12.2.0
psutil==7.2.2
pydantic==2.14.1
pydantic-core==2.50.1
typing-extensions==4.16.0
typing-inspection==0.4.4
websockets==17.3
```

`pillow` 停在 12.2.0 是硬约束：12.3.0 起只发布 `manylinux_2_27` 的 cp313 wheel，与 glibc 2.17 冲突。12.2.0 是最后一个带 `manylinux_2_17` wheel 的版本。详见 `packages/fs/pyproject.toml` 注释。

## 验收结果

### DEB（Ubuntu 24.04，amd64 容器）

| 项 | 结果 |
| --- | --- |
| `dpkg -i` 安装 | 成功 |
| `dpkg -V` 安装后 | 通过，无输出 |
| `dpkg -L` | 10 项，只含 `payloads/*.tar` 与两个 `/usr/bin` 入口 |
| 清单内容 | `0.2.2-native1.tar` 184197120 字节，`0.2.2-native1-bootstrap.tar` 96522240 字节 |
| 安装后指针 | `0.2.2-native1` |
| 打包解释器 | `3.13.17` 可执行 |
| helper 入口 | 正常列出 7 个子命令 |
| 升级 `-1` → `-2` | 成功，preinst 报 "adding 0.2.2-native2 beside current 0.2.2-native1" |
| `dpkg -V` 升级后 | 通过 |
| 升级后指针 | `0.2.2-native2`，旧树已回收，`packages/` 与 `bootstrap/` 均只剩新树 |
| 升级后解释器 / helper | 均可用 |
| `dpkg -r` 卸载 | `/opt/ai-device` 完全移除 |

### RPM（Rocky 9，amd64 容器）

| 项 | 结果 |
| --- | --- |
| `rpm -i` 安装 | 成功 |
| `rpm -V` 安装后 | 通过，无输出 |
| `rpm -ql` | 同样只列 `payloads/*.tar`、`%dir` 与两个入口 |
| 安装后指针 | `0.2.2-native1`（干净的版本名，不含字面 `\n`） |
| 打包解释器 | 可执行 |
| 升级 `-1` → `-2` | **`remove failed` 为 0 条** |
| `rpm -V` 升级后 | 通过 |
| 升级后指针 | `0.2.2-native2`，旧树回收 |
| `rpm -e` 卸载 | `/opt/ai-device` 完全移除 |
| 重装 | 成功 |

## 本次修复对应的症状

改造前（staging 归属）：

- `dpkg -V` 报 9762 项 missing
- `rpm -V` 同样大量 missing
- 每次 rpm 升级输出 9762 条 `remove failed`

改造后上述三项均消失。差异来自包清单改为指向真实存在的归档文件。

## 未覆盖的部分

本次只验证包管理与文件布局。以下仍未验证，不要据本文推断已完成：

- 普通用户实例创建、`register`、systemd unit 实际运行
- 签名升级链路（`native_prepare` / `native_update`）
- 并发升级、崩溃点注入、中断恢复
- 多实例共享 registry 的相互影响
- 真实业务 IPC（本次只到 `--help`）

## 重建方式

制品已删除。需要重建时：

1. 按 `runtime-lock.json` 与 `tool-lock.json` 下载并校验两个构建输入
2. 在仓库根 `uv build` 出三个 wheel（device/bash/fs）
3. 按上表依赖闭包取得 glibc 2.17 兼容 wheel
4. `uv run python packages/device-runtime/scripts/assemble-native-payload.py` 组装 payload
5. 在 `--arch amd64` 的 Linux 容器内运行 `scripts/build-native-linux.sh deb|rpm`

第 5 步必须在 x86_64 上执行，脚本会拒绝其他架构。
