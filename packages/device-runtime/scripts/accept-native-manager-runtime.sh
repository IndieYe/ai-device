#!/bin/sh
# Disposable Linux only. Network-free installation from explicit wheel inputs.
set -eu
[ "${DEVICE_DISPOSABLE_CONTAINER:-}" = manager-runtime-v1 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -s)" = Linux ] || exit 2
[ "$#" = 4 ] || { echo 'usage: SCRIPT V15_DEB WHEELS DEPENDENCIES WHEEL_LOCK_JSON' >&2; exit 2; }
base=/opt/device-manager-runtime-acceptance
[ ! -e "$base" ] || { echo 'fresh namespace required' >&2; exit 2; }
mkdir -p "$base/input" "$base/manager/transactions"
dpkg-deb -x "$1" "$base/input"
cp -a "$base/input/opt/ai-device/packages/0.2.2-native1/runtime" "$base/runtime"
python=$base/runtime/bin/python3.13
# The v15 CPython is only a seed; every declared component/dependency below is
# explicitly reinstalled from local, locked wheel bytes, with no index access.
"$python" -I -B - "$2" "$3" "$4" "$base" <<'PY'
import hashlib,importlib,json,os,subprocess,sys,zipfile
from pathlib import Path
wheels,dependencies,lock,base=map(Path,sys.argv[1:])
inputs=[]
for item in json.loads(lock.read_bytes()):
 path=dependencies/item['name']
 assert path.stat().st_size==item['size']
 assert hashlib.sha256(path.read_bytes()).hexdigest()==item['hash'].split(':')[1]
 inputs.append(path)
components=[]
for prefix in ('ai_device-','ai_device_bash-','ai_device_fs-'):
 matches=list(wheels.glob(prefix+'*.whl'))
 assert len(matches)==1
 components.append(matches[0])
subprocess.run([sys.executable,'-I','-B','-m','pip','--isolated','install','--no-index','--no-deps','--force-reinstall',*map(str,inputs+components)],check=True,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','PIP_NO_INDEX':'1'})
subprocess.run([sys.executable,'-I','-B','-m','pip','--isolated','check'],check=True)
# Use a fresh interpreter: the invoking one may have imported old package code.
probe=r'''
import importlib,json,sys
from pathlib import Path
names=['device_runtime.native_update','device_runtime.native_bootstrap','device_runtime.native_update_job','device_runtime.native_candidate','device_runtime.native_prepare','cryptography.hazmat.primitives.asymmetric.ed25519','zstandard','psutil','PIL.Image']
for name in names:
 module=importlib.import_module(name)
 assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix)),(name,module.__file__)
print(json.dumps({'prefix':sys.prefix,'modules':names}))
'''
subprocess.run([sys.executable,'-I','-B','-c',probe],check=True)
# Verify the installed management module bytes against this exact input wheel.
with zipfile.ZipFile(components[0]) as wheel:
 for name in ('native_update.py','native_bootstrap.py','native_update_job.py','native_prepare.py'):
  expected=wheel.read('device_runtime/'+name)
  site=base/'runtime/lib/python3.13/site-packages/device_runtime'/name
  assert site.read_bytes()==expected,name
code='from device_runtime.native_bootstrap import snapshot_runtime; from pathlib import Path; import sys; snapshot_runtime(Path(sys.prefix),Path(sys.argv[1]))'
snapshot=base/'manager/transactions/full-runtime'
subprocess.run([sys.executable,'-I','-B','-c',code,str(snapshot)],check=True)
subprocess.run([str(snapshot/'runtime/bin/python3.13'),'-I','-B','-c',probe],check=True)
manifest={'state':'offline_manager_runtime_verified','python':str(base/'runtime/bin/python3.13'),'snapshot':str(snapshot),'inputs':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs+components},'installed_offline':True,'service_actions':False}
(base/'acceptance.json').write_text(json.dumps(manifest,sort_keys=True))
print(json.dumps(manifest,sort_keys=True))
PY
