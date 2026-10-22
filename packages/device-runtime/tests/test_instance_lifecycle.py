"""Lifecycle tests use only pytest-created instances; no real device or service."""

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from test_installation import REAL_INVOKE, create, persistent
from test_installation import delivery as delivery  # noqa: PLC0414 -- pytest fixture registration
from test_installation import (
    extra_bundle as extra_bundle,  # noqa: PLC0414 -- pytest fixture registration
)
from test_installation import (
    metadata_environment as metadata_environment,  # noqa: PLC0414 -- pytest fixture registration
)

from device_runtime import cli
from device_runtime import installation as install
from device_runtime import lifecycle as life
from device_runtime.bundle import build_bundle, inspect_bundle
from device_runtime.http_client import DeviceAPI
from device_runtime.installation import InstallationError


@pytest.fixture
def simulated_installer(monkeypatch):
    calls = []
    monkeypatch.setattr(install.sys, "platform", "darwin")
    monkeypatch.setattr(install.shutil, "which", lambda _: "/fake/uv")

    def invoke(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:3] == ["--no-config", "venv"]:
            python = Path(args[-1]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("# simulated interpreter; never executed\n")

    monkeypatch.setattr(install, "_invoke", invoke)
    return calls


@pytest.fixture
def instance(tmp_path, delivery, simulated_installer):
    root = tmp_path / "instance"
    create(root, delivery, roles=("center", "client", "mcp"))
    (root / "state" / "client" / "tasks").mkdir()
    (root / "state" / "client" / "tasks" / "output.bin").write_bytes(b"task output\x00\xff")
    (root / "state" / "center" / "receipt.txt").write_text("unknown operation, never replay")
    return root


@pytest.fixture
def upgraded(tmp_path, delivery):
    manifest = inspect_bundle(delivery)
    other = tmp_path / "different-bundle"
    build_bundle(
        other,
        release_version="0.2.0.post1",
        wheels={c: delivery / m["path"] for c, m in manifest["components"].items()},
        constraints=delivery / "constraints.txt",
        sources={c: "b" * 40 for c in manifest["components"]},
    )
    return other


def all_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def active(root):
    return json.loads((root / "active.json").read_bytes())


def prepare(root, bundle):
    return life.prepare_upgrade(root, bundle, stopped=True)["release"]


def backup(root, destination):
    return life.backup_instance(root, destination, stopped=True)


def assert_untouched(root, pointer, data):
    assert (root / "active.json").read_bytes() == pointer
    assert persistent(root) == data


def test_diagnosis_is_read_only_and_distinguishes_health(instance, simulated_installer):
    before = all_bytes(instance)
    simulated_installer.clear()
    result = life.inspect_instance(instance)
    assert result["release"] == active(instance)["release"]
    assert result["release_version"] == "0.2.0"
    assert result["component_versions"] == {c: "0.2.0" for c in ("device", "bash", "fs")}
    assert result["roles"] == ["center", "client", "mcp"]
    assert result["bundle_sha256"] == active(instance)["bundle_sha256"]
    assert result["installation"] == "complete"
    assert result["persistent"] == "complete"
    assert result["environments"] == "available"
    assert result["online_health"] == {"status": "not_checked"}
    assert result["lock"] == "free"
    assert all_bytes(instance) == before
    assert len(simulated_installer) == 3
    assert all(kwargs["log"] is None and "-B" in argv for argv, kwargs in simulated_installer)
    assert "server_url" not in json.dumps(result)
    assert "unknown operation" not in json.dumps(result)


@pytest.mark.parametrize("damage", ["missing_python", "broken_environment"])
def test_diagnosis_environment_not_just_file_existence(instance, monkeypatch, damage):
    release = instance / "releases" / active(instance)["release"]
    if damage == "missing_python":
        (release / "client" / "venv" / "bin" / "python").unlink()
    else:
        monkeypatch.setattr(
            install,
            "_verify_environment",
            lambda *a, **k: (_ for _ in ()).throw(InstallationError("broken")),
        )
    before = all_bytes(instance)
    result = life.inspect_instance(instance)
    assert result["environments"] == "unavailable"
    assert result["installation"] == "complete"
    assert result["persistent"] == "complete"
    assert all_bytes(instance) == before


@pytest.mark.parametrize(
    "damage", ["identity", "config", "state", "active", "roles", "bundle", "lease_link"]
)
def test_diagnosis_bad_metadata_is_controlled_and_read_only(instance, tmp_path, damage):
    if damage == "identity":
        (instance / "instance.json").write_text("invalid")
    elif damage == "config":
        (instance / "config" / "roles.json").unlink()
    elif damage == "state":
        shutil.rmtree(instance / "state" / "client")
    elif damage == "active":
        (instance / "active.json").write_text("[]")
    elif damage == "roles":
        value = active(instance)
        value["roles"] = ["../../other"]
        install._json(instance / "active.json", value)
    elif damage == "bundle":
        release = instance / "releases" / active(instance)["release"]
        (release / "bundle" / "constraints.txt").write_text("broken")
    else:
        outside = tmp_path / "outside-lock"
        outside.write_text("unchanged")
        (instance / ".installation.lock").unlink()
        (instance / ".installation.lock").symlink_to(outside)
    before = all_bytes(instance)
    result = life.inspect_instance(instance)
    assert result["installation"] == "unavailable"
    assert all_bytes(instance) == before


def test_missing_instance_diagnosis_creates_nothing(tmp_path):
    root = tmp_path / "absent"
    assert life.inspect_instance(root)["installation"] == "unavailable"
    assert not root.exists()
    root.mkdir()
    assert life.inspect_instance(root)["lock"] == "unavailable"
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "mode,status", [(fcntl.LOCK_SH, "shared_occupied"), (fcntl.LOCK_EX, "exclusive_occupied")]
)
def test_real_lock_occupancy_and_write_rejection(instance, upgraded, tmp_path, mode, status):
    before = all_bytes(instance)
    with (instance / ".installation.lock").open("rb") as stream:
        fcntl.flock(stream, mode | fcntl.LOCK_NB)
        assert life.inspect_instance(instance)["lock"] == status
        operations = [
            lambda: prepare(instance, upgraded),
            lambda: life.activate_release(instance, "missing", stopped=True),
            lambda: life.rollback_release(instance, active(instance)["release"], stopped=True),
            lambda: backup(instance, tmp_path / "backup"),
        ]
        for operation in operations:
            with pytest.raises(InstallationError, match="running|another"):
                operation()
    assert all_bytes(instance) == before
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("operation", ["prepare", "activate", "rollback", "backup"])
def test_explicit_stopped_confirmation_is_required(instance, upgraded, tmp_path, operation):
    before = all_bytes(instance)
    callbacks = {
        "prepare": lambda: life.prepare_upgrade(instance, upgraded),
        "activate": lambda: life.activate_release(instance, "missing"),
        "rollback": lambda: life.rollback_release(instance, active(instance)["release"]),
        "backup": lambda: life.backup_instance(instance, tmp_path / "backup"),
    }
    with pytest.raises(InstallationError, match="--stopped"):
        callbacks[operation]()
    assert all_bytes(instance) == before


