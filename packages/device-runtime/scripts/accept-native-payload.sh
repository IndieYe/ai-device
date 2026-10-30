#!/bin/sh
# Disposable Linux container; input mount must be read-only at /task.
# This helper accepts the current v5 development payload (not a signed release).
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = yes ] || exit 2
[ ! -e /opt/native-probe ] || exit 2
mkdir -p /opt/native-probe
cp -R /task/build/native-payload-v5/. /opt/native-probe/
tar -xzf /task/build/native-inputs/cpython-linux-x86_64.tar.gz -C /opt/native-probe
mv /opt/native-probe/python /opt/native-probe/runtime
python=/opt/native-probe/runtime/bin/python3.13
"$python" -I -m pip --isolated install --no-index --no-deps \
  --find-links /opt/native-probe/bundle/dependencies \
  /opt/native-probe/bundle/wheels/*.whl /opt/native-probe/bundle/dependencies/*.whl > /tmp/native-bootstrap.log 2>&1
# No source PYTHONPATH, no host Python/uv on PATH. No network resolver is used.
useradd -m -u 12345 nativeprobe
mkdir /opt/native-instance
chown nativeprobe:nativeprobe /opt/native-instance
# Core requires the instance root to be new, but its parent must be writable.
runuser -u nativeprobe -- env PATH=/usr/bin:/bin PYTHONPATH=/does/not/exist "$python" -I -m device_runtime.native_install \
  --payload /opt/native-probe --root /opt/native-instance/client --instance-id native-probe \
  --server-url https://example.invalid > /tmp/native-install.json
head -1 /opt/native-instance/client/run | grep '^#!/opt/native-probe/runtime/bin/python3.13$'
/opt/native-instance/client/releases/*/client/venv/bin/python -I -c \
  'import device_runtime, mcp_server_bash.core, mcp_server_fs.core, PIL, psutil; from device_runtime.executor import LocalExecutor; print("offline client dependency closure imports passed")'
if runuser -u nativeprobe -- "$python" -I -m device_runtime.native_install --payload /opt/native-probe \
  --root /opt/native-instance/client --instance-id native-probe --server-url https://example.invalid 2>/tmp/native-repeat.log; then
  echo 'ERROR: repeated install unexpectedly overwrote instance' >&2; exit 1
fi
grep 'existing instances' /tmp/native-repeat.log
cat /tmp/native-install.json
