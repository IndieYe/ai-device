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

## P1更新事务启动互锁（开发原型，尚未制包）

helper新启动请求、管理员register/recover-registration与更新事务共用 registry 下 `package-manager.execution.lock`。启动/登记取共享锁，包应用/恢复取独占锁；启动的标准库guard继承共享锁，helper调用方关闭fd后发送者仍持锁。更新使用guard持有独占锁。更新意图写入前要求停止且无Job，并拒绝未确认的旧启动请求。当前共享包只允许单登记实例；多实例收口未实现，明确拒绝而非只停一个实例就替换共享程序。

`updates/*.apply-intent.json` 是持久启动门禁：不依赖调用期间的实例锁；新启动和登记/登记恢复均拒绝，query/reconcile及旧start ID的只读结果仍可用。unknown、包已应用待激活、观察到源包的恢复记录都不解除门禁。尚无安全终态解除协议，不提供删除账本解锁的建议；管理员手工包修复不等于已验证激活。新建updates目录在意图发布前fsync registry目录。

`native_update.py` 目前仅开发用管理员DEB事务台账，非发布入口，不接helper IPC或构建器。现有包仍拒绝覆盖；缺签名候选、安全提取/普通用户准备、任务收口与准确停止、包钩子授权及稳定bootstrap、激活/健康/恢复终态、RPM等，禁止用于真实设备包事务。此前v15不含本轮代码。

验证：helper/更新/管理定向120项、Device完整836项通过；包含独立进程锁争用、调用方fd关闭后子进程持锁、生产启动guard继承fd、持久意图跨helper重建、恢复不解除占用、登记互锁。未进行本轮真实systemd或dpkg验收，不沿用v15证据作为本轮包验证。

## 签名候选认证与安全提取（P1子阶段）

新增 `python -m device_runtime.native_candidate`，需安装 `ai-device[native]`（cryptography Ed25519、zstandard）。CLI显式传入 `--package --envelope --destination --trusted-keys --release-id --source-version --bootstrap-version`；目标暂限linux-amd64-deb。trusted-keys是由管理员在候选外提供的 `{key_id: base64原始32字节Ed25519公钥}` JSON，不自动导入包内信任根，不提供正式私钥或发布签名服务。

release envelope严格为payload/key_id/signature；签名是对既有canonical JSON（UTF-8、排序键、无空白、非ASCII保留）的payload字节作Ed25519，signature为base64。payload schema=1，含release_id、key_id、versions(device/bash/fs)、bootstrap_min、target、allowed_sources、required_space、compatibility(protocol/state数组含当前1)、package。package含name/version/architecture/size/sha256/data_sha256/payload_root/manifest_sha256；data_sha256是DEB压缩data成员准确字节摘要，manifest_sha256是versioned payload下bundle/manifest.json摘要。只接受固定包名及amd64，准确release/源版本/最低bootstrap重核；正式发布描述仍须补全P0系统最低版本等字段和发布服务，不将此局部Schema当完整中心发布契约。

准备先验签，独占新建私有目标目录，按签名大小复制包、校验SHA，再读取标准ar成员。支持gzip/xz/zstd控制与数据归档；不调用dpkg，不提取或执行control脚本，只核对Package/Version/Architecture。数据逐成员检查越界/重复/链接/特殊文件/权限及数量、展开预算，链接延后创建，不允许写穿链接，拒绝逃逸或悬空链接。校验离线Linux bundle及三包准确版本。全部文件与目录fsync后才no-replace发布candidate.json完成回执；失败清理本次新目录，不覆盖原候选。突然中断留下无完成回执的目录视为不完整，后续拒绝覆盖，清理须独立受控。

结果只为`authenticated_extracted`，明确`venv_prepared:false`、`activated:false`。尚未接入native_update批准/应用；旧prepare摘要台账不能当已验签证明。最终目录venv准备、运行候选证据消费、正式信任根、包内native依赖交付/RPM仍缺；现有包覆盖保护不变。Linux包含大小写不同的文件，大小写不敏感文件系统可能被安全拒绝；请在隔离Linux验证，不关闭碰撞检查。

