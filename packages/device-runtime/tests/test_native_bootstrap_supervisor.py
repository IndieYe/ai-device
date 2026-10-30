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


def test_reap_unknown_holds_until_success_and_reports_failure(monkeypatch, capfd):
    attempts = []

    def attempt():
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise b.BootstrapError('unknown')
        if len(attempts) == 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(b.time, 'sleep', lambda _seconds: None)
    with pytest.raises(b.BootstrapError, match='reaped after unknown cleanup'):
        b._hold_until_direct_child_reaped(attempt)
    assert len(attempts) == 3
    assert capfd.readouterr().err.count('holding leases and retrying') == 1


def test_reap_retry_survives_interrupted_sleep_and_diagnostic_failure(monkeypatch):
    attempts = []

    def attempt():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError('unknown')

    def interrupted_sleep(_seconds):
        raise KeyboardInterrupt()

    def failed_write(*_args):
        raise OSError('closed stderr')

    monkeypatch.setattr(b.time, 'sleep', interrupted_sleep)
    monkeypatch.setattr(b.os, 'write', failed_write)
    with pytest.raises(b.BootstrapError, match='reaped after unknown cleanup'):
        b._hold_until_direct_child_reaped(attempt)
    assert len(attempts) == 2


def test_gated_reap_only_returns_after_exact_direct_child(monkeypatch):
    results = iter([(0, 0), (42, 0)])
    monkeypatch.setattr(b.os, 'waitpid', lambda *_args: next(results))
    monkeypatch.setattr(b.time, 'sleep', lambda _seconds: None)
    b._reap_gated_child_waitpid(42, deadline=b.time.monotonic() + 5)


def test_gated_reap_timeout_is_unknown(monkeypatch):
    monkeypatch.setattr(b.os, 'waitpid', lambda *_args: (0, 0))
    with pytest.raises(b.BootstrapError, match='cleanup unknown'):
        b._reap_gated_child_waitpid(42, deadline=b.time.monotonic() - 1)


def test_query_subreaper_refuses_existing_child_without_reaping(monkeypatch):
    calls = []

    def waitid(*args):
        calls.append(args)  # Existing child is alive.

    monkeypatch.setattr(b.os, 'waitid', waitid)
    with pytest.raises(b.BootstrapError, match='existing children'), b._exclusive_query_subreaper():
        pytest.fail('must not enter')
    assert calls[0][2] & b.os.WNOWAIT


def test_adopted_children_waits_and_reaps_until_echild(monkeypatch):
    events = iter([(0, 0), (42, 0), ChildProcessError()])
    sleeps = []

    def waitpid(*_args):
        value = next(events)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(b.os, 'waitpid', waitpid)
    monkeypatch.setattr(b.time, 'sleep', sleeps.append)
    b._wait_adopted_query_children()
    assert sleeps == [0.1]


def test_adopted_children_unknown_does_not_become_echild(monkeypatch):
    def fail(*_args):
        raise OSError('unknown wait error')

    monkeypatch.setattr(b.os, 'waitpid', fail)
    with pytest.raises(OSError, match='unknown wait error'):
        b._wait_adopted_query_children()


def test_query_intent_publication_is_no_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(b.helper, '_path', lambda path: path)
    monkeypatch.setattr(b.helper, '_trusted', lambda *_a, **_kw: None)
    intent = b._publish_query_supervisor_intent(tmp_path, run_id='a'*32, snapshot_sha256='b'*64)
    path = tmp_path / 'query-supervisor.intent.json'
    before = path.read_bytes()
    assert intent['run_id'] == 'a'*32
    with pytest.raises(b.BootstrapError, match='already exists'):
        b._publish_query_supervisor_intent(tmp_path, run_id='c'*32, snapshot_sha256='b'*64)
    assert path.read_bytes() == before


def test_query_intent_rejects_existing_dangling_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(b.helper, '_path', lambda path: path)
    monkeypatch.setattr(b.helper, '_trusted', lambda *_a, **_kw: None)
    path = tmp_path / 'query-supervisor.intent.json'
    path.symlink_to(tmp_path / 'missing')
    with pytest.raises(b.BootstrapError, match='already exists'):
        b._publish_query_supervisor_intent(tmp_path, run_id='a'*32, snapshot_sha256='b'*64)
    assert path.is_symlink()


def test_query_intent_directory_fsync_failure_is_unknown(tmp_path, monkeypatch):
    import stat

    monkeypatch.setattr(b.helper, '_path', lambda path: path)
    monkeypatch.setattr(b.helper, '_trusted', lambda *_a, **_kw: None)
    real_fsync = b.os.fsync

    def fsync(fd):
        if stat.S_ISDIR(b.os.fstat(fd).st_mode):
            raise OSError('injected directory fsync failure')
        return real_fsync(fd)

    monkeypatch.setattr(b.os, 'fsync', fsync)
    with pytest.raises(b.BootstrapError, match='failed or unknown'):
        b._publish_query_supervisor_intent(tmp_path, run_id='a'*32, snapshot_sha256='b'*64)
    path = tmp_path / 'query-supervisor.intent.json'
    assert path.exists()
    with pytest.raises(b.BootstrapError, match='already exists'):
        b._publish_query_supervisor_intent(tmp_path, run_id='a'*32, snapshot_sha256='b'*64)


@pytest.mark.parametrize('run_id,digest', [('a'*31, 'b'*64), ('a'*32, 'b'*63), (True, 'b'*64)])
def test_query_intent_rejects_invalid_input_before_publish(tmp_path, run_id, digest):
    with pytest.raises(b.BootstrapError, match='invalid query supervisor'):
        b._publish_query_supervisor_intent(tmp_path, run_id=run_id, snapshot_sha256=digest)
    assert not list(tmp_path.iterdir())


def test_query_intent_file_fsync_failure_has_no_published_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(b.helper, '_path', lambda path: path)
    monkeypatch.setattr(b.helper, '_trusted', lambda *_a, **_kw: None)

    def fail(_fd):
        raise OSError('injected file fsync failure')

    monkeypatch.setattr(b.os, 'fsync', fail)
    with pytest.raises(b.BootstrapError, match='failed or unknown'):
        b._publish_query_supervisor_intent(tmp_path, run_id='a'*32, snapshot_sha256='b'*64)
    assert not (tmp_path / 'query-supervisor.intent.json').exists()


def test_query_intent_does_not_accept_malformed_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(b.helper, '_path', lambda path: path)
    monkeypatch.setattr(b.helper, '_trusted', lambda *_a, **_kw: None)
    path = tmp_path / 'query-supervisor.intent.json'
    path.write_bytes(b'not-json')
    with pytest.raises(b.BootstrapError, match='already exists'):
        b._publish_query_supervisor_intent(tmp_path, run_id='a'*32, snapshot_sha256='b'*64)
    assert path.read_bytes() == b'not-json'
