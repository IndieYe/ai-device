"""Offline native runtime validation without using external downloads."""

import hashlib
import io
import json
import tarfile

import pytest

from device_runtime import native_runtime as nr


@pytest.fixture
def runtime_input(tmp_path, monkeypatch):
    archive = tmp_path / "python.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        for name, data in [("python/bin/python3.13", b"fixture executable")]:
            entry = tarfile.TarInfo(name)
            entry.mode = 0o755
            entry.size = len(data)
            out.addfile(entry, io.BytesIO(data))
    lock = {
        "schema_version": 1,
        "implementation": "cpython",
        "upstream_release": "20261021",
        "python_version": "3.13.17",
        "license_url": "https://example.org/license",
        "targets": {
            "darwin-arm64": {
                "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20261021/python.tar.gz",
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "size": archive.stat().st_size,
            }
        },
    }
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    monkeypatch.setattr(nr, "_host_target", lambda: "darwin-arm64")
    return archive, path, lock


def test_prepare_probes_before_and_after_relocation(tmp_path, monkeypatch, runtime_input):
    archive, path, _ = runtime_input
    probes = []

    def probe(python, version):
        assert python.read_bytes() == b"fixture executable"
        probes.append(python)
        return {"version": version}

    monkeypatch.setattr(nr, "_probe", probe)
    destination = tmp_path / "final"
    report = nr.prepare_runtime(archive, destination, lock_path=path, target="darwin-arm64")
    assert destination.joinpath("bin/python3.13").is_file()
    assert len(report["probes"]) == 2 and probes[0] != probes[1]
    assert not list(tmp_path.glob(".native-runtime-*"))
    with pytest.raises(nr.RuntimePreparationError, match="already exists"):
        nr.prepare_runtime(archive, destination, lock_path=path, target="darwin-arm64")


@pytest.mark.parametrize("change", ["size", "sha256", "host"])
def test_mismatch_never_publishes(tmp_path, runtime_input, change):
    archive, path, lock = runtime_input
    if change == "host":
        target = "linux-x86_64"
    else:
        target = "darwin-arm64"
        lock["targets"][target][change] = 1 if change == "size" else "0" * 64
        path.write_text(json.dumps(lock))
    destination = tmp_path / "final"
    with pytest.raises(nr.RuntimePreparationError, match="mismatch"):
        nr.prepare_runtime(archive, destination, lock_path=path, target=target)
    assert not destination.exists()


def test_failed_relocated_probe_cleans_candidate(tmp_path, monkeypatch, runtime_input):
    archive, path, _ = runtime_input

    def probe(python, version):
        if "relocated" in python.parts:
            raise nr.RuntimePreparationError("not relocatable")
        return {}

    monkeypatch.setattr(nr, "_probe", probe)
    with pytest.raises(nr.RuntimePreparationError, match="not relocatable"):
        nr.prepare_runtime(archive, tmp_path / "final", lock_path=path, target="darwin-arm64")
    assert not (tmp_path / "final").exists()
    assert not list(tmp_path.glob(".native-runtime-*"))


@pytest.mark.parametrize(
    "name,kind,link",
    [
        ("../escape", tarfile.REGTYPE, ""),
        ("/python/escape", tarfile.REGTYPE, ""),
        ("python/dev", tarfile.CHRTYPE, ""),
        ("python/bin/escape", tarfile.SYMTYPE, "../../../escape"),
    ],
)
def test_reject_unsafe_archive(tmp_path, runtime_input, name, kind, link):
    archive, path, lock = runtime_input
    with tarfile.open(archive, "w:gz") as out:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = link
        out.addfile(entry)
    lock["targets"]["darwin-arm64"].update(
        size=archive.stat().st_size,
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
    )
    path.write_text(json.dumps(lock))
    with pytest.raises(nr.RuntimePreparationError):
        nr.prepare_runtime(archive, tmp_path / "final", lock_path=path, target="darwin-arm64")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "final").exists()


