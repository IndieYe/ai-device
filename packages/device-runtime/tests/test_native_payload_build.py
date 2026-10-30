import importlib.util
from pathlib import Path

import pytest


def load_builder():
    path = Path(__file__).parents[1] / "scripts/assemble-native-payload.py"
    spec = importlib.util.spec_from_file_location("native_payload_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_payload_rejects_unlocked_tool_before_creating_output(tmp_path):
    wheel = tmp_path / "uv-wrong.whl"
    wheel.write_bytes(b"not a tool")
    output = tmp_path / "payload"
    with pytest.raises(ValueError, match="tool lock"):
        load_builder().assemble(
            output, tmp_path, tmp_path, tmp_path / "constraints", wheel, "a" * 40
        )
    assert not output.exists()
