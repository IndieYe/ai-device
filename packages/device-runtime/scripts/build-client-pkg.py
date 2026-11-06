"""Build an arm64 client PKG in one of four deliberately distinct modes.

The default product is still inspection-only and refuses installation. The
installable products are selected with --mode: verification and stage_only are
rehearsal instruments for a disposable machine, and shipping is the deliverable.

The native lifecycle is implemented and this builds a real installer, but a
shipping build is not yet a public release: it is unsigned/notarised, and the
full first-install/A->B acceptance on real arm64 hardware is still outstanding.
"""

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

# Shared inventory of a finished tree; the upgrade payload embeds it so the
# compiled resource table and the shipped tree are the same bytes.
_SPEC = importlib.util.spec_from_file_location(
    "mac_resource_inventory", Path(__file__).with_name("client-macos-resource-manifest.py")
)
INVENTORY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(INVENTORY)

IDENTIFIER = "local.ai-device.client.inspection"
# Installable builds must never share an identifier with the inspection product:
# receipts are keyed by it, so a rehearsal install would be indistinguishable
# from a real one and a later release would collide with it. Each installable
# mode gets its own, and stage-only gets its own again because it leaves the
# machine with a payload in place and nothing published.
VERIFICATION_IDENTIFIER = "local.ai-device.client.verification"
STAGE_ONLY_IDENTIFIER = "local.ai-device.client.verification.stage-only"
# The product identifier carries no mode word on purpose: this is the identity
# the receipt will have in the field. A release built under a "-release"
# suffix would have to migrate its receipt on the next build, so the shipping
# package takes the plain name and every rehearsal product keeps a distinguishing
# suffix. Receipts are keyed by identifier alone, so this is also what makes a
# rehearsal install refuse itself when a shipping package is already present.
SHIPPING_IDENTIFIER = "local.ai-device.client"

MODES = ("inspection", "verification", "stage_only", "shipping")

# Payload location and the live program path are deliberately different.
#
# The installer writes its payload in place, but preinstall and postinstall are
# separate processes, so a lock held by a script dies with that script and can
# never span the real write window between them. Rather than pretend otherwise,
# the payload is staged here and the live path only changes through one atomic
# rename, which is why there is no write window to keep mutually exclusive.
#
# The stage name carries the package version and the source build id, and is
# derived by stage_for(). A single fixed stage path was a correctness bug, not
# just untidiness: RENAME_SWAP leaves the previous tree at the stage path, the
# cleanup entry is disabled, and the installer merges its payload into whatever
# is already there. The next install would therefore publish a tree containing
# files the new version no longer ships. Writing into a path that is fresh by
# construction removes that failure mode without depending on cleanup at all.
STAGE_ROOT = "/opt/ai-device-client-stage"
# Build identity, written into the tree by build-client-tree.py. It covers the
# Python sources only, so it distinguishes two trees built from different client
# code; the package version in front of it distinguishes package builds.
IDENTITY_FILE = "client-identity.json"
# Standalone commit executable, built into the tree. Not the launcher (a fixed
# exec wrapper that ignores argv) and not client-macos-installer.m (which embeds
# a resource-table generated from the finished tree and so cannot be part of it).
COMMIT_ENTRY = "ai-device-client-commit"
# Written by postinstall so a failed commit is diagnosable without the installer
# log. It is a report, not a state source: nothing reads it to decide behaviour.
#
# Location matters. /var/tmp is world-writable and is NOT cleaned at boot, so a
# local user can pre-create this path as a symlink and let the root script
# truncate an arbitrary file. The report therefore goes under the root-owned
# management directory, which the script creates with an explicit mode.
COMMIT_STATUS_DIR = "/Library/Application Support/ai-device-client-management"
COMMIT_STATUS_FILE = f"{COMMIT_STATUS_DIR}/install-status"
# The registered upgrade entry locates its resources relative to its own
# executable (locate_bound_resources uses <exe dir>/resources/client-tree), so
# it has to live at a path that does not carry the version. A per-release
# directory would change the address it is invoked through on every upgrade.
UPGRADE_ROOT = "/opt/ai-device-client-upgrade"
UPGRADE_ENTRY = "installer-inspection"
UPGRADE_IDENTIFIER = "local.ai-device.client.upgrade-entry"
# Written by `register` once a client identity exists. Its presence is what makes
# postinstall take the upgrade path, so `installer -pkg` stays the one correct
# command and the caller never has to say which operation it wants.
REGISTRATION_FILE = f"{COMMIT_STATUS_DIR}/registration.json"


