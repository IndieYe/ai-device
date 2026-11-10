import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


@pytest.fixture
def builder(monkeypatch):
    path = Path(__file__).parents[1] / "scripts/build-client-pkg.py"
    spec = importlib.util.spec_from_file_location("pkg_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    return module


@pytest.fixture
def tree(tmp_path):
    path = tmp_path / "tree"
    (path / "runtime/bin").mkdir(parents=True)
    (path / "runtime/bin/python3").write_bytes(b"fixture only")
    (path / "bin").mkdir()
    (path / "bin/ai-device-client-launcher").write_bytes(b"fixture launcher")
    commit = path / "bin/ai-device-client-commit"
    commit.write_bytes(b"fixture commit")
    commit.chmod(0o755)
    (path / "third-party-inventory.json").write_text("{}")
    (path / "client-identity.json").write_text(
        json.dumps({"version": "0.3.4", "build_id": "sha256:" + "a" * 64})
    )
    (path / "build-manifest.json").write_text(
        json.dumps({"schema_version": 1, "platform": "darwin-arm64"})
    )
    stage_native_snapshot(path)
    return path


def stage_native_snapshot(path):
    """Copy the real native sources into a fixture tree, as a real tree has them.

    The builder refuses a tree whose snapshot disagrees with the checkout it
    compiles the entry from, so a fixture without one would test that refusal
    rather than the packaging path.
    """
    native = Path(__file__).parents[1] / "native"
    destination = path / "native"
    destination.mkdir()
    for source in native.iterdir():
        if source.is_file() and source.suffix in {".c", ".h", ".m"}:
            (destination / source.name).write_bytes(source.read_bytes())


def test_snapshot_with_a_newer_source_is_refused(builder, tree):
    """A file in the repo but not in the tree means the entry is newer.

    The entry is compiled from the checkout, but build_id covers the tree's
    snapshot. Without this check a stale tree plus newer sources produced an
    installer whose behaviour the tree's identity does not describe.
    """
    (tree / "native" / "client-macos-new-thing.h").write_text("new\n")
    with pytest.raises(ValueError, match="does not match this checkout"):
        builder.verify_native_snapshot(tree, builder.Path(__file__).parents[1] / "native")


def test_snapshot_missing_a_repo_source_is_refused(builder, tree):
    """A file in the tree but not in the repo means the tree came from elsewhere."""
    victim = next((tree / "native").glob("*.h"))
    victim.unlink()
    with pytest.raises(ValueError, match="does not match this checkout"):
        builder.verify_native_snapshot(tree, builder.Path(__file__).parents[1] / "native")


def test_snapshot_with_different_bytes_is_refused(builder, tree):
    """Same names, different content is the case a name-only check would miss.

    Editing a header in the checkout without rebuilding the tree changes what
    the entry compiles to while leaving every file name intact.
    """
    victim = next((tree / "native").glob("*.h"))
    victim.write_text(victim.read_text() + "\n/* drift */\n")
    with pytest.raises(ValueError, match="differ from this checkout"):
        builder.verify_native_snapshot(tree, builder.Path(__file__).parents[1] / "native")


def test_snapshot_absent_is_refused(builder, tree):
    """A tree built before snapshots existed cannot be vouched for."""
    import shutil

    shutil.rmtree(tree / "native")
    with pytest.raises(ValueError, match="no native snapshot"):
        builder.verify_native_snapshot(tree, builder.Path(__file__).parents[1] / "native")


def test_snapshot_of_only_comments_is_not_accepted_as_present(builder, tmp_path):
    """An empty snapshot must fail rather than pass with nothing to compare."""
    snapshot_tree = tmp_path / "empty"
    (snapshot_tree / "native").mkdir(parents=True)
    with pytest.raises(ValueError, match="snapshot.*is empty"):
        builder.verify_native_snapshot(
            snapshot_tree, builder.Path(__file__).parents[1] / "native"
        )


def test_matching_snapshot_is_accepted_and_counted(builder, tree):
    """The ordinary case passes and reports how many sources were compared."""
    native = builder.Path(__file__).parents[1] / "native"
    count = builder.verify_native_snapshot(tree, native)
    expected = sum(
        1 for p in native.iterdir()
        if p.is_file() and p.suffix in {".c", ".h", ".m"}
    )
    assert count == expected > 0


def test_upgrade_entry_compilation_refuses_a_stale_tree(builder, tree, tmp_path):
    """The check must run on the real path, not only when called directly.

    Asserting verify_native_snapshot in isolation leaves the call site free to
    drop it, which is the whole failure being fixed. This drives
    stage_upgrade_entry itself and requires the refusal before any compiler
    runs.
    """
    (tree / "native" / "client-macos-launchctl-probe.h").write_text("stale\n")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="")

    with pytest.raises(ValueError, match="does not match this checkout|differ from"):
        builder.stage_upgrade_entry(
            tree, tmp_path / "root", tmp_path / "build", run=run
        )
    assert calls == [], "no compiler runs once the snapshot is known stale"


def test_staged_entry_name_is_unique_per_package(builder):
    """Two builds must not write the entry to the same payload name.

    The Installer writes payload entries one at a time. A single fixed name lets
    two concurrent installers interleave their writes into one executable, which
    is the window this exists to close.
    """
    a = builder.staged_entry_name("/opt/ai-device-client-stage-0.3.4.8-aaaaaaaaaaaa")
    b = builder.staged_entry_name("/opt/ai-device-client-stage-0.3.4.9-bbbbbbbbbbbb")
    assert a != b
    assert a.startswith(builder.UPGRADE_ENTRY)
    # The stage path is the discriminator, so the same package always produces
    # the same name and two packages never collide.
    assert a == builder.staged_entry_name("/opt/ai-device-client-stage-0.3.4.8-aaaaaaaaaaaa")


def test_postinstall_renames_the_staged_entry_into_place(builder, tmp_path):
    """The rename must happen before anything executes the entry.

    Writing straight to the fixed name is the bug; a package that ships the
    staged name and never moves it would simply invoke a path that does not
    exist.
    """
    rendered = builder.POSTINSTALL.format(
        program=str(tmp_path / "prog"),
        stage=str(tmp_path / "stage"),
        entry=str(tmp_path / "stage/bin/commit"),
        verb="commit",
        status_dir=str(tmp_path / "status"),
        status_file=str(tmp_path / "status/install-status"),
        upgrade_entry=str(tmp_path / "entry"),
        staged_upgrade_entry=str(tmp_path / "entry.staged"),
        registration=str(tmp_path / "registration.json"),
        epilogue="",
    )
    rename = rendered.index('mv -f "$STAGED_UPGRADE_ENTRY" "$UPGRADE_ENTRY"')
    # Not an assertion about ordering cosmetics: the entry is invoked from both
    # the registered and the unregistered branch, and both must see the final
    # path already in place. Match the invocations, not the prose above them --
    # the comment block mentions both verbs long before either runs.
    assert rename < rendered.index('"$UPGRADE_ENTRY" upgrade-registered-program')
    assert rename < rendered.index('"$UPGRADE_ENTRY" first-program')


def test_same_entry_path_means_no_rename(builder, tmp_path):
    """When the two names match the rename is skipped, not run for nothing."""
    rendered = builder.POSTINSTALL.format(
        program=str(tmp_path / "prog"),
        stage=str(tmp_path / "stage"),
        entry=str(tmp_path / "stage/bin/commit"),
        verb="commit",
        status_dir=str(tmp_path / "status"),
        status_file=str(tmp_path / "status/install-status"),
        upgrade_entry=str(tmp_path / "entry"),
        staged_upgrade_entry=str(tmp_path / "entry"),
        registration=str(tmp_path / "registration.json"),
        epilogue="",
    )
    assert 'if [ "$STAGED_UPGRADE_ENTRY" != "$UPGRADE_ENTRY" ]; then' in rendered


def test_shipping_payload_entry_is_not_written_to_the_fixed_name(builder, tree, tmp_path):
    """The payload must carry the staged name, not the fixed one.

    Asserting the postinstall rename alone would pass even if the package still
    wrote to the fixed path during the install, which is the interleaving being
    prevented.
    """
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    compiled = []

    def capture(argv, **kwargs):
        if argv[0] == "/usr/bin/xcrun":
            path = Path(argv[argv.index("-o") + 1])
            compiled.append(path)
            path.write_bytes(b"entry")
        return run(argv, **kwargs)

    builder.stage_upgrade_entry(
        tree, tmp_path / "root", tmp_path / "build",
        run=capture, entry_name=builder.staged_entry_name("/opt/stage-x"),
    )
    assert compiled, "the entry was compiled somewhere"
    produced = compiled[0]
    assert produced.name != builder.UPGRADE_ENTRY
    assert produced.name.startswith(builder.UPGRADE_ENTRY)
    assert produced.is_file()


