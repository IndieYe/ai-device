"""Static release contract; not proof of remote Actions or OIDC permissions.

Run with: uv run --no-sync --with pyyaml python -m pytest tests/test_release_workflow.py
"""

import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


@pytest.fixture
def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def test_manual_default_and_source_gate(workflow):
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["publish"]["default"] == "false"
    assert inputs["expected_commit"]["required"] == "true"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    prepare = workflow["jobs"]["prepare"]
    assert prepare["if"] == (
        "github.repository == 'IndieYe/ai-device' && github.ref == 'refs/heads/main'"
    )
    gate = next(s for s in prepare["steps"] if s.get("name") == "Check reviewed commit")
    assert gate["env"]["EXPECTED_COMMIT"] == "${{ inputs.expected_commit }}"
    assert '[[ "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]]' in gate["run"]
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT"' in gate["run"]


@pytest.mark.parametrize(
    "group,environment,project,needs",
    [
        ("bash", "pypi", "ai-device-bash", "prepare"),
        ("fs", "pypi-fs", "ai-device-fs", ["prepare", "publish-bash"]),
        ("device", "pypi-device", "ai-device", ["prepare", "publish-fs"]),
    ],
)
def test_serial_scoped_publish_jobs(workflow, group, environment, project, needs):
    job = workflow["jobs"][f"publish-{group}"]
    assert (
        job["if"] == "inputs.publish"
    )  # Default success() blocks failed dependencies.
    assert job["needs"] == needs
    assert job["environment"] == {
        "name": environment,
        "url": f"https://pypi.org/p/{project}",
    }
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    steps = job["steps"]
    download = next(s for s in steps if "download-artifact" in s.get("uses", ""))
    assert download["with"] == {
        "name": "pypi-release-${{ github.sha }}",
        "path": "release/",
    }
    uploads = [s for s in steps if "pypi-publish" in s.get("uses", "")]
    assert len(uploads) == 1
    assert uploads[0]["with"] == {"packages-dir": f"release/{group}/"}
    check = next(
        s for s in steps if s.get("name") == "Verify immutable candidate before upload"
    )
    assert 'test "$(cat release/source-commit.txt)" = "$GITHUB_SHA"' in check["run"]
    assert "python scripts/release_artifacts.py check" in check["run"]
    assert steps.index(download) < steps.index(check) < steps.index(uploads[0])
    assert (
        steps[-1]["run"]
        == f"python scripts/release_artifacts.py public --group {group}"
    )
    assert not any(s.get("continue-on-error") for s in steps)
    assert not any("uv build" in s.get("run", "") for s in steps)


def test_prepare_is_only_builder_and_has_no_oidc(workflow):
    assert set(workflow["jobs"]) == {
        "prepare",
        "publish-bash",
        "publish-fs",
        "publish-device",
    }
    prepare = workflow["jobs"]["prepare"]
    assert "id-token" not in prepare.get("permissions", {})
    runs = [s.get("run", "") for s in prepare["steps"]]
    required = [
        "uv build --all-packages",
        "uv run --no-sync python scripts/verify_artifacts.py --fs-python 3.10",
        "uv run --no-sync python scripts/verify_delivery.py",
        "uv run --no-sync python scripts/release_artifacts.py stage",
    ]
    assert [runs.index(r) for r in required] == sorted(runs.index(r) for r in required)
    assert not any("pypi-publish" in s.get("uses", "") for s in prepare["steps"])


def test_all_run_blocks_have_valid_bash_syntax(workflow):
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
