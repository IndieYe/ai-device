from contextlib import contextmanager
from pathlib import Path

import pytest

from device_runtime import native_bootstrap as b


class Control:
    def __init__(self, records):
        self.records = iter(records)
        self.sent = []
        self.timeouts = []
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, _size):
        return next(self.records)

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def test_gated_child_loads_only_after_valid_go(monkeypatch):
    control = Control([b'x'*32, b'GO'])
    calls = []
    @contextmanager
    def modules(contents):
        calls.append(('loaded', contents, control.closed))
        class Candidate:
            def _serve_credential_queries_locked(self, registry, endpoint, *, group):
                calls.append((registry, endpoint, group))
        yield Candidate()
    monkeypatch.setattr(b, '_verified_helper_modules', modules)
    b._gated_candidate_query_child(control, {'bytes': 1}, Path('/registry'), Path('/socket'), group=0, deadline=b.time.monotonic()+5)
    assert control.sent == [b'READY:'+b'x'*32]
    assert calls == [('loaded', {'bytes': 1}, True), (Path('/registry'), Path('/socket'), 0)]


@pytest.mark.parametrize('records', [[b''], [b'x'*31], [b'x'*32, b''], [b'x'*32, b'NO'], [b'x'*32, b'GOX']])
def test_gated_child_invalid_gate_never_loads(monkeypatch, records):
    control = Control(records)
    monkeypatch.setattr(b, '_verified_helper_modules', lambda *_: pytest.fail('candidate loaded'))
    with pytest.raises(b.BootstrapError):
        b._gated_candidate_query_child(control, {}, '/registry', '/socket', group=0, deadline=b.time.monotonic()+5)
    assert control.closed


def test_gated_child_expired_deadline_never_reads(monkeypatch):
    control = Control([])
    monkeypatch.setattr(b, '_verified_helper_modules', lambda *_: pytest.fail('candidate loaded'))
    with pytest.raises(b.BootstrapError, match='deadline'):
        b._gated_candidate_query_child(control, {}, '/registry', '/socket', group=0, deadline=b.time.monotonic()-1)
    assert control.closed