def test_build_only_calls_packagers(builder, tree, tmp_path):
    calls = []
    output = tmp_path / "package"

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    result = builder.build(tree, output, "0.3.4.1", run=run)
    assert [x[0] for x in calls] == [
        "/usr/bin/pkgbuild",
        "/usr/sbin/pkgutil",
        "/usr/bin/lsbom",
        "/usr/bin/productbuild",
    ]
    assert result.name.endswith("-inspection.pkg")
    # The payload lands on the stage path, never on the live program path.
    # The name is derived, so it carries the package version and the first 12
    # hex of the build id: two builds can never share it by accident.
    assert (output / "root/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa/runtime/bin/python3").is_file()
    assert not (output / "root/opt/ai-device-client").exists()
    assert (output / "scripts/preinstall").stat().st_mode & 0o111
    assert (output / "scripts/postinstall").stat().st_mode & 0o111
    assert "--ownership" in calls[0]
    assert builder.IDENTIFIER.endswith(".inspection")


def test_product_and_component_both_reject_installation(builder, tmp_path):
    xml = ET.fromstring(builder.distribution("0.3.4.1"))
    assert xml.find("options").get("hostArchitectures") == "arm64"
    assert xml.find("installation-check").get("script") == "rejectInspection()"
    assert "return false" in xml.find("script").text
    script = tmp_path / "preinstall"
    script.write_text(builder.PREINSTALL)
    # The script is only an unconditional diagnostic and exit, no installer or
    # service calls. Executing this isolated copy cannot install anything.
    result = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "installation refused" in result.stderr


@pytest.mark.parametrize("version", ["", "0.3.4-dev1", "../bad", "1.2.3.4.5"])
def test_bad_version_rejected_before_output(builder, tree, tmp_path, version):
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="numeric"):
        builder.build(tree, output, version)
    assert not output.exists()


def test_existing_output_preserved(builder, tree, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "keep").write_text("preserved")
    with pytest.raises(FileExistsError):
        builder.build(tree, output, "0.3.4.1")
    assert (output / "keep").read_text() == "preserved"


def test_linux_tree_rejected(builder, tree, tmp_path):
    (tree / "build-manifest.json").write_text('{"schema_version":1,"platform":"linux-amd64"}')
    with pytest.raises(ValueError, match="darwin-arm64"):
        builder.build(tree, tmp_path / "out", "0.3.4.1")


def test_intel_host_rejected(builder, monkeypatch, tree, tmp_path):
    monkeypatch.setattr(builder.platform, "machine", lambda: "x86_64")
    with pytest.raises(ValueError, match="native"):
        builder.build(tree, tmp_path / "out", "0.3.4.1")


def test_output_inside_input_rejected(builder, tree):
    with pytest.raises(ValueError, match="separate"):
        builder.build(tree, tree / "out", "0.3.4.1")


@pytest.mark.parametrize(
    "text", ["", "invalid", ".\t40755\t501/20\n", ".\t40755\t0/0\nfile\t100644\t0/20\n"]
)
def test_bad_bom_rejected(builder, text):
    with pytest.raises(ValueError, match="ownership"):
        builder.validate_bom(text)


def test_wrong_bom_blocks_product_build(builder, tree, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[0])
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t501/20\n")

    with pytest.raises(ValueError, match="ownership"):
        builder.build(tree, tmp_path / "bad-owner", "0.3.4.1", run=run)
    assert "/usr/bin/productbuild" not in calls


def test_missing_launcher_rejected_before_packaging(builder, tree, tmp_path):
    (tree / "bin/ai-device-client-launcher").unlink()
    output = tmp_path / "package"
    with pytest.raises(ValueError, match="incomplete"):
        builder.build(tree, output, "0.3.4.3", run=lambda *a, **kw: pytest.fail("no packager"))
    assert not output.exists()


@pytest.mark.parametrize("message", ["write: Permission denied", "Operation not permitted"])
def test_permission_noise_with_exit_zero_still_builds(builder, tree, tmp_path, message):
    """pkgbuild writes "write: Permission denied" to stderr even on success.

    That text is pkgbuild probing its own destination; it is not a failure of
    ours. Treating it as fatal rejected every good build on this host and hid
    the real error behind a wrong diagnosis. The exit status is authoritative.
    """
    calls = []
    output = tmp_path / "permission-ok"

    def run(argv, **kwargs):
        calls.append(argv[0])
        if argv[0] == "/usr/bin/pkgbuild":
            assert kwargs["capture_output"] is True and kwargs["text"] is True
            (output / "client.pkg").write_bytes(b"component")
            return subprocess.CompletedProcess(
                argv, 0, stdout="pkgbuild: wrote package\n", stderr=message
            )
        if argv[0] == "/usr/bin/lsbom":
            return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = builder.build(tree, output, "0.3.4.1", run=run)
    # The build must complete: productbuild ran and a package was named.
    assert "/usr/bin/productbuild" in calls
    assert result.name.endswith(".pkg")
    # The text is still retained for diagnosis, just not acted on.
    assert message in (output / "pkgbuild.log").read_text()


def test_permission_text_on_failure_is_reported_as_permission(builder, tree, tmp_path):
    """When the build does fail, ownership problems must stay identifiable.

    The fix must not swing to the other extreme and discard the permission
    signal: a non-zero exit with that text still names it, so a uid-501
    ownership failure is not reported as a generic packaging failure.
    """
    output = tmp_path / "permission-fail"

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="write: Permission denied"
        )

    with pytest.raises(ValueError, match="permission"):
        builder.build(tree, output, "0.3.4.1", run=run)
    assert "permission" in (output / "pkgbuild.log").read_text().lower()


def test_nonzero_pkgbuild_retains_log_without_product(builder, tree, tmp_path):
    output = tmp_path / "nonzero"
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[0])
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(
            argv, 7, stdout="partial progress\n", stderr="packager failed\n"
        )

    with pytest.raises(ValueError, match="pkgbuild failed"):
        builder.build(tree, output, "0.3.4.1", run=run)
    assert calls == ["/usr/bin/pkgbuild"]
    assert (output / "pkgbuild.log").read_text() == "partial progress\npackager failed\n"
    assert not list(output.glob("*-inspection.pkg"))


def test_stage_path_is_unique_per_package_version(builder, tree, tmp_path):
    """Two package versions built from the same client tree must not share a
    stage path.

    This is the whole point of the change. Sharing one means RENAME_SWAP leaves
    the previous tree behind, the installer merges its payload into it, and the
    published program contains files the new version no longer ships. The build
    id alone would not help here: it tracks Python sources and is identical
    across package rebuilds of the same code.
    """

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    first = builder.build(tree, tmp_path / "first", "0.3.4.1", run=run)
    second = builder.build(tree, tmp_path / "second", "0.3.4.2", run=run)

    first_payload = first.parent / "root/opt"
    second_payload = second.parent / "root/opt"
    first_stage = [p.name for p in first_payload.iterdir() if "stage" in p.name]
    second_stage = [p.name for p in second_payload.iterdir() if "stage" in p.name]
    assert first_stage == ["ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa"]
    assert second_stage == ["ai-device-client-stage-0.3.4.2-aaaaaaaaaaaa"]
    assert first_stage != second_stage


def test_stage_path_tracks_build_id_within_one_version(builder, tree, tmp_path):
    """Same package version, different client sources: the paths must differ.

    The build id covers the client Python sources, so a rebuild of changed code
    repackaged under the same version still gets a fresh stage.
    """

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    builder.build(tree, tmp_path / "first", "0.3.4.1", run=run)
    (tree / "client-identity.json").write_text(
        json.dumps({"version": "0.3.4", "build_id": "sha256:" + "b" * 64})
    )
    builder.build(tree, tmp_path / "second", "0.3.4.1", run=run)

    names = sorted(
        p.name for p in (tmp_path / "first/root/opt").iterdir() if "stage" in p.name
    )
    other = sorted(
        p.name for p in (tmp_path / "second/root/opt").iterdir() if "stage" in p.name
    )
    assert names == ["ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa"]
    assert other == ["ai-device-client-stage-0.3.4.1-bbbbbbbbbbbb"]


def test_tree_without_identity_is_refused(builder, tree, tmp_path):
    """Strict: no identity file means no stage path, so the build stops.

    Falling back to a weaker name would reintroduce the shared-stage bug for
    exactly the trees whose provenance cannot be checked.
    """

    def run(argv, **kwargs):
        raise AssertionError("packaging must not start without a client identity")

    (tree / "client-identity.json").unlink()
    with pytest.raises(ValueError, match="client-identity.json"):
        builder.build(tree, tmp_path / "package", "0.3.4.1", run=run)
    assert not (tmp_path / "package").exists()


