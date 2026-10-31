"""Orchestration tests: step order, privilege boundaries and fail-stop.

These run on the current host, which is not Linux, so the platform and euid
checks are stubbed while every sequencing assertion stays real.

The fakes below deliberately mirror the shapes the underlying steps actually
return. An earlier version accepted any return value, which hid three real
contract breaks: ``_instance_owner`` called a method the retained class does not
have, ``prepare_signed`` was handed the parsed receipt instead of its path, and
``prepare_signed`` was assumed to return a digest it never returns.
"""

import os
import sys

import pytest

from device_runtime import native_helper as helper
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


def test_provision_refuses_before_creating_an_unregisterable_instance(
    tmp_path, monkeypatch, as_root
):
    """A registration the next step would refuse must not leave a created tree.

    Provisioning creates the instance first and registers afterwards. If the
    registration prerequisites are wrong, an unguarded sequence leaves a tree
    that a rerun refuses to touch, so the refusal has to come first.
    """
    import pwd as pwd_module

    account = type("E", (), {"pw_uid": 12345, "pw_gid": 12345})()
    monkeypatch.setattr(pwd_module, "getpwnam", lambda name: account)
    # The ancestry and ownership checks require a root-owned host tree.
    monkeypatch.setattr(helper, "_trusted", lambda *a, **kw: None)
    created = []
    monkeypatch.setattr(orchestrate, "_fork_as", lambda uid, gid, work: created.append(work))
    monkeypatch.setattr(install, "_fork_as", lambda uid, gid, work: created.append(work))

    environment = tmp_path / "credentials.env"
    environment.write_text("TOKEN=1\n")
    environment.chmod(0o644)
    units = tmp_path / "units"
    units.mkdir()
    registry = tmp_path / "registry"
    registry.mkdir()

    with pytest.raises(install.InstallationError, match="mode 0600"):
        install.provision(
            payload=tmp_path,
            root=tmp_path / "inst",
            instance_id="probe",
            server_url="https://example.invalid",
            username="someone",
            unit_dir=units,
            environment_file=environment,
            registry=registry,
        )
    assert created == [], "no instance may be created before registration can succeed"


def test_upgrade_steps_run_in_order_and_stop_on_failure(monkeypatch, as_root, tmp_path):
    """The sequence must not skip a failed step or continue past it."""
    calls = []
    prepared_file = tmp_path / "updates" / "5-probe--op1.prepared.json"
    prepared_file.parent.mkdir(parents=True)
    prepared_file.write_text("{}\n")

    class FakePrepare:
        @staticmethod
        def prepare_runtime_candidate(root, package, envelope, **kwargs):
            calls.append("prepare")
            # The real step returns the receipt it wrote; only ``candidate`` is
            # read by the sequence.
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
            # The real method takes the receipt *path* and returns the recorded
            # transaction intent, which carries no digest of its own.
            assert kwargs["runtime_receipt"] == (
                tmp_path / "native-candidates" / "op1.result.json"
            )
            return {"authenticated_candidate": {}, "instance_id": "probe"}

        def approve(self, *args, **kwargs):
            calls.append("approve")
            raise ValueError("approval refused")

        def apply(self, *args, **kwargs):
            calls.append("apply")
            return {"applied": {"state": "applied_package"}}

        def switch_helper(self, *args, **kwargs):
            calls.append("switch")
            return {"helper_switched": {"state": "helper_switched"}}

    monkeypatch.setattr(orchestrate, "prepare", FakePrepare)
    monkeypatch.setattr(orchestrate, "update", type("M", (), {"Updates": FakeUpdates}))
    monkeypatch.setattr(
        orchestrate, "_instance_owner", lambda registry, instance_id: (12345, 12345, tmp_path)
    )
    monkeypatch.setattr(orchestrate, "_fork_as", lambda uid, gid, work: work())
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
            bootstrap_tree="0.2.7-native1",
            uv_executable=str(tmp_path / "uv"),
            stopped=True,
        )

    # Steps after the failure must not run. The key set is loaded once for the
    # owner prepare and once for the root signature check.
    assert calls == ["keys", "prepare", "keys", "prepare_signed", "approve"]


