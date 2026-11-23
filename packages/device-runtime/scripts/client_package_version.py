"""Write the native package version into a client tree.

The installer version and the Python runtime version are independent numbers:
one 0.5.x package can carry different runtimes, and a runtime can ship in more
than one package. `device_runtime.system_info` reports the package version from
`client-package-version.json` at the tree root, so that file has to exist in
what actually ships.

Only the packagers know the package version, so the tree builder cannot write
this file: it runs before anyone passes `--version`. Each packager calls
`write_package_version` on the payload it is about to ship -- after copying
(the tree is an input, not a scratch space) and before any inventory, because
the PKG upgrade entry pins an exact entry count.

Every consumer treats a missing or malformed file as "no version", so this
must be written exactly once and never guessed.
"""

import json
import re
from pathlib import Path

PACKAGE_VERSION_FILE = "client-package-version.json"
SCHEMA_VERSION = 1
# Mirrors PACKAGE_VERSION_PATTERN in device_runtime/system_info.py: a value the
# client cannot parse is reported as nothing, so refuse it at build time while
# the cause is still visible instead of shipping a silently omitted version.
PACKAGE_VERSION_PATTERN = re.compile(r"[A-Za-z0-9.+~_-]{1,80}")


def write_package_version(tree, version):
    """Write `client-package-version.json` at the root of a client tree.

    `tree` must be the tree that ships, not the source it was copied from: the
    installed root is what the runtime reads, and a copy keeps the build input
    pristine for the next package format.

    Refuses to overwrite, so a caller that runs twice on the same payload is a
    build bug rather than a silent second version.
    """
    version = str(version)
    if not PACKAGE_VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"invalid package version: {version!r}")
    tree = Path(tree)
    if not (tree / "runtime").is_dir():
        raise ValueError(f"not a client tree: {tree}")
    path = tree / PACKAGE_VERSION_FILE
    if path.exists() or path.is_symlink():
        raise ValueError(f"{PACKAGE_VERSION_FILE} already present in {tree}")
    path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "package_version": version}) + "\n",
        encoding="utf-8",
    )
    return path