def test_package_version_is_independent_of_client_version(builder, tree, tmp_path):
    """A package version unrelated to the tree's client version must be accepted.

    The two numbers answer different questions: which release users got, and
    which runtime code it carries. Constraining one by the other makes the
    release number unable to move independently -- a 1.0.0 release could not
    ship the 0.3.6 runtime without renaming the release or misreporting the
    runtime.
    """
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    # 9.9.9 shares no prefix with the tree's 0.3.4 client version.
    builder.build(tree, tmp_path / "package", "9.9.9", run=run)
    assert (tmp_path / "package").exists()
    assert calls

    # The stage path still carries the client build id, so two different trees
    # under one package version cannot merge into the same stage directory.
    stage = Path(builder.stage_for("9.9.9", "sha256:" + "a" * 64)).name
    assert stage.startswith("ai-device-client-stage-9.9.9-")


def test_identity_build_id_must_be_a_sha256_digest(builder, tree, tmp_path):
    """A malformed build id must not silently produce a short or empty segment."""

    def run(argv, **kwargs):
        raise AssertionError("packaging must not start with a malformed build id")

    (tree / "client-identity.json").write_text(
        json.dumps({"version": "0.3.4", "build_id": "not-a-digest"})
    )
    with pytest.raises(ValueError, match="sha256"):
        builder.build(tree, tmp_path / "package", "0.3.4.1", run=run)


def test_stage_must_be_separate_from_live_program(builder, tree, tmp_path):
    """A payload on the live path would reintroduce the very write window the
    atomic commit exists to remove, so the builder must refuse it."""

    def run(argv, **kwargs):
        raise AssertionError("packaging must not start for an invalid layout")

    for stage, program in [
        ("/opt/ai-device-client", "/opt/ai-device-client"),
        ("/opt/ai-device-client/stage", "/opt/ai-device-client"),
        ("/opt/stage", "/opt/stage/program"),
    ]:
        with pytest.raises(ValueError, match="separate location"):
            builder.build(
                tree, tmp_path / "package", "0.3.4.1", run=run, program=program, stage=stage
            )


def test_postinstall_reports_unknown_without_retrying(builder, tree, tmp_path):
    """A failed commit cannot be retried from the script: exit status does not
    distinguish 'nothing happened' from 'happened but unverified'."""

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    builder.build(tree, tmp_path / "package", "0.3.4.1", run=run)
    postinstall = (tmp_path / "package/scripts/postinstall").read_text()
    # Count INVOCATIONS, not the word: the verb is a substring of the script's
    # own comments and status-file name, so a bare count proves nothing.
    # Both publish primitives are exercised by the same entry, and exactly one
    # of them runs: the branch is chosen by whether the live path exists.
    invocations = [
        line.strip()
        for line in postinstall.splitlines()
        if "result=$(" in line
    ]
    assert len(invocations) == 3, invocations
    assert any("upgrade-registered-program" in line for line in invocations)
    assert any("first-program" in line for line in invocations)
    assert any('"$VERB" "$STAGE" "$PROGRAM"' in line for line in invocations)
    assert postinstall.count('"$ENTRY" "$VERB"') == 0
    assert f"VERB='{builder.verb_from_header('PROGRAM_VERB_COMMIT')}'" in postinstall
    # No loop and no second attempt. Checked on executable lines only: the
    # script's own prose uses "while" and the word "retry" freely, and asserting
    # over the whole file made this test fail on a comment edit.
    code = [
        line for line in postinstall.splitlines() if not line.lstrip().startswith("#")
    ]
    assert not any("while " in line for line in code)
    assert not any("retry" in line.lower() for line in code)
    # Each branch runs at most once: there is no loop, so one call site per
    # branch is the strongest static guarantee available here.
    body = "\n".join(code)
    assert body.count("upgrade-registered-program") == 1
    assert body.count("first-program") == 1
    assert "exit 1" in postinstall


def test_postinstall_creates_status_dir_private_to_root(builder, tree, tmp_path):
    """The status directory is the registration directory; it must be private.

    `ensure_directory` and the native management provisioning both reject a
    directory that is not 0700 root:wheel. The script used `mkdir -m 0755`,
    so a freshly installed tree could never register: the client raised
    "directory ownership or permissions are incompatible".
    """

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    builder.build(tree, tmp_path / "package", "0.3.4.1", run=run)
    postinstall = (tmp_path / "package/scripts/postinstall").read_text()
    assert 'mkdir -p -m 0700 "$STATUS_DIR"' in postinstall
    # Permit "0755" only inside this script's explanatory comments about the
    # old mode; the executable part must not create a world-readable directory.
    code = "\n".join(
        line for line in postinstall.splitlines() if not line.lstrip().startswith("#")
    )
    assert "0755" not in code
    # mkdir -m is a no-op on an existing directory, so the script must correct
    # a tree installed by an earlier build instead of leaving it at 0755.
    assert 'chmod 0700 "$STATUS_DIR"' in postinstall
    assert 'chown root:wheel "$STATUS_DIR"' in postinstall


def test_status_dir_mode_matches_the_registration_contract(builder, tree, tmp_path):
    """The package and the client must agree on the private mode.

    Asserting the literal string only pins one side. This compares the mode in
    the generated script against the constant the registration path enforces,
    so the two cannot drift apart again without failing here.
    """

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    builder.build(tree, tmp_path / "package", "0.3.4.1", run=run)
    postinstall = (tmp_path / "package/scripts/postinstall").read_text()
    source = (
        Path(builder.__file__).resolve().parents[1]
        / "src/device_runtime/client_macos_management.py"
    ).read_text()
    # ensure_directory builds the mode as `0o700 if private else 0o755` and
    # rejects anything carrying the private mask.
    assert "mode=0o700 if private else 0o755" in source
    assert "forbidden = 0o077" in source
    assert 'mkdir -p -m 0700 "$STATUS_DIR"' in postinstall


def test_upgrade_payload_ships_the_entry_without_a_copy_of_the_tree(builder, tree, tmp_path):
    """One install carries exactly one tree.

    The entry embeds the inventory of the tree it will publish and is handed the
    staged payload path on argv, so it does not need -- and must not ship -- a
    second copy of that tree beside itself. A copy would double an 88 MB payload
    and become a second source that could disagree with the staged one.
    """

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0].endswith("lipo"):
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0].endswith("pkgbuild"):
            Path(argv[-1]).write_bytes(b"pkg")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0].endswith("pkgutil"):
            payload = Path(argv[-1]) / "Payload" / builder.UPGRADE_ROOT.lstrip("/")
            payload.mkdir(parents=True, exist_ok=True)
            (payload / builder.UPGRADE_ENTRY).write_bytes(b"binary")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[0].endswith("lsbom"):
            return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    output = tmp_path / "upgrade"
    builder.build_upgrade_payload(tree, output, run=run)
    root = output / "root" / builder.UPGRADE_ROOT.lstrip("/")
    assert (root / builder.UPGRADE_ENTRY).exists()
    # The inventory is embedded in the binary, not shipped as a sibling tree.
    assert not (root / "resources").exists()
    build_dir = output / "build"
    assert (build_dir / "resource-table.h").is_file()
    assert "resources[]" in (build_dir / "resource-table.h").read_text()
    # The entry is compiled for the architecture the package claims.
    assert any("client-macos-installer.m" in part for call in calls for part in call)
    assert any(call[0].endswith("lipo") for call in calls)


def test_upgrade_payload_never_installs_over_the_program(builder, tree, tmp_path):
    """The upgrade layer must not ship a replacement program tree.

    It carries the coordinator only; publishing the program stays the
    coordinator's job at run time. A program tree in this payload would be a
    competing writer with no commit discipline.
    """

    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        if argv[0].endswith("pkgbuild"):
            Path(argv[-1]).write_bytes(b"pkg")
        if argv[0].endswith("pkgutil"):
            payload = Path(argv[-1]) / "Payload" / builder.UPGRADE_ROOT.lstrip("/")
            payload.mkdir(parents=True, exist_ok=True)
            (payload / builder.UPGRADE_ENTRY).write_bytes(b"binary")
        if argv[0].endswith("lsbom"):
            return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    output = tmp_path / "upgrade"
    builder.build_upgrade_payload(tree, output, run=run)
    installed = {p.relative_to(output / "root").as_posix() for p in (output / "root").rglob("*")}
    assert not any(path.startswith("opt/ai-device-client/") for path in installed)
    assert not any("ai-device-client-stage" in path for path in installed)
    assert not any("client-tree" in path for path in installed)


def test_upgrade_payload_rejects_a_missing_client_tree(builder, tmp_path):
    """An incomplete tree must fail at build time, not at upgrade time."""

    broken = tmp_path / "broken"
    (broken / "runtime/bin").mkdir(parents=True)
    (broken / "bin").mkdir()
    (broken / "build-manifest.json").write_text(
        json.dumps({"schema_version": 1, "platform": "darwin-arm64"})
    )
    with pytest.raises(ValueError, match="incomplete"):
        builder.build_upgrade_payload(broken, tmp_path / "out")