def test_upgrade_refuses_to_activate_after_an_unproven_apply(
    monkeypatch, as_root, tmp_path
):
    """An ``unknown`` apply observation must stop the sequence, not advance it.

    ``apply`` reports a refused or unproven replacement in its returned
    observation instead of raising, so treating a returned value as success is
    what would switch the bootstrap onto a package that was never installed.
    """
    calls = []
    prepared_file = tmp_path / "updates" / "5-probe--op1.prepared.json"
    prepared_file.parent.mkdir(parents=True)
    prepared_file.write_text("{}\n")

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
            return {"keys": []}

    class FakeUpdates:
        def __init__(self, registry):
            pass

        def prepare_signed(self, *args, **kwargs):
            return {"authenticated_candidate": {}}

        def approve(self, *args, **kwargs):
            calls.append("approve")
            return {"approved_by_uid": 0}

        def apply(self, *args, **kwargs):
            calls.append("apply")
            return {"applied": None, "outcome": {"state": "unknown"}}

        def switch_helper(self, *args, **kwargs):
            calls.append("switch")
            return {"helper_switched": {"state": "helper_switched"}}

    monkeypatch.setattr(orchestrate, "prepare", FakePrepare)
    monkeypatch.setattr(orchestrate, "update", type("M", (), {"Updates": FakeUpdates}))
    monkeypatch.setattr(
        orchestrate, "_instance_owner", lambda registry, instance_id: (12345, 12345, tmp_path)
    )
    monkeypatch.setattr(orchestrate, "_fork_as", lambda uid, gid, work: work())
    monkeypatch.setattr(orchestrate.helper, "_path", lambda value: tmp_path)

    with pytest.raises(orchestrate.OrchestrationError, match="applied_package"):
        orchestrate.upgrade(
            registry=str(tmp_path),
            instance_id="probe",
            operation_id="op1",
            package=str(tmp_path / "p.deb"),
            envelope=str(tmp_path / "e.json"),
            release_id="rel-1",
            bootstrap_version="0.2.7-native1",
            bootstrap_tree="0.2.7-native1",
            uv_executable=str(tmp_path / "uv"),
            stopped=True,
        )

    assert calls == ["prepare", "approve", "apply"], "no switch or activation past a bad apply"


def test_upgrade_switches_the_tree_not_the_version(monkeypatch, as_root, tmp_path):
    """The pointer names a directory; the version is only a compatibility input."""
    seen = {}
    prepared_file = tmp_path / "updates" / "5-probe--op1.prepared.json"
    prepared_file.parent.mkdir(parents=True)
    prepared_file.write_text("{}\n")

    class FakePrepare:
        @staticmethod
        def prepare_runtime_candidate(root, package, envelope, **kwargs):
            seen["bootstrap_version"] = kwargs["bootstrap_version"]
            return {"state": "runtime_prepared", "candidate": {"release": "rel-abc"}}

        @staticmethod
        def activate_candidate(root, release, **kwargs):
            return {"state": "activated"}

        @staticmethod
        def _load_trusted_keys(path):
            return {"keys": []}

    class FakeUpdates:
        def __init__(self, registry):
            pass

        def prepare_signed(self, *args, **kwargs):
            return {"authenticated_candidate": {}}

        def approve(self, *args, **kwargs):
            return {"approved_by_uid": 0}

        def apply(self, *args, **kwargs):
            return {"applied": {"state": "applied_package"}}

        def switch_helper(self, *args, **kwargs):
            seen["expected_version"] = kwargs["expected_version"]
            return {"helper_switched": {"state": "helper_switched"}}

    monkeypatch.setattr(orchestrate, "prepare", FakePrepare)
    monkeypatch.setattr(orchestrate, "update", type("M", (), {"Updates": FakeUpdates}))
    monkeypatch.setattr(
        orchestrate, "_instance_owner", lambda registry, instance_id: (12345, 12345, tmp_path)
    )
    monkeypatch.setattr(orchestrate, "_fork_as", lambda uid, gid, work: work())
    monkeypatch.setattr(orchestrate.helper, "_path", lambda value: tmp_path)

    result = orchestrate.upgrade(
        registry=str(tmp_path),
        instance_id="probe",
        operation_id="op1",
        package=str(tmp_path / "p.deb"),
        envelope=str(tmp_path / "e.json"),
        release_id="rel-1",
        bootstrap_version="0.2.7",
        bootstrap_tree="0.2.7-native1",
        uv_executable=str(tmp_path / "uv"),
        stopped=True,
    )

    assert seen["bootstrap_version"] == "0.2.7"
    assert seen["expected_version"] == "0.2.7-native1"
    assert result["bootstrap_tree"] == "0.2.7-native1"


