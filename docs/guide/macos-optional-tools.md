# macOS 可选配套工具：tmux、Harness 与管理员配置

> 本文是独立工具的可选配置参考，不是 Device 安装要求、原生能力或产品验收标准。
> 只有需要交互终端、桌面自动化或整机管理时才选择相应章节；可单独安装、升级和卸载这些工具。
> Device 部署、迁移与连接验收见 [macOS Device 部署指南](macos-ai-workstation-setup-and-verification.md)。
> 默认命令在目标 Mac 执行；标明通过 Device 的步骤必须由目标设备执行链运行。

## 安装准备

### 按所选工具准备安装方式

下列只是本文采用的安装路径，不要求同时安装两个包管理器，也不是工具运行时的必要依赖：

| 选择的工具 | 本文安装路径 | 何时需要准备 |
| --- | --- | --- |
| tmux | Homebrew | 选择 brew 安装且尚无可用 tmux 时 |
| macOS Harness | uv tool（Python 3.12） | 选择本文安装方式且尚无可用 Harness 时 |

已有可用安装时先核对版本、入口与来源，不为使用工具重复安装包管理器。若选择对应路径，仅检查所需命令：

```bash
# 仅选择 Homebrew 安装 tmux 时检查；不是整段必做清单。
command -v brew
```

```bash
# 仅选择 uv 安装 Harness 时检查。
command -v uv
```

缺失时按所选包管理器的官方说明准备。不要以 root 执行 Homebrew。Apple Silicon 常见 brew 路径为 `/opt/homebrew/bin/brew`，Intel 常见为 `/usr/local/bin/brew`，实际位置以本机为准。

通过后台服务调用工具时，不假定加载 `.zshrc` 等配置，优先使用工具的绝对路径。

### 可选：安装进程使用代理

先确认代理位于目标 Mac 且端口实际可用。下面在子 Shell 中设置，只影响其中运行的安装命令及其子进程，不改变外层终端环境：

```bash
(
  set -eu
  # 示例地址及安装命令，按本机已核实信息替换。
  export http_proxy=http://127.0.0.1:7890
  export https_proxy=http://127.0.0.1:7890
  brew install tmux
)
```

不要在文档或日志中写入代理认证信息。已有安装不会自动继承后来设置的代理；原任务未知或仍运行时，不并发重复安装。

## 工具独立安装（按需选择）

### tmux

```bash
brew install tmux
tmux -V
command -v tmux
```

记录其绝对路径，供远程命令使用。tmux 提供 PTY 和持续输入，不提供管理员权限，也不是安全密码传递通道。

### macOS Harness

官方项目：https://github.com/browser-use/macos-harness

按官方要求使用 Python 3.12：

```bash
uv tool install --python 3.12 macos-harness
macos-harness --version
command -v macos-harness
```

已安装时先核对版本与官方升级说明，不默认 force 覆盖。普通 CLI 使用不必给其他 Agent 安装技能或 Hook。需要 Claude/Codex 等专属接入时，再参考上游 `install.md`。

上游匿名遥测默认开启；如不需要：

```bash
macos-harness telemetry disable
```

## 可选：免密 sudo

只在用户明确需要 AI 管理整机时配置。它将 root 能力授予该账号的所有进程，不只 AI。受限任务优先只放行经过审核的管理入口，避免给 shell、解释器或可修改脚本做“伪白名单”。

管理员在目标终端执行：

```bash
sudo visudo -c
sudo env EDITOR=/usr/bin/nano /usr/sbin/visudo
```

保留原规则，按实际短用户名添加：

```sudoers
<SHORT_USERNAME> ALL = (root) NOPASSWD: ALL
```

尖括号不属于真实配置。保存后检查：

```bash
sudo visudo -c
sudo -k -n /usr/bin/id -u
sudo -k -n -l
```

应在不提示密码时返回 UID `0`，有效规则显示 `(root) NOPASSWD: ALL`。

- `-n`：不询问密码，需要认证则立即失败，不是绕过权限。
- `-k` 配合命令：忽略认证缓存，避免假阳性。
- 保留维护终端直至语法与实际行为验证通过。
- 撤销：用 visudo 删除新增规则，重新检查；其他既有规则可能继续允许免密。
- 不经对话、tmux、stdin 或临时文件传管理员密码。

