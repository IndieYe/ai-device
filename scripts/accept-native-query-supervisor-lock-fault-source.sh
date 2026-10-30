#!/bin/sh
# Disposable Linux source-overlay failure probe; no service or real candidate execution.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = query-supervisor-lock-fault-source ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 3 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 36b5ee9ddc3e43c85a7c4c6d3a25b43e2c82b5768ec655017bae03ba2242cbe4 "$1" | sha256sum -c -
dpkg -i "$1"
B=/opt/ai-device/bootstrap/0.2.2-native1/runtime
cp "$2" "$B/lib/python3.13/site-packages/device_runtime/native_bootstrap.py"
cp "$3" "$B/lib/python3.13/site-packages/device_runtime/native_helper.py"
"$B/bin/python3.13" -I -B - <<'PY'
import json, os, subprocess, sys, tempfile
from pathlib import Path
from device_runtime import native_helper as h
with tempfile.TemporaryDirectory(prefix='query-lock-fault-', dir='/root') as root:
    root=Path(root)
    child='''import fcntl,os,pathlib,sys
from device_runtime import native_bootstrap as b
registry=pathlib.Path(sys.argv[1]); mode=sys.argv[2]
def unexpected(*args,**kwargs):
 pathlib.Path(sys.argv[3]).touch(); raise AssertionError('verification or fork executed')
b._verified_helper_bytes=unexpected
os.fork=unexpected
original=b.fcntl.flock
def flock(fd,operation):
 if mode=='downgrade' and operation==fcntl.LOCK_SH|fcntl.LOCK_NB:
  raise OSError('injected downgrade failure')
 return original(fd,operation)
b.fcntl.flock=flock
b._supervise_candidate_queries(registry/'candidates','v1',registry=registry,socket_path=registry/'socket',group=0,expected_snapshot_sha256='a'*64)
'''
    for mode in ('busy','downgrade'):
        registry=root/mode; registry.mkdir(mode=0o700)
        witness=root/(mode+'-unexpected')
        args=[sys.executable,'-I','-B','-c',child,str(registry),mode,str(witness)]
        if mode=='busy':
            with h.package_lease(registry):
                result=subprocess.run(args,capture_output=True,timeout=10)
        else:
            result=subprocess.run(args,capture_output=True,timeout=10)
        assert result.returncode!=0 and not witness.exists() and not (registry/'socket').exists(),(mode,result.stderr)
        intent=registry/'query-supervisor.intent.json'
        if mode=='busy':
            assert b'package or start sender still executing' in result.stderr and not os.path.lexists(intent)
            print('REAL_SUPERVISOR_EX_BLOCKED_BEFORE_INTENT_OR_VERIFY')
        else:
            assert b'injected downgrade failure' in result.stderr and json.loads(intent.read_bytes())['state']=='query_supervisor_intent'
            print('DOWNGRADE_FAILURE_PRESERVES_INTENT_WITHOUT_VERIFY_OR_FORK')
        with h._daemon_lease_pair(registry): pass
    print('FAULT_INJECTION_ONLY_NO_PROCESS_TREE_OR_CRASH_DURABILITY_PROOF')
PY
