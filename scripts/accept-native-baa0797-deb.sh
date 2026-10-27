#!/bin/sh
# Fresh, network-isolated disposable Linux container only.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' 7f0935ad186ece580d3d3e6e7aba6c0c128c52ace649b9dff8866f07257ae542 "$pkg" | sha256sum -c -
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
base = Path('/opt/ai-device/packages/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime')
assert all(Path(m.__file__).parent == base for m in (h, u, j))
assert h._valid_admin_approval({'prepared_sha256':'abc','approved_by_uid':0}, 'abc')
for value in ({'prepared_sha256':'abc'}, {'prepared_sha256':'abc','approved_by_uid':False},
              {'prepared_sha256':'abc','approved_by_uid':0,'extra':1}, []):
    assert not h._valid_admin_approval(value, 'abc')
# Isolated fixtures only; real root ownership/ancestor checks are replaced.
h._trusted = lambda *args, **kwargs: None
root = Path('/tmp/job-evidence'); root.mkdir()
stem = root / '5-probe--one'
path = stem.with_suffix('.job-intent.json')
closed_path = stem.with_suffix('.job-closed.json')
retired_path = stem.with_suffix('.job-retired.json')
for drift in ('none', 'schema', 'retry', 'fence', 'replay', 'missing', 'extra', 'instance', 'unit', 'prepared', 'snapshot'):
    intent = {'schema_version': True if drift == 'schema' else 1,
              'state':'job_dispatch_intent','close_protocol':'package-lease-v1',
              'instance_id':'probe', 'operation_id':'one',
              'unit':'ai-device-package-' + hashlib.sha256(str(stem).encode()).hexdigest() + '.service',
              'prepared_sha256':'a' * 64, 'runtime_snapshot_sha256':'b' * 64}
    if drift == 'missing': del intent['instance_id']
    if drift == 'extra': intent['extra'] = True
    if drift == 'instance': intent['instance_id'] = 'other'
    if drift == 'unit': intent['unit'] = 'wrong'
    if drift == 'prepared': intent['prepared_sha256'] = 'bad'
    if drift == 'snapshot': intent['runtime_snapshot_sha256'] = 'bad'
    path.write_text(json.dumps(intent))
    closed = {'state':'closed_before_apply','job_intent_sha256':h._digest(path),
              'retry_allowed':0 if drift == 'retry' else False,
              'fence_retained':1 if drift == 'fence' else True}
    closed_path.write_text(json.dumps(closed))
    retired = {'state':'retired_before_apply','job_intent_sha256':h._digest(path),
               'closed_sha256':h._digest(closed_path),
               'old_job_replay_allowed':0 if drift == 'replay' else False}
    retired_path.write_text(json.dumps(retired))
    assert h._job_retired(path) is (drift == 'none'), drift
try:
    j._require_proven_job_fence()
except j.JobError:
    pass
else:
    raise AssertionError('dispatch gate opened')
print('CURRENT_DEB_APPROVAL_AND_RETIREMENT_FIXTURE_OK', flush=True)
PY
if dpkg -i "$pkg" >/tmp/reinstall.log 2>&1; then
 echo 'unexpected in-place replacement' >&2; exit 1
fi
grep -q 'helper transaction required' /tmp/reinstall.log
echo CURRENT_DEB_FRESH_INSTALL_AND_PROTECTION_OK
