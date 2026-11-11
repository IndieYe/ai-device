"""Exact GUI service observations, never authorization to replace files.

launchctl print is diagnostic text, not a stable API. Accept only the known
shape; reject changes rather than infer absence. In particular a loaded job
without a PID can start immediately. These observations cannot establish a
startup barrier or prove that all managed tasks have exited.
"""

import re
import subprocess
from dataclasses import dataclass

from .client_macos_launchd_status import LaunchdStatusError
from .client_macos_layout import MacClientLayout
from .client_macos_query import run_query


@dataclass(frozen=True)
class ServiceObservation:
    target: str
    state: str
    pid: int | None


def parse_service_print(text: str, layout: MacClientLayout) -> ServiceObservation:
    """Validate target, definition path and direct state fields only.

    Nested environment/endpoint fields must never be mistaken for job fields.
    No arbitrary launchctl text is included in exceptions or the return value.
    """
    target = f"gui/{layout.uid}/local.ai-device.client"
    if not isinstance(text, str) or len(text.encode("utf-8")) > 262144:
        raise LaunchdStatusError("service observation exceeds limit or is invalid")
    lines = text.splitlines()
    if not lines or lines[0] != f"{target} = {{":
        raise LaunchdStatusError("service observation target is unknown")
    fields = {}
    depth = 1
    closed = False
    for line in lines[1:]:
        stripped = line.strip()
        if closed:
            if stripped:
                raise LaunchdStatusError("unexpected content after service observation")
            continue
        # Only standalone structural delimiters are accepted. The known print
        # format uses tabs for indentation and braces for nested dictionaries.
        if stripped == "}":
            depth -= 1
            if depth == 0:
                closed = True
            continue
        if depth == 1:
            if not line.startswith("\t") or line.startswith("\t\t"):
                raise LaunchdStatusError("unrecognized service observation indentation")
            key, separator, value = stripped.partition(" = ")
            if key in {"path", "state", "pid"} and separator:
                if key in fields:
                    raise LaunchdStatusError("duplicate service observation field")
                fields[key] = value
        if stripped.endswith("{"):
            depth += 1
    if not closed or depth != 0:
        raise LaunchdStatusError("incomplete service observation")
    if fields.get("path") != str(layout.plist):
        raise LaunchdStatusError("loaded service definition path does not match registration")
    state = fields.get("state")
    if state not in {"running", "not running", "waiting", "spawn scheduled"}:
        raise LaunchdStatusError("service execution state is unknown")
    pid_text = fields.get("pid")
    pid = None
    if pid_text is not None:
        if not re.fullmatch(r"[1-9][0-9]{0,9}", pid_text):
            raise LaunchdStatusError("service process identity is unknown")
        pid = int(pid_text)
    if (state == "running") != (pid is not None):
        raise LaunchdStatusError("service state and process identity disagree")
    return ServiceObservation(target, state, pid)


def observe_service(
    layout: MacClientLayout, *, run=None, require_fixed_entry=False
) -> ServiceObservation:
    """Read exact service target; nonzero is UNKNOWN, never absent/stopped.

    This diagnostic adapter is deliberately not connected to package mutation.
    The default runner bounds capture to 256 KiB with a total deadline.
    stdout is ephemeral and must not be logged or persisted by callers.
    """
    target = f"gui/{layout.uid}/local.ai-device.client"
    try:
        result = (run or run_query)(
            ["/bin/launchctl", "print", target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise LaunchdStatusError("service unavailable or unknown; lifecycle operation refused")
        text = result.stdout.decode("utf-8", errors="strict")
    except LaunchdStatusError:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise LaunchdStatusError(
            "service observation failed; lifecycle operation refused"
        ) from None
    return (parse_loaded_entry if require_fixed_entry else parse_service_print)(text, layout)


def observe_registered_service(*, run=None, require_fixed_entry=False) -> ServiceObservation:
    """Check persisted account and disk definition before querying launchd.

    Recheck both after the query to reject changes noticed during observation.
    This is not atomic and deliberately supplies no stopped/replace verdict.
    """
    from .client_macos_registration import registered_account, validate_agent

    account = registered_account()
    validate_agent(account)
    observation = observe_service(account.layout, run=run, require_fixed_entry=require_fixed_entry)
    current = registered_account()
    if current != account:
        raise LaunchdStatusError("registration changed during service observation")
    validate_agent(current)
    return observation


def parse_loaded_entry(text: str, layout: MacClientLayout) -> ServiceObservation:
    """Observe exact fixed launcher shape, not loaded-plist equivalence or stop proof.

    The text format is unstable; unsupported shapes fail closed. This does not
    validate all effective launchd properties and must never authorize bootout.
    """
    from .client_macos_layout import LAUNCHER

    observation = parse_service_print(text, layout)
    depth = 1
    program = None
    arguments = None
    in_arguments = False
    for line in text.splitlines()[1:]:
        stripped = line.strip()
        if stripped == "}":
            if depth == 2 and in_arguments:
                in_arguments = False
            depth -= 1
            continue
        if depth == 1:
            key, separator, value = stripped.partition(" = ")
            entry_field = re.match(r"^(program|arguments)(?=\s|=|$)", stripped)
            if entry_field and (not separator or key != entry_field.group(1)):
                raise LaunchdStatusError("loaded entry assignment shape is unknown")
            if key == "program" and separator:
                if program is not None:
                    raise LaunchdStatusError("duplicate loaded program field")
                program = value
            elif key == "arguments" and separator:
                if value != "{":
                    raise LaunchdStatusError("loaded arguments shape is unknown")
                if arguments is not None:
                    raise LaunchdStatusError("duplicate loaded arguments field")
                arguments = []
                in_arguments = True
        elif depth == 2 and in_arguments:
            if not line.startswith("\t\t") or line.startswith("\t\t\t") or "{" in stripped:
                raise LaunchdStatusError("loaded arguments shape is unknown")
            arguments.append(stripped)
        if stripped.endswith("{"):
            depth += 1
    if program != str(LAUNCHER) or arguments != [str(LAUNCHER)]:
        raise LaunchdStatusError("loaded service entry differs or is unknown")
    return observation
