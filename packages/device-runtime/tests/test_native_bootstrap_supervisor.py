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
