#!/bin/sh
# Disposable isolated Linux only; intentionally retains a short-lived fixture child.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = candidate-publication-descendant-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, shutil, sys, tempfile, time
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h
with tempfile.TemporaryDirectory(prefix='candidate-publish-child-',dir='/root') as d:
    root=Path(d); registry=root/'registry'; registry.mkdir(mode=0o700)
    (registry/'bootstrap-candidates').mkdir(mode=0o700)
    source=root/'fixture-runtime'
    shutil.copytree('/opt/ai-device/bootstrap/0.2.2-native1/runtime',source,symlinks=True)
    module=source/'lib/python3.13/site-packages/device_runtime/native_helper.py'
    ready=root/'child-pid'; release=root/'release'; done=root/'child-done'
    # Candidate code is a controlled test fixture, not a released module.
    with module.open('a') as stream:
        stream.write('\nimport os as _probe_os, time as _probe_time\n')
        stream.write('_probe_pid = _probe_os.fork()\n')
        stream.write('if _probe_pid == 0:\n')
        stream.write('    [_probe_os.close(fd) for fd in (0, 1, 2)]\n')
        stream.write('    open('+repr(str(ready))+', "w").write(str(_probe_os.getpid()))\n')
        stream.write('    _probe_deadline = _probe_time.monotonic() + 30\n')
        stream.write('    while not _probe_os.path.exists('+repr(str(release))+') and _probe_time.monotonic() < _probe_deadline: _probe_time.sleep(.05)\n')
        stream.write('    open('+repr(str(done))+', "w").write("finished")\n')
        stream.write('    _probe_os._exit(0)\n')
    try:
        b.publish_managed_helper_candidate(registry,source,'fixture')
    except b.BootstrapError as exc:
        assert 'process-tree fence not proven' in str(exc)
    else:
        raise AssertionError('candidate publication default gate opened')
    assert not ready.exists() and not (registry/'bootstrap-candidates/fixture').exists()
    print('DEFAULT_PUBLICATION_REFUSED_BEFORE_COPY_OR_SELF_CHECK')
    try:
        b.run_verified_helper_candidate(registry/'bootstrap-candidates','fixture',registry=registry,
                                        socket_path=root/'socket',group=0)
    except b.BootstrapError as exc:
        assert 'launch process-tree fence not proven' in str(exc)
    else:
        raise AssertionError('candidate launch default gate opened')
    assert not ready.exists() and not (root/'socket').exists()
    print('DEFAULT_CANDIDATE_LAUNCH_REFUSED_BEFORE_CODE_EXECUTION')
    units=root/'units'; units.mkdir(mode=0o700)
    runtime=Path('/run/device-candidate-gate-probe')
    assert not runtime.exists()
    try:
        h.deploy_helper(bootstrap_python=Path(sys.executable), registry=registry,
                        unit_dir=units, runtime_dir=runtime, group=0, candidate_version='fixture')
    except b.BootstrapError as exc:
        assert 'launch process-tree fence not proven' in str(exc)
    else:
        raise AssertionError('candidate unit deployment default gate opened')
    assert not list(units.iterdir()) and not runtime.exists()
    print('DEFAULT_CANDIDATE_UNIT_REFUSED_BEFORE_WRITE')
    try:
        # Test-only bypass to reproduce the historical unguarded publication path.
        b._require_proven_candidate_publication_fence=lambda: None
        result=b.publish_managed_helper_candidate(registry,source,'fixture')
        deadline=time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline: time.sleep(.02)
        assert ready.exists() and result['activated'] is False
        pid=int(ready.read_text())
        assert (Path('/proc')/str(pid)).exists() and not done.exists()
        assert not os.path.lexists(registry/'query-supervisor.intent.json')
        with h.package_lease(registry,exclusive=True):
            assert not done.exists() and (Path('/proc')/str(pid)).exists()
        print('TEST_BYPASS_HISTORICAL_PUBLICATION_RETURNED_WHILE_IMPORT_DESCENDANT_LIVE')
        print('PACKAGE_LOCK_REACQUIRED_NO_SUPERVISOR_INTENT')
    finally:
        release.touch()
        deadline=time.monotonic()+10
        while ready.exists() and not done.exists() and time.monotonic()<deadline: time.sleep(.02)
        if ready.exists(): assert done.exists(), 'fixture child did not finish'
    print('CONTROLLED_FIXTURE_CHILD_EXITED_NO_PRODUCTION_ACTION')
PY
