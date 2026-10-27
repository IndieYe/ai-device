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


def test_tree_receipt_requires_exact_type_sensitive_binding():
    import copy

    from device_runtime.native_package_sender import valid_tree_receipt

    intent = {"sender_protocol": PROTOCOL, "operation": "one", "nested": {"retry": False, "n": 1}}
    receipt = {
        "schema_version": 1,
        "state": "package_process_tree_reaped",
        "mechanism": "linux-subreaper-v1",
        "intent": copy.deepcopy(intent),
        "returncode": 0,
    }
    assert valid_tree_receipt(receipt, intent)
    for field, value in (
        ("schema_version", True), ("returncode", False), ("returncode", "0"),
        ("state", "sender_reaped"), ("mechanism", "other"),
    ):
        changed = copy.deepcopy(receipt)
        changed[field] = value
        assert not valid_tree_receipt(changed, intent)
    changed = copy.deepcopy(receipt)
    changed["intent"]["nested"]["retry"] = 0
    assert not valid_tree_receipt(changed, intent)
    changed = copy.deepcopy(receipt)
    changed["intent"]["nested"]["n"] = True
    assert not valid_tree_receipt(changed, intent)
    changed = copy.deepcopy(receipt)
    changed["unexpected"] = "extra"
    assert not valid_tree_receipt(changed, intent)
    changed = copy.deepcopy(receipt)
    del changed["mechanism"]
    assert not valid_tree_receipt(changed, intent)
    assert not valid_tree_receipt(receipt, {**intent, "operation": "two"})
    assert not valid_tree_receipt(receipt, {**intent, "sender_protocol": "old"})
    assert not valid_tree_receipt({"state": "sender_reaped", "intent": intent, "returncode": 0}, intent)
    invalid = copy.deepcopy(intent)
    invalid["nested"]["n"] = float("nan")
    receipt["intent"] = copy.deepcopy(invalid)
    assert not valid_tree_receipt(receipt, invalid)
