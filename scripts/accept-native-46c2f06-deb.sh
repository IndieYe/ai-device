#!/bin/sh
# Fresh, network-isolated disposable Linux container only.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' be58c0e5fcecc6f468a62aa5b01cfa588361049a9057c3915eedc08dfe13f6d8 "$pkg" | sha256sum -c -
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
import json
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
for drift in ('none', 'schema', 'retry', 'fence', 'replay'):
    intent = {'schema_version': True if drift == 'schema' else 1,
              'state':'job_dispatch_intent','close_protocol':'package-lease-v1'}
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
