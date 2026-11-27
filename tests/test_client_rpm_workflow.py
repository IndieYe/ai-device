"""Static contract for the Linux client RPM package workflow.

Covers the parts that would fail silently: the package must be built on an
amd64 runner, the release job must never create the tag the .deb workflow owns,
an existing asset must not be overwritten, and every run block must parse.
It does not prove GitHub Actions ran.

Run with: uv run --no-sync --with pyyaml python -m pytest tests/test_client_rpm_workflow.py
"""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/client-rpm.yml"


@pytest.fixture
def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def _steps(workflow, job):
    return workflow["jobs"][job]["steps"]


def _named(workflow, job, name):
    return next(s for s in _steps(workflow, job) if s.get("name") == name)


def test_manual_only_and_opted_in_release(workflow):
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["publish_release"]["default"] == "false"
    assert inputs["expected_commit"]["required"] == "true"
    assert inputs["version"]["required"] == "true"
    # Building needs no write access; only the release job may ask for more.
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["attach-release"]["if"] == "inputs.publish_release"
    assert workflow["jobs"]["attach-release"]["permissions"] == {"contents": "write"}


def test_builds_on_amd64_for_the_linux_layout(workflow):
    build = workflow["jobs"]["build"]
    assert build["runs-on"] == "ubuntu-latest"
    assert build["if"] == (
        "github.repository == 'IndieYe/ai-device' && github.ref == 'refs/heads/main'"
    )
    # The tree builder refuses hosts other than native Linux amd64 or macOS
    # arm64, so the runner choice is part of the contract, not a preference.
    runs = [s.get("run", "") for s in build["steps"]]
    assert any("stage-client-inputs.py" in r for r in runs)
    assert any("--all-packages --wheel" in r for r in runs)
    assert any("build-client-tree.py" in r for r in runs)
    assert any("build-client-rpm.py" in r for r in runs)


def test_the_rpm_packager_is_installed_before_it_is_used(workflow):
    steps = _steps(workflow, "build")
    install = _named(workflow, "build", "Install the RPM packager")
    package = _named(workflow, "build", "Package the .rpm")
    # Ubuntu's `rpm` binary package ships rpmbuild; pinning the install makes a
    # silent "command not found" impossible to mistake for a packaging failure.
    assert "apt-get install -y --no-install-recommends rpm" in install["run"]
    assert "command -v rpmbuild" in install["run"]
    assert steps.index(install) < steps.index(package)


def test_source_gate_precedes_every_build_step(workflow):
    steps = _steps(workflow, "build")
    gate = _named(workflow, "build", "Check reviewed commit and version")
    assert gate["env"]["EXPECTED_COMMIT"] == "${{ inputs.expected_commit }}"
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"' in gate["run"]
    first_build = next(
        index
        for index, step in enumerate(steps)
        if "stage-client-inputs.py" in step.get("run", "")
    )
    assert steps.index(gate) < first_build


def test_package_is_verified_before_it_can_be_published(workflow):
    steps = _steps(workflow, "build")
    verify = _named(workflow, "build", "Verify the package before publishing it")
    run = verify["run"]
    # Stamp and architecture: a mismatch installs as a downgrade or a no-op.
    assert "--qf '%{VERSION}'" in run
    assert "--qf '%{ARCH}'" in run
    # Declared dependencies must stay the four host packages; discovered sonames
    # would be unsatisfiable because the tree carries its own shared objects.
    assert "--requires" in run
    # Modes must survive a strict umask; a 0700 payload directory still installs
    # and fails only at run time, when the service user cannot read its tree.
    assert "--qf '[%{FILEMODES:octal} %{FILENAMES}\\n]'" in run
    assert "directories that are not 0755" in run
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    assert steps.index(verify) < steps.index(upload)
    assert upload["with"]["if-no-files-found"] == "error"


def test_declared_dependencies_are_only_the_host_packages(workflow):
    """The spec turns AutoReqProv off because the payload supplies its own
    libraries. If the workflow's guard stopped checking for soname requires,
    a rebuilt spec with discovery back on would publish an uninstallable
    package that still passed every other check."""
    run = _named(workflow, "build", "Verify the package before publishing it")["run"]
    assert "grep -q '^lib'" in run
    assert "AutoReqProv must stay off" in run


def test_the_workflow_never_creates_the_release_tag(workflow):
    """client-deb.yml refuses to run when the tag already exists. Creating one
    here would permanently block the .deb for this version."""
    run = _named(workflow, "attach-release", "Attach the .rpm to the existing release")["run"]
    commands = [
        line
        for line in run.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("releases/tags/" in line and "-X POST" in line for line in commands)
    assert not any("gh release create" in line for line in commands)
    # A missing release must fail loudly and name the fix, not create one.
    assert "does not exist; run client-deb.yml" in run


def test_release_does_not_overwrite_a_published_asset(workflow):
    steps = _steps(workflow, "attach-release")
    publish = _named(workflow, "attach-release", "Attach the .rpm to the existing release")
    commands = [
        line
        for line in publish["run"].splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # The job has no checkout, so repository context must be explicit; a bare
    # `gh release view` fails with "not a git repository", which reads the same
    # as "release missing" unless the command is redirected properly.
    assert not any("gh release view" in line for line in commands)
    assert any('gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${TAG}"' in line for line in commands)
    assert any('--repo "$GITHUB_REPOSITORY"' in line for line in commands)
    # `gh release upload` overwrites by default, and an upload is not undoable.
    assert not any("--clobber" in line for line in commands)
    assert "already published on $TAG" in publish["run"]
    # The existence check must run before the upload, not after it.
    guard = next(
        index
        for index, line in enumerate(commands)
        if "already published on $TAG" in line
    )
    upload = next(
        index for index, line in enumerate(commands) if "gh release upload" in line
    )
    assert guard < upload
    # The digest recorded by the build job is rechecked against the download.
    verify = _named(workflow, "attach-release", "Verify the artifact matches this run")
    assert "sha256sum -c rpm.sha256" in verify["run"]
    # --ignore-missing would turn a name mismatch into a silent pass.
    verify_commands = [
        line
        for line in verify["run"].splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("--ignore-missing" in line for line in verify_commands)
    assert steps.index(verify) < steps.index(publish)


def test_recorded_digest_is_relative_so_the_release_job_can_check_it(workflow):
    # The two jobs run on different machines; an absolute runner path can never
    # be found in attach-release and silently skips the comparison.
    verify = _named(workflow, "build", "Verify the package before publishing it")
    assert 'sha256sum "$(basename "$RPM")"' in verify["run"]


def test_all_run_blocks_have_valid_bash_syntax(workflow):
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)


def test_artifact_layout_is_flat_so_the_release_job_finds_the_rpm(workflow):
    # upload-artifact roots the archive at the least common ancestor of its
    # paths. Listing paths from different directories nests the rpm, so the
    # staging step must hand it a single flat directory instead.
    steps = _steps(workflow, "build")
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    assert upload["with"]["path"] == "${{ runner.temp }}/package"
    stage = _named(workflow, "build", "Stage the files to be uploaded")
    assert steps.index(stage) < steps.index(upload)
    assert "rpm.sha256" in stage["run"] and "identity.json" in stage["run"]