def test_upgrade_refuses_a_tree_name_that_is_not_a_directory(monkeypatch, as_root, tmp_path):
    monkeypatch.setattr(
        orchestrate, "_instance_owner", lambda registry, instance_id: (12345, 12345, tmp_path)
    )
    monkeypatch.setattr(orchestrate.helper, "_path", lambda value: tmp_path)

    with pytest.raises(orchestrate.OrchestrationError, match="bootstrap tree"):
        orchestrate.upgrade(
            registry=str(tmp_path),
            instance_id="probe",
            operation_id="op1",
            package=str(tmp_path / "p.deb"),
            envelope=str(tmp_path / "e.json"),
            release_id="rel-1",
            bootstrap_version="0.2.7",
            bootstrap_tree="../../etc",
            uv_executable=str(tmp_path / "uv"),
            stopped=True,
        )


def test_bootstrap_tree_allows_a_same_name_reinstall(monkeypatch, tmp_path):
    """Reinstalling onto the running tree name is legitimate, not a refusal.

    The switch step still verifies the named directory is an installed,
    runnable bootstrap, so rejecting the running name here would only block a
    supported case without adding a check.
    """
    monkeypatch.setattr(orchestrate.sys, "base_prefix", str(tmp_path / "runtime"))
    assert orchestrate._bootstrap_tree("runtime") == "runtime"


def test_instance_owner_uses_the_retained_binding_method(monkeypatch, tmp_path):
    """The retired daemon took ``binding()`` with it; only ``_binding`` remains.

    Resolving the owner through a method that no longer exists raised
    ``AttributeError`` before any step could run, and the CLI did not catch it.
    """
    assert not hasattr(helper.Helper, "binding")
    # The constructor trusts the registry ancestry; this host is not root-owned.
    monkeypatch.setattr(helper, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(
        helper.Helper,
        "_binding",
        lambda self, instance_id: {"uid": 12345, "gid": 12345, "root": str(tmp_path)},
    )
    assert orchestrate._instance_owner(tmp_path, "probe") == (12345, 12345, tmp_path)


@pytest.mark.parametrize("uid,gid", [(0, 0), (0, 12345), (12345, 0), (-1, 1), (12345, -1)])
def test_instance_owner_refuses_privileged_or_invalid_ids(monkeypatch, tmp_path, uid, gid):
    monkeypatch.setattr(helper, "_trusted", lambda *a, **kw: None)
    monkeypatch.setattr(
        helper.Helper,
        "_binding",
        lambda self, instance_id: {"uid": uid, "gid": gid, "root": str(tmp_path)},
    )
    with pytest.raises(orchestrate.OrchestrationError, match="non-root owner"):
        orchestrate._instance_owner(tmp_path, "probe")


def test_uv_defaults_to_the_packaged_copy(monkeypatch, tmp_path):
    """uv is a requirement of the preparation step, not an optional hint."""
    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text("#!/bin/sh\n")
    monkeypatch.setattr(orchestrate.sys, "base_prefix", str(tmp_path / "runtime"))
    assert orchestrate._uv_executable(None) == str(uv)


def test_uv_refuses_when_none_is_packaged(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrate.sys, "base_prefix", str(tmp_path / "runtime"))
    with pytest.raises(orchestrate.OrchestrationError, match="uv-executable"):
        orchestrate._uv_executable(None)
