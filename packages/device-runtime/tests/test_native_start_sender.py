"""Unwired sender candidate: structure is not execution authorization."""

import copy
import json
import subprocess
import sys

import pytest

from device_runtime.native_start_sender import PROTOCOL, TREE_GUARD, valid_tree_receipt


def test_receipt_is_exact_and_type_sensitive():
    intent = {"sender_protocol": PROTOCOL, "operation": "one", "nested": {"n": 1}}
    receipt = {
        "schema_version": 1,
        "state": "start_process_tree_reaped",
        "mechanism": "linux-subreaper-v1",
        "intent": copy.deepcopy(intent),
        "returncode": 0,
    }
    assert valid_tree_receipt(receipt, intent)
    for field, value in (
        ("schema_version", True),
        ("returncode", False),
        ("returncode", "0"),
        ("state", "sender_reaped"),
        ("mechanism", "other"),
    ):
        bad = copy.deepcopy(receipt)
        bad[field] = value
        assert not valid_tree_receipt(bad, intent)
    bad = copy.deepcopy(receipt)
    bad["intent"]["nested"]["n"] = True
    assert not valid_tree_receipt(bad, intent)
    bad = copy.deepcopy(receipt)
    bad["extra"] = 1
    assert not valid_tree_receipt(bad, intent)
    assert not valid_tree_receipt(receipt, {**intent, "operation": "other"})
    assert not valid_tree_receipt(receipt, {**intent, "sender_protocol": "old"})
    assert not valid_tree_receipt(receipt, None)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only candidate")
@pytest.mark.parametrize("intent", [{}, {"sender_protocol": "old"}, [], None])
def test_bad_protocol_refused_before_action(tmp_path, intent):
    receipt = tmp_path / "receipt.json"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", TREE_GUARD, str(receipt), json.dumps(intent), "fixture.service"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"start tree intent protocol required" in result.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only candidate")
@pytest.mark.parametrize("kind", ["file", "dangling-symlink"])
def test_existing_receipt_refused_before_action(tmp_path, kind):
    receipt = tmp_path / "receipt.json"
    if kind == "file":
        receipt.write_bytes(b"preserve")
    else:
        receipt.symlink_to("missing")
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", TREE_GUARD, str(receipt),
         json.dumps({"sender_protocol": PROTOCOL}), "fixture.service"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"start tree receipt already exists" in result.stderr
    if kind == "file":
        assert receipt.read_bytes() == b"preserve"
    else:
        assert receipt.is_symlink()
