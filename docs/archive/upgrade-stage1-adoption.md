# 阶段1显式纳管：审阅模板与交接契约

> **旧实例路线历史资料**：本文不是新客户端完整包的安装、恢复或验收指南。当前 Linux 路线见 [完整包开发操作指南](../development/client-native-linux-development.md)，权威需求见 [客户端安装升级需求](../requirements/client-native-install-upgrade.md)。旧监督器仍有待拆分的 Linux/Mac 职责；保留历史不表示新包支持旧协议，不应将下文命令用于新 DEB。

状态：可生成、可本地校验，未在真实Linux/systemd安装验收。不是正式部署授权。
不覆盖旧runner、不伪造installation、不从裸venv自动升级为标准实例。

## 管理员必须确认的事实

1. 标准安装器创建的单client实例；run原字节等于installation.RUNNER；active、instance、
   config/roles、journal/output map均通过布局校验。非标准旧部署先另案制定迁移，不套模板。
2. 根、run、active、config、releases、upgrades及profile由管理员控制；客户端只能写
   state/client，属专用非root用户、mode0700。目录链不得有可被客户端替换的父目录或symlink。
   active.json是非秘密启动指针，须预先授予专用client只读（例如root:client组0640），
   发布原子替换时保留既有uid/gid/mode；不依赖ACL/xattr自动复制。config/roles与instance及
   release目录同样须client可读/可遍历，管理员可写。操作记录仍0600。
   原state/凭证/身份原样保留，不能递归chown整个实例或复制活数据库来“补齐权限”。
3. 凭证放实例外的root:root0600 EnvironmentFile，systemd读取DEVICE_RUNTIME_TOKEN；
   不把token写进JSON、unit、argv、报告或模板。客户端无需直接读取env文件。
4. run的`/usr/bin/env python3`可在unit固定PATH找到兼容解释器；venv保持原路径。
   本机准备候选需uv、Python3.13及准确bundle；不得为满足模板顺便升级生产依赖。
5. 独立升级器安装在实例目录外、管理员只读控制的准确release中，Python绝对路径固定；
   不通过root/run或客户端自身venv启动它，不能将其放入client的unit/cgroup。
6. supervisor配置中的unit精确指向本client；模板不含PartOf/BindsTo/Requires=client，
   不是客户端子进程。已有unit的alias/dropin、ExecStop、KillMode、启动前后命令和自动重启
   策略须审阅，不把未知旧unit直接用dropin改造成该契约。
7. 客户端模板的ProtectSystem/ProtectHome会限制任意路径文件操作；只有写state是默认能力。
   如实际工作要求其他可写范围，须单独审阅和验收，不自动删除保护，不声称透明旧行为兼容。

## 生成（不安装）

从仓库运行，使用已配置开发环境；输入文件不含凭证。所有值必须显式给出：

```json
{
  "root": "/var/lib/device-example",
  "client_unit": "device-example.service",
  "client_user": "device-example",
  "client_group": "device-example",
  "token_file": "/etc/device-example/client.env",
  "supervisor_home": "/opt/device-upgrader/approved",
  "supervisor_python": "/opt/device-upgrader/approved/venv/bin/python",
  "upgrade_id": "approved-one",
  "channel": "stable",
  "allowed_versions": ["0.2.2"],
  "bundle_channels": {"<准确bundle指纹>": "stable"},
  "maintenance_windows_utc": [
    {"start": "2030-01-01T00:00:00Z", "end": "2030-01-01T01:00:00Z"}
  ]
}
```

日期仅展示格式，不是当前维护窗口；版本不是未来发布许可。
指纹取inspect_bundle/prepare使用的准确bundle fingerprint，不是wheel hash或源码build ID。

```bash
PYTHONPATH=packages/device-runtime/src uv run --no-sync python scripts/render_upgrade_adoption.py \
  --config /absolute/review-input.json --output /absolute/new-review-directory
```

输出：client.service、supervisor.service、upgrade-profile.json、REVIEW.txt。
输出目录必须不存在（0700），文件0600，不覆盖已有目录；不创建用户、读凭证或调用systemctl。
路径拒绝空白、换行、`%`等systemd特殊语法；不假装shell引用能处理unit转义。
**输出profile固定allow_upgrade=false**。管理员审查并在明确授权维护窗口内显式开启，
不能认为renderer已批准现在升级。模板尚未安装，所以不会产生实际纳管。

## Linux隔离验收后才可采用的安装步骤

以下由管理员按本次授权执行，本机AI不据此获得停服或部署权限：

- 备齐准确软件、独立升级器、标准实例与专用用户；核实上述目录权限，凭证外置。
- 把client模板映射到准确client_unit，把supervisor映射为独立且唯一的一次性unit；
  不使用`@`动态unit或任意EnvironmentFile向root升级器注入命令/参数。
- 安装文件后先用systemd-analyze verify检查两unit；审阅实际systemctl show限定字段与
  有效dropin。语法verify通过不证明权限、依赖、网络或任务收口能运行。
- 人工审核准入profile与准确bundle/窗口，客户端已运行维护口且ready；验证客户端不能改
  软件/profile，监督器与client cgroup不同。完成真实验收矩阵后才可开启profile。
- 每个升级请求生成固定upgrade_id的独立supervisor unit。无timer、无Install、Restart=no，
  绝不enable为开机自动升级；主机重启后先读取旧记录，不自动重跑。
- supervisor目前User=root，为systemctl和root-owned发布所需，**不是最小权限broker交付**；
  NoNewPrivileges/ProtectSystem只减小误操作面，ReadWritePaths=root仍可写active/候选。
  ReadOnlyPaths使配置、旧releases、profile和升级器安装只读；prepare单独提前执行，
  不在此只读发布unit内安装候选。不得宣传能抵御恶意本机代码。

模板不含prepare或recovery任务的自动计划。恢复由管理员在独立可信维护终端按
[人工恢复](upgrade-stage1-recovery.md)逐步执行，不通过Device Bash向自己发恢复命令。

## 暂不支持

跨用户零停机接管旧布局、通用数据库迁移、自动boot恢复/回退、任意服务管理、受限权限broker、
launchd升级器均未交付。真实Linux/systemd、主机重启和权限故障验收仍是阶段完成硬门禁。
