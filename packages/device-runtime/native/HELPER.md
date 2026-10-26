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

## 后续实现：独立bootstrap、显式部署与socket恢复

本节更新前文对应的“尚缺”描述，其余边界仍有效。开发包现额外携带 `/opt/ai-device/bootstrap/0.2.2-native1/runtime` 和 `/usr/bin/ai-device-native-helper`；bootstrap使用独立CPython、只保留device_runtime的包入口与标准库helper模块，不安装客户端依赖。客户端目录暂时不可用时部署验收通过。固定开发版本，不是可自动更新的通用bootstrap版本策略。

新增管理员命令：

```text
ai-device-native-helper deploy
  --bootstrap-python <独立bootstrap绝对解释器>
  --registry <root管理的登记目录> --unit-dir <系统unit目录>
  --runtime-dir /run/<专用目录> --group <明确socket组GID>
ai-device-native-helper recover-socket
  --registry <同一登记目录> --socket /run/<专用目录>/helper.sock
```

deploy只创建目录与无覆盖unit，返回started=false/enabled=false；失败可能留下目录，不能当事务成功。显式systemctl daemon-reload、enable、start与普通用户调用是另外步骤。RuntimeDirectory由systemd在开机时创建，Preserve=yes避免daemon退出时隐式移除socket。包安装本身不部署/启用/启动；登记或unit存在时拒绝包卸载。

serve与recover-socket共用root登记目录内的daemon锁。recover-socket仅Linux管理员可用，拒绝活动daemon、监听端点、非root socket、普通文件及未知连接结果；只有明确ECONNREFUSED且inode不变才删除遗留端点并fsync。不修改unknown操作、不重放start。不是断电/文件系统损坏自动恢复承诺。

CLI call在响应ok=false时返回非零退出码；包级验收发现并修复了此前错误返回0的问题，已补成功/失败退出码回归。

验证入口：`../scripts/accept-packaged-helper.py`（独立bootstrap、真实socket/UID及显式遗留恢复），`../scripts/accept-native-systemd.sh`（全新隔离systemd内真实普通用户启动、自启链接、同ID PID稳定、非owner拒绝）。v9 DEB实测通过；helper39项、Device755项通过。v8 RPM离线安装/依赖/独立helper通过，但不代表其包含后补的socket恢复。隔离环境是Apple container的amd64/Rosetta，systemd255、cgroupv2，启动需要cap-add ALL和container=apple；不是目标实机或glibc最低基线证明。中心健康、配对、宿主重启恢复、unknown收敛及更新/卸载仍未验证。

## Unknown显式收敛：resolve-running

请求action新增 `resolve-running`，仍只允许action/instance_id/operation_id。用原启动operation_id调用；query/reconcile保持只读，现同时返回原result和独立recovery。原start同ID仍返回原结果，并附恢复记录，不再次执行。

resolve-running要求匹配当前unit摘要的原start intent，原结果缺失或unknown、登记/实际systemd绑定均有效、安装和监督维护锁允许观察，并确认active/running、MainPID>1且Job为空或0。保存不可覆盖的 `recovery-<instance>-<operation>.json`，state=confirmed_running、start_replayed=false，原intent/result不改。恢复记录成功发布后才解除该旧unknown对新启动ID的阻塞；新ID仍需经过全部现有启动/维护检查，active不会重启。

此记录只证明恢复时服务处于已运行状态，不证明原调用因果成功，也不证明业务健康或未来持续运行。重复恢复返回已保存证据和当前观察，不刷新旧证据。inactive/failed/过渡态、PID或Job证据缺失、维护锁冲突、持久化失败均不解除unknown。尚无“确认未运行后重新启动”入口；需要明确终结旧systemd job的独立契约后再实现，不能换ID绕过。

故障注入脚本 `../scripts/accept-native-unknown.py` 在一次性systemd实例先启动后模拟完成响应丢失，重建Helper并对账，验证原unknown保留、新ID事前阻塞、恢复后already_active、PID不变、实际start仅1次。v10 DEB验证通过，helper51项、Device767项通过。v9 RPM不包含此新增动作，未重建v10 RPM。

## 未运行unknown：resolve-stopped与显式重试

新增action `resolve-stopped`，参数仍为原instance_id/operation_id，不接受任意job、服务、路径或命令。它不是stop操作：当前必须inactive/failed且MainPID=0，才可取消该已登记unit观察到的准确Job ID；随后重新核对inactive/dead或failed/failed、PID=0、Job为空/0。过渡态、auto-restart、已运行、取消失败或状态竞态均不收敛，不停止客户端。

新start意图包含execution_barrier=1；在发布intent之前持有实例执行锁。独立标准库guard继承锁并等待systemctl start退出/回收；systemctl子进程120秒超时由guard回收。即便helper退出而guard仍在发送/等待，锁仍阻止恢复和新启动。不能只给systemctl传fd，因为不能依赖它保留未知描述符。恢复要求已有执行锁可独占获取；缺锁拒绝，不重建证据。resolve-running对新意图也增加此发送者退出屏障。

resolve-stopped在实例管理锁、安装/监督共享锁和执行锁内保存不可覆盖confirmed_stopped恢复记录，原unknown不改、不调用start。只有记录持久成功后，新操作ID才可经原有全部门禁显式启动；旧ID永不重放。取消Job超时或记录写入失败仍unknown，按同ID重新对账。旧格式意图没有发送屏障，不允许resolve-stopped自动收敛，需管理员核实旧进程/job；不自动修改旧记录。

