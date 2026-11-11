"""Strict parsing of legacy launchctl list's documented three columns.

Caller must establish the correct user GUI domain and successful command exit.
A missing label is absence from this snapshot only, NOT a start barrier.
"""

import re
from dataclasses import dataclass


class LaunchdStatusError(ValueError):
    pass


@dataclass(frozen=True)
class ListedJob:
    loaded: bool
    pid: int | None
    last_exit: int | None


def parse_job_list(text, label):
    if not isinstance(text, str) or len(text.encode("utf-8")) > 1024 * 1024:
        raise LaunchdStatusError("launchd listing is unavailable or exceeds limit")
    if not isinstance(label, str) or not label or any(c.isspace() for c in label):
        raise LaunchdStatusError("invalid service label")
    lines = text.splitlines()
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        raise LaunchdStatusError("unrecognized launchd listing header")
    found = None
    labels = set()
    if len(lines) > 16385:
        raise LaunchdStatusError("launchd listing exceeds row limit")
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 3:
            raise LaunchdStatusError("unrecognized launchd listing row")
        pid, status, name = fields
        if name in labels:
            raise LaunchdStatusError("duplicate label in launchd listing")
        labels.add(name)
        if pid != "-" and not re.fullmatch(r"[1-9][0-9]{0,9}", pid):
            raise LaunchdStatusError("invalid launchd PID")
        if not re.fullmatch(r"-?[0-9]{1,10}", status):
            raise LaunchdStatusError("invalid launchd exit status")
        if name == label:
            found = ListedJob(True, None if pid == "-" else int(pid), int(status))
    return found if found is not None else ListedJob(False, None, None)


def require_gui_domain(uid, *, run=None):
    """Prove only that the exact GUI domain was queryable at this instant.

    Discard potentially large/sensitive domain listings rather than capturing
    them. Nonzero exit includes absence AND permissions/other failures; callers
    must not convert it into permission to install while supposedly logged out.
    """
    import subprocess

    if type(uid) is not int or uid < 501:
        raise LaunchdStatusError("an explicit ordinary macOS UID is required")
    try:
        result = (run or subprocess.run)(
            ["/bin/launchctl", "print", f"gui/{uid}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LaunchdStatusError(
            "GUI domain unavailable or unknown; lifecycle operation refused"
        ) from None
    if result.returncode != 0:
        raise LaunchdStatusError("GUI domain unavailable or unknown; lifecycle operation refused")
    return f"gui/{uid}"
