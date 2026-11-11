"""Upgrade window and uninstall ordering; no launchctl and no service actions."""

import os
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from device_runtime import client_macos_service_job as job
from device_runtime import client_macos_upgrade_window as window


@pytest.fixture
def env(monkeypatch, tmp_path):
    state = {"held": True, "order": []}
    plist = tmp_path / "local.ai-device.client.plist"
    plist.write_text("<plist/>")
    layout = SimpleNamespace(
        uid=501,
        domain="gui/501",
        service="gui/501/local.ai-device.client",
        plist=plist,
    )
    state["account"] = SimpleNamespace(layout=layout)

    @contextmanager
    def lock():
        state["held"] = True
        try:
            yield 11
        finally:
            state["held"] = False

    monkeypatch.setattr(window.sys, "platform", "darwin")
    monkeypatch.setattr(window.os, "geteuid", lambda: 0)
    monkeypatch.setattr(job.sys, "platform", "darwin")
    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    monkeypatch.setattr(job, "_account_and_agent", lambda: state["account"])
    state["lock"] = lock
    state["matches"] = lambda descriptor: state["held"]
    return state


def _run(record, name, result=0, raises=None):
    def run(argv, **kwargs):
        record.append((name, argv))
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=result)

    return run


def test_window_unloads_before_publish_and_reloads_after(env):
    order = env["order"]
    unload = _run(order, "unload")
    load = _run(order, "load")

    def publish():
        order.append(("publish", None))
        assert env["held"], "management must stay held across the swap"
        assert not [1 for name, _ in order if name == "load"], "reload must follow publish"

    result = window.replace_registered_program(
        confirm_stopped=lambda: order.append(("stopped", None)) or True,
        publish=publish,
        verify_ready=lambda: order.append(("ready", None)) or {"ready": True},
        lock_factory=env["lock"],
        matches=env["matches"],
        load_run=load,
        unload_run=unload,
    )

    assert [name for name, _ in order] == ["stopped", "unload", "publish", "load", "ready"]
    assert order[1][1] == ["/bin/launchctl", "bootout", "gui/501/local.ai-device.client"]
    assert order[3][1] == [
        "/bin/launchctl",
        "bootstrap",
        "gui/501",
        str(env["account"].layout.plist),
    ]
    assert result["stages"] == ["stopped", "unloaded", "published", "loaded"]
    assert result["rolled_back"] is False


def test_window_refuses_when_instance_not_confirmed_stopped(env):
    calls = []
    with pytest.raises(window.MacUpgradeWindowError, match="not confirmed stopped"):
        window.replace_registered_program(
            confirm_stopped=lambda: False,
            publish=lambda: calls.append(("publish", None)),
            verify_ready=lambda: calls.append(("ready", None)),
            lock_factory=env["lock"],
            matches=env["matches"],
            load_run=_run(calls, "load"),
            unload_run=_run(calls, "unload"),
        )
    assert calls == []


def test_window_stops_at_unload_failure_without_replacing(env):
    calls = []
    with pytest.raises(window.MacUpgradeWindowError, match="unload failed before replacement"):
        window.replace_registered_program(
            confirm_stopped=lambda: True,
            publish=lambda: calls.append(("publish", None)),
            verify_ready=lambda: calls.append(("ready", None)),
            lock_factory=env["lock"],
            matches=env["matches"],
            load_run=_run(calls, "load"),
            unload_run=_run(calls, "unload", result=5),
        )
    # Old program is still in place and the job is in an unknown state.
    assert [name for name, _ in calls] == ["unload"]


def test_window_reports_replaced_but_not_running_when_reload_fails(env):
    calls = []

    def publish():
        calls.append(("publish", None))

    with pytest.raises(window.MacUpgradeWindowError) as error:
        window.replace_registered_program(
            confirm_stopped=lambda: True,
            publish=publish,
            verify_ready=lambda: calls.append("ready"),
            lock_factory=env["lock"],
            matches=env["matches"],
            load_run=_run(calls, "load", raises=subprocess.TimeoutExpired("launchctl", 10)),
            unload_run=_run(calls, "unload"),
        )
    message = str(error.value)
    assert "program is replaced but not running" in message
    assert "do not replay" in message
    # No automatic retry, no kickstart fallback, no rollback to the old program.
    assert [name for name, _ in calls] == ["unload", "publish", "load"]