def stage_for(version: str, identity_build_id: str) -> str:
    """Derive the stage path for one package build.

    Both components are load-bearing and neither is decorative:

    - `version` is the package version, which changes for every package build.
      The client identity's version is the *application* version (for example
      0.3.4) and stays fixed across package rebuilds, so it cannot make the
      path unique on its own.
    - `build_id` changes only when the client Python sources change, so it
      distinguishes two trees shipped under the same package version.

    The build id is stored as `sha256:<hex>`; the prefix is dropped because a
    colon in a path is legal but awkward in shell, logs and `ls` output.
    """
    digest = identity_build_id.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"client identity build_id is not a sha256 digest: {identity_build_id!r}")
    return f"{STAGE_ROOT}-{version}-{digest[:12]}"


def read_client_identity(tree: Path, version: str) -> str:
    """Read and cross-check the tree's client identity before staging it.

    Strict: a tree without an identity file is refused. The stage path is the
    only thing standing between two builds and a merged payload, so silently
    falling back to a weaker name would reintroduce exactly the bug this
    replaces.

    The package version must extend the application version (0.3.4.12 extends
    0.3.4). That catches a package built from a different tree than the one it
    claims, without pretending the two numbers are the same thing.
    """
    path = tree / IDENTITY_FILE
    if not path.is_file():
        raise ValueError(f"client tree has no {IDENTITY_FILE}; rebuild the tree before packaging")
    identity = json.loads(path.read_text())
    application = identity.get("version")
    if not isinstance(application, str) or not application:
        raise ValueError(f"{IDENTITY_FILE} has no version")
    if version != application and not version.startswith(application + "."):
        raise ValueError(
            f"package version {version!r} does not extend client version {application!r}"
        )
    return identity.get("build_id")


def verb_from_header(name: str) -> str:
    """Read a verb word from the C header that the binary also compiles against.

    The point is that the generated script and the shipped binary cannot drift:
    a rename edits one file. Hard-coding the string here is exactly the bug this
    replaces (the script asked for `commit-program` while the binary accepted
    only `commit`).
    """
    header = Path(__file__).resolve().parents[1] / "native" / "client-macos-program-verbs.h"
    text = header.read_text()
    match = re.search(rf'#define\s+{name}\s+"([^"]+)"', text)
    if not match:
        raise ValueError(f"{name} missing from {header.name}")
    return match.group(1)

PREINSTALL = """#!/bin/sh
set -eu
echo 'Inspection-only package: macOS client lifecycle is not enabled; installation refused.' >&2
exit 1
"""

# A verification build may actually install, so its preinstall has to do the
# one job the inspection build never reaches: refuse when the stage path it is
# about to populate already exists.
#
# The reason is that the publish step cannot distinguish its own leftovers from
# foreign content. `commit_staged_program` renames an existing program tree onto
# the stage (RENAME_SWAP), so an A->B upgrade leaves A sitting at B's stage. Any
# tree the installer then merges into a pre-existing stage directory is content
# this package never authored, and the commit would publish it wholesale. The
# check is deliberately fail-closed and deliberately crude: it refuses on
# existence, not on content, because content cannot be attributed here.
#
# -e and -L are separate on purpose: a dangling symlink fails -e but still
# occupies the name and would still be merged into.
#
# Scope limit, stated so this check is not mistaken for an installation mutex:
# it is a point-in-time test that runs in preinstall. It cannot stop a second
# installer that also observed an absent stage from racing this one between the
# check and the payload write, and it says nothing about processes still holding
# the old program tree open. Serialization against a concurrent installer is
# not provided here.
PREINSTALL_RESIDUE = """#!/bin/sh
set -eu
STAGE='{stage}'
if [ -e "$STAGE" ] || [ -L "$STAGE" ]; then
    echo 'PROGRAM_COMMIT_REFUSED_V1: stage path already exists and its content cannot be attributed; remove it and retry' >&2
    exit 1
fi
exit 0
"""

