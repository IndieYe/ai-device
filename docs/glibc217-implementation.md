# glibc 2.17 兼容基线实施

## 范围与基线

- 基于 main `4f7891d` 的独立工作树；不修改旧 0.2.3 发布工作树，不宣称此候选已发布。
- Linux x86_64 / CPython 3.13 的新依赖清单默认 2.17；保留原 2.27 清单的准确语义。
- 不修改宿主 glibc，不停止真实服务，不迁移或删除 wx_test 旧数据。

## 开发阶段

### 阶段1：发行契约与回归
- 目标：新基线2.17与旧2.27制品共存且不混淆。
- 范围：依赖标签、宿主门禁、安装/重建/升级测试与文档。
- 验收：2.17主机可用2.17包；2.16拒绝；2.27包在2.17主机仍拒绝；Hash/完整性验证保持。
- 验证：修改前42项直接测试通过；候选57项直接测试通过，ruff通过。完整Device回归700项、Bash184项、FS116项及版本一致性6项通过。

### 阶段2：准确制品构建与隔离原生验收
- 范围：固定manylinux2014镜像内构建Pillow及依赖、准确候选wheel与bundle，脱离源码目录验证生命周期及图片编解码。
- 验收：构建来源、Hash、平台标签、依赖闭包、PNG/JPEG/WebP及坏图拒绝通过。
- 风险：依赖网络可用性；脚本历史固定版本不等于现有0.2.3/Pillow13包。

### 阶段3：真实旧系统验证
- 在wx_test独立测试目录验证glibc2.17/3.10内核，正式服务维持停止。
- 不将容器新内核结果等同于CentOS7真实内核和自启验收。
- 需要准确传输和明确目录边界；不在未完成验收前覆盖旧安装。

## 当前交付状态

阶段1实现与回归通过。阶段2准确三包wheel和离线bundle已构建，新建glibc2.17/CPython3.13.16容器且无构建库挂载，安装、重建、prepare、activate、rollback、FS PNG/JPEG/WebP、坏图拒绝及篡改拒绝通过。

- 当前候选基于main4f7891d，三包版本仍为0.2.2/0.2.0/0.2.0；是未发布候选，不等同历史0.2.3。
- Pillow12.3.0源码新构建，auditwheel确认manylinux2.17。WebP源码采用固定Hash缓存；其余依赖在宿主按明确cp313/x86_64/2.17标签下载并核验PyPI摘要，未在宿主执行Linux wheel。容器直接下载曾TLS超时，日志保留。
- 完整Device700、Bash184、FS116、版本一致性6项通过；ruff、bash-n和diff-check通过。
- 证据目录：`/Users/<user>/data/project/handoffs/glibc217-ses14203`，含candidate.patch、candidate-source-note.json、candidate-bundle、build-v2/auditwheel.txt、container-acceptance-report.json。
- 真实3.10内核验收及正式安装已完成，详细证据与边界见下文；旧目录仍保留。
- 真实验收包SHA256：05b8794b570b7a61cb35fce25718a7927034ed71c12baf4d9d3b00a04b791be3。
- 传输曾因MacBook SSH地址变化而超时；最终通过会话Artifact下载至MacBook，经SSH传到wx_test并核验Hash，阻塞已解除。验收工具为持久Python3.13.16及独立uv0.12.24。

测试产生的logs/不属于交付代码。公开发布不在本次本地集成范围内。


## 真实 CentOS7 验收（2026-10-26）

用户将原包下载到MacBook后，经Device核验大小与SHA256，再通过其SSH别名wx_test传至全新私有目录`/opt/device-qual-glibc217-ses14203`；两端摘要一致。

首次执行发现验收包uv模式0644与macOS AppleDouble元数据，安装前失败；核实uv字节Hash一致且未创建control后，仅修正验收目录中uv执行位并清理21个归档生成的`._*`文件。没有变更产品wheel或依赖。后续准备v2归档时使用Python tarfile剔除xattr/AppleDouble、固定uv执行位并静态核验；旧Artifact保留，不覆盖。

准确候选在真实CentOS7 / Linux3.10.0-957.21.3.el7.x86_64 / glibc2.17 / CPython3.13.16通过离线安装、重建、prepare、activate、rollback、PNG/JPEG/WebP、坏图及依赖篡改拒绝。报告`acceptance/report.json`，本机证据`native-centos7-report.json`。该离线验收当时正式服务保持停止，旧实例未覆盖；后续正式接入见下节。此项结果本身不包含中心连接、systemd或重启恢复。


## 审核与正式安装（2026-10-26）

本会话自审覆盖依赖清单/标签、旧2.27语义、宿主门禁、Hash完整性、生命周期与构建失败路径；未做独立子会话审核。57项关键回归重跑通过，准确wheel内22个device_runtime模块与任务源码逐字节一致。当前无阻塞安装问题。该审核发生在提交与本地集成前，未公开发布。

用户明确授权正式安装后，新根为`/opt/device-runtime/wx_test-glibc217`，软件输入保留`/opt/device-runtime/wx_test-glibc217-software`，未复用验收实例状态。原凭证文件仍由systemd通过`/etc/device-runtime/wx_test.env`读取；未输出凭证，旧根完整保留。并发三项显式8，身份wx_test、中心device.example.com。继承原root运行方式，未扩大权限。

首次安装传wss URL被安装器在创建实例前拒绝；核实HTTP(S)入口契约后改https，正确生成wss配置。安装完成后新增`/etc/systemd/system/device-wx_test.service.d/90-glibc217.conf`，使用持久Python绝对路径和稳定run入口。systemd-analyze verify通过（另有既有netdata LogNamespace无关警告），daemon-reload后只启动已停止服务，未stop/restart。

正式服务active/running、enabled，PID23926；中心在线上报0.2.2，build_id=`sha256:10973575563356f8b6d9df6015832c05b6ec6b5f5e924027c0f8dfdb7bc99579`，与安装前运行版本一致。软件wheel SHA256=`c36acd46626b5413d062b3527a2016e91572e1f3ada789ce10c60c2439b23d44`；bundle SHA256=`1ded44b4f6c32bc18b20e2f771078f5c9de442d474a4d99076ee2ed68a3915cc`。

正式Device通道命令执行、在独立目录创建UTF8文本/PNG并校验、fs.read_file读回及fs.read_image返回24x16 PNG均通过。原操作ID op-ses14203-formal-e2e-34、formal-read-35、formal-image-36（后两者先sent后按原ID成功回收）。测试目录`acceptance-formal-ses14203`留存；没有旧数据清理或系统重启恢复实测。自启配置enabled不等于已验证重启恢复。
