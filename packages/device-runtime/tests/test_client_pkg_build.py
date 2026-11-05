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
def test_permission_diagnostic_blocks_product_even_exit_zero(builder, tree, tmp_path, message):
    calls = []
    output = tmp_path / "permission"

    def run(argv, **kwargs):
        calls.append(argv[0])
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        (output / "client.pkg").write_bytes(b"retained diagnostic component")
        return subprocess.CompletedProcess(
            argv, 0, stdout="pkgbuild: wrote package\n", stderr=message
        )

    with pytest.raises(ValueError, match="permission error"):
        builder.build(tree, output, "0.3.4.1", run=run)
    assert calls == ["/usr/bin/pkgbuild"]
    assert message in (output / "pkgbuild.log").read_text()
    assert (output / "client.pkg").exists()
    assert (output / "scripts/preinstall").read_text() == builder.PREINSTALL
    postinstall = (output / "scripts/postinstall").read_text()
    # The commit runs exactly once, against the staged tree and the live path.
    # Count INVOCATIONS, not the word: the verb is a substring of the script's
    # own comments and status-file name, so a bare count proves nothing.
    assert postinstall.count('"$ENTRY" "$VERB"') == 1
    assert f"VERB='{builder.verb_from_header('PROGRAM_VERB_COMMIT')}'" in postinstall
    assert "/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa" in postinstall
    assert "'/opt/ai-device-client'" in postinstall


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
    assert postinstall.count('"$ENTRY" "$VERB"') == 1
    assert f"VERB='{builder.verb_from_header('PROGRAM_VERB_COMMIT')}'" in postinstall
    # The entry is invoked once; there is no loop and no second attempt.
    assert "while " not in postinstall and "retry" not in postinstall.lower()
    assert "exit 1" in postinstall


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
    assert calls[0][calls[0].index("--identifier") + 1] == builder.VERIFICATION_IDENTIFIER


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
        return subprocess.CompletedProcess(argv, 0, stdout=".\t40755\t0/0\n")

    output = tmp_path / "stageonly-pre"
    builder.build(tree, output, "0.3.4.1", run=run, mode="stage_only")
    assert (output / "scripts/preinstall").read_text() == builder.PREINSTALL_RESIDUE.format(
        stage="/opt/ai-device-client-stage-0.3.4.1-aaaaaaaaaaaa"
    )


def test_all_three_modes_have_distinct_identifiers(builder):
    """Receipts are keyed by identifier, so sharing one corrupts install state."""
    seen = {
        ET.fromstring(builder.distribution("0.3.4.1", mode=m)).find("pkg-ref").get("id")
        for m in ("inspection", "verification", "stage_only")
    }
    assert len(seen) == 3


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