# Character-for-character the strings `commit_staged_program` returns, so the
# script reports the native outcome instead of re-deriving it in shell.
COMMIT_TOKENS = {
    0: "PROGRAM_COMMITTED_V1",
    1: "PROGRAM_COMMIT_REFUSED_V1",
    2: "PROGRAM_COMMIT_UNKNOWN_V1",
}

# What a shipping install says after the program is published but this host has
# no client identity yet.
#
# Publishing the program on a fresh machine is the whole job the package can do
# by itself: the requirement is explicit that with no named user the installer
# puts the program down and reports that configuration is outstanding, and that
# it must not guess a user from root's HOME or pick one of several logged-in
# sessions. So this is not a fallback -- it is the intended first-install end
# state, and the only thing missing from the payload was saying so.
#
# Both verification and shipping reach this point; only shipping says anything,
# because a rehearsal build is not something a user should be told to configure.
SHIPPING_EPILOGUE = """# A shipping install that published the program on a machine with no client
# identity is complete but not yet useful: nothing will start at login until a
# user is chosen and registered. Report the next steps rather than exiting 0
# silently and leaving the operator to discover an inert installation.
#
# Registration requires a configuration file that already exists and belongs to
# the target user (it is validated as that user before anything is published),
# so telling the operator to "run register --help" would only send them into a
# refusal. The path below is the layout the registrar itself will read.
if [ ! -f "$REGISTRATION" ]; then
    cat >&2 <<'OUTSTANDING'
AI_DEVICE_CLIENT_INSTALLED_NOT_REGISTERED_V1
The client program is installed at $PROGRAM, but this machine has no client
identity yet, so nothing will start at login.

Still required, in this order:
  1. As the user who should run the client, create
     ~/Library/Application Support/ai-device-client/client.json with the centre
     URL and device id, plus the device token in a sibling file named 'token'
     (0600). Both must be owned by that user.
  2. As an administrator, register that user:

       sudo $PROGRAM/runtime/bin/python3 -I -B \\
         -m device_runtime.client_macos_register register \\
         --user <that-user> --confirm-legacy-clients-disabled

The package does not choose a user for you and does not run a client as root.
OUTSTANDING
fi
"""