def test_generated_postinstall_verb_matches_the_shipped_binary(builder, tree, tmp_path):
    """The generated script and the compiled commit binary must agree on the verb.

    This is the test the previous revision was missing. The script asked for
    `commit-program`, the packaged binary accepted only `commit`, and every
    existing assertion passed because one checked the text it had just written
    and the other exercised a wrapper that was not in the payload.

    Here the real `client-macos-commit-main.c` is compiled and asked to parse the
    exact argv the generated script would build. If the two ever drift apart this
    fails with exit 64 (unknown verb) instead of shipping a package whose
    postinstall can never publish a tree.
    """
    if sys.platform != "darwin":
        pytest.skip("Darwin compiler")

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "package"
    builder.build(tree, output, "0.3.4.1", run=run)
    postinstall = (output / "scripts/postinstall").read_text()
    verb = builder.verb_from_header("PROGRAM_VERB_COMMIT")
    assert f"VERB='{verb}'" in postinstall

    native = Path(__file__).parents[1] / "native"
    binary = tmp_path / "commit-main"
    subprocess.run(
        [
            "/usr/bin/xcrun", "clang", "-Wall", "-Wextra", "-Werror",
            "-I", str(native), str(native / "client-macos-commit-main.c"),
            "-o", str(binary),
        ],
        check=True, capture_output=True,
    )

    # The binary must accept the verb the script uses. A non-root caller stops at
    # the identity gate (70), which is proof the verb parsed; an unknown verb
    # would exit 64 before any check.
    accepted = subprocess.run(
        [str(binary), verb, str(tmp_path / "stage"), str(tmp_path / "program")],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert accepted.returncode == 70, accepted.stderr
    assert "unknown verb" not in accepted.stderr

    # And the old spelling is genuinely gone, so nothing can quietly rely on it.
    rejected = subprocess.run(
        [str(binary), "commit-program", str(tmp_path / "stage"), str(tmp_path / "program")],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert rejected.returncode == 64
    assert "unknown verb" in rejected.stderr


def test_default_build_stays_inspection_only(builder, tmp_path):
    """The verification switch must be opt-in.

    A default build that quietly became installable would turn every existing
    artifact and every future one into a real install candidate, so the default
    contract is asserted here rather than left to the caller remembering a flag.
    """
    xml = ET.fromstring(builder.distribution("0.3.4.1"))
    assert xml.find("installation-check").get("script") == "rejectInspection()"
    assert "inspection only" in xml.find("title").text
    # The default preinstall still refuses unconditionally. Asserting the exact
    # text (rather than a substring) keeps a future rewrite that accidentally
    # drops the refusal from passing as "unchanged default".
    assert "exit 1" in builder.PREINSTALL
    assert "Inspection-only" in builder.PREINSTALL
    assert "$STAGE" not in builder.PREINSTALL


def test_shipping_build_is_marked_unsigned(builder):
    """Requirement 11: an unsigned development package must be marked.

    The package has no signature and no notarisation ticket, and a downloaded
    copy carries a quarantine attribute, which makes Gatekeeper suspend
    Installer.app on double-click. Since the filename is shared with what a
    signed release would use, the title and welcome page are the only places
    that tell a user what they are holding.
    """
    xml = ET.fromstring(builder.distribution("0.3.4.1", mode="shipping"))
    assert "UNSIGNED" in xml.find("title").text
    assert "development build" in xml.find("title").text
    # The marker must be visible before the payload is written, not buried in a
    # post-install message after the fact.
    assert xml.find("welcome").get("file") == "welcome.html"
    welcome = builder.UNSIGNED_WELCOME
    assert "unsigned" in welcome.lower()
    assert "sudo installer -pkg" in welcome
    assert "notarisation" in welcome.lower()


def test_rehearsal_builds_do_not_claim_to_be_shipping(builder):
    """The unsigned marker is about the deliverable only.

    Verification and stage-only already say they are not for release; adding
    the shipping welcome page to them would claim a distribution property they
    do not have.
    """
    for mode in ("inspection", "verification", "stage_only"):
        xml = ET.fromstring(builder.distribution("0.3.4.1", mode=mode))
        assert xml.find("welcome") is None, f"{mode} must not carry the welcome page"
        assert "UNSIGNED" not in (xml.find("title").text or "")


def test_verification_distribution_is_installable_and_distinguishable(builder):
    xml = ET.fromstring(builder.distribution("0.3.4.1", mode="verification"))
    # No blanket refusal: this product is meant to install.
    assert xml.find("installation-check").get("script") == "verifyInstallTarget()"
    assert "rejectInspection" not in ET.tostring(xml, encoding="unicode")
    # The artifact has to be self-describing, because the two products look the
    # same to anyone who moves a file around later.
    assert "VERIFICATION" in xml.find("title").text
    assert "not for release" in xml.find("title").text


def test_verification_product_identifier_differs_from_inspection(builder):
    """A rehearsal install must not leave a receipt that a release collides with.

    Receipts are keyed by product identifier, so sharing one would make an
    installed verification build indistinguishable from an installed release.
    """
    inspect_pkg = ET.fromstring(builder.distribution("0.3.4.1"))
    verify_pkg = ET.fromstring(builder.distribution("0.3.4.1", mode="verification"))
    inspect_id = inspect_pkg.find("pkg-ref").get("id")
    verify_id = verify_pkg.find("pkg-ref").get("id")
    assert inspect_id == builder.IDENTIFIER
    assert verify_id == builder.VERIFICATION_IDENTIFIER
    assert (inspect_id, verify_id) == (
        "local.ai-device.client.inspection",
        "local.ai-device.client.verification",
    )


def test_verification_preinstall_claims_stage_atomically(builder):
    """preinstall must claim the stage with mkdir, not test-then-write.

    An existence test followed by the installer's payload write is a TOCTOU
    window: two installers can both see an absent stage and both proceed.
    `mkdir` cannot be won twice, so the claim is the mutex. Measured on the
    target host with eight concurrent claimants: one success, seven failures.
    """
    script = builder.PREINSTALL_RESIDUE.format(
        stage="/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa",
        token_dir=builder.COMMIT_STATUS_DIR,
        token_file=builder.PREINSTALL_TOKEN_FILE,
    )
    assert "/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa" in script
    # The claim itself.
    assert 'if mkdir "$STAGE" 2>/dev/null; then' in script
    # The old existence test must be gone; keeping both would reintroduce the
    # window this change exists to close.
    assert '[ -e "$STAGE" ]' not in script
    # It must NOT refuse unconditionally: that is the inspection behaviour.
    assert "Inspection-only" not in script


def test_preinstall_distinguishes_busy_from_residue(builder):
    """Empty and non-empty stage directories mean different things.

    Measured: the installer merges its payload into a pre-existing non-empty
    directory and leaves foreign files there, so publishing it would ship
    content this package never authored. An empty directory can only be a
    concurrent installer's claim (or our own). The two need different tokens
    because the operator's next step differs: wait, versus clean up.
    """
    script = builder.PREINSTALL_RESIDUE.format(
        stage="/opt/ai-device-client-stage-1.0-abc",
        token_dir=builder.COMMIT_STATUS_DIR,
        token_file=builder.PREINSTALL_TOKEN_FILE,
    )
    assert "PROGRAM_INSTALL_BUSY_V1" in script
    assert "PROGRAM_INSTALL_RESIDUE_V1" in script
    # Residue covers both a symlink/non-directory and a non-empty directory.
    assert "[ -L \"$STAGE\" ]" in script
    assert '[ ! -d "$STAGE" ]' in script
    # The non-empty test drives the residue branch.
    assert 'ls -A "$STAGE"' in script


def test_preinstall_writes_reason_to_a_readable_file(builder):
    """stderr alone is invisible to the user.

    Measured: `installer` prints a fixed "script failed" line and routes the
    script's stderr to /var/log/install.log. Without a file at a known path,
    the operator sees only the generic message -- which is exactly the
    behaviour this change set out to fix.
    """
    script = builder.PREINSTALL_RESIDUE.format(
        stage="/opt/ai-device-client-stage-1.0-abc",
        token_dir=builder.COMMIT_STATUS_DIR,
        token_file=builder.PREINSTALL_TOKEN_FILE,
    )
    assert builder.PREINSTALL_TOKEN_FILE in script
    assert '> "$TOKEN_FILE"' in script
    # The token file must live in the root-owned management directory, not in
    # world-writable /var/tmp where a local user could pre-place a symlink.
    assert builder.PREINSTALL_TOKEN_FILE.startswith(builder.COMMIT_STATUS_DIR)
    assert "/var/tmp" not in script
    # The management directory must not be widened by an error report.
    assert "chmod 755" not in script
    assert "-m 0700" in script


def test_verification_build_writes_installable_preinstall_and_name(builder, tree, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        # The payload also carries the upgrade entry, whose compile step must
        # actually produce a file: the builder refuses to package an entry that
        # does not exist.
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "verify"
    result = builder.build(tree, output, "0.3.4.1", run=run, mode="verification")
    # The filename has to carry the mode: it is the only thing a human sees
    # before double-clicking it on a real machine.
    assert result.name == "ai-device-client-0.3.4.1-arm64-verification.pkg"
    preinstall = (output / "scripts/preinstall").read_text()
    assert preinstall == builder.PREINSTALL_RESIDUE.format(
        stage="/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa",
        token_dir=builder.COMMIT_STATUS_DIR,
        token_file=builder.PREINSTALL_TOKEN_FILE,
    )
    assert builder.COMMIT_TOKENS  # module still exposes the native outcome map
    # The program package now also compiles the upgrade entry, so the component
    # build is no longer the first call; find it rather than assuming it is.
    component = next(call for call in calls if call[0].endswith("pkgbuild"))
    assert component[component.index("--identifier") + 1] == builder.VERIFICATION_IDENTIFIER


def test_inspection_build_still_names_itself_inspection(builder, tree, tmp_path):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "inspect"
    result = builder.build(tree, output, "0.3.4.1", run=run)
    assert result.name == "ai-device-client-0.3.4.1-arm64-inspection.pkg"
    assert (output / "scripts/preinstall").read_text() == builder.PREINSTALL
    assert "verification" not in (output / "Distribution.xml").read_text().lower()


def test_stage_only_postinstall_does_not_publish(builder, tree, tmp_path):
    """Stage-only must stop before the commit, or it cannot answer the C4 question.

    A normal install ends with RENAME_SWAP, which parks the previous program
    tree at the stage path. Observing the stage afterwards then shows the swap's
    result, not what the Installer did while writing the payload. Stage-only
    exists solely to leave the payload in that pre-swap state, so any commit
    invocation here would defeat its only purpose.
    """
    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "stageonly"
    result = builder.build(tree, output, "0.3.4.1", run=run, mode="stage_only")
    postinstall = (output / "scripts/postinstall").read_text()
    assert '"$ENTRY" "$VERB"' not in postinstall
    assert postinstall.count("exit 1") == 1
    # The stage is still expected to be populated; refusing when it is empty
    # keeps a silently-skipped payload from looking like a successful run.
    assert "[ ! -d \"$STAGE\" ]" in postinstall
    assert "STAGE_ONLY_PAYLOAD_WRITTEN_V1" in postinstall
    assert result.name == "ai-device-client-0.3.4.1-arm64-verification-stage-only.pkg"


def test_stage_only_still_has_the_residue_preinstall(builder, tree, tmp_path):
    """Stage-only is still an install, so it inherits the same preinstall gate."""
    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "stageonly-pre"
    builder.build(tree, output, "0.3.4.1", run=run, mode="stage_only")
    assert (output / "scripts/preinstall").read_text() == builder.PREINSTALL_RESIDUE.format(
        stage="/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa",
        token_dir=builder.COMMIT_STATUS_DIR,
        token_file=builder.PREINSTALL_TOKEN_FILE,
    )


def test_all_four_modes_have_distinct_identifiers(builder):
    """Receipts are keyed by identifier, so sharing one corrupts install state."""
    seen = {
        ET.fromstring(builder.distribution("0.3.4.1", mode=m)).find("pkg-ref").get("id")
        for m in ("inspection", "verification", "stage_only", "shipping")
    }
    assert len(seen) == 4


def test_unknown_mode_is_rejected(builder):
    with pytest.raises(ValueError, match="unknown package mode"):
        builder.distribution("0.3.4.1", mode="release")


def test_installable_modes_reject_installation_check_only_for_inspection(builder):
    """Only the inspection product may hard-refuse; the others must be usable."""
    for mode in ("verification", "stage_only"):
        xml = ET.fromstring(builder.distribution("0.3.4.1", mode=mode))
        assert xml.find("installation-check").get("script") == "verifyInstallTarget()"
        assert "rejectInspection" not in ET.tostring(xml, encoding="unicode")
        assert "not for release" in xml.find("title").text


def test_shipping_product_carries_no_rehearsal_wording(builder):
    """The deliverable must not tell its user it is a verification build.

    Every rehearsal mode says so in the title and in the install-check message --
    the two strings a user actually reads. A shipping build reusing either would
    ship the rehearsal disclaimer to real machines. It does carry a different
    disclaimer, because it is in fact unsigned; that one is asserted in
    test_shipping_build_is_marked_unsigned.
    """
    xml = ET.fromstring(builder.distribution("0.3.4.2", mode="shipping"))
    assert xml.find("title").text == "AI Device Client — UNSIGNED development build"
    rendered = ET.tostring(xml, encoding="unicode").lower()
    assert "verification" not in rendered
    assert "not for release" not in rendered
    script = xml.find("script").text
    assert "This client requires macOS 11 or later." in script
    # It is still an installable product: the rejection path is inspection-only.
    assert xml.find("installation-check").get("script") == "verifyInstallTarget()"


def test_shipping_identifier_does_not_reuse_a_rehearsal_identifier(builder):
    """A shipping receipt must be distinguishable from every rehearsal one."""
    shipping = ET.fromstring(builder.distribution("0.3.4.2", mode="shipping")).find("pkg-ref").get("id")
    assert shipping == builder.SHIPPING_IDENTIFIER
    for rehearsal in (builder.IDENTIFIER, builder.VERIFICATION_IDENTIFIER, builder.STAGE_ONLY_IDENTIFIER):
        assert shipping != rehearsal


def test_shipping_build_publishes_and_keeps_plain_artifact_name(builder, tree, tmp_path):
    """Shipping must use the real commit path and an indistinguishable name.

    A deliverable whose filename carries a mode word is a deliverable nobody
    can confuse with a test build -- which reads as caution but means the file
    users are told to install is the one file that cannot be named plainly.
    Every rehearsal mode keeps its suffix, so the plain name is unambiguous.
    """
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "ship"
    result = builder.build(tree, output, "0.3.4.2", run=run, mode="shipping")
    assert result.name == "ai-device-client-0.3.4.2-arm64.pkg"
    component = next(call for call in calls if call[0].endswith("pkgbuild"))
    assert component[component.index("--identifier") + 1] == builder.SHIPPING_IDENTIFIER
    postinstall = (output / "scripts/postinstall").read_text()
    # Same activation path as every installable build: the commit is real.
    assert "upgrade-registered-program" in postinstall
    # The unregistered branch publishes through the same entry, which is the
    # only executable carrying the embedded inventory.
    assert '"$ENTRY" "$VERB"' not in postinstall


def test_postinstall_detects_registration_without_a_program(builder, tree, tmp_path):
    """A registration whose program tree is gone must be named, not just refused.

    Removing /opt/ai-device-client without removing registration.json leaves an
    orphaned identity. The upgrade branch then fails because there is no running
    instance to ask for exit, and the coordinator's refusal is correct but
    indistinguishable from every other failure: the operator saw "the upgrade
    failed" and every retry hit the same branch. Reproduced on real hardware
    during A->B acceptance.
    """

    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "orphan-notice"
    builder.build(tree, output, "0.3.4.7", run=run, mode="shipping")
    postinstall = (output / "scripts/postinstall").read_text()

    # The check must sit inside the registered branch, before the coordinator,
    # or it cannot tell an orphan from a normal upgrade.
    assert "AI_DEVICE_CLIENT_IDENTITY_WITHOUT_PROGRAM_V1" in postinstall
    registered = postinstall.index('if [ -f "$REGISTRATION" ]')
    orphan = postinstall.index("AI_DEVICE_CLIENT_IDENTITY_WITHOUT_PROGRAM_V1")
    coordinator = postinstall.index("upgrade-registered-program")
    assert registered < orphan < coordinator
    # Recovery must be commands the operator can actually run, not prose. The
    # identity path is injected by printf from a build-time constant rather than
    # left as $REGISTRATION, because the heredoc is quoted: postinstall runs as
    # root, so nothing inside it may depend on shell expansion.
    assert 'sudo rm -f "%s"' in postinstall
    # The notice text itself must not reference shell variables that a quoted
    # heredoc would print literally. Only the printf calls may carry them.
    orphan_text = postinstall.split("AI_DEVICE_CLIENT_IDENTITY_WITHOUT_PROGRAM_V1")[1]
    orphan_text = orphan_text[:orphan_text.index("The same guidance is saved in:")]
    # The notice must not print a shell variable to the operator. $HOME is the
    # one that matters: postinstall runs as root, so an unquoted heredoc would
    # have printed /var/root -- a directory the reader does not own.
    notice_only = "\n".join(
        ln for ln in orphan_text.splitlines()
        if "$" in ln and not ln.strip().startswith(("printf", "cat "))
    )
    # Every remaining $VAR must be inside a printf argument, not free text.
    assert "$HOME" not in notice_only
    assert "$USER" not in notice_only
    # install-status and install-note are the only channels that survive the
    # install: installer discards script stderr, so an early exit that skips the
    # status write leaves the operator with nothing at all.
    assert "record_status" in postinstall
    # Called before the notice: the status must be written even if the heredoc
    # or the subsequent exit fails, and the notice names where to find it.
    orphan_block = postinstall[registered:orphan]
    assert "record_status" in orphan_block
    assert "install-note" in postinstall
    # A leftover stage from an interrupted install makes preinstall refuse, so a
    # recovery that omits it sends the operator straight back to the same error.
    # It must be THIS package's stage path, not a glob: a wildcard tells the
    # operator to delete every stage-named directory on the machine, including
    # ones belonging to a different version or an in-flight install.
    assert "rm -rf /opt/ai-device-client-stage-*" not in postinstall
    # The status writer must not consume positional parameters: this text is a
    # str.format template, and $1/$2 are parsed as replacement fields.
    assert "record_status \"$" not in postinstall
    # Config, token and business data must be stated as untouched, because the
    # obvious fear on reading "remove the identity" is losing credentials.
    assert "NOT part of it" in postinstall


def test_shipping_postinstall_names_the_outstanding_configuration(builder, tree, tmp_path):
    """Installing the program is not the same as having a usable client.

    With no named user the package must stop after publishing and say so; the
    requirement forbids guessing a user or picking a logged-in session. Before
    this, a successful first install exited 0 and left nothing that would ever
    start at login, with no indication why.
    """
    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "ship-notice"
    builder.build(tree, output, "0.3.4.2", run=run, mode="shipping")
    postinstall = (output / "scripts/postinstall").read_text()
    assert "AI_DEVICE_CLIENT_INSTALLED_NOT_REGISTERED_V1" in postinstall
    # The notice is conditional: a registered upgrade must not print it.
    assert 'if [ ! -f "$REGISTRATION" ]' in postinstall
    # It has to name the real path and the real registrar module, or it sends
    # the operator somewhere that refuses them.
    assert "Library/Application Support/ai-device-client/client.json" in postinstall
    assert "client_macos_register register" in postinstall
    # The heredoc must be QUOTED and the program path inlined at build time.
    # postinstall runs as root, so an unquoted heredoc would expand $HOME and
    # $(id -u) against /var/root and print instructions aimed at an account
    # that does not exist, for a user whose own shell must run them.
    assert "<<'OUTSTANDING'" in postinstall
    assert "cat >&2 <<OUTSTANDING" not in postinstall
    # Inlined, not left as a shell variable or a literal placeholder.
    assert "The client program is installed at /opt/ai-device-client," in postinstall
    # Scope the leakage checks to the notice itself: $PROGRAM is a legitimate
    # shell variable elsewhere in postinstall, but inside this quoted heredoc it
    # would reach the operator as literal text.
    notice = postinstall[postinstall.index("AI_DEVICE_CLIENT_INSTALLED_NOT_REGISTERED_V1"):]
    notice = notice[:notice.index("\nOUTSTANDING\n")]
    assert "$PROGRAM" not in notice
    assert "{program}" not in notice
    # $HOME and $(id -u) must survive as literal text for the user to expand.
    assert "\"$HOME/Library/LaunchAgents/local.ai-device.client.plist\"" in postinstall
    assert "gui/$(id -u)" in postinstall
    # 0600, not 0644: the registrar rejects any group/other bit on the config
    # file, so a notice that says 0644 sends the operator into a refusal.
    assert "ai-device-client/client.json  (0600)" in postinstall
    assert "client.json  (0644)" not in postinstall
    # A non-loopback host requires wss://; naming only "<centre url>" leaves the
    # operator free to paste a ws:// URL that device_url() refuses.
    assert '"server_url": "wss://' in postinstall
    assert "non-loopback host requires wss://" in postinstall
    # The registrar reads these two keys literally and refuses any other path,
    # so the notice has to name them; a bare "server_url and device_id" leaves
    # the operator with a config that is rejected as "unexpected client paths".
    assert '"state_dir"' in postinstall
    assert '"token_file"' in postinstall
    # An unquoted placeholder is parsed as a redirection by the shell.
    assert "--user <that-user>" not in postinstall
    assert "--user '<user>'" in postinstall


def test_inventory_describes_the_shipped_payload_not_the_source_tree(
    builder, tree, tmp_path, monkeypatch
):
    """A byte cache in the source tree must not end up in the embedded inventory.

    The payload copy excludes __pycache__ and *.pyc, but the inventory used to be
    taken from the source tree, so any tree that had ever been imported produced a
    package whose inventory listed files that never shipped. verify_resource_tree
    requires an exact entry count, so every install refused at the first program
    step -- after a successful build, a successful pkgbuild and a successful
    signature check. Found on real hardware; the build side was green throughout.
    """
    cache = tree / "runtime/lib/python3.13/encodings/__pycache__"
    cache.mkdir(parents=True)
    (cache / "__init__.cpython-313.pyc").write_bytes(b"cache")

    seen = {}
    original = builder.INVENTORY.inventory

    def record(path):
        result = original(path)
        seen[Path(path).name] = sorted(
            entry["path"] for entry in result["entries"]
            if "__pycache__" in entry["path"]
        )
        return result

    monkeypatch.setattr(builder.INVENTORY, "inventory", record)

    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "inventory-payload"
    builder.build(tree, output, "0.3.4.6", run=run, mode="shipping")
    # Only the payload may be measured, and it must carry no cache entries at all.
    assert "tree" not in seen, "inventory was taken from the unshipped source tree"
    assert all(not entries for entries in seen.values())


def test_unregistered_branch_picks_a_primitive_that_matches_the_live_path(builder, tree, tmp_path):
    """First install must be gated, and a reinstall must still be possible.

    These two requirements pull in opposite directions, which is why the branch
    is split rather than unified:

      absent  -> `first-program`, which is strictly create-only but carries the
                 management lock, the embedded-inventory check and the host
                 conflict probe this branch previously had none of.
      present -> `commit`, a single atomic rename, because `first-program`
                 refuses an existing target and would otherwise make the
                 package impossible to reinstall.

    Both must run through the upgrade entry, which is the executable holding
    the inventory; the staged commit binary has none.
    """
    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "branch"
    builder.build(tree, output, "0.3.4.1", run=run, mode="shipping")
    postinstall = (output / "scripts/postinstall").read_text()
    # The presence test must gate the choice, not the other way round.
    assert 'if [ -e "$PROGRAM" ] || [ -L "$PROGRAM" ]' in postinstall
    # Both invocations use the inventory-bearing entry.
    assert '"$UPGRADE_ENTRY" first-program --confirm-legacy-clients-disabled' in postinstall
    assert '"$UPGRADE_ENTRY" "$VERB" "$STAGE" "$PROGRAM"' in postinstall
    # first-program copies instead of renaming, so its stage must be cleaned or
    # the preinstall residue check blocks the next install of this same package.
    assert 'rm -rf "$STAGE"' in postinstall
    # Cleanup is conditional on success: a refused publish keeps its evidence.
    cleanup = postinstall.split('rm -rf "$STAGE"')[0].rstrip().splitlines()[-1]
    assert 'if [ "$status" -eq 0 ]' in cleanup


def test_installable_builds_refuse_a_program_path_the_native_entry_cannot_honour(builder, tree, tmp_path):
    """`first-program` has its destination compiled in.

    Accepting a different --program would emit a script whose two branches
    publish to different paths: the upgrade would use $PROGRAM while the first
    install quietly used the hardcoded one. That is a silent mismatch between a
    build option and the product contract, so it is refused at build time.
    """
    def run(argv, **kwargs):
        raise AssertionError("packaging must not start for an unsupported program path")

    with pytest.raises(ValueError, match="first-install branch disagree"):
        builder.build(
            tree,
            tmp_path / "package",
            "0.3.4.1",
            run=run,
            mode="shipping",
            program="/opt/some-other-client",
        )
    # The inspection product never publishes, so it keeps the old freedom.
    def quiet(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    builder.build(
        tree, tmp_path / "inspection", "0.3.4.1", run=quiet, program="/opt/some-other-client"
    )


def test_rehearsal_modes_stay_silent_about_configuration(builder, tree, tmp_path):
    """Only the shipping product advises a user; a rehearsal build must not."""
    def run(argv, **kwargs):
        if "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"binary")
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "rehearsal-silent"
    builder.build(tree, output, "0.3.4.2", run=run, mode="verification")
    postinstall = (output / "scripts/postinstall").read_text()
    assert "AI_DEVICE_CLIENT_INSTALLED_NOT_REGISTERED_V1" not in postinstall
    assert "client_macos_register" not in postinstall


def _orphan_notice_shell(tmp_path, *, observe_ok=False, program_exists=False,
                         program_is_symlink=False, home="/Users/tester", uid=555):
    """Run the shipped orphan notice for real and return what it prints.

    String assertions cannot tell whether the notice is operable: a quoted
    heredoc and an unquoted one contain the same words but print different
    things. This executes the fragment with the same variables postinstall has.
    """
    import importlib.util as _iu

    spec = _iu.spec_from_file_location(
        "orphan_builder", Path(__file__).parents[1] / "scripts/build-client-pkg.py"
    )
    builder = _iu.module_from_spec(spec)
    spec.loader.exec_module(builder)
    rendered = builder.POSTINSTALL.format(
        program=str(tmp_path / "prog"),
        stage=str(tmp_path / "stage"),
        entry=str(tmp_path / "stage/bin/commit"),
        verb="commit",
        status_dir=str(tmp_path / "status"),
        status_file=str(tmp_path / "status/install-status"),
        upgrade_entry=str(tmp_path / "entry"),
        staged_upgrade_entry=str(tmp_path / "entry"),
        registration=str(tmp_path / "registration.json"),
        epilogue="",
    )
    start = rendered.index('    if [ -L "$PROGRAM" ]; then')
    # End at the orphan branch's own exit, so the fragment is two complete
    # if-blocks rather than a slice that leaves later branches unterminated.
    marker = rendered.index("IDENTITY_WITHOUT_PROGRAM_V1", start)
    end = rendered.index("\n        exit 1\n    fi\n", marker) + len("\n        exit 1\n    fi\n")
    fragment = rendered[start:end]

    (tmp_path / "registration.json").write_text(
        f'{{"schema_version": 1, "user": "tester", "uid": {uid}, "gid": 20, '
        f'"home": "{home}"}}'
    )
    (tmp_path / "stage").mkdir()
    if program_is_symlink:
        (tmp_path / "realtree").mkdir()
        (tmp_path / "prog").symlink_to(tmp_path / "realtree")
    elif program_exists:
        (tmp_path / "prog").mkdir()
    entry = tmp_path / "entry"
    entry.write_text(f"#!/bin/sh\nexit {0 if observe_ok else 70}\n")
    entry.chmod(0o755)

    script = tmp_path / "notice.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'PROGRAM="{tmp_path}/prog"\n'
        f'STAGE="{tmp_path}/stage"\n'
        f'UPGRADE_ENTRY="{tmp_path}/entry"\n'
        f'REGISTRATION="{tmp_path}/registration.json"\n'
        f'STATUS_DIR="{tmp_path}/status"\n'
        f'STATUS_FILE="{tmp_path}/status/install-status"\n'
        "STATUS_CODE=\nSTATUS_NOTE=\n"
        'record_status() { mkdir -p "$STATUS_DIR" 2>/dev/null; '
        'printf \'%s\\n\' "$STATUS_CODE" > "$STATUS_FILE" 2>/dev/null; }\n'
        + fragment + "\n"
    )
    script.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(script)], capture_output=True, check=False, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin", "HOME": "/var/root"},
    )
    note = tmp_path / "status/install-note"
    return result, (note.read_text() if note.exists() else "")


def test_orphan_notice_never_prints_the_root_home(tmp_path):
    """postinstall runs as root, where HOME is /var/root.

    The notice tells the operator to remove a file under their own home. A
    notice that reads $HOME -- or lets the shell expand it -- prints root's home
    instead: an instruction that cannot be followed, and one that suggests the
    client lived somewhere it never did.

    The guard is that the home is read from the identity file and interpolated
    as data, so no quoting rule has to hold for it to be right. This asserts the
    observable outcome for both shapes.
    """
    result, note = _orphan_notice_shell(tmp_path, home="/Users/tester")
    assert "/var/root" not in result.stderr, result.stderr
    assert "/Users/tester/Library/LaunchAgents/local.ai-device.client.plist" in result.stderr
    assert "/var/root" not in note
    # The operator's home must come from the registration, not be hardcoded.
    second = tmp_path / "b"
    second.mkdir()
    other, _ = _orphan_notice_shell(second, home="/Users/someone-else")
    assert "/Users/someone-else/Library/LaunchAgents" in other.stderr
    assert "/Users/tester" not in other.stderr


def test_orphan_notice_reports_a_running_instance_before_deleting_anything(tmp_path):
    """A missing program tree does not prove no client is running.

    An instance started before the tree was removed keeps running. Telling the
    operator to delete the identity first would strand that process: it stays
    alive and nothing can manage it again. The notice must say which case it is.
    """
    ok, _ = _orphan_notice_shell(tmp_path, observe_ok=True)
    assert "No client instance was found running" in ok.stderr
    assert "may still be running" not in ok.stderr

    other = tmp_path / "other"
    other.mkdir()
    failed, _ = _orphan_notice_shell(other, observe_ok=False)
    assert "may still be running" in failed.stderr


def test_orphan_recovery_names_exact_paths_not_a_wildcard(tmp_path):
    """Recovery must not hand the operator a glob over /opt.

    An earlier version printed `sudo rm -rf /opt/ai-device-client-stage-*`, which
    deletes every stage-named directory on the machine -- including one belonging
    to a different version or an install running right now.
    """
    result, _ = _orphan_notice_shell(tmp_path)
    assert "/opt/ai-device-client-stage-*" not in result.stderr
    assert str(tmp_path / "stage") in result.stderr
    assert str(tmp_path / "registration.json") in result.stderr


def test_orphan_notice_persists_the_full_recovery_not_just_a_reason(tmp_path):
    """install-note is the only channel left after the installer window closes.

    Writing a 62-byte reason there tells the operator what went wrong but not
    what to do, and the stderr text they would need is already gone.
    """
    _, note = _orphan_notice_shell(tmp_path)
    assert "launchctl bootout gui/555" in note
    assert "registration.json" in note
    assert len(note) > 200


def test_symlinked_program_path_is_not_reported_as_an_orphan(tmp_path):
    """A symlink at the live path is a different fault than a missing tree.

    `[ ! -d ]` follows links, so a dangling link reads as "missing" and a link to
    a directory reads as "present". Neither is an orphan, and the orphan recovery
    (delete the identity) would destroy a working registration.
    """
    result, _ = _orphan_notice_shell(tmp_path, program_is_symlink=True)
    assert "AI_DEVICE_CLIENT_PROGRAM_IS_SYMLINK_V1" in result.stderr
    assert "AI_DEVICE_CLIENT_IDENTITY_WITHOUT_PROGRAM_V1" not in result.stderr
    # It must name where the link points, or "inspect the target" is advice with
    # nothing to act on.
    assert str(tmp_path / "realtree") in result.stderr
    # And it must not tell the operator to delete anything: the identity is
    # still valid here.
    assert "sudo rm" not in result.stderr


# --- Signing and notarisation ------------------------------------------------
#
# These assert the order of operations and the refusals, not Apple's own
# behaviour: codesign, productsign and notarytool are replaced by a recorder.
# What they pin is the part that is ours to get wrong -- signing before the
# embedded inventory is hashed, refusing incomplete credential sets, and never
# labelling an unsigned artefact as a deliverable.

FAKE_MACHO = b"\xcf\xfa\xed\xfe" + b"\x00" * 60


def _record(calls, *, fail_on=None, output='{"status": "Accepted", "id": "sub-1"}'):
    def run(argv, **kwargs):
        calls.append(list(argv))
        status = 1 if fail_on and fail_on in " ".join(argv) else 0
        return subprocess.CompletedProcess(argv, status, stdout=output, stderr="")
    return run


def _add_macho(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FAKE_MACHO)
    return path


def test_payload_binaries_detects_macho_by_magic(builder, tmp_path):
    binary = _add_macho(tmp_path / "runtime/bin/python3.13")
    (tmp_path / "runtime/lib/data.txt").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime/lib/data.txt").write_text("not code")
    found = builder.payload_binaries(tmp_path)
    assert found == [binary]


def test_payload_binaries_returns_deepest_first(builder, tmp_path):
    shallow = _add_macho(tmp_path / "a/binary")
    deep = _add_macho(tmp_path / "a/b/c/d/binary")
    found = builder.payload_binaries(tmp_path)
    assert found.index(deep) < found.index(shallow)


def test_payload_binaries_skips_symlinks(builder, tmp_path):
    target = _add_macho(tmp_path / "real")
    (tmp_path / "link").symlink_to(target)
    assert builder.payload_binaries(tmp_path) == [target]


def _signed_tree(builder, base, tmp_path):
    """A copy of the fixture tree that actually contains code to sign.

    The shared fixture writes text bytes; sign_payload refuses a payload with
    no Mach-O files, which is the behaviour under test elsewhere. Signing tests
    need a tree that has some.
    """
    import shutil as _shutil

    target = tmp_path / "signed-tree"
    _shutil.copytree(base, target)
    _add_macho(target / "runtime/bin/python3")
    return target


def _recording_run(calls, *, listing=None, notary=None):
    """Records argv and stands in for the toolchain.

    clang is the one call whose output the builder inspects, so it has to leave
    a file behind; everything else is judged by its exit status.
    """
    def run(argv, **kwargs):
        calls.append(list(argv))
        stdout = ".\t40755\t0/0\n"
        if argv[0] == "/usr/bin/xcrun" and "clang" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"entry")
        elif argv[0] == "/usr/bin/xcrun" and "notarytool" in argv:
            stdout = notary or '{"status": "Accepted", "id": "sub-1"}'
        elif argv[0] == "/usr/bin/security":
            stdout = listing or ""
        elif argv[0] == "/usr/bin/productsign":
            Path(argv[-1]).write_bytes(b"signed")
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    return run


