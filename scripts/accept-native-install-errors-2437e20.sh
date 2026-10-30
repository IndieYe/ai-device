#!/bin/sh
# Only a disposable offline Linux container; never use a real host.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = install-errors-2437e20 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] || exit 2
[ "$#" = 2 ] && [ ! -e /opt/ai-device ] && [ ! -e /var/lib/ai-device ] || exit 2
printf '%s  %s\n' 3cae4ae3ea54a84e080ef44198413289ebc6ada4afe42db934366d896b51198b "$1" | sha256sum -c -
dpkg -i "$1"
P=/opt/ai-device/packages/0.2.2-native1
"$P/tools/uv" --no-config pip check --python "$P/runtime/bin/python3.13" --offline
useradd -m -u 12345 probe
runuser -u probe -- "$P/runtime/bin/python3.13" -I -B - "$2" <<'PY'
import zipfile
from pathlib import Path
from device_runtime import installation, native_install

payload=Path('/opt/ai-device/packages/0.2.2-native1')
with zipfile.ZipFile(__import__('sys').argv[1]) as wheel:
 for module in (installation,native_install):
  path=Path(module.__file__)
  assert path.read_bytes()==wheel.read('device_runtime/'+path.name),path
print('INSTALLED_INSTALLATION_MODULES_MATCH_WHEEL',flush=True)
real_prepare=installation._prepare
try:
 for code in (13,28):
  root=Path('/home/probe/partial-'+str(code))
  def fail_prepare(destination,*args,**kwargs):
   (destination/'evidence').write_bytes(b'preserve')
   raise OSError(code,'fixture fault')
  installation._prepare=fail_prepare
  try: native_install.install_payload(payload,root=root,instance_id='probe',server_url='http://127.0.0.1:9')
  except installation.InstallationError as exc:
   assert f'errno {code}' in str(exc) and 'partial instance retained' in str(exc),str(exc)
   assert isinstance(exc.__cause__,OSError) and exc.__cause__.errno==code
  else: raise AssertionError('injected failure unexpectedly accepted')
  assert (root/'evidence').read_bytes()==b'preserve' and not (root/'active.json').exists()
  try: native_install.install_payload(payload,root=root,instance_id='probe',server_url='http://127.0.0.1:9')
  except installation.InstallationError as exc: assert 'refuses existing instances' in str(exc),str(exc)
  else: raise AssertionError('partial instance unexpectedly reused')
  assert (root/'evidence').read_bytes()==b'preserve'
finally:
 installation._prepare=real_prepare
print('NATIVE_IO_ERRORS_PRESERVE_PARTIAL_AND_REFUSE_REUSE',flush=True)
root=Path('/home/probe/clean-instance')
result=native_install.install_payload(payload,root=root,instance_id='probe',server_url='http://127.0.0.1:9')
assert result['instance_root']==str(root) and (root/'active.json').exists()
active=(root/'active.json').read_bytes()
try: native_install.install_payload(payload,root=root,instance_id='probe',server_url='http://127.0.0.1:9')
except installation.InstallationError as exc: assert 'refuses existing instances' in str(exc),str(exc)
else: raise AssertionError('existing instance unexpectedly replaced')
assert (root/'active.json').read_bytes()==active
print('NATIVE_CLEAN_INSTALL_AND_EXISTING_PROTECTION_OK',flush=True)
PY