POSTINSTALL = """#!/bin/sh
# Runs as root inside the installer's own transaction. The staged tree is never
# written to the live path: it is published by one atomic rename either way.
#
# One package, two paths, chosen by state rather than by the caller:
#
#   registered   -> the upgrade coordinator, which asks the running instance
#                   to exit, boots the job out, renames this payload over the
#                   live tree, bootstraps once and observes readiness.
#   unregistered -> the commit verb on the staged binary: one atomic swap of
#                   the payload onto the live path. This does NOT go through
#                   `first-program`, so the first-install host-conflict and
#                   management-lock composition is not exercised here; the only
#                   gate is the preinstall residue check.
#
#                   (This line previously claimed first-program publication,
#                   which the script has never called. Corrected because the
#                   difference is exactly the unwired first-install path, not a
#                   wording detail.)
#
# The upgrade path needs a running client to hand over. A logged-out user has
# none, so the coordinator refuses instead of guessing, the install fails, and
# the old program stays in place. Upgrading therefore requires the user to be
# logged in; there is no logged-out update path by design.
set -eu
PROGRAM='{program}'
STAGE='{stage}'
ENTRY='{entry}'
VERB='{verb}'
STATUS_DIR='{status_dir}'
STATUS_FILE='{status_file}'
UPGRADE_ENTRY='{upgrade_entry}'
REGISTRATION='{registration}'
if [ ! -d "$STAGE" ]; then
    # Nothing was staged: refuse rather than touch the live program.
    echo 'PROGRAM_COMMIT_REFUSED_V1' >&2
    exit 1
fi
if [ ! -x "$ENTRY" ]; then
    echo 'PROGRAM_COMMIT_REFUSED_V1: commit entry missing' >&2
    exit 1
fi
# Exactly one attempt either way. The exit status cannot distinguish "nothing
# happened" from "happened but unverified", so it is recorded verbatim and
# never retried.
if [ -f "$REGISTRATION" ]; then
    if [ ! -x "$UPGRADE_ENTRY" ]; then
        echo 'PROGRAM_COMMIT_REFUSED_V1: registered client but no upgrade entry in payload' >&2
        exit 1
    fi
    # The stage path is this package's own payload. The entry verifies it
    # against its embedded inventory before renaming anything.
    set +e
    result=$("$UPGRADE_ENTRY" upgrade-registered-program --confirm-idle-instance-exits "$STAGE" 2>&1)
    status=$?
    set -e
else
    set +e
    result=$("$ENTRY" "$VERB" "$STAGE" "$PROGRAM" 2>&1)
    status=$?
    set -e
fi
if [ -n "$result" ]; then
    echo "$result" >&2
fi
# Record the outcome without letting a planted symlink redirect the write.
# The directory is private to root by contract: the client registration path
# (ensure_directory) and the native management provisioning both reject a
# directory that is not 0700 root:wheel, so 0755 here made registration fail
# with "directory ownership or permissions are incompatible".
if mkdir -p -m 0700 "$STATUS_DIR" 2>/dev/null; then
    if [ ! -L "$STATUS_DIR" ] && [ -d "$STATUS_DIR" ]; then
        # mkdir -m does not change a directory that already exists, so a tree
        # installed by an earlier build stays 0755 unless it is corrected here.
        chown root:wheel "$STATUS_DIR" 2>/dev/null || true
        chmod 0700 "$STATUS_DIR" 2>/dev/null || true
        if [ ! -L "$STATUS_FILE" ]; then
            printf '%s\\n' "$status" > "$STATUS_FILE" 2>/dev/null || true
        fi
    fi
fi
if [ "$status" -ne 0 ]; then
    exit 1
fi
{epilogue}exit 0
"""


# The inspection build and the default verification build both run the commit
# in postinstall. For residue research that is useless on its own: the commit's
# RENAME_SWAP leaves the previous program tree at the stage path, so inspecting
# the stage afterwards cannot tell "the Installer preserved a foreign file while
# writing the payload" from "the commit swapped an old tree back into place".
#
# Stage-only keeps the payload write and drops the publish, which is the only
# way to observe the post-payload / pre-swap state. Nothing is published, so it
# is a research instrument and not a deployment path.
POSTINSTALL_STAGE_ONLY = """#!/bin/sh
# Verification, stage-only: stop before the publish step on purpose.
set -eu
STAGE='{stage}'
if [ ! -d "$STAGE" ]; then
    echo 'STAGE_ONLY_NO_PAYLOAD_V1' >&2
    exit 1
fi
echo 'STAGE_ONLY_PAYLOAD_WRITTEN_V1'
exit 0
"""