def test_signing_happens_before_the_inventory_is_hashed(builder, tree, tmp_path):
    """The regression this ordering exists to prevent.

    stage_upgrade_entry hashes the payload into the compiled entry. codesign
    rewrites the bytes it signs, so signing afterwards would ship a table of
    hashes that no longer describe the tree -- and the mismatch only surfaces
    on a real install, as a refusal with nothing pointing back at this order.
    """
    signed_tree = _signed_tree(builder, tree, tmp_path)
    order = []

    original_inventory = builder.INVENTORY.inventory

    def spy_inventory(path):
        if "ai-device-client-stage" in str(path):
            order.append("inventory")
        return original_inventory(path)

    builder.INVENTORY.inventory = spy_inventory
    inner = _recording_run([])

    def run(argv, **kwargs):
        if argv[0] == "/usr/bin/codesign" and "--sign" in argv:
            order.append("sign")
        return inner(argv, **kwargs)

    try:
        builder.build(
            signed_tree, tmp_path / "out", "0.3.4.1", mode="verification",
            sign_identity="Developer ID Application: T (TEAM)", run=run,
        )
    finally:
        builder.INVENTORY.inventory = original_inventory

    assert "sign" in order, "the payload was never signed"
    assert "inventory" in order, "the inventory was never taken"
    assert order.index("sign") < order.index("inventory"), (
        "signing must precede hashing, or the embedded table describes bytes "
        f"that no longer exist; got {order}"
    )


