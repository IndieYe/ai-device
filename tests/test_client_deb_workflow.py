"""Static contract for the Linux client package workflow.

Covers the parts that would fail silently: the package must be built on an
amd64 runner, the download stage must not be handed a private credential path,
and the release job must stay opt-in. It does not prove GitHub Actions ran.

Run with: uv run --no-sync --with pyyaml python -m pytest tests/test_client_deb_workflow.py
"""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/client-deb.yml"


@pytest.fixture
def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


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
    assert any("build-client-deb.py" in r for r in runs)


def test_source_gate_precedes_every_build_step(workflow):
    steps = workflow["jobs"]["build"]["steps"]
    gate = next(s for s in steps if s.get("name") == "Check reviewed commit and version")
    assert gate["env"]["EXPECTED_COMMIT"] == "${{ inputs.expected_commit }}"
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"' in gate["run"]
    first_build = next(
        index
        for index, step in enumerate(steps)
        if "stage-client-inputs.py" in step.get("run", "")
    )
    assert steps.index(gate) < first_build


def test_package_is_verified_before_it_can_be_published(workflow):
    steps = workflow["jobs"]["build"]["steps"]
    verify = next(s for s in steps if s.get("name") == "Verify the package before publishing it")
    assert "dpkg-deb -c" in verify["run"]
    assert "drwxr-xr-x" in verify["run"]
    assert "dpkg-deb -I" in verify["run"]
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    assert steps.index(verify) < steps.index(upload)
    assert upload["with"]["if-no-files-found"] == "error"


def test_release_does_not_overwrite_a_published_version(workflow):
    steps = workflow["jobs"]["attach-release"]["steps"]
    publish = next(s for s in steps if s.get("name") == "Publish the release asset")
    assert "gh release view" in publish["run"]
    assert "bump the version instead of overwriting" in publish["run"]
    # The digest recorded by the build job is rechecked against the download.
    verify = next(s for s in steps if s.get("name") == "Verify the artifact matches this run")
    assert "sha256sum -c deb.sha256" in verify["run"]
    # --ignore-missing would turn a name mismatch into a silent pass, which is
    # exactly how this check was a no-op before. Compare against the executable
    # line only, so the explanatory comment above it cannot satisfy the check.
    commands = [
        line for line in verify["run"].splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("--ignore-missing" in line for line in commands)
    assert steps.index(verify) < steps.index(publish)


def test_recorded_digest_is_relative_so_the_release_job_can_check_it(workflow):
    # The two jobs run on different machines; an absolute runner path can never
    # be found in attach-release and silently skips the comparison.
    verify = next(
        s
        for s in workflow["jobs"]["build"]["steps"]
        if s.get("name") == "Verify the package before publishing it"
    )
    assert 'sha256sum "$(basename "$DEB")"' in verify["run"]


def test_release_points_at_the_reviewed_commit(workflow):
    publish = next(
        s
        for s in workflow["jobs"]["attach-release"]["steps"]
        if s.get("name") == "Publish the release asset"
    )
    assert '--target "$SOURCE_COMMIT"' in publish["run"]
    # The notes must carry a digest that is actually present, not a promise to
    # look at something the release does not contain.
    assert 'DIGEST="$(awk' in publish["run"]
    assert "release/deb.sha256" in publish["run"]


def test_all_run_blocks_have_valid_bash_syntax(workflow):
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)

def test_artifact_layout_is_flat_so_the_release_job_finds_the_deb(workflow):
    # upload-artifact roots the archive at the least common ancestor of its
    # paths. Listing paths from different directories nests the deb, so the
    # staging step must hand it a single flat directory instead.
    steps = workflow["jobs"]["build"]["steps"]
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    assert upload["with"]["path"] == "${{ runner.temp }}/package"
    stage = next(s for s in steps if s.get("name") == "Stage the files to be uploaded")
    assert steps.index(stage) < steps.index(upload)
    assert "deb.sha256" in stage["run"] and "identity.json" in stage["run"]
