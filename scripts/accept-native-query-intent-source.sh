#!/bin/sh
# Isolated Linux fixture: local intent publication only, no production entry.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-intent-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import json,os,stat,subprocess,sys,tempfile
from pathlib import Path
from device_runtime import native_bootstrap as b
with tempfile.TemporaryDirectory(prefix='device-query-intent-',dir='/root') as root:
    root=Path(root); race=root/'race'; race.mkdir(mode=0o700)
    start=root/'go'; ready=[root/'ready1',root/'ready2']
    code='''import pathlib,sys,time
from device_runtime import native_bootstrap as b
pathlib.Path(sys.argv[3]).write_text("ready")
end=time.monotonic()+5
while not pathlib.Path(sys.argv[2]).exists() and time.monotonic()<end: time.sleep(.01)
if not pathlib.Path(sys.argv[2]).exists(): sys.exit(9)
try: b._publish_query_supervisor_intent(pathlib.Path(sys.argv[1]),run_id=sys.argv[4]*32,snapshot_sha256="b"*64)
except b.BootstrapError: sys.exit(3)
'''
    ps=[subprocess.Popen([sys.executable,'-I','-B','-c',code,str(race),str(start),str(ready[i]),str(i+1)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for i in range(2)]
    try:
        import time
        end=time.monotonic()+5
        while not all(p.exists() for p in ready) and time.monotonic()<end: time.sleep(.01)
        assert all(p.exists() for p in ready), 'publishers did not start'
        start.touch()
        assert sorted(p.wait(timeout=5) for p in ps)==[0,3], 'no-replace race did not have one winner'
        intent=json.loads((race/'query-supervisor.intent.json').read_text())
        assert intent['run_id'] in ('1'*32,'2'*32)
    finally:
        start.touch()
        for p in ps:
            if p.poll() is None: p.kill(); p.wait(timeout=5)
    uncertain=root/'uncertain'; uncertain.mkdir(mode=0o700)
    old=os.fsync
    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError('injected directory fsync failure')
        return old(fd)
    os.fsync=fsync
    try:
        try: b._publish_query_supervisor_intent(uncertain,run_id='c'*32,snapshot_sha256='d'*64)
        except b.BootstrapError: pass
        else: raise AssertionError('fsync failure accepted')
    finally: os.fsync=old
    path=uncertain/'query-supervisor.intent.json'
    assert path.exists() and json.loads(path.read_text())['run_id']=='c'*32
    try: b._publish_query_supervisor_intent(uncertain,run_id='e'*32,snapshot_sha256='d'*64)
    except b.BootstrapError: pass
    else: raise AssertionError('uncertain intent overwritten')
print('INTENT_NO_REPLACE_RACE_AND_FSYNC_UNKNOWN_RETAINED')
print('LOCAL_FILESYSTEM_FIXTURE_NO_CRASH_DURABILITY_OR_CROSS_ENTRY_FENCE')
PY