def test_prepare_activate_and_rollback_preserve_later_journal(instance, upgraded):
    original = active(instance)
    pointer = (instance / "active.json").read_bytes()
    data = persistent(instance)
    candidate = prepare(instance, upgraded)
    assert_untouched(instance, pointer, data)
    assert candidate != original["release"]
    prepared = life._read(instance / "releases" / candidate / "candidate.json")
    assert prepared["binding"]["active"] == original
    assert prepared["candidate"]["bundle_sha256"] != original["bundle_sha256"]
    life.activate_release(instance, candidate, stopped=True)
    assert active(instance)["release"] == candidate
    assert persistent(instance) == data
    later = instance / "state" / "center" / "later-journal-record"
    later.write_text("new operation: do not revert or replay")
    data = persistent(instance)
    life.rollback_release(instance, original["release"], stopped=True)
    assert active(instance)["release"] == original["release"]
    assert persistent(instance) == data
    assert later.read_text() == "new operation: do not revert or replay"


@pytest.mark.parametrize("change", ["switch", "aba", "config", "identity"])
def test_stale_preparation_is_rejected(instance, upgraded, change):
    original = active(instance)
    first = prepare(instance, upgraded)
    if change in ("switch", "aba"):
        second = prepare(instance, upgraded)
        life.activate_release(instance, second, stopped=True)
        if change == "aba":
            life.rollback_release(instance, original["release"], stopped=True)
    elif change == "config":
        config = life._read(instance / "config" / "roles.json")
        config["center"]["port"] += 1
        install._json(instance / "config" / "roles.json", config)
    else:
        identity = life._read(instance / "instance.json")
        identity["annotation"] = "changed"
        install._json(instance / "instance.json", identity)
    pointer, data = (instance / "active.json").read_bytes(), persistent(instance)
    with pytest.raises(InstallationError, match="stale"):
        life.activate_release(instance, first, stopped=True)
    assert_untouched(instance, pointer, data)


@pytest.mark.parametrize("target", ["../escape", "/absolute", "..", "missing"])
def test_release_selection_is_not_an_arbitrary_path(instance, target):
    before = all_bytes(instance)
    for operation in (life.activate_release, life.rollback_release):
        with pytest.raises(InstallationError):
            operation(instance, target, stopped=True)
    assert all_bytes(instance) == before


@pytest.mark.parametrize(
    "damage", ["bundle", "record", "roles", "interpreter", "candidate", "future_state"]
)
@pytest.mark.parametrize("action", ["activate", "rollback"])
def test_invalid_release_cannot_publish(instance, upgraded, monkeypatch, damage, action):
    candidate = prepare(instance, upgraded)
    directory = instance / "releases" / candidate
    if damage == "bundle":
        (directory / "bundle" / "constraints.txt").write_text("corrupt")
    elif damage == "record":
        (directory / "installation.json").write_text("{}")
    elif damage == "roles":
        record = life._read(directory / "installation.json")
        record["roles"] = ["center"]
        install._json(directory / "installation.json", record)
    elif damage == "interpreter":
        (directory / "client" / "venv" / "bin" / "python").unlink()
    elif damage == "candidate":
        (directory / "candidate.json").write_text("{}")
        if action == "rollback":
            # An existing complete software release needs no upgrade preparation.
            data = persistent(instance)
            life.rollback_release(instance, candidate, stopped=True)
            assert active(instance)["release"] == candidate
            assert persistent(instance) == data
            return
    else:
        monkeypatch.setattr(life, "STATE_COMPATIBILITY", {})
    pointer, data = (instance / "active.json").read_bytes(), persistent(instance)
    with pytest.raises(InstallationError):
        getattr(life, action + "_release")(instance, candidate, stopped=True)
    assert_untouched(instance, pointer, data)


@pytest.mark.parametrize("failure", ["prepare", "verify", "publish", "candidate_metadata"])
def test_failures_retain_candidate_without_overwriting_active(
    instance, upgraded, monkeypatch, failure
):
    pointer, data = (instance / "active.json").read_bytes(), persistent(instance)
    releases = set((instance / "releases").iterdir())
    if failure == "prepare":
        monkeypatch.setattr(
            install,
            "_invoke",
            lambda *a, **k: (_ for _ in ()).throw(InstallationError("prepare failed")),
        )
        with pytest.raises(InstallationError):
            prepare(instance, upgraded)
    elif failure == "candidate_metadata":
        original = install._json

        def write(path, value):
            if path.name == "candidate.json":
                raise OSError("injected metadata failure")
            original(path, value)

        monkeypatch.setattr(install, "_json", write)
        with pytest.raises(InstallationError):
            prepare(instance, upgraded)
    else:
        candidate = prepare(instance, upgraded)
        if failure == "verify":
            monkeypatch.setattr(
                install,
                "_verify_environment",
                lambda *a, **k: (_ for _ in ()).throw(InstallationError("bad closure")),
            )
        else:
            original = install._json

            def write(path, value):
                if path.name == "active.json":
                    raise OSError("injected publication failure")
                original(path, value)

            monkeypatch.setattr(install, "_json", write)
        with pytest.raises(InstallationError):
            life.activate_release(instance, candidate, stopped=True)
    assert set((instance / "releases").iterdir()) > releases
    assert_untouched(instance, pointer, data)


@pytest.mark.parametrize("damage", ["identity", "config", "state", "run", "active"])
def test_missing_persistent_data_is_not_reinitialized(instance, upgraded, tmp_path, damage):
    paths = {
        "identity": "instance.json",
        "config": "config",
        "state": "state/client",
        "run": "run",
        "active": "active.json",
    }
    path = instance / paths[damage]
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    before = all_bytes(instance)
    for operation in (
        lambda: prepare(instance, upgraded),
        lambda: life.rollback_release(instance, "missing", stopped=True),
        lambda: backup(instance, tmp_path / "backup"),
    ):
        with pytest.raises(InstallationError):
            operation()
    assert all_bytes(instance) == before
    assert not (tmp_path / "backup").exists()


