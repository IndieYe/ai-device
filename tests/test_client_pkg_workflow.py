"""Static contract for the macOS client PKG package workflow.

Covers the parts that would fail silently: the package must be built on a native
macOS arm64 runner, unsigned builds must never reach GitHub Release, signing
credentials must be cleaned up unconditionally, the release job must never
create the tag owned by client-deb.yml or overwrite an existing asset, and
every shell step must parse cleanly. It does not prove GitHub Actions ran.

Run with: uv run --no-sync --with pyyaml python -m pytest tests/test_client_pkg_workflow.py
"""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/client-pkg.yml"


@pytest.fixture
def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def _steps(workflow, job):
    return workflow["jobs"][job]["steps"]


def _named(workflow, job, name):
    return next(s for s in _steps(workflow, job) if s.get("name") == name)


def test_manual_only_opted_in_release_and_signed_by_default(workflow):
    assert set(workflow["on"]) == {"workflow_dispatch"}
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["expected_commit"]["required"] == "true"
    assert inputs["version"]["required"] == "true"
    assert inputs["sign_and_notarize"]["default"] == "true"
    assert inputs["publish_release"]["default"] == "false"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["attach-release"]["if"] == "inputs.publish_release"
    assert workflow["jobs"]["attach-release"]["permissions"] == {"contents": "write"}


def test_builds_on_macos_arm64_using_existing_scripts(workflow):
    build = workflow["jobs"]["build"]
    assert build["runs-on"] == "macos-latest"
    assert build["if"] == (
        "github.repository == 'IndieYe/ai-device' && github.ref == 'refs/heads/main'"
    )
    gate = _named(
        workflow, "build", "Check reviewed commit, architecture, version and release gates"
    )["run"]
    assert 'test "$(uname -s)" = "Darwin"' in gate
    assert 'test "$(uname -m)" = "arm64"' in gate
    assert "publish_release=true requires sign_and_notarize=true" in gate
    runs = [s.get("run", "") for s in build["steps"]]
    assert any("stage-client-inputs.py" in r for r in runs)
    assert any("build-client-tree.py" in r for r in runs)
    assert any("build-client-pkg.py" in r for r in runs)


def test_signing_step_checks_secrets_passes_keychain_and_cleans_up_always(workflow):
    steps = _steps(workflow, "build")
    import_step = _named(
        workflow, "build", "Import Apple signing certificates and notary credentials"
    )
    assert import_step["if"] == "inputs.sign_and_notarize"
    for secret in (
        "MACOS_SIGNING_P12_BASE64",
        "MACOS_SIGNING_P12_PASSWORD",
        "MACOS_NOTARY_KEY_P8_BASE64",
        "MACOS_NOTARY_KEY_ID",
        "MACOS_NOTARY_ISSUER_ID",
    ):
        assert secret in import_step["env"]
        assert secret in import_step["run"]
    assert "notarytool store-credentials" in import_step["run"]

    package_step = _named(workflow, "build", "Package the .pkg")
    assert "--mode shipping" in package_step["run"]
    assert "--sign-identity" in package_step["run"]
    assert "--installer-identity" in package_step["run"]
    assert "--notary-profile" in package_step["run"]
    assert "--notary-keychain" in package_step["run"]

    cleanup = _named(
        workflow, "build", "Clean up temporary signing keychain and credentials"
    )
    assert cleanup["if"] == "always()"
    assert "security delete-keychain" in cleanup["run"]
    assert steps.index(import_step) < steps.index(package_step) < steps.index(cleanup)


def test_verify_runs_before_upload_and_checks_signature_staple_spctl_and_identity(workflow):
    steps = _steps(workflow, "build")
    verify = _named(workflow, "build", "Verify the package before publishing it")
    run = verify["run"]
    assert "pkgutil --expand" in run
    assert "pkgutil --check-signature" in run
    assert "xcrun stapler validate" in run
    assert "spctl -a -vvv -t install" in run
    assert "signing-identity.json" in run
    assert 'shasum -a 256 "$(basename "$PKG")"' in run
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    assert steps.index(verify) < steps.index(upload)
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "${{ runner.temp }}/package"
    stage = _named(workflow, "build", "Stage the files to be uploaded")
    assert steps.index(stage) < steps.index(upload)
    assert "pkg.sha256" in stage["run"] and "identity.json" in stage["run"]


def test_release_never_creates_tag_overwrites_asset_or_publishes_unsigned(workflow):
    steps = _steps(workflow, "attach-release")
    verify = _named(workflow, "attach-release", "Verify the artifact matches this run")
    assert "UNSIGNED.txt" in verify["run"]
    assert "sha256sum -c pkg.sha256" in verify["run"]
    verify_commands = [
        line
        for line in verify["run"].splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("--ignore-missing" in line for line in verify_commands)

    publish = _named(workflow, "attach-release", "Attach the .pkg to the existing release")
    commands = [
        line
        for line in publish["run"].splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("releases/tags/" in line and "-X POST" in line for line in commands)
    assert not any("gh release create" in line for line in commands)
    assert not any("gh release view" in line for line in commands)
    assert not any("--clobber" in line for line in commands)
    assert "does not exist; run client-deb.yml" in publish["run"]
    assert any('gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${TAG}"' in line for line in commands)
    assert any('--repo "$GITHUB_REPOSITORY"' in line for line in commands)
    guard = next(
        index
        for index, line in enumerate(commands)
        if "already published on $TAG" in line
    )
    upload = next(
        index for index, line in enumerate(commands) if "gh release upload" in line
    )
    assert guard < upload
    assert steps.index(verify) < steps.index(publish)


def test_all_shell_steps_parse_with_bash_n(workflow):
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
