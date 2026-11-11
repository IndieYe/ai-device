"""Bind an idle lease to its responding Darwin socket peer, never a label.

These primitives neither unload launchd jobs nor supply a startup barrier.
No service signals, socket reconnects or package mutations are performed.
"""

import struct
import sys

from .client_macos_processes import ProcessEvidenceError, inspect_identity


def sample_peer(connection):
    """Darwin sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=2, result pid_t (int)."""
    if sys.platform != "darwin":
        raise ProcessEvidenceError("Darwin lease peer inspection required")
    try:
        data = connection.getsockopt(0, 2, struct.calcsize("i"))
        if len(data) != struct.calcsize("i"):
            raise ProcessEvidenceError("invalid lease peer PID response")
        pid = struct.unpack("i", data)[0]
        return inspect_identity(pid)
    except OSError:
        raise ProcessEvidenceError("lease peer identity unavailable") from None


def bind_lease(lease):
    """Challenge after sampling prevents old buffered responses proving life.

    Assumes trusted local maintenance server handles this socket in its own
    process (no fd transfer). After the challenge the captured original may
    exit, but cannot be replaced by a label successor on this connection.
    """
    return lease.bind_peer(sample_peer)


def request_original_exit(lease, *, timeout=15):
    """Bind, commit once, and observe original exit; NOT safe-to-replace.

    Unknown acknowledgement aborts without replay. The original may already
    be exiting; caller must preserve failure rather than assume it stayed up.
    The bound identity is returned only after disappearance is observed.
    """
    import math

    from .client_macos_processes import wait_original_exit

    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ProcessEvidenceError("positive finite exit timeout required")
    identity = bind_lease(lease)
    lease.request_exit()
    wait_original_exit(identity, timeout=timeout)
    return identity


def peer_uid(connection):
    """Darwin getpeereid on the connected fd, no process-table inference."""
    import ctypes

    if sys.platform != "darwin":
        raise ProcessEvidenceError("Darwin peer credentials required")
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    library = ctypes.CDLL(None, use_errno=True)
    function = library.getpeereid
    function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    function.restype = ctypes.c_int
    if function(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise ProcessEvidenceError("local peer credentials unavailable")
    return uid.value