`accept-native-candidate.py PACKAGE OUTPUT --development-ephemeral-key`仅供开发DEB验收，内存临时密钥不落盘，不构成正式发布认证。已在隔离Linux普通UID12345对v15包成功认证提取10443成员/285493398字节；包SHA da0fadb10d904e48a1d80669cae9eb6d61c8751668e62b9585d093cf9a73e7b0。依赖在临时容器联网安装，不能据此声称native验签依赖离线交付已完成。无安装、启动或激活。

## 普通用户原生运行候选入口（P1开发子阶段）

`python -m device_runtime.native_prepare --root ... --package ... --envelope ... --trusted-keys ... --release-id ... --operation-id ... --bootstrap-version ... --uv-executable ...` 重验签名并从包重新提取，不能只信用户可编辑candidate.json。限Linux登记所有者非root、schema2完整binding及单client角色；核对unit/run/identity/roles摘要。使用专用native_preparation_lease（管理EX）与安装SH锁，允许运行中的客户端继续工作但排除其他维护，旧工具仍拒绝受管实例。当前Python真实路径及显式uv均要求管理员可信非可写持久路径，不执行提取目录工具。

先在`native-candidates/<id>.intent.json`记录源active/marker、签名发布摘要、持久工具摘要，再重新认证提取到保留目录，复用installation._prepare于最终release目录直接创建venv和健康检查。文件落盘后核对active和marker未变，再发布runtime_prepared结果，不切换active。相同ID只在源及release/bundle/解释器证据仍有效时返回原结果；无完成或失败结果禁止相同/新ID自动重放。失败保留release及证据供后续管理员恢复，不提供删除台账解锁流程。完整准备恢复/批准消费尚缺。

新登记schema2 marker以root 0644原子发布，内容仅绑定路径/摘要，不含凭证；其他操作账本仍默认0600，凭证环境仍0600。已有0600 marker不会自动chmod，普通用户读取失败需管理员明确处理，非自动迁移。之前v15包和已运行helper不自动加载新代码。

新增accept-native-prepare.py需在隔离Linux已登记实例运行；开发临时密钥不作为正式发布。真实实验UID12345、持久CPython3.13.17和包内uv：runtime_prepared，同IDrelease稳定，active字节不变；未运行systemd/client或包安装脚本。native验签依赖在临时容器联网安装，运行venv从v15离线bundle构建；不能称新模块原生制包离线交付。完整Device895项通过。更新批准尚未消费本结果，不解除原包门禁。

## 管理员签名候选准备与批准绑定（P1原型）

native_update新增`prepare-signed`，参数package-path/envelope-path/trusted-keys/release-id/runtime-receipt/bootstrap-version，另需既有registry/instance-id/operation-id。CLI信任公钥JSON、包及envelope要求管理员可信路径；外部信任根重新验签，私有临时目录重新提取并验证真实离线bundle，不信普通用户提取JSON。临时认证目录在验证结束清理，不清理实例已有成果。

管理员只读核对普通所有者`native-candidates`直属完成回执、runtime_prepared/activated=false、发布摘要、源active及marker、最终release/installation、签名manifest与真实bundle内容、venv解释器存在及准确字节摘要；不以root运行用户venv。证据摘要并入prepared后approve绑定完整SHA；approve及apply前重查源及实际bundle/回执/installation/manifest/解释器。批准后漂移拒绝，不发送包安装。认证与prepare两次实例lease之间仍按固定源摘要复核，不把新观察覆盖旧源证据。

旧prepare API继续是开发用摘要台账，不声称验签；本原型未成为正式包钩子授权路径。用户venv仍可变，证据检查不是OS隔离或整个site-packages不可变承诺，签名payload也不等于venv运行行为全被认证。正式发布/信任根/包钩子/激活时再次核验与完整健康验收仍缺，不解除包覆盖保护。失败准备的管理员收口尚未实现。

开发验收`accept-native-signed-approval.py`只prepare-signed及approve，断言无apply意图。真实隔离Linux从普通UID运行候选到管理员重新验签批准通过，未apply、未激活或start。临时测试密钥、验签依赖临时联网安装，非正式发布/离线新制品验收。更新测试31项，完整Device909项通过。

