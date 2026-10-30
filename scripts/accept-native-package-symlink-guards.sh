#!/bin/sh
# Disposable, empty Linux container; never run against a real installation.
set -eu
[ "${DEVICE_NATIVE_DISPOSABLE_PROBE:-}" = package-symlink-guards ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(uname -m)" = x86_64 ] && [ "$$" = 1 ] || exit 2
[ "$#" = 1 ] && [ ! -e /opt/ai-device ] && [ ! -L /opt/ai-device ] && [ ! -e /var/lib/ai-device ] && [ ! -L /var/lib/ai-device ] && [ ! -e /etc/systemd/system/ai-device-native-helper.service ] && [ ! -L /etc/systemd/system/ai-device-native-helper.service ] || exit 2
python3 - "$1" <<'PY'
import pathlib, subprocess, sys, tempfile
source=pathlib.Path(sys.argv[1]).read_text()
pre=source.split('cat > "$work/preflight" <<\'EOF\'\n',1)[1].split('\nEOF',1)[0]
remove=source.split('cat > "$root/DEBIAN/prerm" <<\'EOF\'\n',1)[1].split('\nEOF',1)[0]
with tempfile.TemporaryDirectory(prefix='package-guards-',dir='/root') as directory:
    root=pathlib.Path(directory)
    opt=root/'opt/ai-device'; manager=root/'var/lib/ai-device/manager'; service=root/'etc/systemd/system/ai-device-native-helper.service'
    def script(value):
        return value.replace('/opt/ai-device',str(opt)).replace('/var/lib/ai-device/manager',str(manager)).replace('/etc/systemd/system/ai-device-native-helper.service',str(service))
    def run(value): return subprocess.run(['/bin/sh'],input=script(value),text=True,capture_output=True).returncode
    assert run(pre)==0 and run(remove)==0
    # Separate fixtures avoid deleting/replacing any unknown marker.
    for index, name in enumerate(('packages','bootstrap','instances','manager','service')):
        base=root/str(index); base.mkdir()
        opt=base/'opt/ai-device'; manager=base/'var/lib/ai-device/manager'; service=base/'etc/systemd/system/ai-device-native-helper.service'
        path={'packages':opt/'packages','bootstrap':opt/'bootstrap','instances':opt/'instances','manager':manager,'service':service}[name]
        path.parent.mkdir(parents=True); path.symlink_to('missing')
        if name != 'service': assert run(pre)!=0, name
        if name not in ('packages','bootstrap'): assert run(remove)!=0, name
        assert path.is_symlink()
    print('DEB_EXTRACTED_GUARDS_REFUSE_DANGLING_ENTRIES')
    print('SCRIPT_FIXTURE_ONLY_NO_REAL_UNINSTALL_NO_RPM_EXECUTION')
PY
