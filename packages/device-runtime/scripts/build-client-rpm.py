"""Package an already prepared Linux client tree as an RPM; no target-side pip.

The tree, payload layout and stop guard are identical to the DEB build. Only
the transaction scripting differs: RPM runs scriptlets in a different order with
different arguments, and those differences are load-bearing here. See
docs/client-native-linux-development.md, "RPM 脚本语义实测", for the measurements
this module relies on:

- upgrade order is new %pre, unpack, new %post, old %preun, old %postun
- %1 is an instance count (1 install, 2 upgrading in, 1 old instance remaining,
  0 on erase), and %2 is never set
- a %pre failure leaves the installed package untouched and unpack not started
- a %post failure still leaves rpm returning 0

The last two together mean the busy refusal belongs in %pre (before unpack) and
that %post cannot be the only proof the service started.
"""

import argparse
import shutil
import subprocess
from pathlib import Path

# Declared floor, not the build host's version. RPM names the dependency glibc,
# where Debian packaging calls the same library libc6. Keep in step with
# device_runtime.dependencies:MINIMUM_GLIBC (enforced by tests).
MINIMUM_GLIBC = "2.17"

STOP = (Path(__file__).parent / "client_package_stop.sh").read_text()

# Shared with the DEB builder. Plain POSIX sh, no % characters, so it is valid
# verbatim as an RPM scriptlet body.
STOP = (Path(__file__).parent / "client_package_stop.sh").read_text()

# Inside %pre the tree is not unpacked yet, so the stop guard would call the
# python3 of the *installed* package. On a first install there is none and that
# call fails with 127, aborting an install that has nothing to stop. The guard
# only makes sense when an earlier instance exists, which is exactly $1 > 1.
# On a first install ($1 = 1) there is no client to stop and no group to check.
PRE_GUARD = """set -eu
if [ "${1:-0}" -eq 1 ]; then
    exit 0
fi
"""

SPEC = """Name: ai-device-client
Version: {version}
Release: 1
Summary: Self-contained AI Device client
License: Proprietary
BuildArch: {buildarch}
Requires: systemd
Requires: glibc >= {minimum_glibc}
Requires: ca-certificates
Requires: util-linux
%global __brp_python_bytecompile %{{nil}}
%global debug_package %{{nil}}
# rpm's brp scripts rewrite shebangs in the payload and abort on ambiguous ones.
# Both are wrong for a self-contained client: the tree ships its own Python, and
# the recorded interpreter must stay whatever the build produced. Measured on
# alinux3/RPM 4.14: the build failed outright on a "#!/usr/bin/env python" file
# and, before that, silently repointed python3 scripts at
# /usr/libexec/platform-python -- a host interpreter the package does not own.
%global __brp_mangle_shebangs %{{nil}}
%global __brp_mangle_shebangs_exclude_from .*
%global __brp_check_shebang %{{nil}}
# rpm adds a Requires for every shared object it finds in the payload. The tree
# carries its own libpng/libavif/libtiff/libzstd and its own libpython, so those
# are satisfied by files the package installs, not by the host. Left on, the
# build emitted 67 soname Requires -- including libtcl9.0.so, which is not a
# system library at all -- and no RPM host could ever satisfy them. Dependency
# discovery is off; the four real host dependencies are declared explicitly.
%global __requires_exclude .*
%global __provides_exclude .*
# rpm strips and re-links ELF files in the payload. That rewrote the bundled
# python3.13 and libpython (2,051,694 differing bytes) and left a binary that
# aborts with "undefined symbol" -- the tree runs fine before packaging. The
# tree is a prebuilt, already stripped artifact that must ship byte for byte.
%global __os_install_post %{{nil}}
%global __strip /bin/true
AutoReqProv: no

%description
Self-contained AI Device client with a private Python runtime. Configuration,
credentials and state are kept outside the package and survive upgrades and a
plain erase.

%install
rm -rf %{{buildroot}}
mkdir -p %{{buildroot}}/opt
cp -a {tree}/. %{{buildroot}}/opt/ai-device-client/
# The tree is assembled by hand and can carry an unusable mode: measured on
# alinux3, client_host_glibc.py arrived as 0600 and the service user got
# PermissionError for that module (client_host_glibc.py" is imported right
# after parsing, so the service exited 1 and rpm's %post then failed on
# readiness). Shipping the tree's modes verbatim would do the same.
find %{{buildroot}}/opt/ai-device-client -type d -exec chmod 0755 {{}} +
find %{{buildroot}}/opt/ai-device-client -type f -exec chmod 0644 {{}} +
find %{{buildroot}}/opt/ai-device-client -type f -perm -u+x -exec chmod 0755 {{}} +
mkdir -p %{{buildroot}}/usr/bin
printf '#!/bin/sh\\nexec /opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_cli "$@"\\n' > %{{buildroot}}/usr/bin/ai-device-client
printf '#!/bin/sh\\nexec /opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_register_cli "$@"\\n' > %{{buildroot}}/usr/bin/ai-device-client-register
chmod 0755 %{{buildroot}}/usr/bin/ai-device-client %{{buildroot}}/usr/bin/ai-device-client-register
find %{{buildroot}}/opt/ai-device-client -type d -name __pycache__ -prune -exec rm -rf {{}} +

%files
/opt/ai-device-client
/usr/bin/ai-device-client
/usr/bin/ai-device-client-register

%pre
{PRE_GUARD}
{stop}

%post
set -eu
/opt/ai-device-client/runtime/bin/python3 -I -B -m device_runtime.client_package activate --from-package-script

%preun
set -eu
{preun_stop}

%postun
set -eu
# RPM has no purge verb. Erase always removes the package-owned tree but must
# leave configuration, credentials and state in place, exactly as a DEB remove.
if [ "$1" -eq 0 ]; then
    if [ -e /etc/systemd/system/ai-device-client.service ] || [ -L /etc/systemd/system/ai-device-client.service ]; then
        test ! -L /etc/systemd/system/ai-device-client.service || exit 1
        grep -Fxq 'ExecStart=/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --config /etc/ai-device-client/client.json' /etc/systemd/system/ai-device-client.service || exit 1
        systemctl disable ai-device-client.service || exit 1
        rm /etc/systemd/system/ai-device-client.service
    fi
    systemctl daemon-reload
    if [ -d /opt/ai-device-client/runtime ]; then
        find /opt/ai-device-client/runtime -type d -name __pycache__ -prune -exec rm -rf {{}} + || exit 1
        find /opt/ai-device-client -depth -type d -empty -delete || exit 1
    fi
fi
"""