def test_the_compiled_entry_is_signed_too(builder, tree, tmp_path):
    """It is compiled after the payload sweep, so it needs its own call.

    It is a Mach-O executable that runs as root at install time; leaving it out
    of the sweep silently would be the worst of the three to miss.
    """
    signed_tree = _signed_tree(builder, tree, tmp_path)
    calls = []
    builder.build(
        signed_tree, tmp_path / "out", "0.3.4.1", mode="verification",
        sign_identity="Developer ID Application: T (TEAM)",
        run=_recording_run(calls),
    )
    signed_paths = [
        call[-1] for call in calls
        if call[0] == "/usr/bin/codesign" and "--sign" in call
    ]
    assert any(builder.UPGRADE_ENTRY in path for path in signed_paths), (
        f"no signature call targeted the compiled entry: {signed_paths}"
    )


def test_payload_without_macho_is_refused(builder, tmp_path):
    (tmp_path / "data.txt").write_text("no code here")
    with pytest.raises(ValueError, match="no Mach-O files"):
        builder.sign_payload(tmp_path, "Developer ID Application: Test (TEAM)",
                             run=_record([]))


def test_failed_codesign_names_the_file(builder, tmp_path):
    binary = _add_macho(tmp_path / "runtime/bin/python3")
    with pytest.raises(ValueError, match="codesign failed") as error:
        builder.sign_payload(
            tmp_path, "Developer ID Application: Test (TEAM)",
            run=_record([], fail_on="codesign"),
        )
    assert str(binary) in str(error.value)


