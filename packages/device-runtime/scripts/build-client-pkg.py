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
# Python sources, the native executables the tree ships, and the native sources
# they and the upgrade entry are compiled from, so it distinguishes two trees
# built from different client code -- including a change that only reaches the
# out-of-tree entry. The package version in front of it distinguishes package
# builds.
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
# The destination the native entries have compiled in (client-macos-first-entry.h
# and client-macos-upgrade-entry.h). Kept as a constant so the builder can refuse
# a --program that would disagree with it; see the check in build().
NATIVE_PROGRAM_PATH = "/opt/ai-device-client"
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
    - `build_id` changes when the client Python sources, the shipped native
      executables, or the native sources behind them change, so it distinguishes
      two trees shipped under the same package version.

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
    # QUOTED heredoc: nothing inside is expanded by this script. The script runs
    # as root, so an unquoted $HOME would print /var/root -- a path that does not
    # exist for the user who must actually run these steps. The program path is
    # inlined at build time for the same reason: it is a constant of this
    # package, so the shell does not need to resolve it.
    cat >&2 <<'OUTSTANDING'
AI_DEVICE_CLIENT_INSTALLED_NOT_REGISTERED_V1
The client program is installed at {program}, but this machine has no client
identity yet, so nothing will start at login.

Still required, in this order. Do not paraphrase these paths: the registrar
compares client.json against the fixed layout and refuses anything else.
Run all three steps as the user who should run the client, in that user's own
terminal: below, ~ and $HOME mean that user's home, not root's.

  1. Create the configuration directory with mode 0700, and inside it two files
     owned by you:

       ~/Library/Application Support/ai-device-client/client.json  (0600)
         exactly these four keys are required:
         {{"server_url": "wss://<centre host>",
          "device_id": "<device id>",
          "state_dir": "<home>/Library/Application Support/ai-device-client/state",
          "token_file": "<home>/Library/Application Support/ai-device-client/token"}}

         server_url must be a ws/wss URL without embedded credentials; any
         non-loopback host requires wss://. A /device-runtime mount is accepted;
         query strings and fragments are refused.

       ~/Library/Application Support/ai-device-client/token  (0600)
         the device token, one line, no extra keys

     Mode 0600 on client.json is required, not cosmetic: the registrar rejects
     any group/other permission on the config file or the token.

     state_dir and token_file must be these absolute paths, with <home> spelled
     out literally; the probe compares them after expanding the account home.

  2. As an administrator, register that user (quote the name; registration is
     root-only and does not start the client):

       sudo {program}/runtime/bin/python3 -I -B \\
         -m device_runtime.client_macos_register register \\
         --user '<user>' --confirm-legacy-clients-disabled

  3. The client starts at the next GUI login of that user. To start it now
     without logging out, run this as that user (RunAtLoad starts it at once):

       launchctl bootstrap gui/$(id -u) \\
         "$HOME/Library/LaunchAgents/local.ai-device.client.plist"

     The launcher resolves the account home from its own uid, so it must run in
     that user's session; a root-session bootstrap would target the wrong home.

