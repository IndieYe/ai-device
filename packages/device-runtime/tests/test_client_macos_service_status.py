import subprocess
from pathlib import Path

import pytest

from device_runtime.client_macos_launchd_status import LaunchdStatusError
from device_runtime.client_macos_layout import MacClientLayout
from device_runtime.client_macos_service_status import observe_service, parse_service_print

LAYOUT = MacClientLayout(501, Path("/Users/example"))
TARGET = "gui/501/local.ai-device.client"


def document(state="running", pid=123):
    pid_line = "" if pid is None else f"\tpid = {pid}\n"
    return (
        f"{TARGET} = {{\n\tpath = {LAYOUT.plist}\n"
        f"\tstate = {state}\n{pid_line}"
        "\tenvironment = {\n\t\tstate = not running\n\t\tpid = 999\n\t}\n}\n"
    )


def test_exact_service_observation_discards_nested_fields():
    observed = parse_service_print(document(), LAYOUT)
    assert observed.target == TARGET
    assert observed.state == "running"
    assert observed.pid == 123
    assert not hasattr(observed, "safe_to_replace")


@pytest.mark.parametrize("state", ["not running", "waiting", "spawn scheduled"])
def test_loaded_without_pid_does_not_become_absent(state):
    observed = parse_service_print(document(state, None), LAYOUT)
    assert observed.state == state
    assert observed.pid is None
    assert not hasattr(observed, "absent")


@pytest.mark.parametrize(
    "text",
    [
        document().replace("gui/501/", "user/501/"),
        document().replace("gui/501/", "gui/502/"),
        document().replace("state = running", "state = unfamiliar"),
        document().replace("pid = 123", "pid = 0"),
        document().replace("pid = 123", "pid = -1"),
        document().replace("\tpid = 123\n", ""),
        document("not running", 123),
        document().replace("\tstate = running\n", "\tstate = running\n\tstate = running\n"),
        document().replace(str(LAYOUT.plist), "/tmp/other.plist"),
        document().replace("\tpath =", "\t\tpath ="),
        document()[:-2],
        document() + "unexpected private text",
        "",
        "x" * 262145,
    ],
)
def test_unknown_or_inconsistent_observation_is_rejected(text):
    with pytest.raises(LaunchdStatusError):
        parse_service_print(text, LAYOUT)


def test_query_uses_exact_target_and_no_shell():
    def run(argv, **kwargs):
        assert argv == ["/bin/launchctl", "print", TARGET]
        assert kwargs == {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "timeout": 10,
            "check": False,
        }
        return subprocess.CompletedProcess(argv, 0, document().encode())

    assert observe_service(LAYOUT, run=run).pid == 123


@pytest.mark.parametrize("code", [1, 3, 113, -15])
def test_query_failure_is_unknown_not_absence(code):
    with pytest.raises(LaunchdStatusError, match="unknown"):
        observe_service(LAYOUT, run=lambda *a, **k: subprocess.CompletedProcess(a, code))


@pytest.mark.parametrize(
    "error", [OSError("private details"), subprocess.TimeoutExpired("secret", 10)]
)
def test_query_errors_are_sanitized(error):
    def run(*args, **kwargs):
        raise error

    with pytest.raises(LaunchdStatusError) as caught:
        observe_service(LAYOUT, run=run)
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_invalid_encoding_is_unknown():
    with pytest.raises(LaunchdStatusError):
        observe_service(LAYOUT, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, b"\xff"))


@pytest.mark.parametrize("change", ["none", "identity", "plist"])
def test_registered_query_rechecks_identity_and_definition(monkeypatch, change):
    from types import SimpleNamespace

    from device_runtime import client_macos_registration as registration
    from device_runtime.client_macos_service_status import observe_registered_service

    account = SimpleNamespace(layout=LAYOUT, name="example")
    replacement = SimpleNamespace(layout=LAYOUT, name="other")
    identities = iter([account, replacement if change == "identity" else account])
    checks = []
    monkeypatch.setattr(registration, "registered_account", lambda: next(identities))

    def validate(value):
        checks.append(value)
        if len(checks) == 2 and change == "plist":
            raise registration.MacRegistrationError("definition changed")

    monkeypatch.setattr(registration, "validate_agent", validate)

    def run(*args, **kwargs):
        assert len(checks) == 1
        return subprocess.CompletedProcess(args, 0, document().encode())

    if change == "none":
        assert observe_registered_service(run=run).pid == 123
        assert len(checks) == 2
    else:
        with pytest.raises((LaunchdStatusError, registration.MacRegistrationError)):
            observe_registered_service(run=run)