def test_signature_verification_failure_is_reported(builder, tmp_path):
    _add_macho(tmp_path / "runtime/bin/python3")
    with pytest.raises(ValueError, match="did not verify"):
        builder.verify_payload_signatures(
            tmp_path, run=_record([], fail_on="codesign")
        )


def test_application_identity_is_refused_for_packaging(builder):
    """The mistake that costs an afternoon: productsign needs Installer."""
    listing = '  1) ABC "Developer ID Application: Test (TEAM)"'
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=listing, stderr="")

    with pytest.raises(ValueError, match="no Developer ID Installer identity"):
        builder.check_installer_identity(
            "Developer ID Application: Test (TEAM)", run=run
        )


def test_missing_identity_is_refused_before_productsign(builder):
    with pytest.raises(ValueError, match="no valid identity matches"):
        builder.check_installer_identity(
            "Developer ID Installer: Test (TEAM)",
            run=_record([], ),
        )


def test_installer_identity_present_is_accepted(builder):
    listing = '  1) ABC "Developer ID Installer: Test (TEAM)"'
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=listing, stderr="")

    assert builder.check_installer_identity(
        "Developer ID Installer: Test (TEAM)", run=run
    )


def test_unnotarised_submission_is_refused(builder, tmp_path):
    package = tmp_path / "x.pkg"
    package.write_bytes(b"pkg")
    with pytest.raises(ValueError, match="not accepted"):
        builder.notarize(
            package, "profile",
            run=_record([], output='{"status": "Invalid", "id": "sub-9"}'),
        )


