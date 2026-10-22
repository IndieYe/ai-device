"""Startup logging must retain the MCP listener address, not the removed SSH host."""

import logging

import pytest

from mcp_server_bash import server


@pytest.mark.parametrize("transport", ["stdio", "sse", "http"])
def test_startup_log_formats_listener_address(monkeypatch, caplog, tmp_path, transport):
    logger = logging.getLogger("test.bash.startup")
    calls = []
    monkeypatch.setattr(server, "LOGGER", logger)
    monkeypatch.setattr(server, "TRANSPORT", transport)
    monkeypatch.setattr(server, "SSE_HOST", "127.0.0.1")
    monkeypatch.setattr(server, "SSE_PORT", 8765)
    monkeypatch.setattr(server, "DEFAULT_CWD", str(tmp_path))
    monkeypatch.setattr(server, "get_shell_config", lambda: ("/bin/bash", []))
    # Do not clean real directories or start a listening MCP process.
    monkeypatch.setattr(server, "cleanup_expired_artifacts", lambda *_: 0)
    monkeypatch.setattr(server, "cleanup_expired_outputs", lambda *_: 0)
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.append(kwargs), raising=False)

    with caplog.at_level(logging.INFO, logger=logger.name):
        assert server.main() == 0

    messages = [record.getMessage() for record in caplog.records if record.name == logger.name]
    assert (
        f"server starting: transport={transport} host=127.0.0.1 port=8765 "
        f"cwd={tmp_path} shell=/bin/bash"
    ) in messages
    expected = {} if transport == "stdio" else {
        "transport": transport, "host": "127.0.0.1", "port": 8765,
    }
    assert calls == [expected]