## 准备命令发送屏障与管理员只读观察

原生prepare使用限域ContextVar发送者，将共用核心创建venv、安装、依赖检查及健康检查命令交给独立标准库guard。每条命令先持久意图（准确准备request、序号、argv、cwd），guard继承操作execution.lock，等待直接子进程后no-replace发布匹配direct_child_reaped回执。旧非原生安装默认调用不变。超时回执code=124只证明直接子进程被回收，明确descendants_confirmed=false。

`python -m device_runtime.native_prepare_sender --root ROOT --operation-id ID`限管理员，只读检查schema2标记与发送账本。返回sender_busy、unknown或direct_children_reaped_descendants_unconfirmed；**所有结果retry_allowed=false**，不是管理员恢复许可，不写恢复记录、不删除失败现场、不解除新ID门禁。缺回执、意图/请求不匹配、序号缺口拒绝或保持unknown；不能因锁已可独占就声称旧后代全部结束。命令可能派生后代，完整管理员收口仍缺可靠生命周期归属机制，暂不实现解除占用。

真实有限寿命进程测试覆盖guard正常/失败/超时、调用方关闭fd后继承锁、guard异常退出缺回执但旧子进程仍运行、直接子进程退出而后代仍活动；后两种均不允许重试。发送者11项、Device920项通过；本轮无Linux完整准备链或真实cgroup归属回收验证，不沿用旧准备实验冒充新guard实机验收。

## Linux 准备进程树强屏障与显式退休（P1 开发原型）

本节修正早期“仅直接子进程回收、不能退休”的描述，仅适用于新 `linux-subreaper-v1` 操作。guard 在启动命令前设置 Linux child-subreaper，等待直接子进程和收养后代自然退出，直到 `waitpid` 返回 ECHILD 才持久发布 `process_tree_reaped`。改变 session/process group 不绕过收养。命令超时仍只按既有逻辑回收直接进程，后代未退出会继续占用发送锁；本机制不主动终止整棵树、不承诺等待有界。guard 崩溃、缺回执、旧 `direct-child-v1` 回执都不能升级为完整证据。

`observe` 即使报告 `process_trees_reaped`，仍返回 `retry_allowed=false`。管理员必须显式执行：

```bash
python -m device_runtime.native_prepare retire --root <隔离实例绝对路径> --operation-id <失败操作ID>
```

仅 Linux root 可执行。核对实例 owner、可信 marker、源 active、管理独占与安装共享锁、失败/未完成状态、连续命令意图及全部强回执、发送锁空闲后，原子生成 root 所有的 0644 `.retired.json`（只含非秘密绑定/摘要）。成功候选拒绝退休。退休不删除旧 release 或台账、不重放旧 ID、不激活；新准备必须显式给新 ID。重复退休仅在同一源与证据下幂等。既有 0600 marker 不自动改权限。

这是可信本地协作协议，不是防恶意 owner 篡改的 OS 隔离。管理员没有缺回执的通用强制恢复入口，不能删意图绕过阻塞。

可复现验收脚本为 `packages/device-runtime/scripts/accept-native-retirement.py`，仅用于预登记、可丢弃的 Linux 测试实例，使用与 `accept-native-prepare.py` 相同的临时开发签名制品。依次由普通 owner 执行 `fail`、root 执行上述 retire、owner 执行 `retry`：

```bash
python packages/device-runtime/scripts/accept-native-retirement.py fail \
  --root <实例> --package <开发DEB> --envelope <开发签名JSON> \
  --keys <外部公钥映射JSON> --release-id <准确release> --uv <持久uv绝对路径>
# root：retire --root <同一实例> --operation-id failed-tree
# owner：同样参数，将 fail 改为 retry
```

脚本注入真实 exit(7) 准备命令并核对完整回执；退休后核对旧 ID 拒绝、新 ID 成功且稳定、失败成果保留、active 不变。它不安装包、不调用服务、不代表正式离线签名依赖交付、实机或宿主重启验收。完整 P1 更新/激活闭环仍未完成，原包钩子拒绝覆盖保护保持不变。

