"""Orchestration tests: step order, privilege boundaries and fail-stop.

These run on the current host, which is not Linux, so the platform and euid
checks are stubbed while every sequencing assertion stays real.
"""

import os
import sys

import pytest

from device_runtime import native_install as install
from device_runtime import native_orchestrate as orchestrate

LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="fork/setuid require Linux")


@pytest.fixture
def as_root(monkeypatch):
    """Report root on Linux without actually being root."""
    monkeypatch.setattr(orchestrate.sys, "platform", "linux")
    monkeypatch.setattr(orchestrate.os, "geteuid", lambda: 0)
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.os, "geteuid", lambda: 0)


def test_upgrade_refuses_without_stopped_confirmation(as_root):
    with pytest.raises(orchestrate.OrchestrationError, match="stopped"):
        orchestrate.upgrade(
            registry="/var/lib/ai-device/native",
            instance_id="probe",
            operation_id="op1",
            package="/tmp/p.deb",
            envelope="/tmp/e.json",
            release_id="rel-1",
            bootstrap_version="0.2.7-native1",
        )


def test_upgrade_requires_root(monkeypatch):
    monkeypatch.setattr(orchestrate.sys, "platform", "linux")
    monkeypatch.setattr(orchestrate.os, "geteuid", lambda: 1000)
    with pytest.raises(orchestrate.OrchestrationError, match="administrator"):
        orchestrate.upgrade(
            registry="/var/lib/ai-device/native",
            instance_id="probe",
            operation_id="op1",
            package="/tmp/p.deb",
            envelope="/tmp/e.json",
            release_id="rel-1",
            bootstrap_version="0.2.7-native1",
            stopped=True,
        )


@LINUX_ONLY
def test_fork_as_runs_child_under_target_identity(tmp_path):
    uid, gid = os.geteuid(), os.getgid()
    if uid == 0:
        pytest.skip("needs a non-root owner to drop to")
    result = orchestrate._fork_as(uid, gid, lambda: {"uid": os.geteuid(), "ok": True})
    assert result == {"uid": uid, "ok": True}


@LINUX_ONLY
def test_fork_as_propagates_child_refusal(tmp_path):
    uid, gid = os.geteuid(), os.getgid()
    if uid == 0:
        pytest.skip("needs a non-root owner to drop to")

    def refuse():
        raise orchestrate.OrchestrationError("owner says no")

    with pytest.raises(orchestrate.OrchestrationError, match="owner says no"):
        orchestrate._fork_as(uid, gid, refuse)


@LINUX_ONLY
def test_fork_as_reports_exit_status_without_payload():
    uid, gid = os.geteuid(), os.getgid()
    if uid == 0:
        pytest.skip("needs a non-root owner to drop to")

    def crash():
        os._exit(7)

    with pytest.raises(orchestrate.OrchestrationError, match="exit 7"):
        orchestrate._fork_as(uid, gid, crash)


def test_install_start_requires_register(tmp_path, as_root):
    args = [
        "native-install",
        "--payload", str(tmp_path),
        "--root", str(tmp_path / "inst"),
        "--instance-id", "probe",
        "--server-url", "https://example.invalid",
        "--username", "someone",
        "--environment-file", str(tmp_path / "e.env"),
        "--start",
    ]
    parser = pytest.raises(SystemExit)
    with parser as info, pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", args)
        install.main()
    assert info.value.code == 1


def test_install_register_requires_account_and_environment(tmp_path, as_root):
    args = [
        "native-install",
        "--payload", str(tmp_path),
        "--root", str(tmp_path / "inst"),
        "--instance-id", "probe",
        "--server-url", "https://example.invalid",
        "--register",
    ]
    with pytest.raises(SystemExit) as info, pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", args)
        install.main()
    assert info.value.code == 1


def test_provision_refuses_root_account(tmp_path, monkeypatch, as_root):
    import pwd as pwd_module

    monkeypatch.setattr(
        pwd_module, "getpwnam", lambda name: type("E", (), {"pw_uid": 0, "pw_gid": 0})()
    )
    with pytest.raises(install.InstallationError, match="non-root"):
        install.provision(
            payload=tmp_path,
            root=tmp_path / "inst",
            instance_id="probe",
            server_url="https://example.invalid",
            username="root",
            unit_dir=tmp_path / "units",
            environment_file=tmp_path / "e.env",
        )


def test_provision_refuses_without_root(monkeypatch):
    monkeypatch.setattr(install.sys, "platform", "linux")
    monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    with pytest.raises(install.InstallationError, match="administrator"):
        install.provision(
            payload="/tmp/payload",
            root="/tmp/inst",
            instance_id="probe",
            server_url="https://example.invalid",
            username="someone",
            unit_dir="/tmp/units",
            environment_file="/tmp/e.env",
        )


def test_upgrade_steps_run_in_order_and_stop_on_failure(monkeypatch, as_root, tmp_path):
    """The sequence must not skip a failed step or continue past it."""
    calls = []

    class FakePrepare:
        @staticmethod
        def prepare_runtime_candidate(root, package, envelope, **kwargs):
            calls.append("prepare")
            return {"state": "runtime_prepared", "candidate": {"release": "rel-abc"}}

        @staticmethod
        def activate_candidate(root, release, **kwargs):
            calls.append("activate")
            return {"state": "activated"}

        @staticmethod
        def _load_trusted_keys(path):
            calls.append("keys")
            return {"keys": []}

    class FakeUpdates:
        def __init__(self, registry):
            pass

        def prepare_signed(self, *args, **kwargs):
            calls.append("prepare_signed")
            return {"prepared_sha256": "a" * 64}

        def approve(self, *args, **kwargs):
            calls.append("approve")
            raise ValueError("approval refused")

        def apply(self, *args, **kwargs):
            calls.append("apply")
            return {"state": "applied"}

        def switch_helper(self, *args, **kwargs):
            calls.append("switch")
            return {"state": "switched"}

    monkeypatch.setattr(orchestrate, "prepare", FakePrepare)
    monkeypatch.setattr(orchestrate, "update", type("M", (), {"Updates": FakeUpdates}))
    monkeypatch.setattr(
        orchestrate, "_instance_owner", lambda registry, instance_id: (12345, 12345, tmp_path)
    )
    monkeypatch.setattr(
        orchestrate, "_fork_as", lambda uid, gid, work: work()
    )
    monkeypatch.setattr(orchestrate.helper, "_path", lambda value: tmp_path)

    with pytest.raises(ValueError, match="approval refused"):
        orchestrate.upgrade(
            registry=str(tmp_path),
            instance_id="probe",
            operation_id="op1",
            package=str(tmp_path / "p.deb"),
            envelope=str(tmp_path / "e.json"),
            release_id="rel-1",
            bootstrap_version="0.2.7-native1",
            stopped=True,
        )

    # Steps after the failure must not run. The key set is loaded once for the
    # owner prepare and once for the root signature check.
    assert calls == ["keys", "prepare", "keys", "prepare_signed", "approve"]