def test_invalid_registration_never_queries_launchd(monkeypatch):
    from device_runtime import client_macos_registration as registration
    from device_runtime.client_macos_service_status import observe_registered_service

    def invalid():
        raise registration.MacRegistrationError("invalid registration")

    monkeypatch.setattr(registration, "registered_account", invalid)
    with pytest.raises(registration.MacRegistrationError):
        observe_registered_service(run=lambda *a, **k: pytest.fail("query executed"))


def loaded_document():
    from device_runtime.client_macos_layout import LAUNCHER

    return document().replace(
        "\tstate = running\n",
        f"\tprogram = {LAUNCHER}\n\targuments = {{\n\t\t{LAUNCHER}\n\t}}\n\tstate = running\n",
        1,
    )


def test_loaded_entry_observation_is_not_replacement_permission():
    from device_runtime.client_macos_service_status import parse_loaded_entry

    observed = parse_loaded_entry(loaded_document(), LAYOUT)
    assert observed.pid == 123
    assert not hasattr(observed, "safe_to_replace")


@pytest.mark.parametrize(
    "change", ["program", "argument", "extra", "missing", "duplicate", "nested"]
)
def test_loaded_entry_mismatch_or_unknown_is_rejected(change):
    from device_runtime.client_macos_layout import LAUNCHER
    from device_runtime.client_macos_service_status import parse_loaded_entry

    text = loaded_document()
    if change == "program":
        text = text.replace(f"program = {LAUNCHER}", "program = /tmp/other")
    elif change == "argument":
        text = text.replace(f"\t\t{LAUNCHER}\n", "\t\t/tmp/other\n")
    elif change == "extra":
        text = text.replace(f"\t\t{LAUNCHER}\n", f"\t\t{LAUNCHER}\n\t\t--extra\n")
    elif change == "missing":
        text = document()
    elif change == "duplicate":
        text = text.replace(f"\tprogram = {LAUNCHER}\n", f"\tprogram = {LAUNCHER}\n" * 2)
    else:
        text = text.replace(f"\tprogram = {LAUNCHER}\n", "")
        text = text.replace(
            "\tenvironment = {\n", f"\tenvironment = {{\n\t\tprogram = {LAUNCHER}\n"
        )
    with pytest.raises(LaunchdStatusError):
        parse_loaded_entry(text, LAYOUT)


@pytest.mark.parametrize("fixed", [True, False])
def test_strict_loaded_entry_is_opt_in_at_query_boundary(fixed):
    def run(argv, **kwargs):
        assert argv == ["/bin/launchctl", "print", TARGET]
        return subprocess.CompletedProcess(argv, 0, document().encode())

    if fixed:
        with pytest.raises(LaunchdStatusError):
            observe_service(LAYOUT, run=run, require_fixed_entry=True)
    else:
        assert observe_service(LAYOUT, run=run).pid == 123


def test_strict_loaded_entry_query_with_registered_rechecks(monkeypatch):
    from types import SimpleNamespace

    from device_runtime import client_macos_registration as registration
    from device_runtime.client_macos_service_status import observe_registered_service

    account = SimpleNamespace(layout=LAYOUT)
    validations = []
    monkeypatch.setattr(registration, "registered_account", lambda: account)
    monkeypatch.setattr(registration, "validate_agent", lambda target: validations.append(target))

    def run(argv, **kwargs):
        assert validations == [account]
        return subprocess.CompletedProcess(argv, 0, loaded_document().encode())

    assert observe_registered_service(run=run, require_fixed_entry=True).pid == 123
    assert validations == [account, account]


@pytest.mark.parametrize("value", ["unexpected", "{}", "", "["])
def test_loaded_arguments_nonblock_duplicate_is_unknown(value):
    from device_runtime.client_macos_service_status import parse_loaded_entry

    text = loaded_document().replace("\tstate = running", f"\targuments = {value}\n\tstate = running")
    with pytest.raises(LaunchdStatusError):
        parse_loaded_entry(text, LAYOUT)


@pytest.mark.parametrize("field", ["arguments", "program"])
@pytest.mark.parametrize("assignment", ["=unexpected", " =\tunexpected", "\t= unexpected", "=", " = {", ""])
@pytest.mark.parametrize("position", ["before", "after"])
def test_loaded_entry_ambiguous_assignment_is_unknown(field, assignment, position):
    from device_runtime.client_macos_service_status import parse_loaded_entry

    extra = f"\t{field}{assignment}\n"
    marker = "\tprogram = " if position == "before" else "\tstate = running"
    text = loaded_document().replace(marker, extra + marker, 1)
    with pytest.raises(LaunchdStatusError):
        parse_loaded_entry(text, LAYOUT)
