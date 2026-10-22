"""Regression tests for the three independent stage-C review findings."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import capture_evidence as evidence
from verify_delivery import cleanup_process, execute


def candidate(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "artifacts").mkdir()
    for directory, name, version in [
        ("bash", "ai-device-bash", "0.2.0"),
        ("fs", "ai-device-fs", "0.2.0"),
        ("device-runtime", "ai-device", "0.2.2"),
    ]:
        path = tmp_path / "packages" / directory
        path.mkdir(parents=True)
        (path / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="{version}"\n'
        )
        for suffix in (".tar.gz", "-py3-none-any.whl"):
            (
                tmp_path / "dist" / (name.replace("-", "_") + "-" + version + suffix)
            ).write_bytes(b"fixture")
    hashes = {p.name: evidence.digest(p) for p in (tmp_path / "dist").iterdir()}
    delivery = {
        "artifacts": hashes,
        "minimal_roles": {key: {} for key in ["client", "center", "mcp", "ops"]},
        "lifecycle": dict.fromkeys(
            [
                "install_and_inspect",
                "stable_runner_center_http",
                "stable_runner_client_and_mcp",
                "running_lease_rejected",
                "prepare_activate_rebuild",
                "rollback_backup_restore",
                "invalid_release_keeps_active",
                "linux_target_role_rejected",
                "tampered_wheel_keeps_active",
            ],
            True,
        ),
    }
    artifact = {
        "input_artifacts": hashes,
        "rebuilt_wheels": {n: h for n, h in hashes.items() if n.endswith(".whl")},
        "scope": "test",
        "python": "3.13",
    }
    (tmp_path / "artifacts/delivery-verification.json").write_text(json.dumps(delivery))
    (tmp_path / "artifacts/artifact-verification.json").write_text(json.dumps(artifact))
    return hashes


def test_valid_candidate(tmp_path):
    expected = candidate(tmp_path)
    assert evidence.validate_candidate(tmp_path) == expected


@pytest.mark.parametrize(
    "mode", ["empty", "missing", "stale-delivery", "stale-artifact", "stale-rebuilt"]
)
def test_invalid_evidence_preserves_existing(tmp_path, monkeypatch, mode):
    candidate(tmp_path)
    old = tmp_path / "docs/evidence/candidate-manifest.json"
    old.parent.mkdir(parents=True)
    old.write_text("preserve")
    if mode in ("empty", "missing"):
        for p in list((tmp_path / "dist").iterdir())[: (6 if mode == "empty" else 1)]:
            p.unlink()
    else:
        report = "delivery" if mode == "stale-delivery" else "artifact"
        p = tmp_path / f"artifacts/{report}-verification.json"
        d = json.loads(p.read_text())
        if report == "delivery":
            d["artifacts"][next(iter(d["artifacts"]))] = "stale"
        else:
            key = "rebuilt_wheels" if mode == "stale-rebuilt" else "input_artifacts"
            d[key][next(iter(d[key]))] = "stale"
        p.write_text(json.dumps(d))
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        evidence.main()
    assert old.read_text() == "preserve"


def test_timeout_kills_owned_group(tmp_path):
    pidfile = tmp_path / "pid"
    code = f"import os,signal,time;open({str(pidfile)!r},'w').write(str(os.getpid()));signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        execute([sys.executable, "-c", code], tmp_path, {}, timeout=0.3)
    import os

    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_cleanup_already_exited():
    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    p.wait(timeout=5)
    cleanup_process(p, grace=0.1)