## 包事务独立运行时副本（P1开发原型）

Debian发送者不再直接使用待替换包的 `sys.executable`。管理员持包事务独占锁，在apply意图前将当前 `sys.base_prefix` CPython树复制到该事务直属 `.bootstrap/runtime`，逐成员核对root可信路径、类型、相对内部链接、权限、大小与SHA256；复制使用独立文件，不硬链接包内文件。复制前后核对源与副本一致、逐文件及目录fsync，最后无覆盖发布 `snapshot.json`。总预算50000成员/2GB；特殊文件、绝对或越界链接拒绝。

apply意图绑定snapshot回执SHA256，启动前重新核对副本内容及回执摘要，使用副本解释器 `-I -B` 执行标准库guard；`-B`避免运行时写pycache改变快照。快照失败不写apply意图；无回执目录保持阻塞，不自动覆盖、清理或迁移。完成副本可重复核对，不要求原包文件继续存在或不变。当前副本仅支撑发送者，尚未把整个管理器切换到独立job，也未放开包钩子。

`packages/device-runtime/scripts/accept-native-bootstrap.py --source <可信CPython根> --destination <管理器事务目录下新目录>` 用于隔离root验证。实际副本6238成员，独立inode、同ID稳定、重定位prefix及ssl/sqlite3/ctypes加载通过；未运行真实dpkg替换，未构建包含此模块的新制品，不能视为完整升级验收。现有包保护和多实例拒绝不变。

独立发送者跨替换已增加标准dpkg验收：`accept-native-bootstrap-replacement.sh V15_DEB REPOSITORY` 仅在标记 `DEVICE_DISPOSABLE_CONTAINER=bootstrap-replacement-v1` 的新root Linux临时容器中运行。脚本构建独立名测试包v1/v2，替换移除原CPython解释器及标准库后验证快照guard匹配回执和副本继续可用；不会安装Device包或授予其覆盖许可。真实实验同时修复Debian guard缺少sbin的PATH：固定为 `/usr/sbin:/usr/bin:/sbin:/bin`，不继承调用方PATH。该证据不代表整个管理器已脱离待替换包；独立job、正式包钩子许可和激活恢复仍缺。

快照guard现在独立执行准确包数据库观察并无覆盖/fsync发布outcome：意图绑定target_package，只有dpkg返回0且dpkg-query准确status/version/architecture匹配才为pending activation，其余unknown。dpkg日志写事务.package.log，不依赖父进程管道。sender回执仍先于outcome，缺outcome不猜成功；不解除持久意图或执行激活。隔离替换脚本可加 `BOOTSTRAP_PARENT_EXIT=1` 注入发起进程exit23，已验证其退出后guard仍完成替换及终态落盘。这不是完整systemd独立job/任务收口或Device包覆盖许可。

独立job新增Python显式原型dispatch_job，尚未公开CLI/IPC或真实systemd验收。准确批准后包锁内核对完整快照与当前worker源码，先job-intent持久化，解锁后一次systemd-run；dispatch_accepted/unknown仅表示派发。job意图持续阻止启动、登记和其他包apply，同ID不重发。worker在快照内重进原apply并验证意图、源/批准/停止与锁，不将派发时快照代替执行前重验。没有自动重试/激活/包钩子许可，未知派发暂保持阻塞；新原型尚未纳入v17制品，动态验收待完成。

Python只读诊断observe_job读取现有job意图/批准/apply/sender/outcome并systemctl show，不创建目录/锁或修改台账。区分dispatch_unknown、job_in_progress、job_failed_requires_review与package_outcome_recorded；包终态要求完整匹配证据。缺unit/超时不推断未执行，failed不代表可重试；retry_allowed始终false、门禁保留。仅单测验证只读及证据拒绝，尚未从新runtime做动态诊断验收；显式恢复/解除排队未知入口仍缺。

