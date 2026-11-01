"""Private runtime installation from a locked archive.

These tests cover the P3 path: an instance is installed from a real archive
instead of being handed a ready-made runtime directory. The archive is verified
against the lock before it is unpacked, so every assertion here runs the same
verification the native installation path uses.
"""

import hashlib
import io
import json
import tarfile
import zipfile

import pytest
from test_bundle import make_wheel

from device_runtime import installation as module
from device_runtime import native_runtime
from device_runtime.bundle import COMPONENTS, build_bundle
from device_runtime.installation import InstallationError, install_instance, rebuild_instance


@pytest.fixture
def bundle(tmp_path):
    wheels = {c: make_wheel(tmp_path, c) for c in COMPONENTS}
    wheel = wheels["device"]
    with zipfile.ZipFile(wheel) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    metadata = next(n for n in entries if n.endswith("/METADATA"))
    entries[metadata] += (
        b"Provides-Extra: client\n"
        b'Requires-Dist: ai-device-bash==0.2.0; extra == "client"\n'
        b'Requires-Dist: ai-device-fs==0.2.0; extra == "client"\n'
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    constraints = tmp_path / "pins.txt"
    constraints.write_text("pydantic==2.13.5\n")
    target = tmp_path / "bundle"
    build_bundle(
        target,
        release_version="0.2.0",
        wheels=wheels,
        constraints=constraints,
        sources={c: "a" * 40 for c in COMPONENTS},
    )
    return target


@pytest.fixture
def fake_uv(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/fake/uv")
    monkeypatch.setattr(module, "_invoke", lambda args, **kwargs: None)


@pytest.fixture
def locked_archive(tmp_path, monkeypatch):
    """A real archive plus a lock that pins it, honouring the extraction rules.

    The archive contains a shell stub so the instance can start without a real
    interpreter; the probe is replaced because only a genuine CPython would
    report the pinned version.
    """
    archive = tmp_path / "cpython.tar.gz"
    stub = b"#!/bin/sh\nexec /usr/bin/env python3 \"$@\"\n"
    with tarfile.open(archive, "w:gz") as out:
        for name, data, mode in [
            ("python", b"", 0o755),
            ("python/bin/python3.13", stub, 0o755),
        ]:
            entry = tarfile.TarInfo(name)
            entry.mode = mode
            entry.size = len(data)
            entry.type = tarfile.DIRTYPE if name == "python" else tarfile.REGTYPE
            out.addfile(entry, io.BytesIO(data) if name != "python" else None)
    lock = {
        "schema_version": 1,
        "implementation": "cpython",
        "upstream_release": "20261021",
        "python_version": "3.13.17",
        "license_url": "https://example.org/license",
        "targets": {
            "darwin-arm64": {
                "url": (
                    "https://github.com/astral-sh/python-build-standalone/"
                    "releases/download/20261021/cpython-fake.tar.gz"
                ),
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "size": archive.stat().st_size,
            }
        },
    }
    lock_path = tmp_path / "runtime-lock.json"
    lock_path.write_text(json.dumps(lock))
    monkeypatch.setattr(native_runtime, "_host_target", lambda: "darwin-arm64")
    monkeypatch.setattr(
        native_runtime, "_probe", lambda python, version: {"version": version}
    )
    return archive, lock_path


def install(bundle, root, **kwargs):
    return install_instance(
        bundle,
        root=root,
        instance_id="private-runtime",
        roles=("client",),
        server_url="http://127.0.0.1:28765",
        port=28765,
        **kwargs,
    )


def test_archive_installs_a_private_runtime_and_launcher(bundle, fake_uv, locked_archive, tmp_path):
    archive, lock = locked_archive
    root = tmp_path / "instance"
    value = install(bundle, root, runtime_archive=archive, runtime_lock=lock)

    private = root / "releases" / value["release"] / "runtime" / "bin" / "python3.13"
    assert private.is_file() and private.stat().st_mode & 0o111
    # The stable entry must be the shell launcher, not a single Python file: the
    # launcher resolves the interpreter per activation instead of pinning one.
    assert (root / "run").read_text() == module.LAUNCHER
    assert (root / "run-runner").read_text() == module.RUNNER_BODY
    assert (root / "current").read_text().strip() == value["release"]


def test_staging_directory_is_removed_after_success(bundle, fake_uv, locked_archive, tmp_path):
    archive, lock = locked_archive
    root = tmp_path / "instance"
    install(bundle, root, runtime_archive=archive, runtime_lock=lock)
    assert not list(tmp_path.glob(".instance.runtime-*"))


def test_staging_directory_is_removed_after_failure(bundle, fake_uv, locked_archive, tmp_path):
    archive, lock = locked_archive
    root = tmp_path / "instance"
    with pytest.raises(InstallationError):
        # An existing destination fails after the runtime was already staged, so
        # the cleanup path is exercised with a real prepared runtime on disk.
        root.mkdir()
        install(bundle, root, runtime_archive=archive, runtime_lock=lock)
    assert not list(tmp_path.glob(".instance.runtime-*"))


def test_archive_without_lock_is_refused(bundle, fake_uv, locked_archive, tmp_path):
    archive, _ = locked_archive
    with pytest.raises(InstallationError, match="requires its lock"):
        install(bundle, tmp_path / "instance", runtime_archive=archive)


def test_lock_without_archive_is_refused(bundle, fake_uv, locked_archive, tmp_path):
    _, lock = locked_archive
    with pytest.raises(InstallationError, match="only meaningful with an archive"):
        install(bundle, tmp_path / "instance", runtime_lock=lock)


def test_archive_and_source_are_mutually_exclusive(bundle, fake_uv, locked_archive, tmp_path):
    archive, lock = locked_archive
    source = tmp_path / "runtime"
    (source / "bin").mkdir(parents=True)
    with pytest.raises(InstallationError, match="either a runtime archive or a runtime source"):
        install(
            bundle,
            tmp_path / "instance",
            runtime_archive=archive,
            runtime_lock=lock,
            runtime_source=source,
        )


def test_tampered_archive_is_refused_before_extraction(bundle, fake_uv, locked_archive, tmp_path):
    archive, lock = locked_archive
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(InstallationError, match="size mismatch"):
        install(bundle, tmp_path / "instance", runtime_archive=archive, runtime_lock=lock)
    assert not (tmp_path / "instance").exists()


def test_mismatched_target_is_refused(bundle, fake_uv, locked_archive, tmp_path, monkeypatch):
    archive, lock = locked_archive
    monkeypatch.setattr(native_runtime, "_host_target", lambda: "linux-x86_64")
    with pytest.raises(InstallationError, match="target mismatch"):
        install(bundle, tmp_path / "instance", runtime_archive=archive, runtime_lock=lock)


def test_archive_must_be_a_plain_file(bundle, fake_uv, locked_archive, tmp_path):
    archive, lock = locked_archive
    link = tmp_path / "link.tar.gz"
    link.symlink_to(archive)
    with pytest.raises(InstallationError, match="plain file"):
        install(bundle, tmp_path / "instance", runtime_archive=link, runtime_lock=lock)


def test_rebuild_migrates_a_legacy_instance(bundle, fake_uv, locked_archive, tmp_path):
    """A pointer-less instance gains a private runtime and a launcher on rebuild."""
    archive, lock = locked_archive
    root = tmp_path / "instance"
    legacy = install(bundle, root)
    # The legacy form: a single Python file, no pointer, no private runtime.
    assert not (root / "current").exists()
    assert (root / "run").read_text() != module.LAUNCHER

    value = rebuild_instance(root, runtime_archive=archive, runtime_lock=lock)

    assert (root / "current").read_text().strip() == value["release"]
    assert (root / "run").read_text() == module.LAUNCHER
    assert (root / "run-runner").read_text() == module.RUNNER_BODY
    private = root / "releases" / value["release"] / "runtime" / "bin" / "python3.13"
    assert private.is_file()
    # The older release is retained; migration must not destroy the previous form.
    assert (root / "releases" / legacy["release"]).is_dir()


def test_rebuild_keeps_the_private_runtime_without_an_archive(bundle, fake_uv, locked_archive, tmp_path):
    archive, lock = locked_archive
    root = tmp_path / "instance"
    install(bundle, root, runtime_archive=archive, runtime_lock=lock)
    value = rebuild_instance(root)
    private = root / "releases" / value["release"] / "runtime" / "bin" / "python3.13"
    assert private.is_file()
    assert (root / "run").read_text() == module.LAUNCHER


def test_install_cli_accepts_the_archive_flags(bundle, locked_archive):
    archive, lock = locked_archive
    argparse = __import__("argparse")
    parser = argparse.ArgumentParser()
    module.add_instance_parser(parser.add_subparsers(dest="mode"))
    args = parser.parse_args(
        [
            "instance",
            "install",
            "--bundle",
            str(bundle),
            "--instance-id",
            "cli-check",
            "--role",
            "client",
            "--runtime-archive",
            str(archive),
            "--runtime-lock",
            str(lock),
        ]
    )
    assert args.runtime_archive == archive
    assert args.runtime_lock == lock

    rebuild = parser.parse_args(
        ["instance", "rebuild", "--root", "/tmp/x", "--runtime-archive", str(archive)]
    )
    assert rebuild.runtime_archive == archive