def test_backup_and_restore_complete_data_without_original_venvs(
    instance, tmp_path, simulated_installer
):
    destination = tmp_path / "backup"
    original = active(instance)
    source = all_bytes(instance)
    data = persistent(instance)
    backup(instance, destination)
    assert all_bytes(instance) == source
    assert not list(destination.rglob("venv"))
    assert (destination / "complete.json").is_file()
    assert all(p.stat().st_mode & 0o077 == 0 for p in destination.rglob("*"))
    assert destination.stat().st_mode & 0o077 == 0
    shutil.rmtree(instance / "releases")  # Recovery cannot depend on old environments.
    restored = tmp_path / "restored"
    simulated_installer.clear()
    result = life.restore_instance(destination, restored)
    assert result["status"] == "restored_not_started"
    assert persistent(restored) == data
    assert active(restored)["bundle_sha256"] == original["bundle_sha256"]
    assert active(restored)["release"] != original["release"]
    assert life.inspect_instance(restored)["environments"] == "available"
    assert len([a for a, _ in simulated_installer if a[1:3] == ["--no-config", "venv"]]) == 3
    assert (restored / "run").stat().st_mode & 0o100


@pytest.mark.parametrize(
    "destination", ["existing_empty", "existing_data", "inside", "parent", "symlink"]
)
def test_backup_destination_rejections(instance, tmp_path, destination):
    if destination == "inside":
        target = instance / "nested" / "backup"
    elif destination == "parent":
        target = instance.parent
    else:
        target = tmp_path / "target"
        if destination == "symlink":
            target.symlink_to(tmp_path / "missing-target", target_is_directory=True)
        else:
            target.mkdir()
            if destination == "existing_data":
                (target / "important").write_text("retain")
    source = all_bytes(instance)
    with pytest.raises(InstallationError):
        backup(instance, target)
    assert all_bytes(instance) == source
    if destination == "existing_data":
        assert (target / "important").read_text() == "retain"
    if destination == "inside":
        assert not (instance / "nested").exists()


