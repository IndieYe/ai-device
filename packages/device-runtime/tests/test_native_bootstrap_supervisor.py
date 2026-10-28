import pytest

from device_runtime import native_bootstrap as b


def test_query_supervisor_refuses_unsupported_platform_before_fork(monkeypatch):
    monkeypatch.setattr(b.sys, 'platform', 'darwin')
    monkeypatch.setattr(b.os, 'fork', lambda: pytest.fail('fork called'))
    with pytest.raises(b.BootstrapError, match='Linux administrator'):
        b._supervise_candidate_queries('/c', 'v', registry='/r', socket_path='/s', group=0, expected_snapshot_sha256='a'*64)


def test_query_supervisor_requires_fixed_digest_before_fork(monkeypatch):
    monkeypatch.setattr(b.sys, 'platform', 'linux')
    monkeypatch.setattr(b.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(b.os, 'fork', lambda: pytest.fail('fork called'))
    with pytest.raises(b.BootstrapError, match='digest'):
        b._supervise_candidate_queries('/c', 'v', registry='/r', socket_path='/s', group=0, expected_snapshot_sha256=None)


def test_parent_watchdog_refuses_already_dead_parent(monkeypatch):
    monkeypatch.setattr(b, '_handshake_pidfd_alive', lambda _fd: False)
    monkeypatch.setattr(b.threading, 'Thread', lambda **_kw: pytest.fail('must not start thread'))
    with pytest.raises(b.BootstrapError, match='already exited'):
        b._start_parent_exit_watchdog(7)


def test_parent_watchdog_thread_start_failure_does_not_continue(monkeypatch):
    monkeypatch.setattr(b, '_handshake_pidfd_alive', lambda _fd: True)
    class FailedThread:
        def start(self):
            raise RuntimeError('thread unavailable')
    monkeypatch.setattr(b.threading, 'Thread', lambda **_kw: FailedThread())
    with pytest.raises(RuntimeError, match='thread unavailable'):
        b._start_parent_exit_watchdog(7)
