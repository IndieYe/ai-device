"""Candidate guard contracts; the production package path remains disconnected."""

import json
import subprocess
import sys

import pytest

from device_runtime.native_package_sender import PROTOCOL, TREE_GUARD


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only candidate guard")
@pytest.mark.parametrize("intent", [{}, {"sender_protocol": "old"}, [], None])
def test_bad_protocol_fails_before_package_execution(tmp_path, intent):
    receipt = tmp_path / "one.sender.json"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", TREE_GUARD, str(receipt), json.dumps(intent), "unused"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"package tree intent protocol required" in result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only candidate guard")
@pytest.mark.parametrize("kind", ["file", "dangling-symlink"])
def test_existing_receipt_fails_before_package_execution(tmp_path, kind):
    receipt = tmp_path / "one.sender.json"
    if kind == "file":
        receipt.write_bytes(b"preserve")
    else:
        receipt.symlink_to("missing")
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", TREE_GUARD, str(receipt),
         json.dumps({"sender_protocol": PROTOCOL}), "unused"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"package tree receipt already exists" in result.stderr
    assert not (tmp_path / "one.package.log").exists()
    if kind == "file":
        assert receipt.read_bytes() == b"preserve"
    else:
        assert receipt.is_symlink()
