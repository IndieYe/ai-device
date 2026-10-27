#!/bin/sh
# Fresh disposable Linux container; creates only an isolated transaction snapshot.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' 0d3ccfd5647bf27b1d6965fec90c0ed20e18cb486939e7837e8231af4df10001 "$pkg" | sha256sum -c -
dpkg -i "$pkg"
mkdir -p /var/lib/ai-device/manager/transactions
python=/opt/ai-device/packages/0.2.2-native1/runtime/bin/python3.13
"$python" -I -B - <<'PY'
import subprocess, sys
from pathlib import Path
from device_runtime import native_bootstrap as nb
source = Path(sys.prefix)
snapshot = Path('/var/lib/ai-device/manager/transactions/fixture')
nb.snapshot_runtime(source, snapshot)
nb.validate_snapshot(snapshot)
for name in ('native_update.py', 'native_update_job.py', 'native_helper.py', 'native_bootstrap.py', 'management_lock.py'):
    relative = Path('lib/python3.13/site-packages/device_runtime') / name
    assert (source / relative).read_bytes() == (snapshot / 'runtime' / relative).read_bytes(), name
subprocess.run([str(snapshot / 'runtime/bin/python3.13'), '-I', '-B', '-c',
    'import device_runtime.native_update as u, device_runtime.native_update_job as j; '
    'assert callable(u._verify_job_intent); '
    '\ntry: j._require_proven_job_fence()\nexcept j.JobError: pass\nelse: raise AssertionError("dispatch opened")'], check=True)
print('B4158DF_TRANSACTION_SNAPSHOT_MODULES_MATCH_GATE_CLOSED', flush=True)
PY