def _rpm_version(version):
    """Reject anything that would change the spec's meaning or split a token."""
    if (
        not version
        or any(c not in "0123456789abcdefghijklmnopqrstuvwxyz._+" for c in version)
        or not version[0].isdigit()
    ):
        raise ValueError("invalid RPM version")
    return version


def build(tree, output, version, buildarch="x86_64"):
    _rpm_version(version)
    if not (tree / "runtime/bin/python3").is_file():
        raise ValueError("prepared client runtime missing")
    output.mkdir(parents=True, exist_ok=True)
    topdir = output / "rpmbuild"
    for sub in ("BUILD", "RPMS", "SOURCES", "SPECS", "SRPMS", "BUILDROOT"):
        (topdir / sub).mkdir(parents=True, exist_ok=True)
    # During an upgrade the old %preun still runs, but by then the new payload
    # is already unpacked and %post has started the new version. Stopping there
    # would kill what %post just started, so %preun only refuses to proceed
    # while processes remain; it never stops anything itself.
    spec = SPEC.format(
        version=version,
        minimum_glibc=MINIMUM_GLIBC,
        tree=tree,
        buildarch=buildarch,
        PRE_GUARD=PRE_GUARD,
        stop=STOP,
        preun_stop=(
            'case "${1:-}" in\n'
            '    0) ;;\n'  # full erase: the payload is about to go away
            '    *) exit 0 ;;\n'  # upgrade: %pre already handled the running client
            "esac\n"
        ),
    )
    spec_path = topdir / "SPECS/ai-device-client.spec"
    spec_path.write_text(spec)
    subprocess.run(
        [
            "rpmbuild",
            "--define",
            f"_topdir {topdir}",
            "--define",
            "_build_id_links none",
            "-bb",
            str(spec_path),
        ],
        check=True,
    )
    built = sorted((topdir / "RPMS").rglob(f"ai-device-client-{version}-1.*.rpm"))
    if len(built) != 1:
        raise RuntimeError(f"expected one RPM, found {built}")
    target = output / built[0].name
    shutil.copy2(built[0], target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    # The shipped client targets amd64; the override exists so the spec can be
    # built and exercised on a non-x86_64 host without pretending it is one.
    parser.add_argument("--buildarch", default="x86_64")
    args = parser.parse_args()
    print(build(args.tree.resolve(), args.output.resolve(), args.version, args.buildarch))