def distribution(version, *, mode="inspection"):
    """Build the Distribution.xml for one of four deliberately distinct products.

    inspection  — refuses at both the product and the component level. The
                  historical contract; unchanged.
    verification — installs and publishes via postinstall. For rehearsing the
                  real lifecycle on a disposable machine.
    stage_only  — installs the payload and stops before publishing. Exists
                  because the commit's RENAME_SWAP leaves the previous program
                  tree at the stage path, so "stage after a normal install"
                  cannot distinguish Installer payload behaviour from commit
                  behaviour. Only this mode can observe the post-payload /
                  pre-swap state.
    shipping    — the deliverable. Same activation path as verification, but it
                  reports a success the user can act on: an unregistered client
                  is told how to configure and register, instead of the install
                  succeeding silently on a machine where nothing will ever run.
    """
    if mode not in MODES:
        raise ValueError(f"unknown package mode: {mode!r}")
    installable = mode != "inspection"
    identifier = {
        "inspection": IDENTIFIER,
        "verification": VERIFICATION_IDENTIFIER,
        "stage_only": STAGE_ONLY_IDENTIFIER,
        "shipping": SHIPPING_IDENTIFIER,
    }[mode]
    title = {
        "inspection": "AI Device Client — inspection only",
        "verification": "AI Device Client — VERIFICATION BUILD, not for release",
        "stage_only": "AI Device Client — VERIFICATION stage-only, not for release",
        "shipping": "AI Device Client",
    }[mode]
    root = ET.Element("installer-gui-script", {"minSpecVersion": "2"})
    ET.SubElement(root, "title").text = title
    ET.SubElement(
        root,
        "options",
        {
            "customize": "never",
            "require-scripts": "true",
            "hostArchitectures": "arm64",
        },
    )
    ET.SubElement(
        root,
        "domains",
        {
            "enable_anywhere": "false",
            "enable_currentUserHome": "false",
            "enable_localSystem": "true",
        },
    )
    if installable:
        # No blanket refusal: this build exists to be installed on a disposable
        # arm64 machine. The check still runs so a wrong-architecture or
        # already-present live program stops the install before any payload is
        # written.
        ET.SubElement(root, "installation-check", {"script": "verifyInstallTarget()"})
        # The shipping product drops the "VERIFICATION" wording from the only
        # message an ordinary user is guaranteed to see.
        message = (
            "This client requires macOS 11 or later."
            if mode == "shipping"
            else "Verification build requires macOS 11 or later."
        )
        ET.SubElement(root, "script").text = f"""
function verifyInstallTarget() {{
    if (system.compareVersions(system.version.ProductVersion, '11.0') < 0) {{
        my.result.type = 'Fatal';
        my.result.message = '{message}';
        return false;
    }}
    return true;
}}
"""
    else:
        ET.SubElement(root, "installation-check", {"script": "rejectInspection()"})
        ET.SubElement(root, "script").text = """
function rejectInspection() {
    my.result.type = 'Fatal';
    my.result.message = 'Inspection-only package; installation is disabled.';
    return false;
}
"""
    outline = ET.SubElement(root, "choices-outline")
    ET.SubElement(outline, "line", {"choice": "client"})
    choice = ET.SubElement(root, "choice", {"id": "client", "visible": "false"})
    ET.SubElement(choice, "pkg-ref", {"id": identifier})
    ET.SubElement(root, "pkg-ref", {"id": identifier, "version": version}).text = "client.pkg"
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def validate_bom(text):
    """System payload must not inherit the developer's account ownership."""
    lines = text.splitlines()
    if not lines:
        raise ValueError("package ownership inventory is empty")
    for line in lines:
        fields = line.split("\t")
        if len(fields) < 3 or fields[2] != "0/0":
            raise ValueError("package payload ownership is not root:wheel")