Python recover_job新增管理员显式对账原型：仅已进入apply、匹配sender、准确批准和已退出无Job的loaded unit，调用原recover锁内重验后写绑定摘要的job-recovery记录。重复不重执行，retry_allowed=false、fence_retained=true，不解除意图/启动门禁或激活。已单测及完整回归，尚未真实systemd验收/公开CLI。执行前失败、unit未知、缺sender仍阻塞；不是通用退休或新ID重试许可。

已执行job隔离systemd对账验收：新派发unit使用RemainAfterExit=yes；恢复仅额外接受loaded active/exited、Result=success且PID为0、无Job。缺unit不等于退出，旧已回收unit仍阻塞。低版本空源fixture和真实Device候选保护拒绝的隔离测试覆盖缺sender拒绝、幂等和门禁保留；不证明真实设备升级成功，不解除执行前失败job门禁。

执行前关闭屏障原型：新job声明package-lease-v1，管理员close_before_apply在实例锁及全局包EX锁下核验批准、意图与相同worker快照，缺apply/sender/outcome时持久写closed；迟到worker在同一锁检查并拒绝。隔离systemd排队迟到验证通过。此操作不退休job、不清除门禁、不允许新ID；旧快照或旧协议不能适用。

新代码含执行前关闭job的显式退休原型：精确绑定intent及关闭摘要，无apply/sender/outcome，保留全部记录；新门禁识别有效退休，新ID须重新准备批准，旧ID不能重放。已安装旧helper不识别退休，仍保守阻塞；不可把隔离验证当作现有生产bootstrap支持或成功升级。原包钩子不放行。

开发包构建脚本已加固：DEB对任意已有packages/bootstrap根（含悬空链接）拒绝普通安装覆盖；RPM增加根路径拒绝但仅静态检查，未验证悬空链接。版本化布局、旧helper升级切换和真实RPM验收仍未完成。

开发v24 DEB/RPM已在隔离包管理器验证其他版本目录及悬空bootstrap链接拒绝、全新安装和manager存在时卸载拒绝；RPM用--nodeps。此为钩子验证而非正式发行、稳定bootstrap升级或真实设备验收。

独立事务快照支持管理员候选helper自检原型：绑定快照与模块摘要、检查隔离Python及helper加载路径，前后核验快照；未验签、未启动服务或切换bootstrap，不代表旧helper已迁移。

版本化helper候选发布原型在调用方持包锁时复制并自检独立快照，以版本ID和摘要写不可覆盖的未激活证据；冲突或不完整现场拒绝。不含管理入口、签名、旧helper切换或服务兼容保证。

管理候选发布入口要求预设可信目录并自行持包EX锁；只读检查重验快照和发布证据，不执行候选。仅隔离验证，仍不包括签名、激活或旧helper切换。

切换只读观察原型绑定现有unit与候选摘要，但明确不检查进程静止或协议兼容，switch_allowed始终false；不修改unit或执行服务操作。

**重要更正（第48节）**：v17包内旧helper只检查apply-intent，实际忽略未退休job-intent，故不保证排队窗口阻止启动；此前“旧helper保守阻塞”不适用于该版本。不得与新job派发混合用于真实设备；须先验证运行helper身份并完成安全迁移/兼容屏障。

当前新代码的dispatch_job对新job默认失败关闭，直到跨版本持久启动屏障得到实现和验证；已有job仍可观察、不重派。旧运行代码不受此新门禁约束，不能据此声称真实设备已安全。

accept-native-old-helper-fence.sh在一次性容器复现v17旧helper忽略未退休job、新helper阻塞、新派发默认拒绝；不证明跨重启持久互斥或实际迁移完成。

### 旧测试helper手动退休（当前方案）

用户确认旧helper仅为测试原型，不支持原地自动迁移或接管旧台账。管理员需在维护窗口手动停止并退休旧测试环境，核实所有启动入口、在途sender和未终态操作；有未知或残留不得安装/切换。AI不停止真实服务、不自行删除旧数据。已有包覆盖拒绝继续保留；新job派发仍默认关闭，待干净安装、实际helper运行身份及新版本自身互斥和失败恢复经过隔离验证后，才能另行评估。手动退休不代表以上验证或真实部署授权已完成。此前迁移草案仅作历史分析，不是实施计划。
