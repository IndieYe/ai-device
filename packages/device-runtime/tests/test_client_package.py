from types import SimpleNamespace

import pytest

from device_runtime import client_package as module


def test_unconfigured_package_does_not_start(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "CONFIG", tmp_path / "missing")
    monkeypatch.setattr(module, "UNIT_PATH", tmp_path / "no-unit")
    service = SimpleNamespace(start=lambda: pytest.fail("unexpected start"))
    assert "configuration" in module.activate(service=service)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "check_conflicts", lambda: None)
    path = tmp_path / "config"
    path.write_text(
        "ExecStart=/opt/ai-device-client/runtime/bin/python3 -I -m device_runtime.client_cli --config /etc/ai-device-client/client.json\n"
    )
    monkeypatch.setattr(module, "CONFIG", path)
    monkeypatch.setattr(module, "UNIT_PATH", path)
    calls = []
    return SimpleNamespace(start=lambda: calls.append("start"), validate_config=lambda: None), calls


def test_readiness_requires_exact_runtime_identity(configured, monkeypatch):
    service, calls = configured
    monkeypatch.setattr(
        module,
        "request",
        lambda *a, **k: {
            "ready": True,
            "runtime_version": module.runtime_version(),
        },
    )
    assert "locally ready" in module.activate(service=service)
    assert calls == ["start"]


def test_wrong_running_version_is_failure(configured, monkeypatch):
    service, _ = configured
    monkeypatch.setattr(module, "request", lambda *a, **k: {"ready": True, "runtime_version": {}})
    with pytest.raises(RuntimeError, match="verification failed"):
        module.activate(service=service, timeout=0.01)


def test_registered_missing_config_is_failure(tmp_path, monkeypatch):
    unit = tmp_path / "unit"
    unit.write_text("registered")
    monkeypatch.setattr(module, "UNIT_PATH", unit)
    monkeypatch.setattr(module, "CONFIG", tmp_path / "missing")
    with pytest.raises(RuntimeError, match="configuration is missing"):
        module.activate(service=SimpleNamespace(start=lambda: pytest.fail("unexpected start")))


@pytest.mark.parametrize("response", [None, {"ready": False}])
def test_unready_or_unreachable_is_not_success(configured, monkeypatch, response):
    service, calls = configured

    def request(*args, **kwargs):
        if response is None:
            raise OSError("unavailable")
        return {**response, "runtime_version": module.runtime_version()}

    monkeypatch.setattr(module, "request", request)
    with pytest.raises(RuntimeError, match="verification failed"):
        module.activate(service=service, timeout=0.01)
    assert calls == ["start"]


def test_invalid_config_does_not_start(configured):
    service, calls = configured

    def invalid():
        raise RuntimeError("configuration unusable")

    service.validate_config = invalid
    with pytest.raises(RuntimeError, match="configuration unusable"):
        module.activate(service=service)
    assert calls == []


def test_busy_cli_has_clear_redacted_message(monkeypatch, capsys):
    from contextlib import nullcontext

    from device_runtime import client_package as package
    from device_runtime.maintenance import ClientBusy

    class BusyService:
        def state(self):
            return "stopped"

        def stop_for_install(self, socket):
            raise ClientBusy("PRIVATE_COMMAND_AND_TOKEN")

    monkeypatch.setattr(package, "management_lock", lambda **kw: nullcontext())
    monkeypatch.setattr(package, "SystemdClient", BusyService)
    with pytest.raises(SystemExit) as error:
        package.main(["stop"])
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "busy with active tasks" in output
    assert "retry when tasks finish" in output
    assert "PRIVATE_COMMAND_AND_TOKEN" not in output


def test_conflict_blocks_activation_before_start(configured, monkeypatch):
    from device_runtime.client_conflicts import ClientConflict

    service, calls = configured

    def conflict():
        raise ClientConflict("another running client detected")

    monkeypatch.setattr(module, "check_conflicts", conflict)
    with pytest.raises(ClientConflict):
        module.activate(service=service)
    assert calls == []
