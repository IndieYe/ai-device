#!/bin/sh
# Fresh disposable, network-isolated Linux container only; never use a host service.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' 0d3ccfd5647bf27b1d6965fec90c0ed20e18cb486939e7837e8231af4df10001 "$pkg" | sha256sum -c -
mkdir /tmp/control
dpkg-deb -e "$pkg" /tmp/control
grep -q 'native lifecycle helper transaction required' /tmp/control/preinst
grep -q 'explicit instance/service uninstall required' /tmp/control/prerm
dpkg -i "$pkg"
base=/opt/ai-device/packages/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime
bootstrap=/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime
for module in __init__.py native_bootstrap.py native_helper.py management_lock.py native_update.py native_update_job.py; do
 cmp "/source/$module" "$base/$module"
done
for module in __init__.py native_bootstrap.py native_helper.py management_lock.py; do
 cmp "/source/$module" "$bootstrap/$module"
done
/opt/ai-device/packages/0.2.2-native1/runtime/bin/python3.13 -I -B - <<'PY'
import hashlib, json
from pathlib import Path
from device_runtime import native_helper as h, native_update as u, native_update_job as j
assert str(Path(u.__file__)).startswith('/opt/ai-device/packages/0.2.2-native1/')
root = Path('/run/device-job-intent-fixture'); root.mkdir(mode=0o700)
stem = root / '5-probe--one'
snapshot = stem.with_suffix('.bootstrap'); snapshot.mkdir(mode=0o700)
(snapshot / 'snapshot.json').write_text('fixture')
stem.with_suffix('.prepared.json').write_text('prepared')
u.nb.validate_snapshot = lambda _: None
intent = {'schema_version': 1, 'state': 'job_dispatch_intent', 'close_protocol': 'package-lease-v1',
          'unit': 'ai-device-package-' + hashlib.sha256(str(stem).encode()).hexdigest() + '.service',
          'instance_id': 'probe', 'operation_id': 'one',
          'prepared_sha256': h._digest(stem.with_suffix('.prepared.json')),
          'runtime_snapshot_sha256': h._digest(snapshot / 'snapshot.json')}
path = stem.with_suffix('.job-intent.json'); path.write_text(json.dumps(intent))
u._verify_job_intent(stem, 'probe', 'one', h._digest(path))
intent['schema_version'] = True; path.write_text(json.dumps(intent))
try:
    u._verify_job_intent(stem, 'probe', 'one', h._digest(path))
except u.UpdateError:
    pass
else:
    raise AssertionError('invalid schema accepted')
try:
    j._require_proven_job_fence()
except j.JobError:
    pass
else:
    raise AssertionError('dispatch gate opened')
print('CURRENT_DEB_JOB_INTENT_AND_GATE_FIXTURE_OK', flush=True)
PY
if dpkg -i "$pkg" >/tmp/reinstall.log 2>&1; then
 echo 'unexpected in-place replacement' >&2; exit 1
fi
grep -q 'helper transaction required' /tmp/reinstall.log
echo B4158DF_DEB_FRESH_INSTALL_AND_PROTECTION_OK
