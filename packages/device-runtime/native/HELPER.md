# P1 Linux有限权限helper与systemd绑定（进行中）

实现入口：`device_runtime.native_helper`。仅Linux系统helper daemon使用root；客户端unit明确使用登记的普通UID/GID。本文是开发契约，不是让用户在正式机器直接执行的部署步骤。

## 已实现边界

- 管理员register明确接收实例路径、普通用户、root管理的0600环境文件、registry及unit目录。核验单client角色、身份、owner、绝对解释器稳定入口，拒绝已有升级记录和重复登记。
- unit与登记记录采用no-replace发布，记录root、UID/GID、unit名/路径和内容摘要、runner/identity/roles摘要。失败可能留下孤立unit；不自动覆盖或接管，管理员应核实并按明确授权恢复。
- 客户端unit：稳定`run client`，普通用户、network-online、Restart=on-failure、UMask0077、NoNewPrivileges，SendSIGKILL=no。不以停止超时自动强杀任务。
- helper unit：独立bootstrap绝对解释器、root、Restart=no，无自动删除socket钩子。当前只提供生成函数，尚未接入原生包部署。
- AF_UNIX有界请求（4096字节）、5秒读取超时、Linux SO_PEERCRED核验peer UID。socket由root持有、0660授权给明确组，仍需UID匹配实例owner。身份不能由body自报。
- 请求只含action、instance_id、operation_id，不能指定argv、服务名或路径。只开放query、start、reconcile；其他动作包括stop/apply-package/activate/uninstall均拒绝。
- 每次校验登记文件和root管理祖先权限、unit摘要、实例owner、runner/identity/roles摘要及已加载systemd的User/Group/FragmentPath/完整ExecStart。变更后拒绝，不自动重绑定。
- query/reconcile只读观察，不安装、启动、清锁或修复。start只启动inactive/failed服务；active记录already_active。启动不等于设备上线、配对或中心确认。
- registry实例锁保护请求；启动持有现有.installation.lock共享锁并检查现有监督锁和非终态升级，避免维护期间重启客户端。未完成或unknown升级阻塞；外部旧管理工具仍应遵守同一锁契约，不能把helper锁当全局安全沙箱。
- start先fsync保存intent再调用systemctl，结果另外原子保存。相同ID不重复启动；意图无结果或启动超时为unknown，换新ID也不能绕过。对账不暗含重新执行；当前尚无自动清除unknown或继续执行入口。

## 开发命令契约

以下是模块入口形态，使用前需有经确认的系统路径、普通用户、凭证和bootstrap。不要把占位值复制到正式机器：

```text
<独立bootstrap-python> -I -m device_runtime.native_helper register
  --registry <root-owned-registry> --unit-dir <system-unit-dir>
  --root <existing-client-instance> --instance-id <registered-id>
  --username <existing-non-root-user> --environment-file <root-owned-0600-file>

<独立bootstrap-python> -I -m device_runtime.native_helper serve
  --registry <root-owned-registry> --socket <root-owned-runtime-dir/socket>
  --group <explicit-numeric-group>

<已安装的python> -I -m device_runtime.native_helper call
  --socket <registered-socket> --action query|start|reconcile
  --instance-id <registered-id> --operation-id <stable-operation-id>
```

register返回started=false、enabled=false、daemon_reload_required=true。是否安装helper unit、daemon-reload、自启和启动均属于另外的显式操作，当前不会自动执行。call无自动重试；响应丢失后查询同一ID。socket已有文件时serve拒绝，不能自动unlink未知对象；此时重启恢复需管理员核查，不宣称helper崩溃恢复已闭环。

代码模块同步执行systemctl、文件校验与锁，不得直接放入HTTP事件循环。socket串行执行、start最长120秒，当前适用于本机单实例开发；不声称已具备多实例并发吞吐。

## 本轮证据

- helper单元测试30项：有限输入、普通用户、绑定漂移、幂等、unknown阻止新ID、安装/监督锁、非终态升级、socket调用和响应丢失等。
- Device完整回归746项通过。
- 一次性Linux容器实际执行root注册普通UID12345实例、重复注册拒绝、客户端/helper unit的systemd-analyze verify通过，无服务启动。
- 初次实际注册拒绝上游解释器775权限。已在包构建器新增chmod -R go-w程序树；不放松helper校验。规范化后真实注册通过。修改后原生包尚未重建/重验，旧v5包不能宣称满足新权限要求。

实验入口：`../scripts/accept-native-helper.py`，需DEVICE_NATIVE_DISPOSABLE_PROBE=yes，只用于一次性Linux容器。fixture不连接中心，环境变量是假凭证，无真实秘密。

## P1仍缺

独立bootstrap稳定交付、helper包内部署与socket生命周期恢复、systemd安装/enable/普通用户真实启动与健康验证、与旧升级器完整互斥、受控更新/卸载及故障恢复、正式签名与许可、glibc主线对齐和实机自启恢复。不能因为unit语法通过就把这些标为完成，也不能进入P2。