## 授予 macOS 桌面权限

独立使用 Harness 时，从准备长期调用它的应用／终端环境运行原版 CLI；这是工具自身的权限检查，不需要 Device：

```bash
/absolute/path/to/macos-harness doctor
```

如果计划通过 Device 调用，须另从**实际 Device 执行链**执行同样的 doctor 和真实截图检查；不要只在 Terminal 运行后就推断 Device 同样有权限。macOS 通常把权限归属到启动链的责任程序，可能是终端、Agent 应用或 Python 可执行文件，不一定是 Harness CLI。

根据 doctor 及实际责任进程定位，由用户在“系统设置 → 隐私与安全性”授权：

- 辅助功能：读取和操作界面。
- 屏幕录制：获取截图。
- 发送事件权限：根据诊断确认。
- 输入监控：用于检测用户操作、减少冲突；是否必需以当前版本为准。

不要无差别给所有 Python 或终端授权。选择隐藏路径时可按 Command+Shift+G 输入准确路径。解释器升级、账号或启动方式改变后复查。

### 通过 Device 使用时的附加要求：Helper 来源与刷新

本小节仅用于 Device 组合场景。独立使用时由实际调用环境创建 Helper，不要求从 Device 启动；Helper 管理仍由用户执行。

若系统要求退出并重开，须同时考虑 Device 与独立 Helper，不要未经恢复计划退出远程控制进程。

Harness 的 helper/approve 管理命令由用户执行。首先确定使用默认 socket 还是独立 socket；同一个 Helper 的所有管理和调用必须保持配置一致。

**本机终端，仅准备目录和停止旧 Helper：** 默认 socket 不设置环境变量。若选独立 socket，先在目标账号准备私有目录（已存在时先核对归属和 0700 权限），再使用同一配置停止：

```bash
mkdir -m 700 "$HOME/.macos-harness-desktop"
export MACOS_HARNESS_SOCKET="$HOME/.macos-harness-desktop/helper.sock"
/absolute/path/to/macos-harness helper stop
```

上述 stop 只停止指定 socket 的实例，不停止默认或其他实例；需要清理旧默认实例时，由用户在未设置该变量的命令环境中单独停止。不要把“未运行”误认为所有 Helper 都退出。

**下面必须通过 Device 执行，不在本机终端执行：**

```bash
export MACOS_HARNESS_SOCKET="$HOME/.macos-harness-desktop/helper.sock"
/absolute/path/to/macos-harness doctor
```

由 Device 自动创建 Helper，防止终端先启动而改变权限归属。若使用默认 socket，则这两处都不设置 MACOS_HARNESS_SOCKET。不要从本机终端执行 doctor/restart 来代替上述启动。

后续通过 Device 的每次 Harness 调用都必须使用同一 socket 配置。可在自有启动配置中统一注入，并核实执行命令确实继承；临时 shell 的 export 不会自动跨 Device 请求保留。独立 socket 是工具配置，不是产品集成。停止／重启 Helper 后，仍需重新验证真实截图。

## 可选管理员配置验收

仅在用户选择并完成免密 sudo 配置后，验证目标普通账号的有效授权；不将此项作为 Device 安装或连接验收条件。本机验证已见上文 sudo 配置章节。若还需验证通过 Device 执行提权命令，可从实际 Device 执行链运行：

```bash
sudo -k -n /usr/bin/id -u
```

该可选组合测试应不提示密码、返回 UID 0 和退出码 0；失败时检查账号与 sudo 规则，不据此判定 Device 基础连接失败。不要传递管理员密码。

## 工具功能验收（仅在选择该工具后执行）

### Harness 独立功能验证：界面、交互与截图

从实际使用环境调用原版 Harness，并保持约定的 socket 配置。独立使用不需要 Device；若验证 Device 组合调用，以下操作及图片取回必须另在该执行链完成：

```bash
macos-harness doctor
macos-harness get_app_state --app Calculator --keep
```

