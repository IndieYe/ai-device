"""Cooperative start handoff gate, independent of the runtime lifetime lock.

Not wired to production: a Python-level gate cannot protect interpreter loads.
The future native entry must hold shared access BEFORE reading the runtime,
and release only after acquiring runtime.lock. Management holds exclusive
access before requesting old-instance exit and through all protected writes.
Neither side may unlink the preprovisioned start.lock inode.
"""

from contextlib import contextmanager

from .client_macos_lock import _private_lock, provision_runtime_lock


def provision_start_gate(directory, *, uid, gid):
    """Use the same private-file provisioning invariants as runtime.lock."""
    provision_runtime_lock(directory / "start.lock", uid=uid, gid=gid)


@contextmanager
def startup_handoff(directory, *, uid):
    """Shared access only across startup handoff, NOT the whole client run."""
    with _private_lock(directory / "start.lock", uid=uid, shared=True):
        yield


@contextmanager
def prevent_start(directory, *, uid):
    """Nonblocking exclusive access; a concurrent startup causes refusal.

    An already-running client need not hold this gate. Exclusive ownership
    proves exclusion only for cooperating starters, not arbitrary Python CLI,
    launchd state or a surviving Installer writer after owner death.
    """
    with _private_lock(directory / "start.lock", uid=uid):
        yield
