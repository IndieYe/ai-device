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


def _bootstrap_requirements():
    """Names the package build pre-installs into the shipped runtime."""
    script = (Path(__file__).parents[1] / "scripts/build-native-linux.sh").read_text()
    marker = "for name in packaging"
    start = script.index(marker)
    body = script[start : script.index("; do", start)]
    return set(body.split())


def test_bootstrap_carries_every_import_the_signed_path_reaches():
    """A dependency used while unpacking must be inside the shipped runtime.

    ``native_candidate`` decompresses a ``.zst`` DEB member through
    ``zstandard``. Leaving it out of the bootstrap only surfaced once dpkg
    started emitting zstd, and then every upgrade failed in the owner step with
    ModuleNotFoundError rather than at build time.
    """
    requirements = _bootstrap_requirements()
    for name in ("zstandard", "cryptography", "websockets", "pydantic"):
        assert name in requirements, f"bootstrap must ship {name}"


def test_bootstrap_self_check_exercises_the_zstd_extractor():
    """The build-time self-check must import what the zstd path imports."""
    script = (Path(__file__).parents[1] / "scripts/build-native-linux.sh").read_text()
    selfcheck = [line for line in script.splitlines() if "device_url(" in line]
    assert selfcheck, "the build self-check must exercise the client import path"
    assert "import zstandard" in selfcheck[0]