The package does not choose a user for you and does not run a client as root.
OUTSTANDING
fi
"""

POSTINSTALL = r"""#!/bin/sh
# Runs as root inside the installer's own transaction. The staged tree is never
# written to the live path: it is published by one atomic rename either way.
#
# One package, three branches, chosen by state rather than by the caller:
#
#   registration.json exists
#                  -> the upgrade coordinator, which asks the running instance
#                     to exit, boots the job out, renames this payload over the
#                     live tree, bootstraps once and observes readiness.
#   no registration, live program present
#                  -> `commit`: one atomic rename of the payload onto the live
#                     path. No host-conflict probe runs. That is acceptable only
#                     because an absent identity means no registered service of
#                     ours is running from that path; a program left by an
#                     earlier install whose identity was removed is NOT detected
#                     here, so a stale instance from it would keep running.
#   no registration, live program absent
#                  -> `first-program`, which carries the gates a bare rename
#                     lacks: private management lock, inventory check against
#                     this build's embedded hashes, and a read-only host
#                     conflict probe. It publishes by copying, so the stage is
#                     removed only after success (see below).
#
# Both non-registered branches invoke the upgrade entry, because that is the
# executable carrying the inventory; the commit binary in the payload has none.
#
# The upgrade branch needs a running client to hand over. A logged-out user has
# none, so the coordinator refuses instead of guessing, the install fails, and
# the old program stays in place. Upgrading therefore requires that user to be
# logged in and the client running; there is no logged-out update path.
set -eu
PROGRAM='{program}'
STAGE='{stage}'
ENTRY='{entry}'
VERB='{verb}'
STATUS_DIR='{status_dir}'
STATUS_FILE='{status_file}'
UPGRADE_ENTRY='{upgrade_entry}'
REGISTRATION='{registration}'
# Record an outcome even on the early-refusal paths below. installer shows the
# operator only "the install failed" and discards script stderr, so a message
# that exists solely on stderr is a message nobody reads. The status file and
# the note beside it are the only channel that survives the install.
#
# Takes its values from STATUS_CODE and STATUS_NOTE rather than positional
# parameters: this text is a str.format template, and $1/$2 would be read as
# replacement fields.
record_status() {{
    if mkdir -p -m 0700 "$STATUS_DIR" 2>/dev/null; then
        if [ ! -L "$STATUS_DIR" ] && [ -d "$STATUS_DIR" ]; then
            chown root:wheel "$STATUS_DIR" 2>/dev/null || true
            chmod 0700 "$STATUS_DIR" 2>/dev/null || true
            if [ ! -L "$STATUS_FILE" ]; then
                printf '%s\n' "$STATUS_CODE" > "$STATUS_FILE" 2>/dev/null || true
            fi
            if [ -n "$STATUS_NOTE" ] && [ ! -L "$STATUS_DIR/install-note" ]; then
                printf '%s\n' "$STATUS_NOTE" > "$STATUS_DIR/install-note" 2>/dev/null || true
                chmod 0600 "$STATUS_DIR/install-note" 2>/dev/null || true
            elif [ -z "$STATUS_NOTE" ] && [ -f "$STATUS_DIR/install-note" ] && [ ! -L "$STATUS_DIR/install-note" ]; then
                # A successful install leaves no note behind. Keeping the
                # previous failure's text next to status 0 tells the operator
                # their new install has a problem it does not have.
                rm -f "$STATUS_DIR/install-note" 2>/dev/null || true
            fi
        fi
    fi
}}
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
    # A registration with no live program tree is an orphaned identity: the
    # registrar wrote registration.json, but the program it registers is gone.
    # This happens whenever the program directory is removed without also
    # removing the identity -- a manual rm, a disk error, a third-party cleaner.
    #
    # The upgrade coordinator cannot handle it, and its refusal is correct: it
    # needs a running instance to ask for exit, and there is no program to run
    # one from. But the refusal alone left the operator with "the install
    # failed", no cause, and no way out -- every later install retried the same
    # doomed branch. Detect it here, name it, and print the exact recovery
    # command instead of running a coordinator that cannot succeed.
    if [ -L "$PROGRAM" ]; then
        # The live path is a symlink, not a directory. An orphan is "the tree is
        # gone"; a link is a different fault (a relocation or a stray link), and
        # the recovery for it is not "delete the identity". Name it and stop
        # rather than reporting the case the notice can actually explain.
        STATUS_CODE=70
        STATUS_NOTE="program path is a symlink, not a program tree: $PROGRAM"
        record_status
        cat >&2 <<'SYMLINKED'
AI_DEVICE_CLIENT_PROGRAM_IS_SYMLINK_V1
The client program path exists but is a symbolic link, not the installed
program tree:
SYMLINKED
        printf '  %s -> %s\n' "$PROGRAM" "$(readlink "$PROGRAM" 2>/dev/null || echo '?')" >&2
        cat >&2 <<'SYMLINKED'

