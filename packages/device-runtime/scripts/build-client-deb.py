"""Package an already prepared Linux client tree; no target-side pip or uv."""

import argparse
import shutil
import subprocess
from pathlib import Path

# Minimum host glibc for the packaged runtime. The tree ships a CPython build
# whose binaries were measured to need at most GLIBC_2.17, and every locked
# wheel is tagged manylinux_2_17 or lower, so declare what is actually
# required rather than the build host's version. A higher floor silently
# rejects hosts that can run the package. Keep in step with
# device_runtime.dependencies:MINIMUM_GLIBC (enforced by tests).
MINIMUM_GLIBC = "2.17"

STOP = r"""set -eu
unit=ai-device-client.service
if [ -e /etc/systemd/system/$unit ] || [ -L /etc/systemd/system/$unit ]; then
    test ! -L /etc/systemd/system/$unit || exit 1
    grep -Fxq 'ExecStart=/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --config /etc/ai-device-client/client.json' /etc/systemd/system/$unit || {
        echo 'unrelated client service; refusing package operation' >&2; exit 1;
    }
fi
# Legacy layouts are never silently adopted or overwritten.
if [ -e /opt/ai-device/packages ] || [ -L /opt/ai-device/packages ] || [ -e /opt/ai-device/bootstrap ] || [ -L /opt/ai-device/bootstrap ]; then
    echo 'unsupported legacy Device installation; explicit migration required' >&2
    exit 1
fi
fragment=$(systemctl show "$unit" --property=FragmentPath --value) || exit 1
case "$fragment" in
    '') ;;
    /etc/systemd/system/ai-device-client.service) ;;
    *) echo 'external same-name service; refusing package operation' >&2; exit 1 ;;
esac
overrides=$(systemctl show "$unit" --property=DropInPaths --value) || exit 1
if [ -n "$overrides" ]; then
    echo 'client service has unsupported overrides; refusing package operation' >&2
    exit 1
fi
state=$(systemctl show "$unit" --property=ActiveState --value) || exit 1
main=$(systemctl show "$unit" --property=MainPID --value) || exit 1
control=$(systemctl show "$unit" --property=ControlPID --value) || exit 1
# A later unpack failure cannot roll back a stop, and dpkg does not call
# postinst afterwards. Say so while the operator is looking at this output.
case "$state:$main:$control" in
    active:*:0)
        echo 'note: stopping the client; if unpacking then fails it stays stopped — reinstall a complete package to repair' >&2
        ;;
esac
case "$state:$main:$control" in
    inactive:0:0|failed:0:0) ;;
    active:*:0)
        sigkill=$(systemctl show "$unit" --property=SendSIGKILL --value) || exit 1
        killmode=$(systemctl show "$unit" --property=KillMode --value) || exit 1
        if [ "$sigkill:$killmode" != no:control-group ]; then
            echo 'unsafe client shutdown policy; refusing package operation' >&2; exit 1
        fi
        /opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_package stop
        ;;
    *) echo 'client state unknown; refusing package replacement' >&2; exit 1 ;;
esac
# Check descendants too, without depending on an intact old Python runtime.
group=$(systemctl show "$unit" --property=ControlGroup --value) || exit 1
if [ -n "$group" ]; then
    case "$group" in /*) ;; *) exit 1 ;; esac
    case "$group" in *..*) exit 1 ;; esac
    if [ -d "/sys/fs/cgroup$group" ]; then
        grep -qx 'populated 0' "/sys/fs/cgroup$group/cgroup.events" || {
            echo 'client process group is not confirmed empty; refusing replacement' >&2; exit 1;
        }
    fi
fi
"""


def build(tree, output, version):
    if (
        not version
        or any(c not in "0123456789abcdefghijklmnopqrstuvwxyz.+~-" for c in version)
        or not version[0].isdigit()
    ):
        raise ValueError("invalid Debian version")
    if not (tree / "runtime/bin/python3").is_file():
        raise ValueError("prepared client runtime missing")
    output.mkdir(parents=True, exist_ok=False)
    root = output / "root"
    payload = root / "opt/ai-device-client"
    payload.parent.mkdir(parents=True)
    # Runtime caches are not package-owned files; never ship builder bytecode.
    shutil.copytree(
        tree,
        payload,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    # Stable client-only entry; never route through the legacy management CLI.
    entry = root / "usr/bin/ai-device-client"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        '#!/bin/sh\nexec /opt/ai-device-client/runtime/bin/python3 -I -B '
        '-m device_runtime.client_cli "$@"\n'
    )
    entry.chmod(0o755)
    # Registration is a separate root-only entry so choosing the service user
    # never runs through the client loop or the legacy management CLI.
    register = root / "usr/bin/ai-device-client-register"
    register.write_text(
        '#!/bin/sh\nexec /opt/ai-device-client/runtime/bin/python3 -I -B '
        '-m device_runtime.client_register_cli "$@"\n'
    )
    register.chmod(0o755)
    control = root / "DEBIAN"
    control.mkdir()
    (control / "control").write_text(f"""Package: ai-device-client
Version: {version}
Architecture: amd64
Maintainer: AI Device developers
Depends: systemd, libc6 (>= {MINIMUM_GLIBC}), ca-certificates, util-linux
Description: Self-contained AI Device client
 Includes private Python and client dependencies; configuration is kept separately.
""")
    scripts = {
        "preinst": "#!/bin/sh\n" + STOP,
        "prerm": '#!/bin/sh\ncase "${1:-}" in upgrade|remove|deconfigure) ;; *) exit 0 ;; esac\n'
        + STOP,
        "postinst": """#!/bin/sh
set -eu
[ "${1:-}" = configure ] || exit 0
/opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_package activate
""",
        "postrm": """#!/bin/sh
set -eu
case "${1:-}" in
 remove|purge)
    # Check ownership before disabling; an unconfigured package has no unit.
    if [ -e /etc/systemd/system/ai-device-client.service ] || [ -L /etc/systemd/system/ai-device-client.service ]; then
        test ! -L /etc/systemd/system/ai-device-client.service || exit 1
        grep -Fxq 'ExecStart=/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --config /etc/ai-device-client/client.json' /etc/systemd/system/ai-device-client.service || exit 1
        systemctl disable ai-device-client.service || exit 1
        rm /etc/systemd/system/ai-device-client.service
    fi
    systemctl daemon-reload
    # Interpreter caches are created at run time and are absent from the
    # manifest, so dpkg cannot remove them; leftover directories would block
    # the parent directory removal and strand the whole runtime tree.
    if [ -d /opt/ai-device-client/runtime ]; then
        find /opt/ai-device-client/runtime -type d -name __pycache__ -prune -exec rm -rf {} + || exit 1
        # Those caches were the only entries left in some stdlib directories, so
        # dpkg had already given up on the parents before this script ran. Prune
        # what is empty now, deepest first; a directory holding files is kept.
        find /opt/ai-device-client -depth -type d -empty -delete || exit 1
    fi
    ;;
esac
# Configuration and credentials outlive an upgrade and a plain remove; only an
# explicit purge may discard them. State is never touched here.
case "${1:-}" in
 purge)
    rm -rf /etc/ai-device-client || exit 1
    ;;
esac
""",
    }
    for name, content in scripts.items():
        path = control / name
        path.write_text(content)
        path.chmod(0o755)
        subprocess.run(["sh", "-n", str(path)], check=True)
    target = output / f"ai-device-client_{version}_amd64.deb"
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "--build", str(root), str(target)], check=True
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(build(args.tree.resolve(), args.output.resolve(), args.version))
