#!/bin/sh
# Only execute inside a disposable Linux container; no production entry point.
set -eu
[ "$(uname -s)" = Linux ] && [ "$(id -u)" = 0 ] && [ "$$" = 1 ] || exit 2
pkg=/input/ai-device-native-client_0.2.2-1_amd64.deb
printf '%s  %s\n' 909d10088315e1e64bfb9fdaad264ee41c6b71797d1032e43752c53a494c1d2a "$pkg" | sha256sum -c -
dpkg -i "$pkg"
base=/opt/ai-device/bootstrap/0.2.2-native1/runtime/lib/python3.13/site-packages/device_runtime
# The installed package is an older test input. Explicitly overlay current source
# in this fresh container only; this is not an e7-built package acceptance.
for module in __init__.py native_bootstrap.py native_helper.py management_lock.py; do
    cp "/source/$module" "$base/$module"
    cmp "/source/$module" "$base/$module"
done
mkdir -p /var/lib/device-fork-probe/bootstrap-candidates /run/device-fork-probe
chmod 700 /var/lib/device-fork-probe /var/lib/device-fork-probe/bootstrap-candidates /run/device-fork-probe
py=/opt/ai-device/bootstrap/0.2.2-native1/runtime/bin/python3.13
"$py" -I -B - <<'PY'
import os, pathlib, select, signal, socket, sys, time, types
from device_runtime import native_bootstrap as b, native_helper as h
registry=pathlib.Path('/var/lib/device-fork-probe')
source=pathlib.Path('/opt/ai-device/bootstrap/0.2.2-native1/runtime')
endpoint=pathlib.Path('/run/device-fork-probe/helper.sock')
result=b.publish_managed_helper_candidate(registry,source,'candidate')
contents=b._verified_helper_bytes(registry/'bootstrap-candidates','candidate',result['snapshot_sha256'])
# Force a post-validation source mutation; the child must use inherited bytes.
candidate_module=contents['native_helper'][0]
original=contents['native_helper'][1]
candidate_module.write_bytes(b"raise RuntimeError('candidate reread from disk')\n")
assert candidate_module.read_bytes() != original
pid=None
pidfd=None
try:
    with h._daemon_lease(registry) as lease:
        pid=os.fork()
        if pid == 0:
            try:
                for name in ('__init__','management_lock','native_helper'):
                    path,data=contents[name]
                    qualified='device_runtime' if name=='__init__' else 'device_runtime.'+name
                    module=types.ModuleType(qualified)
                    module.__file__=str(path)
                    module.__package__='device_runtime'
                    if name=='__init__': module.__path__=[str(path.parent)]
                    sys.modules[qualified]=module
                    exec(compile(data,str(path),'exec'),module.__dict__)
                # The inherited real daemon lease is held. This is deliberately
                # not a production entry: _serve itself does not validate it.
                sys.modules['device_runtime.native_helper']._serve(registry,endpoint,group=0)
                os._exit(0)
            except BaseException:
                os._exit(1)
        pidfd=os.pidfd_open(pid)
        poll=select.poll(); poll.register(pidfd,select.POLLIN)
        deadline=time.monotonic()+30
        while not endpoint.exists() and not poll.poll(0) and time.monotonic()<deadline:
            time.sleep(.05)
        assert endpoint.exists() and not poll.poll(0), 'child did not bind'
        with socket.socket(socket.AF_UNIX) as c:
            c.settimeout(5); c.connect(str(endpoint))
            with h._socket_peer_process(c) as peer:
                assert peer['pid']==pid
                c.sendall(b'{}\n'); assert b'helper_request_refused' in c.recv(1024)
        assert os.fstat(lease).st_ino==(registry/'helper-daemon.lock').stat().st_ino
    try:
        with h._daemon_lease(registry): pass
    except h.HelperError as exc:
        assert 'already active' in str(exc)
    else:
        raise AssertionError('child did not retain daemon lease')
    os.kill(pid,signal.SIGTERM)
    _,status=os.waitpid(pid,0); pid=None
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGTERM
    assert poll.poll(1000), 'child pidfd did not report exit'
    with h._daemon_lease(registry): pass
    print('VERIFIED_FORK_FIXTURE_OK: inherited verified bytes; peer child; lease retained then released',flush=True)
finally:
    if pid is not None:
        os.kill(pid,signal.SIGTERM)
        os.waitpid(pid,0)
    if pidfd is not None: os.close(pidfd)
PY