This is not an orphaned registration: the identity still describes a program
that was placed elsewhere. Nothing was changed by this attempt. Inspect the
link target before removing anything; if the tree really is gone, remove the
link first and run this installer again.
SYMLINKED
        exit 1
    fi
    if [ ! -d "$PROGRAM" ]; then
        STATUS_CODE=70
        # "No program directory" does not mean "no client running": an instance
        # started before the tree was removed keeps running from its open files.
        # Observe first -- a read-only host-conflict check driven by this
        # payload's own inventory -- because deleting the identity out from under
        # a live client leaves a process that can never be managed again, which
        # is worse than the orphan it was meant to fix.
        observe_status=1
        if [ -x "$UPGRADE_ENTRY" ] && [ -d "$STAGE" ]; then
            set +e
            "$UPGRADE_ENTRY" observe-unregistered-program --confirm-legacy-clients-disabled "$STAGE" >/dev/null 2>&1
            observe_status=$?
            set -e
        fi
        if [ "$observe_status" -eq 0 ]; then
            host_line='No client instance was found running from this program.'
        else
            host_line='WARNING: a client instance may still be running. Stop it first;'
            host_line="$host_line
removing the identity of a running client leaves a process nothing can manage."
        fi
        # The registered home comes from the identity file, never from this
        # shell: postinstall runs as root, where $HOME is /var/root.
        owner_home=$(sed -n 's/.*"home":[[:space:]]*"\([^"]*\)".*/\1/p' "$REGISTRATION" 2>/dev/null | head -1)
        owner_uid=$(sed -n 's/.*"uid":[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$REGISTRATION" 2>/dev/null | head -1)
        [ -n "$owner_home" ] || owner_home='<registered user home>'
        [ -n "$owner_uid" ] || owner_uid='<uid>'
        STATUS_NOTE="orphan: registration without program at $PROGRAM; recovery steps recorded in install-note"
        record_status
        # Every heredoc below is QUOTED and every path is either a build-time
        # constant or read from the identity file, so nothing depends on this
        # root shell's environment.
        cat >&2 <<'ORPHANED'
AI_DEVICE_CLIENT_IDENTITY_WITHOUT_PROGRAM_V1
This machine has a client registration but no program tree, so the upgrade
path has nothing to hand over and this install cannot proceed. No program,
configuration, token or business data has been changed by this attempt.

ORPHANED
        printf '%s\n' "$host_line" >&2
        cat >&2 <<'ORPHANED'

The registration records a user, uid, gid and home only; configuration, token
and business data are NOT part of it and are not removed with it.

To recover, in this order:

  1. Make sure no client process is left running, then unload the job if it is
     still registered:
ORPHANED
        printf '       launchctl bootout gui/%s/local.ai-device.client\n' "$owner_uid" >&2
        cat >&2 <<'ORPHANED'

  2. Remove that user's LaunchAgent plist:
ORPHANED
        printf '       rm -f "%s/Library/LaunchAgents/local.ai-device.client.plist"\n' "$owner_home" >&2
        cat >&2 <<'ORPHANED'

  3. Remove the orphaned identity -- it is the only file that must go:
ORPHANED
        printf '       sudo rm -f "%s"\n' "$REGISTRATION" >&2
        cat >&2 <<'ORPHANED'

  4. If an interrupted attempt left this package's own stage directory behind,
     remove exactly this path (no other directory is involved):
ORPHANED
        printf '       sudo rm -rf "%s"\n' "$STAGE" >&2
        cat >&2 <<'ORPHANED'

Then run this same installer again; it will take the first-install path.

The same guidance is saved in:
ORPHANED
        printf '  %s\n' "$STATUS_DIR/install-note" >&2
        # install-note is the only channel the operator can read afterwards:
        # the installer window shows just "the install failed".
        if [ -d "$STATUS_DIR" ] && [ ! -L "$STATUS_DIR" ] && [ ! -L "$STATUS_DIR/install-note" ]; then
            RECOVERY_TEXT=$(printf '%s\n' \
                'AI_DEVICE_CLIENT_IDENTITY_WITHOUT_PROGRAM_V1' \
                "$(printf 'program missing: %s' "$PROGRAM")" \
                "$host_line" \
                "$(printf 'registered home: %s (uid %s)' "$owner_home" "$owner_uid")" \
                'recover:' \
                "$(printf '  1) launchctl bootout gui/%s/local.ai-device.client' "$owner_uid")" \
                "$(printf '  2) rm -f \"%s/Library/LaunchAgents/local.ai-device.client.plist\"' "$owner_home")" \
                "$(printf '  3) sudo rm -f \"%s\"' "$REGISTRATION")" \
                "$(printf '  4) sudo rm -rf \"%s\"' "$STAGE")" \
                '  5) run this installer again')
            printf '%s\n' "$RECOVERY_TEXT" > "$STATUS_DIR/install-note" 2>/dev/null || true
            chmod 0600 "$STATUS_DIR/install-note" 2>/dev/null || true
        fi
        exit 1
    fi
    # The stage path is this package's own payload. The entry verifies it
    # against its embedded inventory before renaming anything.
    set +e
    result=$("$UPGRADE_ENTRY" upgrade-registered-program --confirm-idle-instance-exits "$STAGE" 2>&1)
    status=$?
    set -e
else
    # No client identity on this host, but the live path may still hold a program
    # from an earlier install whose identity was removed (or an abandoned one).
    # The two cases need different primitives, and neither can stand in for the
    # other:
    #
    #   absent  -> first-program. It is strictly create-only, so it is the only
    #              choice here, and it carries the gates a bare rename lacks: a
    #              private management lock, an inventory check against this
    #              build's embedded hashes, and a read-only host conflict probe
    #              that refuses while a legacy client is still running.
    #
    #   present -> commit. first-program refuses an existing target by design, so
    #              using it unconditionally would make this package impossible to
    #              reinstall. commit is a single atomic rename and performs no
    #              conflict probe, which is acceptable precisely because no
    #              identity means no registered service of ours is running from
    #              that path.
    #
    # Both are invoked through the upgrade entry: it is the executable that
    # carries the inventory. The commit binary staged in the payload has none.
    if [ ! -x "$UPGRADE_ENTRY" ]; then
        echo 'PROGRAM_COMMIT_REFUSED_V1: no entry in payload to publish the program' >&2
        exit 1
    fi
    if [ -e "$PROGRAM" ] || [ -L "$PROGRAM" ]; then
        set +e
        result=$("$UPGRADE_ENTRY" "$VERB" "$STAGE" "$PROGRAM" 2>&1)
        status=$?
        set -e
    else
        set +e
        result=$("$UPGRADE_ENTRY" first-program --confirm-legacy-clients-disabled "$STAGE" 2>&1)
        status=$?
        set -e
        # first-program publishes by COPYING the staged payload, so unlike the
        # rename-based commit the source stage is still there afterwards. Left
        # in place it would make the preinstall residue check refuse this same
        # package on the next attempt, so the published payload is removed.
        # Only on success: a failed publish keeps the stage as its evidence.
        if [ "$status" -eq 0 ] && [ -d "$STAGE" ]; then
            rm -rf "$STAGE" 2>/dev/null || echo 'stage cleanup failed; it will block a reinstall' >&2
        fi
    fi
fi
if [ -n "$result" ]; then
    echo "$result" >&2
fi
STATUS_CODE="$status"
STATUS_NOTE=''
record_status
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
        # Requirement 11: a local development package may be unsigned but must
        # say so. The title is the one string an ordinary user always sees, and
        # it is what distinguishes this artifact from a signed public release --
        # the filename deliberately carries no mode word.
        "shipping": "AI Device Client — UNSIGNED development build",
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
    if mode == "shipping":
        # Requirement 11 requires the package to be MARKED, not merely built
        # unsigned. Measured behaviour of an unsigned package with a quarantine
        # attribute: Installer.app is suspended by Gatekeeper and nothing is
        # installed, while `sudo installer` still succeeds -- so the difference
        # between "works for me" and "works for a user" is entirely whether
        # they double-click. Saying that here, before the payload is written,
        # is the honest place for it.
        ET.SubElement(root, "welcome", {"file": "welcome.html"})
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


UNSIGNED_NOTICE = """UNSIGNED DEVELOPMENT BUILD -- NOT FOR PUBLIC DISTRIBUTION

This package has no Developer ID signature and no notarisation ticket.

Measured on macOS 27.0.1 (2026-11-07):

  spctl -a -vv -t install <pkg>       rejected / no usable signature
  sudo installer -pkg <pkg> -target /  succeeds (rc=0), with or without
                                       com.apple.quarantine
  double-click the .pkg                Gatekeeper suspends Installer.app; the
                                       payload is never written, so the install
                                       does not happen

The third line is the one that matters for a real user: the graphical install
path -- the only one most users try -- is blocked when the package carries a
quarantine attribute, which a downloaded copy always does.

To install deliberately, as an administrator:

  xattr -d com.apple.quarantine <pkg>
  sudo installer -pkg <pkg> -target /

Signing and notarisation require an Apple Developer Program account and are
outstanding work for real release. Requirement 11 permits an unsigned local
development package, but requires it to be marked as such.
"""


UNSIGNED_WELCOME = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body { font: 13px -apple-system, sans-serif; margin: 0; }
h2 { font-size: 14px; margin: 0 0 8px; }
p { margin: 0 0 8px; }
code { font-family: ui-monospace, monospace; font-size: 12px; }
.note { color: #a05000; font-weight: 600; }
</style></head><body>
<h2>This build is unsigned</h2>
<p class="note">Not for public distribution.</p>
<p>The package carries no Developer ID signature and no notarisation ticket.
Because of that, macOS Gatekeeper blocks it when it is installed by
double-clicking, which is how a user normally installs a package. The install
will not start; the dialog will say the developer cannot be verified.</p>
<p>An administrator can install the same package from a terminal, where the
installer does not consult Gatekeeper:</p>
<p><code>sudo installer -pkg &lt;path&gt; -target /</code></p>
<p>If this is a downloaded copy, remove the quarantine attribute first:
<code>xattr -d com.apple.quarantine &lt;path&gt;</code></p>
<p>Signing and notarisation are outstanding work for real release. Installing
this build means accepting that its origin is not authenticated.</p>
</body></html>
"""


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

    `tree` must be the tree that actually ships, not the source it was copied
    from: the inventory is compared for exact entry count, so a superset makes
    every install refuse. Callers that filter the copy must pass the filtered
    result.

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
    # which includes a generated resource-table.h. It is built from the tree this
    # package stages, so the embedded inventory describes exactly that payload.
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
    # The first-install branch in postinstall calls `first-program`, which takes
    # no destination: the native entry has /opt/ai-device-client compiled in. A
    # builder that accepted a different --program would emit a script whose two
    # branches publish to different paths, with the first install silently
    # landing on the hardcoded one. Refuse the mismatch instead of
    # parameterising it: the fixed path is the product contract, and the
    # registered upgrade already uses the same constant.
    if mode != "inspection" and program != NATIVE_PROGRAM_PATH:
        raise ValueError(
            f"installable packages publish to {NATIVE_PROGRAM_PATH}; "
            f"program={program!r} would make the first-install branch disagree"
        )
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
    # `payload`, not `tree`: __pycache__ and *.pyc are excluded from the copy, so
    # an inventory taken from the source tree would list four files the installed
    # tree does not have, and verify_resource_tree requires an exact entry count.
    # The result was a package that built and signed cleanly and then refused
    # every install on real hardware. The manifest must describe what ships.
    if mode != "inspection":
        stage_upgrade_entry(payload, output / "root", output / "upgrade-build", run=run)
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
                epilogue=SHIPPING_EPILOGUE.format(program=program) if mode == "shipping" else "",
            )
        )
    postinstall.chmod(0o755)
    dist = output / "Distribution.xml"
    dist.write_text(distribution(version, mode=mode))
    resources = None
    if mode == "shipping":
        # productbuild only embeds a welcome page when it is found through
        # --resources; a file sitting next to Distribution.xml is referenced by
        # name and silently dropped, leaving a distribution that points at
        # nothing. Keep it in its own directory so nothing else is swept in.
        resources = output / "resources"
        resources.mkdir(exist_ok=True)
        (resources / "welcome.html").write_text(UNSIGNED_WELCOME)
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
            *(["--resources", str(resources)] if resources else []),
            str(result),
        ],
        check=True,
    )
    if mode == "shipping":
        # The artifact travels outside this directory, so the marker goes with
        # it: whoever picks up the .pkg must not have to open the installer or
        # read the build script to learn it is unsigned.
        (output / f"{result.name}.UNSIGNED.txt").write_text(UNSIGNED_NOTICE)
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
