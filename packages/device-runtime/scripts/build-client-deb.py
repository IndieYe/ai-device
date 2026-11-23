"""Package an already prepared Linux client tree; no target-side pip or uv."""

import argparse
import importlib.util
import shutil
import subprocess
from pathlib import Path

# Shared with the RPM and PKG builders so a payload cannot ship without the
# native package version the client reports alongside its runtime version.
_PACKAGE_VERSION_SPEC = importlib.util.spec_from_file_location(
    "client_package_version", Path(__file__).with_name("client_package_version.py")
)
PACKAGE_VERSION = importlib.util.module_from_spec(_PACKAGE_VERSION_SPEC)
_PACKAGE_VERSION_SPEC.loader.exec_module(PACKAGE_VERSION)

# Minimum host glibc for the packaged runtime. The tree ships a CPython build
# whose binaries were measured to need at most GLIBC_2.17, and every locked
# wheel is tagged manylinux_2_17 or lower, so declare what is actually
# required rather than the build host's version. A higher floor silently
# rejects hosts that can run the package. Keep in step with
# device_runtime.dependencies:MINIMUM_GLIBC (enforced by tests).
MINIMUM_GLIBC = "2.17"

# The stop guard is shared with the RPM builder so the two package formats
# cannot drift apart. It is plain POSIX sh with no % characters, so the same
# text is valid as a Debian maintainer script body and inside an RPM scriptlet.
STOP = (Path(__file__).parent / "client_package_stop.sh").read_text()


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
    # The tree is a build input and carries application identity, not the
    # package version (the tree builder runs before --version is known). Write
    # it into the copy so the shipped payload can report it.
    PACKAGE_VERSION.write_package_version(payload, version)
    # Stable client-only entry; never route through the legacy management CLI.
    entry = root / "usr/bin/ai-device-client"
    entry.parent.mkdir(parents=True)
    # mkdir(parents=True) also creates usr/ under the umask. Shipping that as
    # 0700 would restrict the host's own /usr after install, so it is created
    # explicitly and normalised with the rest below.
    usr = root / "usr"
    usr.mkdir(exist_ok=True)
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
    # Directories this builder creates are masked by the process umask, so a
    # root build with the common umask 077 yields 0700. dpkg-deb refuses that
    # for DEBIAN, but for the payload it packages the modes verbatim: the .deb
    # then installs cleanly and the service user cannot read its own tree.
    # Copied content keeps the modes it shipped with, so only the directories
    # created here need fixing.
    PACKAGE_VERSION.ensure_readable_directories(
        root, payload.parent, usr, entry.parent, control
    )
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
/opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_package activate --from-package-script
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
