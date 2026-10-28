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
from device_runtime import native_bootstrap as b, native_helper as h
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
    # Force the publication window between EX acquisition and durable publish.
    fenced=root/'fenced'; fenced.mkdir(mode=0o700)
    locked=root/'publisher-locked'; publish=root/'publish'; published=root/'published'; release=root/'release'
    writer='''import fcntl,pathlib,sys,time
from device_runtime import native_bootstrap as b, native_helper as h
with h.package_lease(pathlib.Path(sys.argv[1]),exclusive=True) as fd:
 pathlib.Path(sys.argv[2]).touch()
 end=time.monotonic()+8
 while not pathlib.Path(sys.argv[3]).exists() and time.monotonic()<end: time.sleep(.01)
 assert pathlib.Path(sys.argv[3]).exists()
 b._publish_query_supervisor_intent(pathlib.Path(sys.argv[1]),run_id='a'*32,snapshot_sha256='b'*64)
 fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
 pathlib.Path(sys.argv[4]).touch()
 end=time.monotonic()+8
 while not pathlib.Path(sys.argv[5]).exists() and time.monotonic()<end: time.sleep(.01)
 assert pathlib.Path(sys.argv[5]).exists()
'''
    p=subprocess.Popen([sys.executable,'-I','-B','-c',writer,str(fenced),str(locked),str(publish),str(published),str(release)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        end=time.monotonic()+5
        while not locked.exists() and p.poll() is None and time.monotonic()<end: time.sleep(.01)
        assert locked.exists() and p.poll() is None
        try:
            with h.package_lease(fenced): raise AssertionError('SH acquired during publication')
        except h.HelperError as exc: assert 'package or start sender still executing' in str(exc)
        assert not (fenced/'query-supervisor.intent.json').exists()
        publish.touch()
        end=time.monotonic()+5
        while not published.exists() and p.poll() is None and time.monotonic()<end: time.sleep(.01)
        assert published.exists() and p.poll() is None
        with h.package_lease(fenced):
            try: h._assert_no_query_supervisor_intent(fenced)
            except h.HelperError: pass
            else: raise AssertionError('SH participant ignored published intent')
        release.touch(); assert p.wait(timeout=5)==0
    finally:
        publish.touch(); release.touch()
        if p.poll() is None: p.kill(); p.wait(timeout=5)
    print('EX_PUBLISH_THEN_SH_CHECK_FENCES_BOTH_INTERLEAVINGS')
    reverse=root/'reverse'; reverse.mkdir(mode=0o700)
    with h.package_lease(reverse):
        attempt=subprocess.run([sys.executable,'-I','-B','-c',
            'from device_runtime import native_helper as h; import sys\nwith h.package_lease(sys.argv[1],exclusive=True): pass',str(reverse)],capture_output=True,timeout=5)
        assert attempt.returncode!=0 and b'package or start sender still executing' in attempt.stderr
        assert not (reverse/'query-supervisor.intent.json').exists()
    print('EX_PUBLISHER_REFUSED_WHILE_START_SH_HELD')
print('INTENT_NO_REPLACE_RACE_AND_FSYNC_UNKNOWN_RETAINED')
print('LOCAL_FILESYSTEM_FIXTURE_NO_CRASH_DURABILITY_OR_CROSS_ENTRY_FENCE')
PY
