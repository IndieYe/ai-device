"""Decide whether this host may receive a native package over an existing layout.

Both package managers run a pre-install guard, and they cannot share code: dpkg
runs a POSIX shell preinst, rpm a lua pretrans. Keeping the decision in one
module and having both hooks call it is the only way to stop the two guards
from drifting into different answers.

The previous hooks refused unconditionally. That was correct while no upgrade
transaction existed, but it also meant a registered instance could never move to
a new package. This module replaces "refuse everything" with "refuse anything
we do not understand", which is the property that actually matters: a layout we
cannot classify must not be overwritten.

Exit status is the contract with the hooks:

    0  no existing layout, or a recognised layout this package may upgrade
    1  a layout we do not recognise, or an upgrade that is not safe yet
"""

import json
import re
import stat
import sys
from pathlib import Path

PACKAGES = Path("/opt/ai-device/packages")
BOOTSTRAP = Path("/opt/ai-device/bootstrap")
INSTANCE_ROOT = Path("/opt/ai-device/instances")
INSTANCE_MARKERS = (Path("/var/lib/ai-device/manager"),)
# Names that used to hold administrator-managed state inside the instance root.
# Anything else there is an instance directory, which an upgrade leaves alone.
INSTANCE_NAMES = ("manager",)
# A versioned directory name: 0.2.2-native1, 0.3.0-native2, ...
_VERSIONED = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class LayoutError(ValueError):
    """The layout cannot be classified, so no package may touch it."""


# The bootstrap pointer is a file that lives beside the versioned trees it
# selects. It belongs to the layout by design, so the classifier has to know it
# and check its shape rather than treat it as a stray child.
POINTER_NAME = "current"


def _versioned_directories(root):
    """Return the versioned children of a layout root.

    Anything that is not a plain directory with a versioned name is reported as
    an error rather than skipped: a symlink or stray file here could be an
    attempt to redirect the package into an unrelated tree. A pointer is allowed
    but must still be a regular file naming a plausible directory.
    """
    if not root.exists() and not root.is_symlink():
        return []
    if root.is_symlink():
        raise LayoutError(f"{root} must not be a symlink")
    if not root.is_dir():
        raise LayoutError(f"{root} is not a directory")
    names = []
    for entry in sorted(root.iterdir()):
        if entry.name == POINTER_NAME:
            _check_pointer(entry)
            continue
        if not _VERSIONED.fullmatch(entry.name):
            raise LayoutError(f"{entry} has an unrecognised name")
        if entry.is_symlink() or not entry.is_dir():
            raise LayoutError(f"{entry} must be a plain directory")
        names.append(entry.name)
    return names


def _check_pointer(path):
    """The pointer selects a versioned tree, so it may only name one."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise LayoutError(f"{path} must be a regular file")
    try:
        name = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LayoutError(f"{path} is unreadable") from exc
    if not _VERSIONED.fullmatch(name):
        raise LayoutError(f"{path} names an invalid directory")


def _looks_complete(package_name):
    """A payload directory is complete when its runtime and bundle are present."""
    directory = PACKAGES / package_name
    return (
        (directory / "runtime" / "bin" / "python3.13").is_file()
        and (directory / "bundle" / "manifest.json").is_file()
    )


def _bootstrap_complete(bootstrap_name):
    directory = BOOTSTRAP / bootstrap_name
    return (directory / "runtime" / "bin" / "python3.13").is_file()


def classify():
    """Describe the current layout in terms the hooks can act on.

    Returns a dict with ``kind``:

    ``empty``     nothing installed
    ``upgrade``   one complete versioned payload, safe to add another beside it
    """
    # /opt/ai-device/instances is where a normal install puts its instance, so
    # requiring it to be absent refused every upgrade on a host that had one --
    # the exact hosts an upgrade is for. What matters is not the directory but
    # whether it holds something the package cannot account for.
    for name in INSTANCE_NAMES:
        marker = INSTANCE_ROOT / name
        if marker.exists() or marker.is_symlink():
            raise LayoutError(f"legacy management layout {marker} requires explicit migration")

    packages = _versioned_directories(PACKAGES)
    bootstraps = _versioned_directories(BOOTSTRAP)

    if not packages and not bootstraps:
        return {"kind": "empty", "packages": [], "bootstraps": []}

    if len(packages) != 1 or len(bootstraps) != 1:
        # More than one version already present means a previous upgrade or a
        # manual copy left state we have no transaction record for. Adding a
        # third tree without knowing which one is live would make the bootstrap
        # pointer ambiguous, so stop and require an explicit decision.
        raise LayoutError(
            "expected exactly one versioned payload and bootstrap; "
            f"found packages={packages} bootstrap={bootstraps}"
        )

    package, bootstrap = packages[0], bootstraps[0]
    if not _looks_complete(package):
        raise LayoutError(f"payload {package} is incomplete")
    if not _bootstrap_complete(bootstrap):
        raise LayoutError(f"bootstrap {bootstrap} is incomplete")

    return {
        "kind": "upgrade",
        "packages": [package],
        "bootstraps": [bootstrap],
        "package_complete": True,
        "bootstrap_complete": True,
    }


def main(argv):
    try:
        observation = classify()
    except (LayoutError, OSError) as exc:
        print(f"native package pre-install refused: {exc}", file=sys.stderr)
        return 1

    target = argv[2] if len(argv) > 2 else ""
    if observation["kind"] == "empty":
        print("native package pre-install: fresh install", file=sys.stderr)
        return 0
    if not target:
        # Reporting only. A caller that sees an existing layout must also name
        # what it intends to add, or nothing here can judge the request.
        print(json.dumps(observation, sort_keys=True))
        return 0
    if target in observation["packages"]:
        # Reinstalling the same version in place is not an upgrade; dpkg would
        # overwrite files this package already owns while instances still run
        # from them. Refuse and let the operator choose an explicit path.
        print(
            f"native package pre-install refused: {target} is already installed",
            file=sys.stderr,
        )
        return 1
    print(
        f"native package pre-install: adding {target} beside {observation['packages'][0]}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
