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
    return path


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


def test_package_version_must_extend_client_version(builder, tree, tmp_path):
    """A package version unrelated to the tree's client version is refused.

    The two numbers are not the same thing (application 0.3.4, package
    0.3.4.12), so equality is the wrong test; containment is the real contract.
    """

    def run(argv, **kwargs):
        raise AssertionError("packaging must not start for a mismatched version")

    with pytest.raises(ValueError, match="does not extend"):
        builder.build(tree, tmp_path / "package", "9.9.9", run=run)
    assert not (tmp_path / "package").exists()


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


def test_verification_preinstall_refuses_existing_stage(builder):
    """The residue check is the reason a verification build can install safely.

    It refuses on existence rather than on content: the installer cannot
    attribute what it finds, and `commit_staged_program` swaps an existing
    program tree into the stage, so a merge into a pre-existing stage would
    publish content this package never authored.
    """
    script = builder.PREINSTALL_RESIDUE.format(
        stage="/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa"
    )
    assert "/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa" in script
    assert "[ -e \"$STAGE\" ]" in script
    # A dangling symlink fails -e but still occupies the name and would still
    # be merged into, so the link test is not redundant.
    assert "[ -L \"$STAGE\" ]" in script
    assert "exit 1" in script
    # It must NOT refuse unconditionally: that is the inspection behaviour.
    assert "Inspection-only" not in script
    assert script.count("exit 1") == 1


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
        stage="/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa"
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
        stage="/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa"
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

    Every other installable mode says so in the title and in the install-check
    message -- the two strings a user actually reads. A shipping build reusing
    either would ship the rehearsal disclaimer to real machines.
    """
    xml = ET.fromstring(builder.distribution("0.3.4.2", mode="shipping"))
    assert xml.find("title").text == "AI Device Client"
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
    # The heredoc must be unquoted, or $PROGRAM prints literally and the
    # operator is handed a command that cannot run.
    assert "<<'OUTSTANDING'" not in postinstall
    assert "cat >&2 <<OUTSTANDING" in postinstall
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
