"""Fail-closed native package preflight across versioned layout roots."""

import subprocess
from pathlib import Path

import pytest


def test_builder_uses_isolated_offline_uv_for_explicit_runtime():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    assert 'env -i PATH=/usr/bin:/bin HOME="$work" TMPDIR="$work"' in source
    # The payload is assembled in the staging tree; the post-install step renames
    # it into place so a running caller never sees a half-written interpreter.
    assert '"$stage/tools/uv" pip install --offline --no-cache --no-config --system' in source
    assert '--python "$stage/runtime/bin/python3.13" --no-index --no-deps --link-mode copy' in source
    # The bootstrap installs a chosen subset, not every shipped wheel: the rest
    # is pre-installed into the instance venv from the same bundle.
    assert " $required > \"$work/bootstrap.log\"" in source
    assert ' -m pip ' not in source


def test_builder_bootstrap_set_covers_signed_release_path():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    # native_prepare verifies the Ed25519 envelope through cryptography; leaving
    # it out builds a package whose signed-release path cannot run. Pillow and
    # zstandard are not reachable from the installer or the prepare path.
    for name in (
        "packaging",
        "pydantic",
        "pydantic_core",
        "annotated_types",
        "typing_extensions",
        "typing_inspection",
        "websockets",
        "psutil",
        "cryptography",
        "cffi",
        "pycparser",
    ):
        assert f" {name}" in source
    assert "for name in packaging pydantic" in source


def test_builder_self_check_exercises_real_entry_points():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    # A missing import that only surfaces during install is shipped, not caught.
    assert "import device_runtime.native_install" in source
    assert "from device_runtime.client import device_url" in source
    assert "from device_runtime import native_prepare, native_candidate" in source
    assert "native package build refused: installer imports are incomplete" in source
    assert (
        "native package build refused: client URL validation or release authentication"
        in source
    )


def test_builder_requires_exactly_one_wheel_per_bootstrap_name():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    # A total count alone lets one name ship nothing while another ships twice.
    assert 'expected one wheel for $name, found $#' in source
    assert '"$#" -eq 1' in source


def test_builder_drops_unused_runtime_components():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    for component in ("ensurepip", "idlelib", "pydoc_data", "tkinter", "test"):
        assert f'lib/python3.13/{component}"' in source
    assert 'lib/tcl9.0' in source and 'lib/tk9.0' in source


def test_independent_bootstrap_includes_candidate_launcher():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    assert (
        "for module in __init__.py entrypoint.py native_helper.py "
        "management_lock.py native_bootstrap.py; do"
    ) in source
    # The stable helper entry is unchanged; candidate launch is not activated.
    assert "-I -B -m device_runtime.native_helper" in source