- doctor 应显示 required permissions 已满足。
- 界面读取应返回窗口和可操作元素。
- 先确认普通计算界面，清除并读回 `0`；再用本次读取的元素编号点击 `2`、`+`、`3`、`=`，再读取界面确认 `2 + 3` 和 `5`。
- 发送 Escape 并读回 0，减少对原界面的影响。
- 操作会改变元素编号，不凭历史编号继续点击。
- 应生成真实截图，不接受 `Screenshot: none`。
- 独立本机验证直接检查生成的图片，确认格式、尺寸和画面；仅在 Device 组合验证时，通过同设备 read-image 取回，不将远端路径直接交给控制端文件工具。

典型点击形式：

```bash
macos-harness click --app Calculator --element_index '<当前编号>'
macos-harness get_app_state --app Calculator --keep
```

不要把认证窗口、钥匙串、私人文档当作测试对象。Harness 不是绕过系统密码窗口或 Touch ID 的工具。

### tmux 独立交互验证与 Device 跨请求附加验证

独立使用 tmux 时，可在目标 Mac 的两个独立 Bash 调用中分别运行以下两段代码，复用第一段返回的测试目录，不需要 Device。仅当验证 Device 组合调用时，才要求两段代码分别通过 Device 请求在目标 Mac 执行。使用已核实的 tmux 绝对路径。每次新建私有短路径目录，用显式配置启动独立服务器，不连接已有服务器；本测试不包含密码。独立工具验证使用两次独立 Bash 调用；Device 组合验证使用两次独立 Device 请求，120 秒输入等待超时即失败，不及时完成则重新建立新测试。

**请求一：建立测试并输入第一轮。**

```bash
set -eu
TMUX_BIN='/absolute/path/to/tmux'
TEST_DIR="$(mktemp -d /tmp/ai-tmux.XXXXXX)"
SOCKET="$TEST_DIR/s"
cat > "$TEST_DIR/test.sh" <<'SH'
#!/bin/bash
set -eu
[ -t 0 ] && [ -t 1 ] || exit 10
printf 'TTY_OK\nFIRST>\n'
IFS= read -r -t 120 first || exit 11
[ "$first" = 'hello-device' ] || exit 12
printf 'FIRST_OK\nSECOND>\n'
IFS= read -r -t 120 second || exit 13
[ "$second" = 'confirm' ] || exit 14
printf 'PASS_TWO_ROUNDS\n'
SH
# 使用受控配置而非用户默认配置。非默认编号也不影响实际 pane ID 定位。
cat > "$TEST_DIR/tmux.conf" <<'CONF'
set -g base-index 1
setw -g pane-base-index 1
set -g exit-empty on
CONF
# mktemp 的路径不含空格；仍保持命令参数边界。
PANE="$("$TMUX_BIN" -f "$TEST_DIR/tmux.conf" -S "$SOCKET" new-session -d -P -F '#{pane_id}' -s verify "/bin/bash '$TEST_DIR/test.sh'")"
printf '%s\n' "$PANE" > "$TEST_DIR/pane-id"
"$TMUX_BIN" -S "$SOCKET" set-window-option -t "$PANE" remain-on-exit on
"$TMUX_BIN" -S "$SOCKET" send-keys -t "$PANE" -l -- 'hello-device'
"$TMUX_BIN" -S "$SOCKET" send-keys -t "$PANE" Enter
"$TMUX_BIN" -S "$SOCKET" capture-pane -p -S - -t "$PANE"
printf 'TEST_DIR=%s\n' "$TEST_DIR"
```

记录返回的 TEST_DIR。应读到 TTY_OK、FIRST_OK 和 SECOND>；若尚未出现，可在相同 socket 再抓取一次画面，不重复发送第一轮。失败时保留现场，不把终端回显的输入当成程序验证通过。

**请求二：使用上次返回的目录输入确认值，最多等待约 5 秒收尾。**

