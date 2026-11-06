"""Validate existing maintenance status evidence, without querying or starting.

The caller must bind peer_identity to the same status connection and recheck
registered ownership. This validator alone is NOT a PID binding, health monitor,
launchd observation, installation commit, or permission to replace programs.
"""

import re

from .client_macos_processes import ProcessIdentity


class MacReadyError(ValueError):
    pass


def validate_ready(status, *, expected_version, peer_identity):
    """Return exact runtime version only for a ready, same-peer snapshot."""
    if not isinstance(peer_identity, ProcessIdentity):
        raise MacReadyError("bound client identity required")
    if (
        not isinstance(expected_version, dict)
        or set(expected_version) != {"version", "build_id"}
        or not isinstance(expected_version["version"], str)
        or not expected_version["version"]
        or not isinstance(expected_version["build_id"], str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_version["build_id"])
    ):
        raise MacReadyError("exact target version and build identity required")
    if (
        not isinstance(status, dict)
        or status.get("ready") is not True
        or type(status.get("pid")) is not int
        or status["pid"] != peer_identity.pid
        or status.get("runtime_version") != expected_version
    ):
        raise MacReadyError("client readiness or build identity mismatch")
    return expected_version.copy()


def query_ready(
    path,
    *,
    expected_version,
    timeout=5,
    connect=None,
    sample=None,
    expected_uid=None,
    uid_sample=None,
):
    """One status connection, kernel peer sampled before/after a nonce response.

    Does not acquire a maintenance lease or change admission. Any unknown fails
    without retry. Uses existing status framing, no new service protocol.
    """
    import json
    import math
    import socket
    import time
    import uuid

    from .client_macos_lease import peer_uid, sample_peer
    from .client_macos_processes import inspect_identity

    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise MacReadyError("positive finite readiness timeout required")
    if expected_uid is not None and (type(expected_uid) is not int or expected_uid <= 0):
        raise MacReadyError("explicit registered UID required")
    credential = uid_sample or peer_uid
    connector = connect or (lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
    sampler = sample or sample_peer
    deadline = time.monotonic() + timeout
    try:
        with connector() as connection:

            def budget():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MacReadyError("readiness query timed out")
                connection.settimeout(remaining)

            budget()
            connection.connect(str(path))
            if expected_uid is not None and credential(connection) != expected_uid:
                raise MacReadyError("readiness peer UID mismatch")
            original = sampler(connection)
            if not isinstance(original, ProcessIdentity):
                raise MacReadyError("readiness peer unavailable")
            nonce = "ready_" + uuid.uuid4().hex
            budget()
            connection.sendall(
                json.dumps({"action": "status", "upgrade_id": nonce}).encode() + b"\n"
            )
            data = b""
            while not data.endswith(b"\n"):
                budget()
                chunk = connection.recv(min(4096, 4097 - len(data)))
                if not chunk or len(data) + len(chunk) > 4096:
                    raise MacReadyError("invalid readiness response")
                data += chunk
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("upgrade_id") != nonce:
                raise MacReadyError("readiness response mismatch")
            # status closes its socket after replying. LOCAL_PEERPID is not
            # guaranteed after peer close; recheck captured PID creation time,
            # never reconnect or substitute a newly discovered socket peer.
            current = sampler(connection) if sample is not None else inspect_identity(original.pid)
            if current != original:
                raise MacReadyError("readiness peer changed")
            validate_ready(result, expected_version=expected_version, peer_identity=original)
            return original
    except MacReadyError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise MacReadyError("readiness query unavailable or invalid") from exc


def query_registered_ready(
    *, expected_version, timeout=5, account_reader=None, agent_validator=None, query=None
):
    """Read-only registered ownership checks around a bound status query.

    Management exclusion is the lifecycle caller's responsibility. Does not
    assert the loaded launchd definition matches disk, nor start any job.
    """
    from .client_macos_registration import registered_account, validate_agent

    read_account = account_reader or registered_account
    validate = agent_validator or validate_agent
    query_status = query or query_ready
    account = read_account()
    validate(account)
    peer = query_status(
        account.layout.state / "maintenance.sock",
        expected_version=expected_version,
        timeout=timeout,
        expected_uid=account.layout.uid,
    )
    current = read_account()
    if current != account:
        raise MacReadyError("registration changed during readiness observation")
    validate(current)
    return peer