def test_invalid_lock(tmp_path):
    path = tmp_path / "lock.json"
    path.write_text('{"schema_version":2}')
    with pytest.raises(nr.RuntimePreparationError, match="invalid runtime lock"):
        nr.load_lock(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("python_version", "3.13"),
        ("upstream_release", "latest"),
        ("targets", []),
    ],
)
def test_lock_requires_exact_build(runtime_input, field, value):
    _, path, lock = runtime_input
    lock[field] = value
    path.write_text(json.dumps(lock))
    with pytest.raises(nr.RuntimePreparationError):
        nr.load_lock(path)


def test_runtime_lock_pins_both_release_assets():
    from pathlib import Path

    path = Path(__file__).parents[1] / "native/runtime-lock.json"
    lock = nr.load_lock(path)
    assert set(lock["targets"]) == {"darwin-arm64", "linux-x86_64"}
    for entry in lock["targets"].values():
        assert lock["python_version"] in entry["url"]
        assert lock["upstream_release"] in entry["url"]


def test_destination_race_never_overwrites(tmp_path, monkeypatch, runtime_input):
    archive, path, _ = runtime_input
    destination = tmp_path / "final"

    def probe(python, version):
        if "relocated" in python.parts:
            destination.mkdir()
            (destination / "other-owner").write_text("preserve")
        return {}

    monkeypatch.setattr(nr, "_probe", probe)
    with pytest.raises(nr.RuntimePreparationError, match="already exists"):
        nr.prepare_runtime(archive, destination, lock_path=path, target="darwin-arm64")
    assert (destination / "other-owner").read_text() == "preserve"


def test_probe_validates_interpreter_version(tmp_path):
    executable = tmp_path / "fake-python"
    executable.write_text('#!/bin/sh\necho \'{"version":"3.12.1","implementation":"CPython"}\'\n')
    executable.chmod(0o755)
    with pytest.raises(nr.RuntimePreparationError, match="locked CPython"):
        nr._probe(executable, "3.13.17")


@pytest.mark.parametrize("mode", ["exit", "timeout", "invalid-json"])
def test_probe_failure_removes_staging(tmp_path, monkeypatch, runtime_input, mode):
    import subprocess

    archive, path, _ = runtime_input

    def run(*args, **kwargs):
        if mode == "exit":
            raise subprocess.CalledProcessError(1, args[0])
        if mode == "timeout":
            raise subprocess.TimeoutExpired(args[0], 30)
        return subprocess.CompletedProcess(args[0], 0, stdout="not-json")

    monkeypatch.setattr(nr.subprocess, "run", run)
    with pytest.raises(nr.RuntimePreparationError):
        nr.prepare_runtime(archive, tmp_path / "final", lock_path=path, target="darwin-arm64")
    assert not (tmp_path / "final").exists()
    assert not list(tmp_path.glob(".native-runtime-*"))


def test_prepare_keeps_internal_python_symlinks(tmp_path, monkeypatch, runtime_input):
    archive, path, lock = runtime_input
    with tarfile.open(archive, "w:gz") as out:
        target = tarfile.TarInfo("python/bin/python3.13")
        target.mode = 0o755
        target.size = 3
        out.addfile(target, io.BytesIO(b"bin"))
        link = tarfile.TarInfo("python/bin/python3")
        link.type = tarfile.SYMTYPE
        link.linkname = "python3.13"
        out.addfile(link)
    lock["targets"]["darwin-arm64"].update(
        size=archive.stat().st_size, sha256=hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    path.write_text(json.dumps(lock))
    monkeypatch.setattr(nr, "_probe", lambda *a: {})
    destination = tmp_path / "final"
    nr.prepare_runtime(archive, destination, lock_path=path, target="darwin-arm64")
    assert (destination / "bin/python3").read_bytes() == b"bin"
    assert (destination / "bin/python3").is_symlink()
