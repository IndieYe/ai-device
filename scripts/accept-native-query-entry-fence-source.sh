#!/bin/sh
# Isolated source-overlay probe only; does not activate Device or helper services.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-entry-fence-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import os, socket, subprocess, sys, tempfile
from pathlib import Path
from device_runtime import native_bootstrap as b, native_helper as h

with tempfile.TemporaryDirectory(prefix='device-entry-fence-', dir='/root') as root:
    root = Path(root)
    registry = root / 'registry'; registry.mkdir(mode=0o700)
    candidates = registry / 'bootstrap-candidates'; candidates.mkdir(mode=0o700)
    endpoint = root / 'helper.sock'
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(endpoint))
    inode = endpoint.lstat().st_ino
    marker = registry / 'query-supervisor.intent.json'
    marker.symlink_to('missing')
    def refuse(call):
        try:
            call()
        except h.HelperError as exc:
            assert 'query supervisor intent remains unknown' in str(exc), str(exc)
        else:
            raise AssertionError('unknown supervisor accepted')
    refuse(lambda: h.recover_socket(registry, endpoint))
    assert endpoint.lstat().st_ino == inode and marker.is_symlink()
    called = []
    original = h._serve
    h._serve = lambda *args, **kwargs: called.append(True)
    try:
        refuse(lambda: h.serve(registry, root/'new.sock', group=0))
        assert not called and not (root/'new.sock').exists()
    finally:
        h._serve = original
    original_publish = b.publish_helper_candidate
    b.publish_helper_candidate = lambda *args: called.append(True)
    try:
        refuse(lambda: b.publish_managed_helper_candidate(registry, root/'source', 'v1'))
        assert not called and not list(candidates.iterdir()) and marker.is_symlink()
    finally:
        b.publish_helper_candidate = original_publish
    units = root / 'units'; units.mkdir(mode=0o700)
    runtime = Path('/run/device-entry-fence-probe')
    assert not runtime.exists()
    refuse(lambda: h.deploy_helper(bootstrap_python=Path(sys.executable), registry=registry,
                                   unit_dir=units, runtime_dir=runtime, group=0))
    assert not runtime.exists() and not list(units.iterdir()) and marker.is_symlink()
    print('UNKNOWN_INTENT_REFUSED_SERVE_SOCKET_RECOVERY_PUBLISH_AND_DEPLOY')
    # A separate clean registry is used instead of removing the unknown marker.
    clean = root / 'clean'; clean.mkdir(mode=0o700)
    child = '''from pathlib import Path\nimport sys\nfrom device_runtime import native_helper as h\ntry:\n with h._daemon_lease_pair(Path(sys.argv[1])): pass\nexcept h.HelperError as e:\n assert 'already active' in str(e); sys.exit(3)\nsys.exit(0)\n'''
    def probe(*args, **kwargs):
        result = subprocess.run([sys.executable, '-I', '-B', '-c', child, str(clean)], timeout=5)
        assert result.returncode == 3, result.returncode
        with h.package_lease(clean, exclusive=True): pass
    h._serve = probe
    try:
        h.serve(clean, root/'unused.sock', group=0)
    finally:
        h._serve = original
    print('SERVER_DAEMON_LEASE_BLOCKS_OTHER_PROCESS_SUPERVISOR_LOCK')
    print('SOURCE_OVERLAY_FIXTURE_NO_SERVICE_STARTED_NO_OLD_SNAPSHOT_PROTECTION')
PY
