#!/bin/sh
# Disposable Linux container only. Extracts the development DEB and installs wheels.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = old-helper-fence-v1 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -s)" = Linux ] || exit 2
[ "$#" = 2 ] || { echo 'usage: SCRIPT V17_DEB NEW_WHEELS' >&2; exit 2; }
[ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
dpkg-deb -x "$1" /
old=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
new=/opt/ai-device/packages/0.2.2-native1/runtime/bin/python3.13
"$new" -I -B -m pip --isolated install --no-index --no-deps --force-reinstall "$2/"*.whl >/tmp/old-helper-fence-pip.log
mkdir -p /var/lib/ai-device/manager/updates
# The job record is an isolated fixture, not an apply-intent or an authorization.
printf '{}\n' > /var/lib/ai-device/manager/updates/5-probe--one.job-intent.json
"$old" -I -B - <<'PY'
from pathlib import Path
from device_runtime import native_helper
native_helper._assert_no_package_intent(Path('/var/lib/ai-device/manager'))
print('OLD_HELPER_IGNORES_JOB_INTENT')
PY
"$new" -I -B - <<'PY'
from pathlib import Path
from device_runtime import native_helper
registry = Path('/var/lib/ai-device/manager')
try:
    native_helper._assert_no_package_intent(registry)
except native_helper.HelperError:
    print('NEW_HELPER_BLOCKS_UNRETIRED_JOB')
else:
    raise AssertionError('new helper did not block job')
PY
"$new" -I -B - <<'PY'
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from device_runtime import native_update, native_update_job
stem = Path('/var/lib/ai-device/manager/updates/5-probe--new')
@contextmanager
def transaction(*args):
    yield {}, stem
manager = SimpleNamespace(registry=stem.parent.parent, packages=native_update.Debian(),
                          _transaction=transaction, _prepared=lambda *args: {})
calls = []
try:
    native_update_job.dispatch_job(manager, 'probe', 'new', runner=lambda *a, **k: calls.append(a))
except native_update_job.JobError as exc:
    assert 'compatibility unproven' in str(exc)
else:
    raise AssertionError('new dispatch unexpectedly allowed')
assert not calls and not stem.with_suffix('.job-intent.json').exists()
assert not stem.with_suffix('.job-dispatch.json').exists()
print('NEW_DISPATCH_DISABLED_BEFORE_WRITES')
PY
