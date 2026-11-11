"""Service load/unload behaviour; no real launchctl, no service actions."""

import subprocess
from types import SimpleNamespace

import pytest

from device_runtime import client_macos_query as query
from device_runtime import client_macos_service_job as job


class Result(SimpleNamespace):
    pass


@pytest.fixture
def context(monkeypatch):
    state = {"held": True, "calls": []}
    layout = SimpleNamespace(
        uid=501,
        domain="gui/501",
        service="gui/501/local.ai-device.client",
        plist=__import__("pathlib").Path(
            "/Users/someuser/Library/LaunchAgents/local.ai-device.client.plist"
        ),
    )
    account = SimpleNamespace(layout=layout)
    monkeypatch.setattr(job.sys, "platform", "darwin")
    monkeypatch.setattr(job.os, "geteuid", lambda: 0)
    state["account"] = account
    monkeypatch.setattr(job, "_account_and_agent", lambda: state["account"])
    return state


def _guard(state):
    return lambda: state["held"]


def test_load_bootstraps_exact_registered_definition(context):
    def run(argv, **kwargs):
        context["calls"].append(argv)
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 10
        return Result(returncode=0)

    result = job.load_registered_service(run=run, guard_current=_guard(context))

    assert context["calls"] == [
        [
            "/bin/launchctl",
            "bootstrap",
            "gui/501",
            "/Users/someuser/Library/LaunchAgents/local.ai-device.client.plist",
        ]
    ]
    assert result["service_loaded"] is True
    assert result["ready_verified"] is False


def test_unload_boots_out_exact_target(context):
    def run(argv, **kwargs):
        context["calls"].append(argv)
        return Result(returncode=0)

    result = job.unload_registered_service(run=run, guard_current=_guard(context))

    assert context["calls"] == [
        ["/bin/launchctl", "bootout", "gui/501/local.ai-device.client"]
    ]
    assert result["service_unloaded"] is True
    # Unload alone never claims that the program stopped.
    assert result["stop_verified"] is False


@pytest.mark.parametrize("case", ["nonzero", "timeout", "oserror"])
def test_action_failures_are_unknown_and_not_retried(context, case):
    attempts = []

    def run(argv, **kwargs):
        attempts.append(argv)
        if case == "nonzero":
            return Result(returncode=5)
        if case == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        raise OSError("no launchctl")

    with pytest.raises(job.MacServiceJobError) as error:
        job.unload_registered_service(run=run, guard_current=_guard(context))

    assert "do not replay" in str(error.value)
    assert len(attempts) == 1


def test_guard_lost_before_action_refuses_without_running(context):
    context["held"] = False

    def run(argv, **kwargs):
        raise AssertionError("must not run without the management guard")

    with pytest.raises(job.MacServiceJobError, match="guard is unavailable"):
        job.load_registered_service(run=run, guard_current=_guard(context))


def test_registration_change_during_action_is_unknown(context):
    def run(argv, **kwargs):
        changed = SimpleNamespace(
            layout=SimpleNamespace(
                uid=501,
                domain="gui/501",
                service="gui/501/local.ai-device.client",
                plist=__import__("pathlib").Path(
                    "/Users/otheruser/Library/LaunchAgents/local.ai-device.client.plist"
                ),
            ),
            gid=20,
        )
        context["account"] = changed  # registered account replaced mid-action
        return Result(returncode=0)

    with pytest.raises(job.MacServiceJobError, match="outcome unknown"):
        job.load_registered_service(run=run, guard_current=_guard(context))


def test_requires_administrator(context, monkeypatch):
    monkeypatch.setattr(job.os, "geteuid", lambda: 501)
    with pytest.raises(job.MacServiceJobError, match="administrator"):
        job.unload_registered_service(run=None, guard_current=_guard(context))


def test_requires_darwin(context, monkeypatch):
    monkeypatch.setattr(job.sys, "platform", "linux")
    with pytest.raises(job.MacServiceJobError, match="administrator"):
        job.unload_registered_service(run=None, guard_current=_guard(context))


@pytest.mark.parametrize(
    "argv",
    [
        ["/bin/launchctl", "bootstrap", "gui/501"],
        ["/bin/launchctl", "bootstrap", "system", "/tmp/x.plist"],
        ["/bin/launchctl", "bootstrap", "-k", "gui/501", "/Users/u/Library/LaunchAgents/local.ai-device.client.plist"],
        ["/bin/launchctl", "bootstrap", "gui/501", "/Users/u/Library/LaunchAgents/other.plist"],
        ["/bin/launchctl", "bootout", "gui/501"],
        ["/bin/launchctl", "bootout", "system/local.ai-device.client"],
        ["/bin/launchctl", "bootout", "-k", "gui/501/local.ai-device.client"],
    ],
)
def test_command_adapter_rejects_unsupported_shapes(argv):
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "timeout": 10,
        "check": False,
    }
    with pytest.raises(ValueError):
        if argv[1] == "bootstrap":
            query.run_bootstrap(argv, **kwargs)
        else:
            query.run_bootout(argv, **kwargs)


def test_adapter_accepts_registered_shapes(monkeypatch):
    # Contract test: the adapter must validate the shape and hand the exact
    # argv to the capture seam. _capture is substituted so this can never load
    # or unload a real service, as the module docstring promises.
    seen = []

    def fake_capture(argv, *, timeout):
        seen.append((tuple(argv), timeout))
        return Result(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(query, "_capture", fake_capture)

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "timeout": 10,
        "check": False,
    }
    for argv, adapter in (
        (
            [
                "/bin/launchctl",
                "bootstrap",
                "gui/501",
                "/Users/u/Library/LaunchAgents/local.ai-device.client.plist",
            ],
            query.run_bootstrap,
        ),
        (["/bin/launchctl", "bootout", "gui/501/local.ai-device.client"], query.run_bootout),
    ):
        try:
            adapter(argv, **kwargs)
        except ValueError:
            raise AssertionError(f"unexpected rejection: {argv}")

    # Both shapes must reach the seam unchanged and no sweep may be used.
    assert [list(argv) for argv, _ in seen] == [
        [
            "/bin/launchctl",
            "bootstrap",
            "gui/501",
            "/Users/u/Library/LaunchAgents/local.ai-device.client.plist",
        ],
        ["/bin/launchctl", "bootout", "gui/501/local.ai-device.client"],
    ]


def test_absent_target_after_unload_confirms_job_absence(context, monkeypatch):
    def run(argv, **kwargs):
        return Result(returncode=0)

    monkeypatch.setattr(
        "device_runtime.client_macos_service_status.observe_service",
        lambda layout, **kwargs: (_ for _ in ()).throw(
            __import__(
                "device_runtime.client_macos_service_status",
                fromlist=["LaunchdStatusError"],
            ).LaunchdStatusError("unknown")
        ),
    )
    with pytest.raises(job.MacServiceJobError, match="absence not confirmed"):
        job.unload_then_verify_absent(run=run, guard_current=_guard(context))


def test_service_still_present_after_unload_refuses(context):
    def run(argv, **kwargs):
        return Result(returncode=0)

    with pytest.raises(job.MacServiceJobError, match="still reported"):
        job.unload_then_verify_absent(
            run=run,
            guard_current=_guard(context),
            observe=lambda layout, **kwargs: SimpleNamespace(target="x"),
        )