def stage_upgrade_entry(tree, root, build_directory, *, run=None):
    """Compile the upgrade entry, embedding the inventory of `tree`.

    The entry does NOT ship a copy of the tree. It embeds the inventory
    (~300 KB of hashes) and is given the staged payload path on argv, so one
    install carries exactly one tree. Shipping a second copy would double an
    88 MB payload and introduce a second source that could disagree with the
    first; the embedded inventory already pins the content it will publish.

    The layer still lives outside the program tree: that tree is the object
    being replaced, and its stage path changes per build, so an entry inside it
    could not survive the swap it performs.
    """
    tree, root = Path(tree), Path(root)
    payload = root / UPGRADE_ROOT.lstrip("/")
    payload.mkdir(parents=True, exist_ok=True)
    native = Path(__file__).resolve().parents[1] / "native"
    build_directory = Path(build_directory)
    build_directory.mkdir(parents=True, exist_ok=True)
    # `client-macos-installer.m` reaches resource-check.c through writer-probe.c,
    # which includes a generated resource-table.h. It is built from the tree the
    # package stages, so the embedded inventory and that payload cannot disagree.
    manifest = INVENTORY.inventory(tree)
    records = []
    for item in manifest["entries"]:
        kind = {"file": 0, "directory": 1, "symlink": 2}[item["kind"]]
        value = item.get("sha256", item.get("target", ""))
        records.append(
            "{{{},{},{},{},{}}}".format(
                json.dumps(item["path"]), kind, item.get("mode", 0),
                item.get("size", 0), json.dumps(value),
            )
        )
    (build_directory / "resource-table.h").write_text(
        "static const struct resource resources[] = {\n"
        + ",\n".join(records)
        + "\n};\n#define RESOURCE_COUNT (sizeof(resources)/sizeof(resources[0]))\n"
    )
    entry = payload / UPGRADE_ENTRY
    execute = run or subprocess.run
    execute(
        [
            "/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror", "-O2",
            "-arch", "arm64", "-mmacosx-version-min=11.0", "-fobjc-arc",
            "-framework", "Foundation",
            "-I", str(build_directory),
            str(native / "client-macos-installer.m"),
            "-o", str(entry),
        ],
        check=True,
    )
    if not entry.is_file():
        raise ValueError(f"upgrade entry {UPGRADE_ENTRY} was not produced")
    entry.chmod(0o755)
    execute(["/usr/bin/lipo", "-verify_arch", "arm64", str(entry)], check=True)
    return entry