def test_window_requires_guard_for_every_action(env):
    env["held"] = False
    with pytest.raises(window.MacUpgradeWindowError, match="management lock is unavailable"):
        window.replace_registered_program(
            confirm_stopped=lambda: True,
            publish=lambda: None,
            verify_ready=lambda: None,
            lock_factory=env["lock"],
            matches=lambda descriptor: False,
            load_run=None,
            unload_run=None,
        )


def test_window_publish_failure_leaves_job_unloaded(env):
    calls = []

    def publish():
        calls.append(("publish", None))
        raise RuntimeError("swap failed")

    with pytest.raises(RuntimeError, match="swap failed"):
        window.replace_registered_program(
            confirm_stopped=lambda: True,
            publish=publish,
            verify_ready=lambda: calls.append("ready"),
            lock_factory=env["lock"],
            matches=env["matches"],
            load_run=_run(calls, "load"),
            unload_run=_run(calls, "unload"),
        )
    # Never reloaded on failure: the half-published state must stay visible.
    assert [name for name, _ in calls] == ["unload", "publish"]


def test_uninstall_removes_definition_and_preserves_data(env, tmp_path):
    calls = []
    plist = env["account"].layout.plist

    def remove(path):
        calls.append(("remove", str(path)))

    result = window.uninstall_registered_client(
        confirm_stopped=lambda: True,
        lock_factory=env["lock"],
        matches=env["matches"],
        unload_run=_run(calls, "unload"),
        remove_definition=remove,
        account_reader=lambda: env["account"],
    )
    assert result["stages"] == ["stopped", "unloaded", "definition_removed"]
    assert result["data_preserved"] is True
    assert calls[1] == ("remove", str(plist))


def test_uninstall_requires_explicit_definition_removal(env):
    with pytest.raises(window.MacUpgradeWindowError, match="definition removal was not requested"):
        window.uninstall_registered_client(
            confirm_stopped=lambda: True,
            lock_factory=env["lock"],
            matches=env["matches"],
            unload_run=_run([], "unload"),
            account_reader=lambda: env["account"],
        )


def test_uninstall_refuses_when_stop_unconfirmed(env):
    calls = []
    with pytest.raises(window.MacUpgradeWindowError, match="uninstall refused"):
        window.uninstall_registered_client(
            confirm_stopped=lambda: False,
            lock_factory=env["lock"],
            matches=env["matches"],
            unload_run=_run(calls, "unload"),
            remove_definition=lambda path: calls.append("remove"),
        )
    assert calls == []


def test_definition_removal_deletes_only_the_registered_file(env):
    plist = env["account"].layout.plist
    result = job.remove_registered_definition(account=env["account"])
    assert result == {"definition_removed": True, "already_absent": False}
    assert not plist.exists()


def test_definition_removal_is_idempotent_when_already_absent(env):
    plist = env["account"].layout.plist
    plist.unlink()
    result = job.remove_registered_definition(account=env["account"])
    assert result == {"definition_removed": False, "already_absent": True}


def test_definition_removal_refuses_a_foreign_path(env, tmp_path):
    other = tmp_path / "other.plist"
    other.write_text("<plist/>")
    with pytest.raises(job.MacServiceJobError, match="target changed"):
        job.remove_registered_definition(other, account=env["account"])
    assert other.exists()


def test_definition_removal_refuses_a_symlink(env, tmp_path):
    target = tmp_path / "real.plist"
    target.write_text("<plist/>")
    link = env["account"].layout.plist
    link.unlink()
    os.symlink(target, link)
    with pytest.raises(job.MacServiceJobError, match="not the registered regular file"):
        job.remove_registered_definition(account=env["account"])
    assert target.exists()
