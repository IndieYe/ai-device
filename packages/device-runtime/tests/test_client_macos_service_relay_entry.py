"""Fixed relay entry behaviour, exercised in an isolated child process.

Each entry closes the inherited management descriptor and takes over SIGTERM, so
it is never run inside the pytest process. No launchctl call happens: the child
substitutes the service-job layer and the lock check. A real descriptor is
published as fd4, mirroring the native parent's `dup2(inherited, 4)`.
"""

import inspect
import os
import subprocess
import sys
import tempfile

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="fd semantics")

from device_runtime import client_macos_service_load_entry as load_entry
from device_runtime import client_macos_service_unload_entry as unload_entry

CHILD = r"""
import os, signal, sys
from device_runtime import client_macos_service_load_entry as load_entry
from device_runtime import client_macos_service_unload_entry as unload_entry

kind, held, uid, flag = sys.argv[1], sys.argv[2] == "held", int(sys.argv[3]), sys.argv[4]
argv = [flag] if flag != "-" else []

module = load_entry if kind == "load" else unload_entry
module.management_lock_matches = lambda *a, **k: held
module.os.getuid = lambda: uid
module.os.geteuid = lambda: uid
executed = []

def stub(**kwargs):
    ok = kwargs["guard_current"]()
    executed.append("%s:%s" % (kind, ok))
    return {}

module.load_registered_service = stub
module.unload_registered_service = stub

code = 0
try:
    module.main(argv)
except SystemExit as exit_signal:
    code = exit_signal.code if isinstance(exit_signal.code, int) else 1
finally:
    sys.stderr.write("\nRELAY_SUMMARY run=%s code=%d\n" % (",".join(executed) or "-", code))
sys.exit(code)
"""


def _child(kind, *, held=True, uid=0, flag="--inherit-management-lock", fd4=True):
    """A regular file is required: flock is unsupported on pipes."""
    descriptor, path = tempfile.mkstemp()
    try:
        if fd4:
            argv = [sys.executable, "-c", f"import os,sys;os.dup2({descriptor},4);" + CHILD,
                    kind, "held" if held else "lost", str(uid), flag]
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=60,
                pass_fds=(descriptor,), check=False,
            )
        else:
            result = subprocess.run(
                [sys.executable, "-c", CHILD, kind, "held" if held else "lost",
                 str(uid), flag],
                capture_output=True, text=True, timeout=60, check=False,
            )
    finally:
        os.close(descriptor)
        os.unlink(path)
    return result


def _summary(result):
    """Return (runs, exit code); runs is "-" when no job action happened."""
    for line in result.stderr.splitlines():
        if line.startswith("RELAY_SUMMARY"):
            parts = dict(item.split("=", 1) for item in line.split()[1:])
            runs = parts["run"]
            return ("-" if runs == "-" else runs), int(parts["code"])
    return None, None


@pytest.mark.parametrize(
    "kind,token",
    [
        ("load", "AI_DEVICE_REGISTERED_SERVICE_LOADED_V1"),
        ("unload", "AI_DEVICE_REGISTERED_SERVICE_UNLOADED_V1"),
    ],
)
def test_entry_runs_once_under_held_lock_and_prints_fixed_token(kind, token):
    result = _child(kind)
    executed, code = _summary(result)
    assert executed == f"{kind}:True"
    assert code == 0, result.stderr
    assert result.stdout == token + "\n"


@pytest.mark.parametrize("kind", ["load", "unload"])
def test_entry_refuses_when_guard_is_lost(kind):
    result = _child(kind, held=False)
    executed, code = _summary(result)
    assert code != 0
    assert result.stdout == ""  # no token: unknown, never retried
    assert executed == "-"


@pytest.mark.parametrize("kind", ["load", "unload"])
def test_entry_refuses_without_administrator(kind):
    result = _child(kind, uid=501)
    executed, code = _summary(result)
    assert code != 0
    assert result.stdout == ""
    assert executed == "-"


@pytest.mark.parametrize("flag", ["-", "--other", "--label"])
def test_entry_rejects_other_arguments(flag):
    result = _child("load", flag=flag)
    executed, code = _summary(result)
    assert code != 0
    assert result.stdout == ""
    assert executed == "-"


@pytest.mark.parametrize("kind", ["load", "unload"])
def test_entry_refuses_without_inherited_descriptor(kind):
    result = _child(kind, fd4=False)
    executed, code = _summary(result)
    assert code != 0
    assert result.stdout == ""
    assert executed == "-"


def test_modules_accept_only_the_single_inherited_lock_flag():
    """No target, label, domain, path or uid can be selected through argv."""
    for module in (load_entry, unload_entry):
        source = inspect.getsource(module.main)
        assert source.count("add_argument") == 1
        assert '"--inherit-management-lock"' in source
        for forbidden in ("--target", "--label", "--domain", "--path", "--uid", "--user"):
            assert forbidden not in source


def test_modules_never_unlock_the_shared_descriptor():
    """LOCK_UN would also release the parent's flock; only close is allowed.

    Docstrings may explain the prohibition, so only executable lines count.
    """
    for module in (load_entry, unload_entry):
        executable = _executable_tokens(inspect.getsource(module))
        assert "LOCK_UN" not in executable
        assert "os . close" in executable


def _executable_tokens(source):
    """Concatenate only NAME/OP tokens, dropping comments and string literals."""
    import io
    import tokenize

    kept = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.NAME, tokenize.OP):
            kept.append(token.string)
    return " ".join(kept)