def build_upgrade_payload(tree, output, *, run=None):
    """Build the registered-upgrade entry as its own standalone product.

    This emits only the entry. The shipping PKG stages the same entry through
    `stage_upgrade_entry`, so one install carries both the program and the means
    to replace it; this separate product stays for focused experiments.
    """
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise ValueError("native macOS arm64 builder required")
    tree, output = Path(tree).resolve(), Path(output).absolute()
    if output.is_relative_to(tree) or tree.is_relative_to(output):
        raise ValueError("output must be separate from the input tree")
    manifest = json.loads((tree / "build-manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("platform") != "darwin-arm64":
        raise ValueError("upgrade payload requires a darwin-arm64 client tree")
    for name in ("runtime/bin/python3", "bin/ai-device-client-launcher",
                 "third-party-inventory.json"):
        if not (tree / name).is_file():
            raise ValueError("client tree is incomplete")
    output.mkdir(parents=True, exist_ok=False)
    # Only the entry is staged: it embeds the inventory of `tree`, and is given
    # the staged payload path when the Installer runs it.
    stage_upgrade_entry(tree, output / "root", output / "build", run=run)
    execute = run or subprocess.run
    # The entry validates the tree against the owner of the running user (0 for
    # the administrator entry); the package itself is audited by validate_bom.
    package = output / f"ai-device-client-upgrade-{UPGRADE_ENTRY}.pkg"
    execute(
        [
            "/usr/bin/pkgbuild",
            "--root",
            str(output / "root"),
            "--identifier",
            UPGRADE_IDENTIFIER,
            "--version",
            "1.0",
            "--install-location",
            "/",
            "--ownership",
            "recommended",
            str(package),
        ],
        check=True,
    )
    expanded = output / "component-audit"
    # --expand-full, not --expand: the latter leaves Payload as an opaque
    # archive, so the layout this package exists to guarantee cannot be checked.
    execute(["/usr/sbin/pkgutil", "--expand-full", str(package), str(expanded)], check=True)
    bom = execute(["/usr/bin/lsbom", str(expanded / "Bom")], check=True,
                  capture_output=True, text=True)
    validate_bom(bom.stdout)
    # The entry looks for this exact relative shape; a package that installed
    # it anywhere else would be present but unusable. `pkgutil --expand` yields
    # Payload/<install-location>/..., so the audit path drops the leading slash
    # exactly as pkgbuild does when it lays the root down.
    audited = expanded / "Payload" / UPGRADE_ROOT.lstrip("/")
    if not (audited / UPGRADE_ENTRY).is_file():
        raise ValueError("upgrade payload has no entry executable")
    # This layer must never carry a program tree: the shipping package already
    # stages exactly one, and a second copy here would be a competing source.
    if (audited / "resources").exists():
        raise ValueError("upgrade payload must not ship a resource tree")
    return package


def build(tree, output, version, *, run=None, program="/opt/ai-device-client",
          stage=None, mode="inspection"):
    if mode not in MODES:
        raise ValueError(f"unknown package mode: {mode!r}")
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise ValueError("PKG construction requires native macOS arm64")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", version):
        raise ValueError("PKG version must have one to four numeric components")
    tree, output = Path(tree).resolve(), Path(output).absolute()
    if output.is_relative_to(tree) or tree.is_relative_to(output):
        raise ValueError("output must be separate from the input tree")
    # Default is derived per build; an explicit stage stays supported so the
    # separation checks below can be exercised with arbitrary layouts.
    if stage is None:
        stage = stage_for(version, read_client_identity(tree, version))
    # The payload must never land on the live path: that is the whole point of
    # staging it, so refuse a configuration that would reintroduce the write
    # window the atomic commit exists to remove.
    if not stage.startswith("/") or not program.startswith("/"):
        raise ValueError("program and stage paths must be absolute")
    if stage == program or stage.startswith(program + "/") or program.startswith(stage + "/"):
        raise ValueError("stage must be a separate location from the live program")
    manifest = json.loads((tree / "build-manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("platform") != "darwin-arm64":
        raise ValueError("PKG input must be a darwin-arm64 client tree")
    for name in (
        "runtime/bin/python3",
        "bin/ai-device-client-launcher",
        "third-party-inventory.json",
    ):
        if not (tree / name).is_file():
            raise ValueError("client tree is incomplete")
    # Fail rather than overwrite a previous build or delete debug evidence.
    output.mkdir(parents=True, exist_ok=False)
    payload = output / "root" / stage.lstrip("/")
    payload.parent.mkdir(parents=True)
    shutil.copytree(
        tree, payload, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    # postinstall calls the same binary it just staged, so the commit entry has
    # to exist inside the payload. Reusing the shipped CLI avoids a second
    # artifact that could drift from it.
    entry = payload / "bin" / COMMIT_ENTRY
    if not entry.is_file():
        raise ValueError(f"staged tree has no {COMMIT_ENTRY}; build the client tree first")
    # The commit entry must be a real executable that can take a subcommand.
    # The launcher is deliberately excluded: it ignores argv and execs a fixed
    # target, so wiring it here would silently do the wrong thing.
    if not os.access(entry, os.X_OK):
        raise ValueError(f"staged {COMMIT_ENTRY} is not executable")
    # The shipping package also carries the entry that will replace this tree on
    # the next install. Without it the first upgrade would have nothing to run,
    # and splitting it into a second product would mean two commands for what
    # the requirement calls one ("install and upgrade share one path").
    #
    # Only the entry is added, not a second tree: it embeds the inventory of the
    # very tree staged above and is handed that stage path at install time.
    if mode != "inspection":
        stage_upgrade_entry(tree, output / "root", output / "upgrade-build", run=run)
    scripts = output / "scripts"
    scripts.mkdir()
    preinstall = scripts / "preinstall"
    # The inspection build keeps its unconditional refusal; only an installable
    # mode gets a preinstall that is allowed to proceed.
    preinstall.write_text(
        PREINSTALL_RESIDUE.format(stage=stage) if mode != "inspection" else PREINSTALL
    )
    preinstall.chmod(0o755)
    postinstall = scripts / "postinstall"
    if mode == "stage_only":
        postinstall.write_text(POSTINSTALL_STAGE_ONLY.format(stage=stage))
    else:
        postinstall.write_text(
            POSTINSTALL.format(
                program=program,
                stage=stage,
                entry=f"{stage}/bin/{COMMIT_ENTRY}",
                verb=verb_from_header("PROGRAM_VERB_COMMIT"),
                status_dir=COMMIT_STATUS_DIR,
                status_file=COMMIT_STATUS_FILE,
                upgrade_entry=f"{UPGRADE_ROOT}/{UPGRADE_ENTRY}",
                registration=REGISTRATION_FILE,
                epilogue=SHIPPING_EPILOGUE if mode == "shipping" else "",
            )
        )
    postinstall.chmod(0o755)
    dist = output / "Distribution.xml"
    dist.write_text(distribution(version, mode=mode))
    identifier = {
        "inspection": IDENTIFIER,
        "verification": VERIFICATION_IDENTIFIER,
        "stage_only": STAGE_ONLY_IDENTIFIER,
        "shipping": SHIPPING_IDENTIFIER,
    }[mode]
    execute = run or subprocess.run
    packaged = execute(
        [
            "/usr/bin/pkgbuild",
            "--root",
            str(output / "root"),
            "--scripts",
            str(scripts),
            "--identifier",
            identifier,
            "--version",
            version,
            "--install-location",
            "/",
            "--ownership",
            "recommended",
            str(output / "client.pkg"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    diagnostic = (packaged.stdout or "") + (packaged.stderr or "")
    (output / "pkgbuild.log").write_text(diagnostic)
    # pkgbuild emits "write: Permission denied" on stderr even when it succeeds:
    # that is its own probe of the destination, not a failure of ours. The exit
    # status is the authoritative signal, so the permission text must not be
    # treated as fatal on its own -- doing that rejected every good build and
    # masked the real error. When the build does fail, the text is kept in
    # pkgbuild.log and quoted in the message so an ownership problem can still
    # be told apart from a packaging problem.
    if packaged.returncode != 0:
        detail = ""
        if "permission denied" in diagnostic.lower() or "operation not permitted" in diagnostic.lower():
            detail = " (pkgbuild reported a permission error; see pkgbuild.log)"
        raise ValueError(
            f"pkgbuild failed with status {packaged.returncode}{detail}; "
            "retained output/pkgbuild.log for diagnosis"
        )
    # Verify the emitted package rather than assuming --ownership succeeded.
    # Expansion does not install or execute package scripts.
    expanded = output / "component-audit"
    execute(
        [
            "/usr/sbin/pkgutil",
            "--expand",
            str(output / "client.pkg"),
            str(expanded),
        ],
        check=True,
    )
    bom = execute(
        [
            "/usr/bin/lsbom",
            str(expanded / "Bom"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    validate_bom(bom.stdout)
    # Shipping keeps the plain artifact name: every rehearsal product is
    # distinguishable from the deliverable by its filename alone.
    if mode == "shipping":
        result = output / f"ai-device-client-{version}-arm64.pkg"
    else:
        suffix = {
            "inspection": "inspection",
            "verification": "verification",
            "stage_only": "verification-stage-only",
        }[mode]
        result = output / f"ai-device-client-{version}-arm64-{suffix}.pkg"
    execute(
        [
            "/usr/bin/productbuild",
            "--distribution",
            str(dist),
            "--package-path",
            str(output),
            str(result),
        ],
        check=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="inspection",
        help="Which product to emit. inspection (default) refuses installation; "
        "verification installs and publishes for rehearsal on a disposable "
        "machine; stage_only writes the payload and stops before publishing; "
        "shipping is the deliverable.",
    )
    parser.add_argument(
        "--upgrade-entry",
        action="store_true",
        help="Emit only the registered-upgrade entry and its bound resource "
        "tree at a version-free path. This layer replaces no program; the "
        "entry still refuses a busy instance and requires an explicit "
        "acknowledgement when invoked.",
    )
    args = parser.parse_args()
    if args.upgrade_entry:
        if args.mode != "inspection":
            parser.error("--upgrade-entry is a separate product; it takes no --mode")
        print(build_upgrade_payload(args.tree, args.output))
        raise SystemExit(0)
    print(build(args.tree, args.output, args.version, mode=args.mode))
