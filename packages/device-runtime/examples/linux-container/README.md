# Apple Container Ubuntu ARM64 隔离验收

这些脚本仅用于新建、一次性的 Linux 验收实例，不是正式服务启动器。不得对用户已有数据或正式实例执行。

## 工具链镜像

`Containerfile` 基于 Ubuntu 24.04，提供 uv 0.10.9、Python 3.13 和可被稳定run入口找到的python3。默认普通用户，不包含凭证、源码或交付wheel。实际验收须保留基础镜像解析到的digest、Python完整版本和构建日志；标签本身不是不可变制品标识。

```bash
container build --cpus 2 --memory 2G --platform linux/arm64 \
  --file Containerfile --tag <unique-test-image> .
```

## 制品布局

只读输入目录必须由准确wheel组成，不可用PyPI同名包替代：

```text
<delivery>/
├── wheels/ai_device-0.2.2-py3-none-any.whl
├── linux-bundle/          # 原版本，target linux、仅client角色
└── linux-upgrade-bundle/  # 独立release_version，已验证的同Device状态版本
```

两个bundle的三包来源提交、Hash和第三方约束必须已验证。脚本的Device wheel名称当前对应0.2.0；组件版本变化须同步脚本和状态兼容验收，不默认跳过兼容门禁。

## 单次执行

先准备**全新空输出目录**，确保所选非root UID/GID拥有写权限。只挂载输入制品、这些脚本和输出目录，不挂载个人目录、正式凭证或主配置，不发布端口，不使用特权模式。

```bash
container run --name <unique-test-container> --cpus 2 --memory 2G \
  --platform linux/arm64 --network default --user "$(id -u):$(id -g)" \
  --env HOME=/work/home --env UV_CACHE_DIR=/work/cache \
  --volume <delivery>:/delivery:ro \
  --volume <scripts>:/input:ro \
  --volume <new-output>:/work \
  <unique-test-image> /bin/bash /input/bootstrap.sh
```

脚本安装独立client实例，以临时回环中心和stdio MCP验证11工具、中文命令、文件及两项dry-run、图片、任务和7字节输出游标，然后在自有测试进程退出后验证重建、升级、回退、备份恢复及恢复后上线。认证值随机生成，只在内存和子进程环境中供给，不写文件或打印。

`run-acceptance.py`只收尾自己刚创建的临时进程，不接管或停止真实服务。容器中的center/mcp是测试装置，不是Linux center/mcp角色正式安装支持。

## 结果未知或失败

`started.json`、`evidence/operation-manifest.json`和已有操作ID是对账入口。**禁止原样重跑已有输出目录，禁止换ID重放未知操作。** 按具体检查点只读定位并单独续做尚未执行的部分，保存原失败脚本与日志。功能验收通过但收口或维护失败时，不重复功能操作。

最终以功能报告、两侧操作库、任务库及维护报告综合判断；一次脚本退出码不应抹掉已完成的操作证据。任务库与操作库均使用`operations`表，但属于不同数据库。

## 覆盖限制

本例不验收Mac主ServerPool、公网、VPN/TLS远端入口、systemd、主机重启或x86_64。Ubuntu容器成功不等于所有Linux发行版/架构正式支持。镜像构建器可能按需启动；不得为了测试收尾停止既有真实容器或构建服务。