并发前提仍是登记helper管理路径及既有维护锁契约；此恢复不是对不遵循锁的外部管理员任意操作提供独占保证。Job取消不证明历史上从未启动过，只证明当前屏障后没有该请求的延迟发送者和已观察残留Job；业务健康另行验证。

v11 DEB在真实隔离systemd中验证已运行丢响应恢复及明确失败后恢复：测试专用ExecStartPre=false/Restart=no制造失败，原unknown/提前重试阻塞，confirmed_stopped后移除测试故障，用新ID启动成功并同ID PID稳定。真实子进程测试验证父fd关闭后guard仍持锁。helper65项、Device781项通过。v11 RPM未重建，v9 RPM不包含这些恢复动作。

## 回收屏障修正：execution_barrier=2

重启后复核发现版本1仅凭执行锁释放不足以证明guard异常退出时其子进程已退出。新版本guard在systemctl退出/超时回收后，原子no-replace发布 `sender-<instance>-<operation>.json`（sender_reaped、准确intent及返回码），文件与父目录fsync。新intent版本2；resolve-running/resolve-stopped均要求匹配的完整回执及独占执行锁。guard崩溃、回执缺失/不匹配仍unknown，不能仅凭锁释放恢复。版本1或无版本旧意图不允许未运行自动恢复。

guard回执证明直接systemctl子进程已经回收，不宣称任意后代/外部管理者也已终结；随后仍必须核对systemd Job与服务状态。回执写入失败保守阻塞，不自动伪造回执。v12 DEB实际systemd两恢复分支通过，helper69项/Device785项通过；真实排队Job取消尚未实测，RPM尚未对齐本轮恢复实现。

## 后补验收：真实Job、guard异常退出与RPM

`../scripts/accept-native-recovery-faults.py` 在一次性systemd实例验证：依赖屏障产生实际排队Job，准确取消后confirmed_stopped再新ID启动；准确终止测试guard且fixture子进程自行退出，无回执仍阻止恢复和重试。排队场景使用测试发送适配器--no-block注入丢完成，不冒充生产guard超时路径。所有测试实例已回收。

v12 RPM已对齐：与DEB helper源码和bundle manifest逐字节一致，Rocky9离线安装、普通UID依赖/独立bootstrap和恢复动作检查通过。RPM SHA256 `1d02abd52d0339401804dea7c4f81abd81b4091200bdb5e1f8055905884d57fd`。实际systemd故障运行在DEB隔离实例，未在Rocky复跑。完整Device785项通过。更新卸载、旧工具完整互斥、签名许可和实机恢复仍缺，P1未完成。

## 旧工具与受管实例的协作互斥

新增标准库 `management_lock.py`，以resolve后的实例路径使用 `.management.lock`：旧写工具持共享管理锁，native register持独占管理锁并获取安装独占锁。锁序为管理协调→旧监督/升级锁→安装锁；登记只在已有普通用户实例停止且无升级记录时允许。新包bootstrap同步携带该模块。

登记在发布unit/registry记录前no-replace保存 `.native-management.json`；记录保存标记摘要，helper每次核验。标记存在或异常symlink时，旧安装独占写锁、生命周期准备/激活/回退/备份、旧upgrade prepare/activate/reconcile及监督supervise/recover/resume-admission/reconcile入口拒绝，不允许先创建upgrades再失败。只读status/inspect不因标记自动变为修改授权；恢复到新目录不携带受管身份。

中断可能留下封锁标记或unit，必须显式核实后恢复，不自动删除、覆盖或接管。旧登记缺management摘要时helper拒绝并要求明确接管；本轮不实现迁移。客户端run继续使用安装共享锁，不因管理标记禁止正常运行。

这是同版本可信本机工具的协作协议，不是OS安全边界；不承诺阻止旧二进制、手工改文件或不遵循锁的管理员操作。受管实例当前未实现更新卸载，所以旧工具拒绝不是更新能力完成。

验证：新增管理锁13项、helper70项，完整Device799项通过；登记中断、路径别名/跨进程争用、标记漂移和旧入口无副作用拒绝覆盖。v13 DEB真实隔离systemd验证登记后旧prepare/监督拒绝、helper正常启动及全套unknown/Job/guard故障链通过。v13 RPM未构建，v12 RPM不含管理标记门禁。

## 显式登记恢复：recover-registration

新登记标记schema_version=2包含完整预期binding（路径、UID/GID、unit/runner/identity/roles摘要、凭证文件路径），先于unit/registry发布。unit改为临时文件fsync后hardlink no-replace发布，避免部分unit被恢复误接管。

管理员使用 `ai-device-native-helper recover-registration`，参数与register相同。持管理独占锁及安装独占锁，重新核验实例普通owner、凭证权限、解释器、单client身份、无旧升级记录；标记须root-owned普通文件、组/其他不可写，但允许实例祖先为普通用户owner。完整基准须一致，已有unit/record逐项一致才补缺失成果。先检查全部已有成果再写，冲突不覆盖。完整恢复可重复，无start/enable/reload，登记失败仍保留封锁。

schema1标记、无标记旧登记、漂移/损坏unit及不匹配record拒绝，不提供自动迁移、删除或重新绑定；需明确管理员审核方案。此处不把拒绝称作旧登记迁移已完成。

v15 DEB真实隔离验收marker发布后注入中断、公开CLI幂等恢复和冲突unit保持通过，原helper/unknown/Job/guard故障链通过。管理测试23项、helper70项、Device809项通过。v15 RPM已携带同一helper/management模块，离线安装/普通UID依赖/恢复CLI及manifest一致通过；未在Rocky复跑实际systemd。详见实施记录第17节。