@pytest.mark.parametrize(
    "existing",
    [None, "packages/old-version", "bootstrap/old-version", "instances", "manager"],
)
def test_deb_preflight_rejects_existing_versioned_layout(tmp_path, existing):
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    script = source.split("cat > \"$work/preflight\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    root = tmp_path / "opt/ai-device"
    manager = tmp_path / "var/lib/ai-device/manager"
    script = script.replace("/opt/ai-device", str(root)).replace(
        "/var/lib/ai-device/manager", str(manager)
    )
    if existing:
        path = manager if existing == "manager" else root / existing
        path.mkdir(parents=True)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert (result.returncode == 0) == (existing is None)
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


def test_builder_refuses_rpm_because_only_deb_is_delivered():
    """rpm is kept for layout checks but must not be produced as a package.

    The signed update path fixes its target to linux-amd64-deb, extracts ar
    members and drives dpkg. An rpm installs and upgrades, but has no verified
    update path, so building one as a deliverable would ship a half-implemented
    lifecycle. The refusal has to happen before any work, not after a build.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    assert "rpm is not a declared support target" in source
    assert "DEVICE_NATIVE_ALLOW_UNDELIVERED_RPM" in source


def test_builder_rpm_refusal_precedes_extraction():
    """A refused rpm build must exit before touching the payload or output."""
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    guard = source.index("rpm is not a declared support target")
    # The archive digest check and every tar/uv step come later, so a rejected
    # build cannot leave a partially assembled tree behind.
    assert guard < source.index("runtime size mismatch")
    assert guard < source.index("tar -xzf")


def test_builder_rpm_refusal_is_off_by_default(tmp_path):
    """Run the real guard: unset means refuse, =1 means continue past it."""
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    guard = source[
        source.index('if [ "$kind" = rpm ]') : source.index("[ \"$(uname -m)\" = x86_64 ]")
    ]
    script = "set -eu\nkind=rpm\n" + guard
    refused = subprocess.run(
        ["/bin/sh"], input=script, text=True, capture_output=True, check=False, env={}
    )
    assert refused.returncode == 1
    assert "rpm is not a declared support target" in refused.stderr
    allowed = subprocess.run(
        ["/bin/sh"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        env={"DEVICE_NATIVE_ALLOW_UNDELIVERED_RPM": "1"},
    )
    assert allowed.returncode == 0


def test_rpm_preflight_guards_layout_roots():
    """rpm must classify the layout, not refuse unconditionally.

    The old lua refused any versioned tree, which blocked every upgrade. The
    tree may now gain a sibling version -- but an unclassifiable layout, or a
    reinstall of the same version, still stops the transaction.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text().split("%pretrans -p <lua>", 1)[1].split("%preun", 1)[0]
    assert "'/opt/ai-device/packages'" in source
    assert "'/opt/ai-device/bootstrap'" in source
    assert "pre-install refused" in source
    assert "already installed" in source
    assert "posix.stat(path, 'type')" in source


@pytest.mark.parametrize("name", ["packages", "bootstrap"])
def test_deb_preflight_rejects_dangling_layout_symlink(tmp_path, name):
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    script = source.split("cat > \"$work/preflight\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    root = tmp_path / "opt/ai-device"
    root.mkdir(parents=True)
    (root / name).symlink_to(root / "missing")
    script = script.replace("/opt/ai-device", str(root)).replace(
        "/var/lib/ai-device/manager", str(tmp_path / "manager")
    )
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode != 0

def test_bootstrap_extract_excludes_bytecode_but_keeps_source(tmp_path):
    import subprocess
    import tarfile

    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    line = next(
        line for line in builder.read_text().splitlines()
        if line.startswith("tar --exclude=") and '"$stage_boot"' in line
    )
    source = tmp_path / "source/python/lib/python3.13/encodings"
    (source / "__pycache__").mkdir(parents=True)
    (source / "__init__.py").write_text("source")
    (source / "__pycache__/__init__.cpython-313.pyc").write_bytes(b"cache")
    (source / "legacy.pyc").write_bytes(b"cache")
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(tmp_path / "source/python", arcname="python")
    output = tmp_path / "stage-boot/0.2.2-native1"
    output.mkdir(parents=True)
    subprocess.run(
        ["sh", "-c", line], check=True,
        env={"PATH": "/usr/bin:/bin", "archive": str(archive),
             "stage_boot": str(output)},
    )
    extracted = output / "python/lib/python3.13/encodings"
    assert (extracted / "__init__.py").read_text() == "source"
    assert not (extracted / "__pycache__").exists()
    assert not (extracted / "legacy.pyc").exists()

def test_packaged_helper_entry_disables_bytecode_writes():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    script = builder.read_text()
    # The entrypoint resolves the tree through the pointer. Naming one build's
    # path is what left the wrapper pointing at a directory an upgrade deleted.
    assert 'root=/opt/ai-device/bootstrap' in script
    assert 'version=$(cat "$root/current")' in script
    assert 'exec "$interpreter" -I -B -m device_runtime.native_helper' in script
    # The wrapper must not exec a tree that an upgrade is replacing: dpkg removes
    # the old tree before unpacking the new one, so the interpreter can be absent
    # for a moment. Refuse to reach exec in that case and report a retryable code
    # instead of leaking the shell's "not found" or a Python traceback.
    assert 'interpreter="$root/$version/runtime/bin/python3.13"' in script
    assert 'exit 75' in script
    # A long-running ``serve`` must stay exec'd; capturing its output would block
    # and the probe-before-exec variants measured 300ms on every management call.
    assert 'exec "$interpreter" -I -B -m device_runtime.native_helper' in script
    # The fallback still names this build, substituted at package time.
    assert 'version=__TREE__' in script
    assert 'sed -i "s#__TREE__#$tree#g" "$root/usr/bin/ai-device-native-helper"' in script


@pytest.mark.parametrize("existing", ["instances", "manager"])
def test_deb_preflight_refuses_dangling_management_symlink(tmp_path, existing):
    source = (Path(__file__).parents[1] / "scripts/build-native-linux.sh").read_text()
    script = source.split("cat > \"$work/preflight\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    root = tmp_path / "opt/ai-device"
    manager = tmp_path / "var/lib/ai-device/manager"
    script = script.replace("/opt/ai-device", str(root)).replace("/var/lib/ai-device/manager", str(manager))
    path = manager if existing == "manager" else root / existing
    path.parent.mkdir(parents=True)
    path.symlink_to("missing")
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode != 0 and path.is_symlink()


@pytest.mark.parametrize("existing", [None, "instances", "manager", "service"])
@pytest.mark.parametrize("kind", ["file", "dangling_symlink"])
def test_deb_uninstall_refuses_existing_or_dangling_entry(tmp_path, existing, kind):
    source = (Path(__file__).parents[1] / "scripts/build-native-linux.sh").read_text()
    script = source.split("cat > \"$root/DEBIAN/prerm\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    root = tmp_path / "opt/ai-device"
    manager = tmp_path / "var/lib/ai-device/manager"
    service = tmp_path / "etc/systemd/system/ai-device-native-helper.service"
    script = script.replace("/opt/ai-device", str(root)).replace(
        "/var/lib/ai-device/manager", str(manager)
    ).replace("/etc/systemd/system/ai-device-native-helper.service", str(service))
    if existing:
        path = {"instances": root / "instances", "manager": manager, "service": service}[existing]
        path.parent.mkdir(parents=True)
        if kind == "file":
            path.touch()
        else:
            path.symlink_to("missing")
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert (result.returncode == 0) == (existing is None)
    if existing:
        assert path.is_file() if kind == "file" else path.is_symlink()


def test_rpm_uninstall_guard_checks_dangling_entries():
    source = (Path(__file__).parents[1] / "scripts/build-native-linux.sh").read_text()
    script = source.split("%preun -p <lua>", 1)[1].split("%files", 1)[0]
    for path in ("/opt/ai-device/instances", "/var/lib/ai-device/manager",
                 "/etc/systemd/system/ai-device-native-helper.service"):
        assert path in script
    assert "posix.stat(path) or posix.readlink(path)" in script


def _deb_prerm(tmp_path):
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    script = source.split("cat > \"$root/DEBIAN/prerm\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    root = tmp_path / "opt/ai-device"
    units = tmp_path / "etc/systemd/system"
    units.mkdir(parents=True)
    script = script.replace("/opt/ai-device/instances", str(root / "instances"))
    script = script.replace("/var/lib/ai-device/manager", str(tmp_path / "manager"))
    script = script.replace("/var/lib/ai-device/native", str(tmp_path / "registry"))
    script = script.replace("/etc/systemd/system/ai-device-native-helper.service", str(units / "helper.service"))
    script = script.replace("/etc/systemd/system/ai-device-client-*.service", str(units / "ai-device-client-*.service"))
    return script


@pytest.mark.parametrize("marker", ["instances", "manager"])
def test_remove_refuses_when_instance_state_exists(tmp_path, marker):
    """Erasing deletes the payload, so any instance state must block removal."""
    script = _deb_prerm(tmp_path)
    target = {
        "instances": tmp_path / "opt/ai-device/instances",
        "manager": tmp_path / "manager",
    }[marker]
    target.mkdir(parents=True)
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "explicit instance/service uninstall required" in result.stderr


def test_remove_refuses_when_a_client_unit_is_registered(tmp_path):
    script = _deb_prerm(tmp_path)
    (tmp_path / "etc/systemd/system/ai-device-client-dev1.service").write_text("")
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "registered device instance still uses this payload" in result.stderr


def test_remove_allows_erase_on_a_clean_host(tmp_path):
    script = _deb_prerm(tmp_path)
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_remove_refuses_while_a_registration_record_exists(tmp_path):
    script = _deb_prerm(tmp_path)
    registry = tmp_path / "registry"
    registry.mkdir(parents=True)
    (registry / "dev1.json").write_text("{}")
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "unregister it first" in result.stderr


def test_remove_allows_an_empty_registry_directory(tmp_path):
    """The registry directory survives unregister because it holds the lease.

    Blocking on the bare directory would keep the erase guard closed forever
    after every instance was properly unregistered.
    """
    script = _deb_prerm(tmp_path)
    (tmp_path / "registry").mkdir(parents=True)
    result = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_rpm_preun_guards_registered_client_units():
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text().split("%preun -p <lua>", 1)[1].split("%files", 1)[0]
    assert "/var/lib/ai-device/native" in source
    assert "ai%-device%-client%-" in source
    assert "registered device instance still uses this payload" in source


def test_remove_clears_runtime_bytecode_caches():
    """Erase must drop __pycache__ trees the bundled interpreter created.

    They are not package-owned, so leaving them would block the next install
    through the same layout guard that protects the payload.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    deb = source.split("cat > \"$root/DEBIAN/prerm\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    assert "__pycache__" in deb
    assert "/opt/ai-device/packages/__TREE__" in deb
    rpm = source.split("%preun -p <lua>", 1)[1].split("%files", 1)[0]
    assert "drop_caches" in rpm
    assert "__pycache__" in rpm


def test_build_strips_bytecode_before_packaging():
    """The shipped payload must not carry caches made while building it."""
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    strip = '-type d -name __pycache__ -prune -exec rm -rf {} +'
    assert strip in source
    # Packaging starts at the deb/rpm branch, so the strip must come first.
    assert source.index(strip) < source.index('if [ "$kind" = deb ]')


def test_deb_preflight_carries_its_own_classifier():
    """Upgrading must not depend on the installed version shipping the classifier.

    An earlier build ran ``-m device_runtime.native_layout`` with the *installed*
    interpreter. Every host on a payload older than that module then failed with
    ModuleNotFoundError -- exactly the devices the upgrade exists for. The hook
    therefore ships the classifier beside itself and runs that copy.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    script = source.split("cat > \"$work/preflight\" <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    assert "-m device_runtime.native_layout" not in script
    assert '"$here/classify.py" check' in script
    # The classifier must be copied into the control area, not just referenced.
    assert 'cp "$work/classify.py" "$root/DEBIAN/classify.py"' in source
    assert 'cp "$stage/runtime/lib/python3.13/site-packages/device_runtime/native_layout.py"' in source


def test_rpm_preflight_matches_deb_protections():
    """The lua hook must not fall behind the dpkg classifier.

    They cannot share code, so the only defence against drift is asserting both
    carry the same decisions: legacy management roots, symlink refusal (stat
    follows links, so a linked directory would otherwise look like a tree), and
    payload/bootstrap completeness.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    lua = source.split("%pretrans -p <lua>", 1)[1].split("%preun", 1)[0]
    assert "/opt/ai-device/instances" in lua
    assert "/var/lib/ai-device/manager" in lua
    assert "posix.readlink(root)" in lua
    assert "posix.readlink(path)" in lua
    assert "is missing or incomplete" in lua
    assert "directories" in lua or "plain_dir" in lua
    # The pointer is the authority on both sides. A tree-count rule here would
    # refuse the second upgrade while dpkg accepts it, so the lua hook must not
    # carry the old "exactly one tree per root" decision.
    assert "expected one versioned payload and bootstrap" not in lua
    assert "bootstrap/current" in lua


def test_both_package_managers_publish_the_pointer():
    """Both managers must publish the same way: expand the archive, rename, then flip.

    dpkg got a postinst first; rpm had nothing, which would have left an RPM
    upgrade with a working package and an entrypoint naming a deleted path.
    Now both also rename instead of leaving the live tree to be written in place.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()

    deb = source.split('cat > "$root/DEBIAN/postinst"', 1)[1].split("\nEOF", 1)[0]
    assert "mv -f" in deb and "current" in deb

    spec = source.split("%post", 1)[1].split("%preun", 1)[0]
    assert "mv -f" in spec and "current" in spec
    # Both hooks publish the same way: the package ships one archive per tree,
    # the hook expands it into a private work directory and renames the finished
    # tree into place before the pointer flips. The spec is an unquoted heredoc,
    # so install-time values are escaped and the tree name stays a placeholder.
    for body, prefix in ((deb, ""), (spec, "\\")):
        assert f'{prefix}$payload' in body
        assert 'tar -xf' in body
        assert "mv" in body
        # The tree must never be written in place: the expansion goes to a work
        # directory that only becomes the live tree through one rename.
        assert "mktemp -d /opt/ai-device/.incoming-" in body
    assert 'sed -i "s#__TREE__#$tree#g" "$work/rpm/SPECS/native.spec"' in source


def test_rpm_pointer_writes_a_real_newline():
    """The spec's double escaping is easy to get wrong in a way that still runs.

    Measured on a real rpm build: with '\\\\n' in the spec the pointer file held
    ``T1\\n`` -- a literal backslash and n -- because the unquoted heredoc halved
    the escape and sh's printf then emitted it verbatim. The wrapper reads the
    pointer with ``cat`` and uses the result as a directory name, so that value
    names a tree that cannot exist. Nothing fails at install time, which is what
    made it survive.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    spec = source.split("\n%post\n", 1)[1].split("%preun", 1)[0]
    # Compare on the literal source text. After the unquoted heredoc halves the
    # escapes, sh must receive '%s\\n'; four backslashes would arrive as a
    # literal backslash-n and never become a newline.
    pointer = [l for l in spec.splitlines() if '__TREE__' in l and 'printf' in l]
    assert len(pointer) == 1, pointer
    assert "%s" + chr(92) + "n" in pointer[0], pointer[0]
    assert chr(92) * 2 + "n" not in pointer[0], pointer[0]
    # The digest is compared byte-for-byte against $(cat ...), same rule.
    digest = [l for l in spec.splitlines() if 'digest' in l and 'printf' in l]
    assert len(digest) == 1, digest
    assert "%s" + chr(92) + "n" in digest[0], digest[0]
    assert chr(92) * 2 + "n" not in digest[0], digest[0]


def _extract_helper_wrapper():
    """Return the wrapper body the builder writes to /usr/bin/ai-device-native-helper."""
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    marker = 'cat > "$root/usr/bin/ai-device-native-helper" <<\'EOF\'\n'
    return source.split(marker, 1)[1].split("\nEOF", 1)[0]


def _run_wrapper(tmp_path, tree, *, installed=(), interpreter=True, stdlib=True):
    """Execute the wrapper against a synthetic bootstrap root.

    ``installed`` names the version trees that exist under the root. The pointer
    always names ``tree`` so the test can model the moment dpkg has published the
    pointer but the tree it names is not on disk yet.
    """
    body = _extract_helper_wrapper().replace("__TREE__", tree)
    root = tmp_path / "bootstrap"
    root.mkdir()
    (root / "current").write_text(tree + "\n")
    for name in installed:
        bin_dir = root / name / "runtime" / "bin"
        bin_dir.mkdir(parents=True)
        if interpreter:
            stub = bin_dir / "python3.13"
            # Behave like a real runtime: the import probe passes only when the
            # standard library is complete. ``stdlib=False`` models a tree whose
            # files are still being unpacked, where the probe must fail.
            if stdlib:
                stub.write_text("#!/bin/sh\nexit 0\n")
            else:
                stub.write_text(
                    "#!/bin/sh\n"
                    "case \"$*\" in *-c*) echo 'ModuleNotFoundError: encodings' >&2;"
                    " exit 1;; esac\nexit 0\n"
                )
            stub.chmod(0o755)
        libroot = root / name / "runtime" / "lib" / "python3.13"
        if stdlib:
            (libroot / "encodings").mkdir(parents=True)
            (libroot / "encodings" / "__init__.py").write_text("")
        (libroot / "site-packages" / "device_runtime").mkdir(parents=True)
    wrapper = tmp_path / "helper"
    wrapper.write_text(body.replace("/opt/ai-device/bootstrap", str(root)))
    wrapper.chmod(0o755)
    return subprocess.run(
        [str(wrapper), "serve"],
        check=False, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"},
    )


def test_wrapper_reports_retryable_error_when_pointer_tree_is_absent(tmp_path):
    """An upgrade deletes the old tree before unpacking the new one.

    A caller racing that window sees the pointer name a directory that is
    temporarily gone. It must get a retryable signal, not a raw exec failure or a
    Python traceback.
    """
    result = _run_wrapper(tmp_path, "0.2.2-native2", installed=("0.2.2-native1",))
    assert result.returncode == 75
    assert "retry" in result.stderr
    assert "Traceback" not in result.stderr
    assert "exec:" not in result.stderr
    assert "not found" not in result.stderr


def test_wrapper_reports_retryable_error_when_interpreter_is_missing(tmp_path):
    """The tree directory can exist while its interpreter is still being written."""
    result = _run_wrapper(tmp_path, "0.2.2-native2", installed=("0.2.2-native2",), interpreter=False)
    assert result.returncode == 75
    assert "retry" in result.stderr
    assert "Traceback" not in result.stderr


def test_wrapper_reports_retryable_error_when_payload_is_incomplete(tmp_path):
    """A half-unpacked tree has the interpreter but not the module it runs.

    There is deliberately no test for "interpreter present, standard library still
    unpacking". The wrapper cannot detect it: unpacking lands files one at a time,
    so any probe can pass and a later import can still fail. Attempting it with
    ``import device_runtime.native_helper`` cost ~300ms on every management call
    and still leaked import tracebacks, so that probe was removed. The window case
    that is reliably detectable is a wholly absent interpreter or module tree.
    """
    body = _extract_helper_wrapper().replace("__TREE__", "0.2.2-native2")
    root = tmp_path / "bootstrap"
    bin_dir = root / "0.2.2-native2" / "runtime" / "bin"
    bin_dir.mkdir(parents=True)
    stub = bin_dir / "python3.13"
    stub.write_text(
        "#!/bin/sh\ncase \"$*\" in *-c*) exit 1;; esac\nexit 0\n"
    )
    stub.chmod(0o755)
    (root / "current").write_text("0.2.2-native2\n")
    wrapper = tmp_path / "helper"
    wrapper.write_text(body.replace("/opt/ai-device/bootstrap", str(root)))
    wrapper.chmod(0o755)
    result = subprocess.run(
        [str(wrapper), "serve"], check=False, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 75
    assert "retry" in result.stderr
    assert "Traceback" not in result.stderr


def test_wrapper_keeps_refusal_distinct_from_retryable_window(tmp_path):
    """An invalid pointer is a refusal (1), never confused with the retry code."""
    body = _extract_helper_wrapper().replace("__TREE__", "0.2.2-native2")
    root = tmp_path / "bootstrap"
    root.mkdir()
    (root / "current").write_text("../escape\n")
    wrapper = tmp_path / "helper"
    wrapper.write_text(body.replace("/opt/ai-device/bootstrap", str(root)))
    wrapper.chmod(0o755)
    result = subprocess.run(
        [str(wrapper), "serve"], check=False, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "pointer is invalid" in result.stderr


def test_wrapper_execs_the_installed_interpreter(tmp_path):
    """The healthy path still resolves through the pointer, not a baked-in tree."""
    result = _run_wrapper(tmp_path, "0.2.2-native2", installed=("0.2.2-native2",))
    # The stub interpreter exits 0, which proves exec reached it.
    assert result.returncode == 0
    assert result.stderr == ""


def test_manifest_owns_the_archives_not_the_derived_trees():
    """The package must own paths that exist after installation.

    The previous layout owned the staging trees, which %post renamed away, so
    `dpkg -V` reported the whole payload missing (9762 entries) and an rpm
    upgrade printed one "remove failed" per file. Owning the archive instead
    makes the record true: the file is shipped, stays in place, and both
    managers can verify it.

    The derived trees must stay out of the manifest. Listing one would make an
    upgrade delete the tree a running caller is executing, which is what the
    expansion step exists to prevent.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    files = source.split("%files", 1)[1].split("%preun", 1)[0]
    assert "/opt/ai-device/payloads/$tree.tar" in files
    assert "/opt/ai-device/payloads/$tree-bootstrap.tar" in files
    # A published tree in the manifest would make the upgrade delete the tree a
    # running caller is executing, which is what expansion-into-work-dir prevents.
    assert "/opt/ai-device/packages/$tree" not in files
    assert "/opt/ai-device/bootstrap/$tree" not in files
    # The old ownership record named staging paths that never exist at run time.
    assert "/opt/ai-device/.staging/$tree" not in files
    # %ghost was tried and rejected: it silently stops shipping the payload.
    # Only directives count, not the comment recording why it was rejected.
    directives = [l for l in files.splitlines() if not l.lstrip().startswith("#")]
    assert not any(l.startswith("%ghost") for l in directives)
    assert "stops shipping" in files


def test_publish_compares_archive_digest_not_directory_existence():
    """Idempotence must be decided by content, not by the tree being present.

    Measured on a same-name reinstall with a directory-existence check: the
    archive was replaced, the hook saw an existing tree and skipped expansion,
    and the live tree kept the old contents while the package manager reported
    the new archive as installed. dpkg -V still passed because it only verifies
    the archive. The digest file beside each published tree is what makes the
    difference observable.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()

    deb = source.split('cat > "$root/DEBIAN/postinst"', 1)[1].split("\nEOF", 1)[0]
    spec = source.split("%post", 1)[1].split("%preun", 1)[0]
    for body, escaper in ((deb, ""), (spec, "\\")):
        assert ".payload-sha256" in body
        assert "sha256sum" in body
        # A digest mismatch is a refusal, not a silent overwrite of a live tree.
        assert "holds different contents" in body


def test_package_ships_one_archive_per_tree():
    """Both trees are archived from a build path outside the package root.

    The build stages the payload outside the package root so no unarchived copy
    ships, and no compression is applied: dpkg and rpm already compress, so
    gzip here would only add an install-time decompression pass.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    assert 'payload_tar=$payload_dir/$tree.tar' in source
    assert 'payload_boot_tar=$payload_dir/$tree-bootstrap.tar' in source
    assert 'tar -cf "$root$payload_tar"' in source
    assert 'tar -cf "$root$payload_boot_tar"' in source
    # Uncompressed on purpose: -cf, never -czf, for the shipped archives.
    assert 'tar -czf "$root$payload' not in source


def _heredoc(source: str, header: str) -> str:
    """Return the body of a `cat > path <<'EOF' ... EOF` block."""
    start = source.index(header)
    body = source.index("\n", start) + 1
    return source[body:source.index("\nEOF", body)]


def test_both_managers_retire_replaced_trees():
    """Old version trees must not accumulate: ~330MB each, no consumer left.

    Instances run from their own releases/ copies and the entrypoint resolves
    through bootstrap/current, so the trees a package replaced have nothing
    depending on them. Upgrades delete them; erases delete everything, or an
    uninstall strands about a gigabyte across three versions.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()

    dpkg_postinst = _heredoc(source, 'cat > "$root/DEBIAN/postinst"')
    assert "retire" in dpkg_postinst
    assert "/opt/ai-device/packages" in dpkg_postinst

    dpkg_postrm = _heredoc(source, 'cat > "$root/DEBIAN/postrm"')
    # Only an erase drops everything; abort-upgrade/update must not.
    assert "remove|purge)" in dpkg_postrm
    assert "bootstrap/current" in dpkg_postrm

    rpm_post = source.split("%post\n", 1)[1].split("%preun", 1)[0]
    assert "retire" in rpm_post
    rpm_postun = source.split("%postun", 1)[1].split("%files", 1)[0]
    # rpm passes the remaining-instance count: an upgrade keeps %post in charge.
    assert '"\\$1" -gt 0' in rpm_postun
    assert "bootstrap/current" in rpm_postun


def test_retire_never_follows_links():
    """Deleting a symlinked entry would remove its target, which is not ours.

    Verified against a real package: an entry symlinked to /opt/important left
    the target intact and the link in place; the layout classifier then refused
    the install rather than silently deleting foreign data.
    """
    builder = Path(__file__).parents[1] / "scripts/build-native-linux.sh"
    source = builder.read_text()
    for hook in (
        _heredoc(source, 'cat > "$root/DEBIAN/postinst"'),
        _heredoc(source, 'cat > "$root/DEBIAN/postrm"'),
        source.split("%post\n", 1)[1].split("%preun", 1)[0],
        source.split("%postun", 1)[1].split("%files", 1)[0],
    ):
        assert "-L" in hook