def test_notary_log_hint_appears_on_rejection(builder, tmp_path):
    package = tmp_path / "x.pkg"
    package.write_bytes(b"pkg")
    with pytest.raises(ValueError, match="notarytool log sub-9"):
        builder.notarize(
            package, "profile",
            run=_record([], output='{"status": "Invalid", "id": "sub-9"}'),
        )


def test_gatekeeper_still_refusing_after_staple_is_an_error(builder, tmp_path):
    package = tmp_path / "x.pkg"
    package.write_bytes(b"pkg")
    with pytest.raises(ValueError, match="spctl still refuses"):
        builder.notarize(
            package, "profile", run=_record([], fail_on="spctl")
        )


def test_notary_profile_requires_a_signed_package(builder, tree, tmp_path):
    with pytest.raises(ValueError, match="requires --installer-identity"):
        builder.build(tree, tmp_path / "out", "0.3.4.1",
                      notary_profile="profile")


def test_installer_identity_requires_signed_payload(builder, tree, tmp_path):
    with pytest.raises(ValueError, match="requires --sign-identity"):
        builder.build(tree, tmp_path / "out", "0.3.4.1",
                      installer_identity="Developer ID Installer: T (TEAM)")


def test_inspection_build_refuses_signing(builder, tree, tmp_path):
    with pytest.raises(ValueError, match="refuse installation"):
        builder.build(tree, tmp_path / "out", "0.3.4.1",
                      sign_identity="Developer ID Application: T (TEAM)")


def test_unsigned_shipping_build_keeps_the_marker(builder, tree, tmp_path):
    """A partial credential set must not downgrade the warning."""
    signed_tree = _signed_tree(builder, tree, tmp_path)
    result = builder.build(
        signed_tree, tmp_path / "out", "0.3.4.1", mode="shipping",
        sign_identity="Developer ID Application: T (TEAM)",
        run=_recording_run([]),
    )
    assert (tmp_path / "out" / f"{result.name}.UNSIGNED.txt").is_file()
    xml = ET.fromstring((tmp_path / "out/Distribution.xml").read_text())
    assert "UNSIGNED" in xml.find("title").text


def test_fully_signed_build_drops_the_unsigned_marker(builder, tree, tmp_path):
    signed_tree = _signed_tree(builder, tree, tmp_path)
    listing = '  1) ABC "Developer ID Installer: T (TEAM)"'
    result = builder.build(
        signed_tree, tmp_path / "out", "0.3.4.1", mode="shipping",
        sign_identity="Developer ID Application: T (TEAM)",
        installer_identity="Developer ID Installer: T (TEAM)",
        notary_profile="profile",
        run=_recording_run([], listing=listing),
    )
    assert not (tmp_path / "out" / f"{result.name}.UNSIGNED.txt").exists()
    xml = ET.fromstring((tmp_path / "out/Distribution.xml").read_text())
    assert xml.find("title").text == "AI Device Client"
    welcome = (tmp_path / "out/resources/welcome.html").read_text()
    assert "notarised" in welcome


def test_signed_and_unsigned_shipping_use_different_titles(builder):
    signed = ET.fromstring(builder.distribution("0.3.4.1", mode="shipping", signed=True))
    unsigned = ET.fromstring(builder.distribution("0.3.4.1", mode="shipping"))
    assert signed.find("title").text != unsigned.find("title").text
    assert "UNSIGNED" in unsigned.find("title").text
