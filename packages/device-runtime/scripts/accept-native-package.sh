#!/bin/sh
# Disposable Ubuntu/Rocky acceptance of an UNSIGNED development package only.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = yes ] || exit 2
kind=$1
[ ! -e /opt/ai-device ] || exit 2
if [ "$kind" = deb ]; then
 dpkg -i /packages/ai-device-native-client_0.2.2-1_amd64.deb
else
 rpm -i /packages/ai-device-native-client-0.2.2-1.x86_64.rpm
fi
# The installed package must contain the full offline bootstrap, not a source mount.
[ ! -e /task/packages/device-runtime/src ]
# Explicit normal user for the client; package installation alone uses container root.
useradd -m -u 12345 nativeprobe
mkdir -p /opt/ai-device/instances
chown nativeprobe:nativeprobe /opt/ai-device/instances
runuser -u nativeprobe -- env PATH=/usr/bin:/bin ai-device-native-install --root /opt/ai-device/instances/probe \
 --instance-id package-probe --server-url https://example.invalid
python=/opt/ai-device/instances/probe/releases/*/client/venv/bin/python
$python -I -m pip check 2>/dev/null || {
 # uv intentionally creates a venv without pip; verify through packaged uv instead.
 /opt/ai-device/packages/0.2.2-native1/tools/uv --no-config pip check --python $python
}
runuser -u nativeprobe -- $python -I -c 'import os; assert os.geteuid() == 12345; from device_runtime.executor import LocalExecutor; import PIL,psutil; from mcp_server_bash.core import ExecutionRuntime; import mcp_server_fs.core; print("non-root installed package offline client imports passed")'
head -1 /opt/ai-device/instances/probe/run | grep '^#!/opt/ai-device/packages/0.2.2-native1/runtime/bin/python3.13$'
echo preserve > /opt/ai-device/instances/probe/state/user-file
if [ "$kind" = deb ]; then
 if dpkg -i /packages/ai-device-native-client_0.2.2-1_amd64.deb >/tmp/repeat.log 2>&1; then exit 1; fi
 if dpkg -r ai-device-native-client >/tmp/remove.log 2>&1; then exit 1; fi
else
 if rpm -U --replacepkgs /packages/ai-device-native-client-0.2.2-1.x86_64.rpm >/tmp/repeat.log 2>&1; then exit 1; fi
 if rpm -e ai-device-native-client >/tmp/remove.log 2>&1; then exit 1; fi
fi
[ "$(cat /opt/ai-device/instances/probe/state/user-file)" = preserve ]
$python -I -c 'import device_runtime; print("refused replacement/removal preserved instance")'