@pytest.mark.parametrize("link", ["config", "state", "run", "bundle", "special"])
def test_snapshot_rejects_symlinks_and_special_files(instance, tmp_path, link):
    outside = tmp_path / "outside-secret"
    outside.write_text("private test payload")
    if link in ("config", "state"):
        (instance / link / "escape").symlink_to(outside)
    elif link == "run":
        (instance / "run").unlink()
        (instance / "run").symlink_to(outside)
    elif link == "bundle":
        (instance / "releases" / active(instance)["release"] / "bundle" / "escape").symlink_to(
            outside
        )
    else:
        os.mkfifo(instance / "state" / "fifo")
    with pytest.raises(InstallationError):
        backup(instance, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()
    assert outside.read_text() == "private test payload"


@pytest.mark.parametrize("failure", ["copy", "source_changed", "completion"])
def test_failed_backup_has_no_usable_completion(instance, tmp_path, monkeypatch, failure):
    target = tmp_path / "backup"
    source = all_bytes(instance)
    if failure in ("copy", "source_changed"):
        original = life._copy

        def copy(src, dest):
            if failure == "copy":
                raise OSError("copy failed")
            original(src, dest)
            (instance / "state" / "late-writer").write_text("changed")

        monkeypatch.setattr(life, "_copy", copy)
    else:
        original = install._json

        def write(path, value):
            if path.name == "complete.json":
                raise OSError("completion failed")
            original(path, value)

        monkeypatch.setattr(install, "_json", write)
    with pytest.raises(InstallationError):
        backup(instance, target)
    assert target.is_dir()
    assert not (target / "complete.json").exists()
    with pytest.raises(InstallationError):
        life.restore_instance(target, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    if failure != "source_changed":
        assert all_bytes(instance) == source


def test_wal_shm_snapshot_is_consistent_and_byte_preserved(instance, tmp_path):
    producer = tmp_path / "producer.sqlite"
    db = sqlite3.connect(producer)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE receipts (value TEXT)")
        db.execute("INSERT INTO receipts VALUES ('only present in WAL')")
        db.commit()
        for path in tmp_path.glob("producer.sqlite*"):
            shutil.copy2(path, instance / "state" / "center" / path.name)
    finally:
        db.close()
    data = persistent(instance)
    target = tmp_path / "backup"
    backup(instance, target)
    assert (target / "state" / "center" / "producer.sqlite-wal").stat().st_size > 0
    assert (target / "state" / "center" / "producer.sqlite-shm").is_file()
    assert persistent(instance) == data
    restored = tmp_path / "restored"
    life.restore_instance(target, restored)
    assert persistent(restored) == data
    # Open an expendable clone, never the preserved recovery data.
    clone = tmp_path / "sql-check"
    shutil.copytree(restored / "state" / "center", clone)
    with sqlite3.connect(clone / "producer.sqlite") as check:
        assert check.execute("SELECT value FROM receipts").fetchall() == [("only present in WAL",)]


def test_corrupt_sqlite_prevents_completed_backup(instance, tmp_path):
    (instance / "state" / "center" / "bad.sqlite").write_bytes(
        b"SQLite format 3\x00" + b"broken" * 100
    )
    target = tmp_path / "backup"
    with pytest.raises(InstallationError):
        backup(instance, target)
    assert target.exists()
    assert not (target / "complete.json").exists()


@pytest.mark.parametrize(
    "damage",
    [
        "missing_completion",
        "bad_manifest",
        "bad_hash",
        "state",
        "bundle",
        "extra",
        "symlink",
        "format",
    ],
)
def test_restore_rejects_incomplete_or_corrupt_backup_before_target(instance, tmp_path, damage):
    target = tmp_path / "backup"
    backup(instance, target)
    if damage == "missing_completion":
        (target / "complete.json").unlink()
    elif damage == "bad_manifest":
        (target / "backup.json").write_text("{}")
    elif damage == "bad_hash":
        complete = life._read(target / "complete.json")
        complete["manifest_sha256"] = "0" * 64
        install._json(target / "complete.json", complete)
    elif damage == "state":
        (target / "state" / "client" / "tasks" / "output.bin").write_bytes(b"changed")
    elif damage == "bundle":
        (target / "bundle" / "constraints.txt").write_text("changed")
    elif damage == "extra":
        (target / "unlisted-file").write_text("unexpected")
    elif damage == "symlink":
        (target / "state" / "escape").symlink_to(instance / "instance.json")
    else:
        manifest = life._read(target / "backup.json")
        manifest["state_format"] = 2
        install._json(target / "backup.json", manifest)
        install._json(
            target / "complete.json",
            {
                "format_version": 1,
                "manifest_sha256": hashlib.sha256(
                    (target / "backup.json").read_bytes()
                ).hexdigest(),
            },
        )
    before = all_bytes(instance)
    with pytest.raises(InstallationError):
        life.restore_instance(target, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert all_bytes(instance) == before


@pytest.mark.parametrize("kind", ["source", "inside", "existing_instance", "empty", "symlink"])
def test_restore_only_accepts_nonexistent_separate_target(instance, tmp_path, kind):
    source = tmp_path / "backup"
    backup(instance, source)
    target = tmp_path / "restored"
    if kind == "source":
        target = source
    elif kind == "inside":
        target = source / "nested"
    elif kind == "existing_instance":
        target = instance
    elif kind == "empty":
        target.mkdir()
    else:
        target.symlink_to(tmp_path / "not-created", target_is_directory=True)
    before = all_bytes(instance)
    with pytest.raises(InstallationError):
        life.restore_instance(source, target)
    assert all_bytes(instance) == before


@pytest.mark.parametrize("failure", ["prepare", "verify", "source_changed", "publish"])
def test_failed_restore_retains_candidate_but_never_activates(
    instance, tmp_path, monkeypatch, failure
):
    source, target = tmp_path / "backup", tmp_path / "restored"
    backup(instance, source)
    original = install._prepare
    if failure == "prepare":
        monkeypatch.setattr(
            install,
            "_invoke",
            lambda *a, **k: (_ for _ in ()).throw(InstallationError("prepare failed")),
        )
    elif failure == "verify":
        monkeypatch.setattr(
            life,
            "_check_release",
            lambda *a, **k: (_ for _ in ()).throw(InstallationError("bad environment")),
        )
    elif failure == "source_changed":

        def prepare_and_change(*args, **kwargs):
            result = original(*args, **kwargs)
            (source / "state" / "changed").write_text("late source mutation")
            return result

        monkeypatch.setattr(install, "_prepare", prepare_and_change)
    else:
        write_original = install._json

        def write(path, value):
            if path.name == "active.json":
                raise OSError("publish failed")
            write_original(path, value)

        monkeypatch.setattr(install, "_json", write)
    before = all_bytes(instance)
    with pytest.raises(InstallationError):
        life.restore_instance(source, target)
    assert target.is_dir()
    assert not (target / "active.json").exists()
    assert list((target / "releases").iterdir())
    assert life.inspect_instance(target)["installation"] == "unavailable"
    assert all_bytes(instance) == before


@pytest.mark.parametrize("response", ["healthy", "unauthorized", "invalid", "timeout", "redirect"])
def test_optional_health_is_authenticated_get_and_never_leaks_payload(
    instance, monkeypatch, response
):
    requests = []
    secret = "test-secret-never-display"
    monkeypatch.setenv("DEVICE_RUNTIME_ADMIN_TOKEN", secret)

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/devices"
        assert request.headers["authorization"] == "Bearer " + secret
        if response == "healthy":
            return httpx.Response(200, json=[])
        if response == "timeout":
            raise httpx.ReadTimeout("sensitive-response", request=request)
        if response == "redirect":
            return httpx.Response(302, headers={"Location": "https://unknown.example/"})
        return httpx.Response(401 if response == "unauthorized" else 200, text="sensitive-response")

    def factory(url, token):
        return DeviceAPI(url, token, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("device_runtime.http_client.DeviceAPI", factory)
    before = all_bytes(instance)
    result = life.inspect_instance(instance, health_url="http://127.0.0.1:18999")
    assert len(requests) == 1
    assert result["online_health"]["status"] == (
        "healthy" if response == "healthy" else "unavailable"
    )
    assert secret not in json.dumps(result)
    assert "sensitive-response" not in json.dumps(result)
    assert all_bytes(instance) == before


def test_health_without_credentials_is_unavailable_without_request(instance, monkeypatch):
    monkeypatch.delenv("DEVICE_RUNTIME_ADMIN_TOKEN", raising=False)
    result = life.inspect_instance(instance, health_url="http://127.0.0.1:18999")
    assert result["online_health"]["status"] == "unavailable"
    assert result["installation"] == "complete"


def invoke_cli(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["device-runtime", "instance", *arguments])
    cli.main()


def test_cli_lifecycle_roundtrip(instance, upgraded, tmp_path, monkeypatch, capsys):
    original = active(instance)["release"]
    root = ["--root", str(instance)]
    invoke_cli(monkeypatch, ["inspect", *root])
    assert json.loads(capsys.readouterr().out)["environments"] == "available"
    invoke_cli(monkeypatch, ["prepare", *root, "--bundle", str(upgraded), "--stopped"])
    candidate = json.loads(capsys.readouterr().out)["release"]
    assert active(instance)["release"] == original
    invoke_cli(monkeypatch, ["activate", *root, "--release", candidate, "--stopped"])
    assert json.loads(capsys.readouterr().out)["status"] == "activated"
    invoke_cli(monkeypatch, ["rollback", *root, "--release", original, "--stopped"])
    assert json.loads(capsys.readouterr().out)["status"] == "software_rollback"
    source, target = tmp_path / "backup", tmp_path / "restored"
    invoke_cli(monkeypatch, ["backup", *root, "--destination", str(source), "--stopped"])
    assert json.loads(capsys.readouterr().out)["status"] == "backup_complete"
    invoke_cli(monkeypatch, ["restore", "--backup", str(source), "--destination", str(target)])
    assert json.loads(capsys.readouterr().out)["status"] == "restored_not_started"


@pytest.mark.parametrize("action", ["prepare", "activate", "rollback", "backup", "restore"])
def test_cli_failure_is_controlled(instance, upgraded, tmp_path, monkeypatch, capsys, action):
    args = [action, "--root", str(instance)]
    if action == "prepare":
        args += ["--bundle", str(upgraded)]
    elif action in ("activate", "rollback"):
        args += ["--release", "../invalid"]
    elif action == "backup":
        args += ["--destination", str(tmp_path / "backup")]
    else:
        args = [
            "restore",
            "--backup",
            str(tmp_path / "absent"),
            "--destination",
            str(tmp_path / "target"),
        ]
    with pytest.raises(SystemExit) as failure:
        invoke_cli(monkeypatch, args)
    assert failure.value.code == 2
    error = capsys.readouterr().err
    assert "device-runtime instance:" in error
    assert "Traceback" not in error


def test_readonly_environment_invocation_has_timeout_and_no_log(tmp_path, monkeypatch):
    def run(args, **kwargs):
        assert kwargs["timeout"] == 60
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired(args, 60)

    monkeypatch.setattr(install.subprocess, "run", run)
    with pytest.raises(InstallationError, match="timed out"):
        install._invoke(["python", "-B"], cwd=tmp_path, log=None)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("action", ["backup", "restore"])
def test_new_leaf_inside_another_instance_is_not_isolated(instance, tmp_path, action):
    other = tmp_path / "other-instance"
    other.mkdir()
    (other / "instance.json").write_text('{"instance_id":"existing"}')
    target = other / "state" / "new-target"
    before = all_bytes(instance)
    if action == "backup":
        call = lambda: backup(instance, target)
    else:
        source = tmp_path / "backup"
        backup(instance, source)
        call = lambda: life.restore_instance(source, target)
    with pytest.raises(InstallationError, match="existing instance"):
        call()
    assert not target.exists()
    assert all_bytes(instance) == before
    assert list(other.iterdir()) == [other / "instance.json"]


def test_preparation_does_not_infer_future_schema_compatibility(instance, upgraded, monkeypatch):
    original = life.inspect_bundle

    def future(path):
        manifest = original(path)
        if Path(path) == upgraded:
            manifest["components"]["device"]["version"] = "0.3.0"
        return manifest

    monkeypatch.setattr(life, "inspect_bundle", future)
    before = all_bytes(instance)
    with pytest.raises(InstallationError, match="no validated state-format"):
        prepare(instance, upgraded)
    assert all_bytes(instance) == before


def test_backup_rejects_active_pointer_mutation_during_copy(instance, tmp_path, monkeypatch):
    original = life._copy

    def copy(source, target):
        original(source, target)
        value = active(instance)
        value["activation_id"] = "f" * 32
        install._json(instance / "active.json", value)

    monkeypatch.setattr(life, "_copy", copy)
    target = tmp_path / "backup"
    with pytest.raises(InstallationError, match="source changed"):
        backup(instance, target)
    assert not (target / "complete.json").exists()


def test_health_has_a_total_timeout(instance, monkeypatch):
    import asyncio

    observed = []
    original = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        observed.append(timeout)
        return await original(awaitable, timeout=0.001)

    class SlowAPI:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def list_devices(self, **kwargs):
            await asyncio.sleep(60)

    monkeypatch.setattr("device_runtime.http_client.DeviceAPI", SlowAPI)
    monkeypatch.setattr(life.asyncio, "wait_for", wait_for)
    result = life.inspect_instance(instance, health_url="http://127.0.0.1:18999")
    assert result["online_health"]["status"] == "unavailable"
    assert observed == [10]


@pytest.mark.parametrize("mode,status", [("SH", "shared_occupied"), ("EX", "exclusive_occupied")])
def test_diagnostic_lease_with_an_independent_process(instance, mode, status):
    code = """import fcntl, sys
with open(sys.argv[1], 'rb') as lock:
    fcntl.flock(lock, getattr(fcntl, 'LOCK_' + sys.argv[2]))
    print('ready', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, str(instance / ".installation.lock"), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        assert life.inspect_instance(instance)["lock"] == status
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()  # Exact disposable child created in this test, not a service.
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
    assert process.returncode == 0


def test_new_state_records_between_prepare_and_activate_are_preserved(instance, upgraded):
    candidate = prepare(instance, upgraded)
    (instance / "state" / "client" / "later-receipt").write_text("never replay this receipt")
    expected = persistent(instance)
    life.activate_release(instance, candidate, stopped=True)
    assert persistent(instance) == expected


def test_actual_release_link_cannot_escape_instance(instance, upgraded, tmp_path):
    candidate = prepare(instance, upgraded)
    outside = tmp_path / "external-release"
    shutil.move(str(instance / "releases" / candidate), outside)
    (instance / "releases" / candidate).symlink_to(outside, target_is_directory=True)
    pointer, data = (instance / "active.json").read_bytes(), persistent(instance)
    for operation in (life.activate_release, life.rollback_release):
        with pytest.raises(InstallationError):
            operation(instance, candidate, stopped=True)
    assert_untouched(instance, pointer, data)


async def seed_portable_task(instance, *, external=False, task_id="old-task"):
    from device_runtime.journal import Journal
    from device_runtime.protocol import Operation

    state = instance / "state" / "client"
    output = (
        (instance.parent / "external-output") if external else (state / "output" / f"{task_id}.bin")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"old task: \xe4\xb8\xad\x00\xff\n")
    request = Operation(
        device_id="isolated-mac",
        operation_id=task_id,
        action="bash.execute",
        arguments={
            "mode": "run",
            "argv": ["never-execute-this-fixture"],
            "cwd": str(instance.parent),
        },
    )
    for name, owner in (
        ("tasks.sqlite3", "tasks:isolated-mac"),
        ("operations.sqlite3", "isolated-fixture"),
    ):
        journal = Journal(state / name, owner=owner)
        try:
            await journal.initialize()
            await journal.accept(request, "unknown")
            await journal.update(
                task_id,
                "unknown",
                {"output_path": str(output), "exit_code": None, "code": "UNKNOWN"},
            )
        finally:
            await journal.close()
    return output


async def read_portable_task(root, limit=100, task_id="old-task"):
    from device_runtime.executor import LocalExecutor
    from device_runtime.protocol import Operation

    executor = LocalExecutor(root / "state" / "client", "isolated-mac")
    try:
        return await executor.tasks.execute(
            Operation(
                device_id="isolated-mac",
                operation_id="read-only-query",
                action="bash.get",
                arguments={"execution_id": task_id, "bytes": limit},
            )
        )
    finally:
        await executor.aclose()


@pytest.mark.parametrize("original", ["unavailable", "changed"])
async def test_restored_old_task_output_independent_of_original(instance, tmp_path, original):

    output = await seed_portable_task(instance)
    expected = output.read_bytes()
    source, target = tmp_path / "snapshot", tmp_path / "restored"
    backup(instance, source)
    life.restore_instance(source, target)
    journal_before = {
        name: (target / "state" / "client" / name).read_bytes()
        for name in ("tasks.sqlite3", "operations.sqlite3")
    }
    if original == "unavailable":
        output.parent.rename(output.parent.with_name("disconnected-output"))
    else:
        output.write_bytes(b"wrong ORIGINAL source after backup")
    result = await read_portable_task(target)
    assert result["state"] == "unknown"
    assert result["output"] == expected.decode("utf-8", errors="replace")
    assert {
        name: (target / "state" / "client" / name).read_bytes() for name in journal_before
    } == journal_before


@pytest.mark.parametrize(
    "database",
    [
        "client/tasks.sqlite3",
        "client/operations.sqlite3",
        "center/operations.sqlite3",
        "mcp/operations.sqlite3",
    ],
)
@pytest.mark.parametrize("damage", ["magic", "truncated", "empty"])
def test_formal_sqlite_bad_header_never_completes_backup(instance, tmp_path, database, damage):
    path = instance / "state" / database
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE fixture(value)")
    db.close()
    raw = path.read_bytes()
    path.write_bytes(
        (b"broken SQLite!!\x00" + raw[16:])
        if damage == "magic"
        else (raw[:8] if damage == "truncated" else b"")
    )
    before = all_bytes(instance)
    target = tmp_path / "snapshot"
    with pytest.raises(InstallationError):
        backup(instance, target)
    assert not (target / "complete.json").exists()
    assert all_bytes(instance) == before


@pytest.mark.parametrize("damage", ["magic", "truncated", "empty"])
def test_formal_sqlite_bad_header_never_activates_restore(instance, tmp_path, damage):
    source = tmp_path / "snapshot"
    path = instance / "state" / "client" / "operations.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE fixture(value)")
    db.close()
    backup(instance, source)
    path = source / "state" / "client" / "operations.sqlite3"
    raw = path.read_bytes()
    path.write_bytes(
        (b"broken SQLite!!\x00" + raw[16:])
        if damage == "magic"
        else (raw[:8] if damage == "truncated" else b"")
    )
    manifest = life._read(source / "backup.json")
    manifest["inventory"] = life._snapshot_inventory(source)
    install._json(source / "backup.json", manifest)
    complete = life._read(source / "complete.json")
    complete["manifest_sha256"] = hashlib.sha256((source / "backup.json").read_bytes()).hexdigest()
    install._json(source / "complete.json", complete)
    before = all_bytes(instance)
    target = tmp_path / "restored"
    with pytest.raises(InstallationError):
        life.restore_instance(source, target)
    assert not (target / "active.json").exists()
    assert all_bytes(instance) == before


@pytest.mark.parametrize("action", ["inspect", "activate", "rollback"])
def test_lifecycle_missing_role_extra_uses_real_verifier(
    tmp_path, extra_bundle, metadata_environment, simulated_installer, monkeypatch, action
):
    root = tmp_path / "instance"
    create(root, extra_bundle, roles=("center",))
    original = active(root)["release"]
    metadata_environment(root / "releases" / original / "center", missing=None)
    if action == "inspect":
        candidate = original
    else:
        candidate = prepare(root, extra_bundle)
    metadata_environment(root / "releases" / candidate / "center", missing="uvicorn")
    monkeypatch.setattr(install, "_invoke", REAL_INVOKE)
    pointer, data = (root / "active.json").read_bytes(), persistent(root)
    if action == "inspect":
        assert life.inspect_instance(root)["environments"] == "unavailable"
    else:
        with pytest.raises(InstallationError):
            getattr(life, action + "_release")(root, candidate, stopped=True)
    assert_untouched(root, pointer, data)


async def test_restored_old_task_output_survives_second_restore_chain(instance, tmp_path):

    original = await seed_portable_task(instance)
    expected = original.read_bytes()
    source, first = tmp_path / "snapshot-one", tmp_path / "restored-one"
    backup(instance, source)
    life.restore_instance(source, first)
    original.parent.rename(original.parent.with_name("detached-original-output"))
    assert (await read_portable_task(first, limit=100))["output"] == expected.decode(
        "utf-8", errors="replace"
    )
    second_snapshot, second = tmp_path / "snapshot-two", tmp_path / "restored-two"
    backup(first, second_snapshot)
    life.restore_instance(second_snapshot, second)
    (first / "state" / "client" / "output").rename(
        first / "state" / "client" / "disconnected-output"
    )
    result = await read_portable_task(second, limit=100)
    assert result["state"] == "unknown"
    assert result["output"] == expected.decode("utf-8", errors="replace")
    assert (
        life._read(source / "backup.json")["task_outputs"]
        == life._read(second_snapshot / "backup.json")["task_outputs"]
    )
    for name in ("tasks.sqlite3", "operations.sqlite3"):
        assert (second / "state" / "client" / name).read_bytes() == (
            instance / "state" / "client" / name
        ).read_bytes()


async def test_direct_managed_output_and_nonmanaged_receipt_not_basename_mapped(instance, tmp_path):

    original = await seed_portable_task(instance, external=True)
    expected = original.read_bytes()
    direct = await read_portable_task(instance, limit=100)
    assert direct["output"] == expected.decode("utf-8", errors="replace")
    # Same basename under managed output is not proof an external receipt belongs here.
    managed = instance / "state" / "client" / "output" / original.name
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b"unrelated local file")
    source, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(instance, source)
    life.restore_instance(source, restored)
    assert life._read(source / "backup.json")["task_outputs"] == {str(original): None}
    result = await read_portable_task(restored, limit=100)
    assert result["output_unavailable"]


async def test_missing_mapped_copy_never_falls_back_to_existing_original(instance, tmp_path):
    await seed_portable_task(instance)
    source, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(instance, source)
    life.restore_instance(source, restored)
    (restored / "state" / "client" / "output" / "old-task.bin").unlink()
    result = await read_portable_task(restored)
    assert result["output_unavailable"]


@pytest.mark.parametrize("damage", ["checksum", "identity", "escape", "extra_receipt", "missing"])
async def test_task_output_map_damage_is_controlled(instance, tmp_path, damage):
    from device_runtime.tasks import OUTPUT_MAP_FILE

    await seed_portable_task(instance)
    source, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(instance, source)
    life.restore_instance(source, restored)
    path = restored / OUTPUT_MAP_FILE
    value = life._read(path)
    if damage == "checksum":
        value["sha256"] = "0" * 64
    elif damage == "identity":
        value["instance_sha256"] = "0" * 64
    elif damage == "escape":
        value["paths"] = {next(iter(value["paths"])): "../outside"}
    else:
        value["paths"]["/unreferenced/output"] = "unrelated"
    if damage == "missing":
        path.unlink()
    else:
        install._json(path, value)
    pointer, data = (restored / "active.json").read_bytes(), persistent(restored)
    assert life.inspect_instance(restored)["persistent"] == "unavailable"
    with pytest.raises(InstallationError):
        backup(restored, tmp_path / "new-snapshot")
    assert not (tmp_path / "new-snapshot").exists()
    assert_untouched(restored, pointer, data)
    result = await read_portable_task(restored)
    assert not result["success"]


def test_ordinary_output_with_sqlite_filename_is_not_a_formal_database(instance, tmp_path):
    path = instance / "state" / "client" / "output" / "operations.sqlite3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"ordinary output, not SQLite")
    source, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(instance, source)
    life.restore_instance(source, restored)
    assert (restored / path.relative_to(instance)).read_bytes() == path.read_bytes()


async def test_restored_output_mapping_survives_software_changes_and_new_receipts(
    instance, tmp_path
):

    from device_runtime.tasks import OUTPUT_MAP_FILE

    original_output = await seed_portable_task(instance)
    expected = original_output.read_bytes()
    assert (await read_portable_task(instance, limit=100))["output"] == expected.decode(
        "utf-8", errors="replace"
    )
    source, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(instance, source)
    life.restore_instance(source, restored)
    original_output.parent.rename(original_output.parent.with_name("original-offline"))
    mapping_bytes = (restored / OUTPUT_MAP_FILE).read_bytes()
    previous = active(restored)["release"]
    bundle = restored / "releases" / previous / "bundle"
    candidate = prepare(restored, bundle)
    for transition in (
        lambda: life.activate_release(restored, candidate, stopped=True),
        lambda: life.rollback_release(restored, previous, stopped=True),
        lambda: install.rebuild_instance(restored),
    ):
        transition()
        assert (restored / OUTPUT_MAP_FILE).read_bytes() == mapping_bytes
        assert (
            active(restored)["task_output_map_sha256"] == hashlib.sha256(mapping_bytes).hexdigest()
        )
        assert (await read_portable_task(restored, limit=100))["output"] == expected.decode(
            "utf-8", errors="replace"
        )
    newer = await seed_portable_task(restored, task_id="new-task")
    new_expected = newer.read_bytes()
    snapshot_two, final = tmp_path / "snapshot-two", tmp_path / "final"
    backup(restored, snapshot_two)
    assert len(life._read(snapshot_two / "backup.json")["task_outputs"]) == 2
    life.restore_instance(snapshot_two, final)
    newer.parent.rename(newer.parent.with_name("restored-output-offline"))
    for task_id, content in (("old-task", expected), ("new-task", new_expected)):
        result = await read_portable_task(final, limit=100, task_id=task_id)
        assert result["execution_id"] == task_id
        assert result["output"] == content.decode("utf-8", errors="replace")


async def test_restore_mapping_publication_failure_never_activates(instance, tmp_path, monkeypatch):
    from device_runtime.tasks import OUTPUT_MAP_FILE

    await seed_portable_task(instance)
    source, restored = tmp_path / "snapshot", tmp_path / "restored"
    backup(instance, source)
    before = all_bytes(instance)
    original = install._json

    def write(path, value):
        if path.name == OUTPUT_MAP_FILE:
            raise OSError("injected output mapping publication failure")
        original(path, value)

    monkeypatch.setattr(install, "_json", write)
    with pytest.raises(InstallationError):
        life.restore_instance(source, restored)
    assert restored.is_dir() and not (restored / "active.json").exists()
    assert all_bytes(instance) == before


@pytest.mark.parametrize("damage", ["missing_field", "missing_reference"])
async def test_backup_mapping_is_integrity_bound_and_missing_mapping_is_rejected(
    instance, tmp_path, damage
):
    await seed_portable_task(instance)
    source = tmp_path / "snapshot"
    backup(instance, source)
    manifest = life._read(source / "backup.json")
    if damage == "missing_field":
        manifest.pop("task_outputs")
    else:
        manifest["task_outputs"] = {}
    install._json(source / "backup.json", manifest)
    complete = life._read(source / "complete.json")
    complete["manifest_sha256"] = hashlib.sha256((source / "backup.json").read_bytes()).hexdigest()
    install._json(source / "complete.json", complete)
    with pytest.raises(InstallationError):
        life.restore_instance(source, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.fixture
async def mapped_instance(tmp_path, extra_bundle, simulated_installer):
    """Fresh offline restore with an unreachable original output, never old evidence roots."""
    root = tmp_path / "map source"
    create(root, extra_bundle, roles=("client",))
    original = await seed_portable_task(root)
    snapshot = tmp_path / "map backup"
    backup(root, snapshot)
    target = tmp_path / "map restored"
    life.restore_instance(snapshot, target)
    original.parent.rename(original.parent.with_name("disconnected-output"))
    assert not original.exists()
    return target, {"output_path": str(original), "task_id": "old-task", "state": "unknown"}


def map_bound_bytes(root):
    return {
        "active": (root / "active.json").read_bytes(),
        "map": (root / "task-output-map.json").read_bytes(),
        "persistent": persistent(root),
    }


def installed_output(python, root, snapshot):
    """Execute the target's actual tasks reader without creating/opening a business DB."""
    code = """import json, sys
from pathlib import Path
from device_runtime.tasks import BackgroundTasks
reader = BackgroundTasks.__new__(BackgroundTasks)
reader.state_dir = Path(sys.argv[1]) / 'state' / 'client'
path = reader._output_path(json.loads(sys.argv[2])['output_path'])
print(json.dumps({'success': True, 'output': path.read_bytes()[:7].decode('utf-8', errors='replace')}))
"""
    return json.loads(
        subprocess.check_output(
            [str(python), "-I", "-B", "-c", code, str(root), json.dumps(snapshot)],
            cwd=root.parent,
            text=True,
        )
    )


@pytest.mark.parametrize("support", ["missing_module", "undeclared", "unsupported"])
@pytest.mark.parametrize("action", ["activate", "rollback", "inspect"])
def test_required_output_map_target_rejected_by_real_environment(
    mapped_instance, extra_bundle, metadata_environment, monkeypatch, support, action
):
    root, _ = mapped_instance
    if action == "inspect":
        release = active(root)["release"]
    else:
        # A candidate prepared before metadata was required must be rechecked at publication.
        release = prepare(root, extra_bundle)
    metadata_environment(
        root / "releases" / release / "client",
        role="client",
        missing=None,
        output_map_support=support,
    )
    monkeypatch.setattr(install, "_invoke", REAL_INVOKE)  # No mock of the core verifier.
    before = all_bytes(root)
    if action == "inspect":
        assert life.inspect_instance(root)["environments"] == "unavailable"
    else:
        with pytest.raises(InstallationError):
            function = life.activate_release if action == "activate" else life.rollback_release
            function(root, release, stopped=True)
    assert all_bytes(root) == before


@pytest.mark.parametrize("action", ["prepare", "rebuild"])
@pytest.mark.parametrize("support", ["undeclared", "unsupported"])
def test_required_output_map_preparation_rejects_before_publish(
    mapped_instance, extra_bundle, metadata_environment, monkeypatch, action, support
):
    root, _ = mapped_instance
    before = map_bound_bytes(root)

    def preparation(args, *, cwd, log):
        if args[1:3] == ["--no-config", "venv"]:
            metadata_environment(cwd, role="client", missing=None, output_map_support=support)
        elif len(args) > 1 and args[1] == "-I":
            REAL_INVOKE(args, cwd=cwd, log=log)
        # Only wheel installation/pip-check is simulated, not target import/probe execution.

    monkeypatch.setattr(install, "_invoke", preparation)
    with pytest.raises(InstallationError):
        if action == "prepare":
            prepare(root, extra_bundle)
        else:
            install.rebuild_instance(root)
    assert map_bound_bytes(root) == before


@pytest.mark.parametrize("action", ["activate", "rollback", "rebuild"])
def test_required_output_map_supporting_target_reads_local_output(
    mapped_instance, extra_bundle, metadata_environment, monkeypatch, action
):

    root, snapshot = mapped_instance
    before = map_bound_bytes(root)
    current_home = root / "releases" / active(root)["release"] / "client"
    python, _ = metadata_environment(
        current_home, role="client", missing=None, output_map_support="current"
    )
    expected = installed_output(python, root, snapshot)
    assert expected["success"]

    def preparation(args, *, cwd, log):
        if args[1:3] == ["--no-config", "venv"]:
            metadata_environment(cwd, role="client", missing=None, output_map_support="current")
        elif len(args) > 1 and args[1] == "-I":
            REAL_INVOKE(args, cwd=cwd, log=log)

    monkeypatch.setattr(install, "_invoke", preparation)
    if action == "rebuild":
        result = install.rebuild_instance(root)
    else:
        candidate = prepare(root, extra_bundle)
        monkeypatch.setattr(install, "_invoke", REAL_INVOKE)
        function = life.activate_release if action == "activate" else life.rollback_release
        result = function(root, candidate, stopped=True)
    python = root / "releases" / result["release"] / "client" / "venv" / "bin" / "python"
    output = installed_output(python, root, snapshot)
    assert output == expected
    assert output["output"] == "old tas"
    assert life.inspect_instance(root)["environments"] == "available"
    after = map_bound_bytes(root)
    assert after["map"] == before["map"] and after["persistent"] == before["persistent"]
    assert active(root)["task_output_map_sha256"] == hashlib.sha256(before["map"]).hexdigest()


def test_no_required_output_map_allows_legacy_target_and_rebuild(
    tmp_path, extra_bundle, metadata_environment, simulated_installer, monkeypatch
):
    root = tmp_path / "direct instance"
    create(root, extra_bundle, roles=("client",))
    before = persistent(root)
    support = "undeclared"

    def preparation(args, *, cwd, log):
        if args[1:3] == ["--no-config", "venv"]:
            metadata_environment(cwd, role="client", missing=None, output_map_support=support)
        elif len(args) > 1 and args[1] == "-I":
            REAL_INVOKE(args, cwd=cwd, log=log)

    monkeypatch.setattr(install, "_invoke", preparation)
    old = prepare(root, extra_bundle)
    life.activate_release(root, old, stopped=True)
    install.rebuild_instance(root)
    support = "current"
    new = prepare(root, extra_bundle)
    life.activate_release(root, new, stopped=True)
    life.rollback_release(root, old, stopped=True)
    assert life.inspect_instance(root)["environments"] == "available"
    assert persistent(root) == before
    assert "task_output_map_sha256" not in active(root)


@pytest.mark.parametrize("support", ["undeclared", "current"])
def test_restore_checks_output_map_support_before_first_publication(
    mapped_instance, tmp_path, metadata_environment, monkeypatch, support
):
    root, snapshot = mapped_instance
    source = tmp_path / "another snapshot"
    backup(root, source)
    source_before = all_bytes(source)
    target = tmp_path / "another restored"

    def preparation(args, *, cwd, log):
        if args[1:3] == ["--no-config", "venv"]:
            metadata_environment(cwd, role="client", missing=None, output_map_support=support)
        elif len(args) > 1 and args[1] == "-I":
            REAL_INVOKE(args, cwd=cwd, log=log)

    monkeypatch.setattr(install, "_invoke", preparation)
    if support == "undeclared":
        with pytest.raises(InstallationError):
            life.restore_instance(source, target)
        assert not (target / "active.json").exists()
    else:
        result = life.restore_instance(source, target)
        python = target / "releases" / result["release"] / "client" / "venv" / "bin" / "python"
        assert installed_output(python, target, snapshot)["success"]
        assert life.inspect_instance(target)["environments"] == "available"
        assert persistent(target) == persistent(root)
    assert all_bytes(source) == source_before


@pytest.mark.parametrize("role", ["center", "mcp", "client"])
def test_stable_run_resolves_active_roles_after_switch_rollback_and_rebuild(
    tmp_path, delivery, upgraded, simulated_installer, role
):
    """Exercise the real stable launcher, not a release path in caller config."""
    root = tmp_path / "instance with spaces"
    initial = create(root, delivery, roles=("center", "client", "mcp"))["release"]
    (root / "state" / "client" / "preserved-output.bin").write_bytes(b"unknown\x00\xff")
    caller = {"command": str(root / "run"), "args": [role]}
    before = persistent(root)

    def observe():
        release = active(root)["release"]
        python = root / "releases" / release / role / "venv" / "bin" / "python"
        python.write_text(
            f"#!{sys.executable}\n"
            "import json,os,sys\n"
            "print(json.dumps({'python': __file__, 'args': sys.argv[1:], "
            "'cwd': os.getcwd(), 'url': os.environ.get('DEVICE_RUNTIME_API_URL')}))\n"
        )
        python.chmod(0o700)
        # No credentials inherited, no service launched: child is the fixture spy.
        result = subprocess.run(
            [caller["command"], *caller["args"]],
            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        observed = json.loads(result.stdout)
        assert Path(observed["python"]) == python
        assert Path(observed["cwd"]) == root
        if role == "mcp":
            assert observed["args"] == ["-m", "device_runtime.mcp_server"]
            assert observed["url"] == "http://127.0.0.1:28765"
        else:
            expected = [
                "-m",
                "device_runtime.cli",
                "server" if role == "center" else "client",
                "--state-dir",
                str(root / "state" / role),
            ]
            expected += (
                ["--host", "127.0.0.1", "--port", "28765"]
                if role == "center"
                else ["--server-url", "ws://127.0.0.1:28765", "--device-id", "isolated-mac"]
            )
            assert observed["args"] == expected
            assert observed["url"] is None
        assert caller == {"command": str(root / "run"), "args": [role]}
        assert persistent(root) == before

    observe()
    candidate = prepare(root, upgraded)
    life.activate_release(root, candidate, stopped=True)
    assert active(root)["release"] == candidate
    observe()
    life.rollback_release(root, initial, stopped=True)
    assert active(root)["release"] == initial
    observe()
    install.rebuild_instance(root, bundle=delivery)
    assert active(root)["release"] not in (initial, candidate)
    observe()
