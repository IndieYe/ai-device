"""Offline recovery invariants; never uploads or contacts GitHub/PyPI."""

import io
import json
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recover_release as recovery


@pytest.fixture
def original(tmp_path, monkeypatch):
    root = tmp_path / "release"
    for name in recovery.FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    import hashlib

    files = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in recovery.FILES
    }
    monkeypatch.setattr(recovery, "FILES", files)
    for name in recovery.REPORTS:
        (root / name).write_text("{}")
    monkeypatch.setattr(
        recovery,
        "REPORTS",
        {name: hashlib.sha256(b"{}").hexdigest() for name in recovery.REPORTS},
    )
    (root / "manifest.json").write_text(json.dumps(files))
    (root / "source-commit.txt").write_text(recovery.SOURCE + "\n")
    return root


def test_fixed_candidate(original):
    recovery.validate(original)


@pytest.mark.parametrize(
    "change", ["source", "manifest", "file", "report", "extra", "missing"]
)
def test_candidate_changes_fail_before_network(original, change, monkeypatch):
    if change == "source":
        (original / "source-commit.txt").write_text("0" * 40)
    elif change == "manifest":
        data = dict(recovery.FILES)
        data[next(iter(data))] = "0" * 64
        (original / "manifest.json").write_text(json.dumps(data))
    elif change == "file":
        (original / next(iter(recovery.FILES))).write_bytes(b"modified")
    elif change == "report":
        (original / "delivery-verification.json").write_text("changed")
    elif change == "extra":
        (original / "fs/extra").write_text("extra")
    else:
        (original / next(iter(recovery.FILES))).unlink()
    monkeypatch.setattr(
        recovery, "check_public", lambda *_: pytest.fail("network before validation")
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        recovery.preflight(original, "prepare")


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("prepare", ["public:bash", "absent:fs", "absent:device"]),
        ("fs", ["public:bash", "absent:fs", "absent:device"]),
        ("device", ["public:bash", "public:fs", "absent:device"]),
    ],
)
def test_preflight_order(original, monkeypatch, phase, expected):
    calls = []
    monkeypatch.setattr(
        recovery, "check_public", lambda _, group: calls.append("public:" + group)
    )
    monkeypatch.setattr(
        recovery, "absent", lambda group: calls.append("absent:" + group)
    )
    recovery.preflight(original, phase)
    assert calls == expected


@pytest.mark.parametrize("code", [401, 403, 429, 500, 404, None])
def test_absence_only_accepts_404(monkeypatch, code):
    def request(*args, **kwargs):
        if code is not None:
            raise urllib.error.HTTPError(args[0], code, "error", {}, None)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(recovery.urllib.request, "urlopen", request)
    if code == 404:
        recovery.absent("fs")
    else:
        with pytest.raises((ValueError, urllib.error.HTTPError)):
            recovery.absent("fs")


def test_network_failure_not_absent(monkeypatch):
    def request(*args, **kwargs):
        raise urllib.error.URLError("unavailable")

    monkeypatch.setattr(recovery.urllib.request, "urlopen", request)
    with pytest.raises(urllib.error.URLError):
        recovery.absent("device")


@pytest.mark.parametrize("extra", [None, "../escape", "fs/orphan", "duplicate"])
def test_zip_members_and_hash(original, tmp_path, monkeypatch, extra):
    import hashlib

    archive = tmp_path / "candidate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for file in original.rglob("*"):
            if file.is_file():
                bundle.write(file, str(file.relative_to(original)))
        if extra:
            bundle.writestr("manifest.json" if extra == "duplicate" else extra, "extra")
    target = tmp_path / "out"
    with pytest.raises(ValueError, match="ZIP hash"):
        recovery.extract(archive, target)
    assert not target.exists()
    monkeypatch.setattr(
        recovery, "ZIP_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    if extra:
        with pytest.raises(ValueError, match="member set"):
            recovery.extract(archive, target)
        assert not target.exists()
    else:
        recovery.extract(archive, target)
        recovery.validate(target)
        with pytest.raises(ValueError, match="must not exist"):
            recovery.extract(archive, target)


def test_commit_gate(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setattr(
        sys, "argv", ["recover_release.py", "validate", "--expected-commit", "b" * 40]
    )
    with pytest.raises(ValueError, match="workflow commit"):
        recovery.main()


def test_recovery_workflow_contract():
    root = Path(__file__).resolve().parents[1]
    data = yaml.load(
        (root / ".github/workflows/recover-release.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert set(data["on"]) == {"workflow_dispatch"}
    assert data["on"]["workflow_dispatch"]["inputs"]["publish"]["default"] == "false"
    assert data["concurrency"] == {
        "group": "pypi-release",
        "cancel-in-progress": "false",
    }
    jobs = data["jobs"]
    assert set(jobs) == {"prepare", "publish-fs", "publish-device"}
    assert jobs["publish-fs"]["needs"] == "prepare"
    assert jobs["publish-device"]["needs"] == ["prepare", "publish-fs"]
    for name, job in jobs.items():
        phase = name.removeprefix("publish-")
        assert "github.ref == 'refs/heads/main'" in job["if"]
        assert "github.repository == 'IndieYe/ai-device'" in job["if"]
        steps = job["steps"]
        uploads = [s for s in steps if "pypi-publish" in s.get("uses", "")]
        assert not any(s.get("continue-on-error") for s in steps)
        text = json.dumps(steps)
        assert "12471078590/zip" in text
        assert "recover_release.py extract" in text
        assert f"preflight --phase {phase}" in text
        assert "uv build" not in text and "skip-existing" not in text.replace(
            "no retry or skip-existing", ""
        )
        if phase == "prepare":
            assert not uploads and "id-token" not in job["permissions"]
        else:
            assert job["if"].startswith("inputs.publish &&")
            assert job["environment"]["name"] == "pypi-" + phase
            assert job["permissions"]["id-token"] == "write"
            assert len(uploads) == 1
            assert uploads[0]["with"] == {"packages-dir": f"release/{phase}/"}
            preflight = next(
                s for s in steps if "preflight --phase" in s.get("run", "")
            )
            public = next(
                s for s in steps if "release_artifacts.py public" in s.get("run", "")
            )
            assert (
                steps.index(preflight) < steps.index(uploads[0]) < steps.index(public)
            )
    assert (
        "test_recover_release.py" in (root / ".github/workflows/test.yml").read_text()
    )