```bash
set -eu
TMUX_BIN='/absolute/path/to/tmux'
TEST_DIR='/tmp/ai-tmux.替换为请求一返回的后缀'
SOCKET="$TEST_DIR/s"
[ -d "$TEST_DIR" ] && [ -S "$SOCKET" ]
PANE="$(cat "$TEST_DIR/pane-id")"
[ -n "$PANE" ]
"$TMUX_BIN" -S "$SOCKET" send-keys -t "$PANE" -l -- 'confirm'
"$TMUX_BIN" -S "$SOCKET" send-keys -t "$PANE" Enter
for attempt in 1 2 3 4 5; do
  dead="$("$TMUX_BIN" -S "$SOCKET" display-message -p -t "$PANE" '#{pane_dead}')"
  [ "$dead" = 1 ] && break
  sleep 1
done
[ "$dead" = 1 ] || { echo 'Test has not exited' >&2; exit 1; }
status="$("$TMUX_BIN" -S "$SOCKET" display-message -p -t "$PANE" '#{pane_dead_status}')"
"$TMUX_BIN" -S "$SOCKET" capture-pane -p -S - -t "$PANE"
[ "$status" = 0 ] || { printf 'Test failed: %s\n' "$status" >&2; exit 1; }
for marker in TTY_OK FIRST_OK PASS_TWO_ROUNDS; do
  "$TMUX_BIN" -S "$SOCKET" capture-pane -p -S - -t "$PANE" | grep -Fx "$marker"
done
printf 'Verified exit status: %s\n' "$status"
```

显式 `-f` 不读取用户默认配置；本例主动使用从 1 开始的窗口／窗格编号，并始终使用 new-session 返回的 pane ID，不依赖编号。系统级配置是否加载以该 tmux 版本为准；如系统配置还含额外 Hook，须先核实其影响。

同时满足 TTY_OK、两轮读回成功标记、PASS_TWO_ROUNDS 和实际退出码 0 才通过。remain-on-exit 只保留退出后的窗格，不把错误变成成功。

验收并保存必要结果后，使用相同 TMUX_BIN、TEST_DIR、SOCKET，仅清理本次测试会话（不使用 kill-server）：

```bash
set -eu
"$TMUX_BIN" -S "$SOCKET" kill-session -t verify
# 本服务器仅含本次 verify 会话；结束后仅删除这个已知 socket。
# 若会话结束失败，set -e 会阻止后续清理。
if [ -S "$SOCKET" ]; then
  rm -- "$SOCKET"
fi
# 确认目录归属本次测试后，只删除明确创建的文件，不递归删除未知路径。
rm -- "$TEST_DIR/test.sh" "$TEST_DIR/tmux.conf" "$TEST_DIR/pane-id"
rmdir -- "$TEST_DIR"
```

若目录仍非空，rmdir 会失败；检查归属及残留原因，不扩大删除范围。失败测试也只在确认该测试已不再需要后清理。

## 桌面工具排障

### 权限通过，但截图失败

区分：用户身份、sudo 权限、图形会话上下文、TCC 权限。四者不是同一等级的权限。

排查顺序：

1. Device 是否在正确账号的 Aqua 会话中执行。
2. Helper 是否由旧终端或旧后台服务启动。
3. 目标桌面是否解锁、窗口是否实际存在。
4. 系统截图命令的退出码、stderr、耗时及实际文件大小。
5. 图像能否解码、是否黑屏。

曾有 System 启动域权限预检通过而窗口截图失败、进入桌面上下文后成功的实测案例，但这不代表所有 LaunchDaemon 必然无法截图，不要将现象泛化成操作系统绝对限制。

### root 仍不能操作某个功能

root 不自动授予屏幕录制、辅助功能、所有钥匙串秘密、Secure Enclave 私钥或本人认证能力。不要为排障修改 TCC 数据库或关闭系统保护。

## 工具验收边界与历史基线

- tmux：TTY、两轮交互与真实退出码；Harness：权限、界面读回、截图和同设备图片读取。
- 如果使用 Device 调用工具，重新登录后应另行验证工具权限与截图，不能仅凭 Device 在线判断工具可用。
- 仅清理本次测试的会话、Helper 与临时数据，不触碰其他工具实例或正式进程。

历史实际验收日期为 2026-11-01：Apple Silicon macOS 27.0.1、tmux 3.8、Harness 0.2.1，通过 Device 0.2.3 调用完成终端交互及界面操作、截图。这不是新机器或其他版本的通过证据。Harness 安装与权限说明基于该版本官方指引和历史核验，不表示本轮重新联网核验上游。

此前在编写文档的本机独立临时 tmux 中执行过两轮输入、TTY／标记／退出码检查及清理，未经过远端 Device。本轮仅整理文档和静态检查，未重新运行工具验收。
