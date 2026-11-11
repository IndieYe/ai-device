import pytest

from device_runtime.client_macos_launchd_status import LaunchdStatusError, ListedJob, parse_job_list

LABEL = "local.ai-device.client"


@pytest.mark.parametrize(
    "pid,status,expected",
    [
        ("123", "0", ListedJob(True, 123, 0)),
        ("-", "0", ListedJob(True, None, 0)),
        ("-", "-15", ListedJob(True, None, -15)),
    ],
)
def test_documented_columns(pid, status, expected):
    assert parse_job_list(f"PID\tStatus\tLabel\n{pid}\t{status}\t{LABEL}\n", LABEL) == expected


def test_absence_not_loaded_zero_pid():
    result = parse_job_list("PID Status Label\n1 0 unrelated\n", LABEL)
    assert result == ListedJob(False, None, None)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not permitted",
        "PID Status Label\n0 0 local.ai-device.client\n",
        "PID Status Label\n- unknown local.ai-device.client\n",
        "PID Status Label\n- 0 local.ai-device.client extra\n",
        "PID Status Label\n- 0 local.ai-device.client\n1 0 local.ai-device.client\n",
        "PID Status Label\n- 0 unrelated\nbroken row\n",
    ],
)
def test_unknown_output_never_becomes_absent(text):
    with pytest.raises(LaunchdStatusError):
        parse_job_list(text, LABEL)


def test_similar_label_not_match():
    assert not parse_job_list(f"PID Status Label\n123 0 {LABEL}.other\n", LABEL).loaded


def test_loaded_without_pid_is_not_absent():
    result = parse_job_list(f"PID Status Label\n- 1 {LABEL}\n", LABEL)
    assert result.loaded and result.pid is None and result.last_exit == 1


def test_gui_probe_exact_target_and_discarded_output():
    import subprocess

    from device_runtime.client_macos_launchd_status import require_gui_domain

    def run(argv, **kwargs):
        assert argv == ["/bin/launchctl", "print", "gui/502"]
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(argv, 0)

    assert require_gui_domain(502, run=run) == "gui/502"


@pytest.mark.parametrize("code", [1, 3, 113, -15])
def test_gui_failure_is_unknown_not_logged_out(code):
    import subprocess

    from device_runtime.client_macos_launchd_status import require_gui_domain

    with pytest.raises(LaunchdStatusError, match="unknown"):
        require_gui_domain(501, run=lambda *a, **k: subprocess.CompletedProcess(a, code))


@pytest.mark.parametrize("uid", [0, 500, True, "501"])
def test_gui_invalid_uid_never_executes(uid):
    from device_runtime.client_macos_launchd_status import require_gui_domain

    with pytest.raises(LaunchdStatusError):
        require_gui_domain(uid, run=lambda *a, **k: pytest.fail("executed"))


def test_gui_timeout_is_sanitized():
    import subprocess

    from device_runtime.client_macos_launchd_status import require_gui_domain

    def run(*a, **k):
        raise subprocess.TimeoutExpired("private command", 10)

    with pytest.raises(LaunchdStatusError, match="unknown") as caught:
        require_gui_domain(501, run=run)
    assert "private" not in str(caught.value)
