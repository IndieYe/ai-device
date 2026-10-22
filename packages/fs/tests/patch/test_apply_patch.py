"""Tests for the apply_patch tool."""

from __future__ import annotations

import json
import stat
import tempfile
from pathlib import Path

import pytest

import main


def get_original_function(func_name):
    """Extract the original callable from a FastMCP tool."""
    tool = getattr(main, func_name)
    if hasattr(tool, "fn"):
        return tool.fn
    return tool


def load_tool_result_json(tool_result):
    """Decode FastMCP ToolResult content into JSON."""
    content = tool_result.content
    if isinstance(content, list) and content:
        first = content[0]
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return json.loads(text)
    if isinstance(content, str):
        return json.loads(content)
    raise TypeError(f"Unsupported ToolResult content: {type(content).__name__}")


@pytest.fixture
def temp_dir():
    """Create a temporary directory for patch tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir).resolve()


def test_apply_patch_add_file(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "new.txt"

    patch = f"""*** Begin Patch
*** Add File: {target}
+hello
+world
*** End Patch"""

    tool_result = apply_patch(patch)
    result = load_tool_result_json(tool_result)

    assert result["success"] is True
    assert target.read_text(encoding="utf-8") == "hello\nworld\n"
    assert result["summary"]["files_added"] == 1
    assert tool_result.meta["summary"] == "Add new.txt"


def test_apply_patch_update_file_multiple_hunks(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "app.py"
    target.write_text('host = "localhost"\nport = 3000\n', encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {target}
@@
-host = "localhost"
+host = "0.0.0.0"
@@
-port = 3000
+port = 4000
*** End Patch"""

    tool_result = apply_patch(patch)
    result = load_tool_result_json(tool_result)

    assert result["success"] is True
    assert target.read_text(encoding="utf-8") == 'host = "0.0.0.0"\nport = 4000\n'
    assert result["summary"]["files_updated"] == 1
    assert result["summary"]["hunks_applied"] == 2
    assert tool_result.meta["summary"] == "Update app.py"


def test_apply_patch_move_file(temp_dir):
    apply_patch = get_original_function("apply_patch")
    source = temp_dir / "config.old.py"
    target = temp_dir / "config.py"
    source.write_text("DEBUG = True\n", encoding="utf-8")
    source.chmod(0o755)

    patch = f"""*** Begin Patch
*** Update File: {source}
*** Move to: {target}
@@
-DEBUG = True
+DEBUG = False
*** End Patch"""

    tool_result = apply_patch(patch)
    result = load_tool_result_json(tool_result)

    assert result["success"] is True
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "DEBUG = False\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert result["summary"]["files_moved"] == 1
    assert tool_result.meta["summary"] == "Move config.old.py → config.py"


def test_apply_patch_delete_file(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "legacy.py"
    target.write_text("print('legacy')\n", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Delete File: {target}
*** End Patch"""

    tool_result = apply_patch(patch)
    result = load_tool_result_json(tool_result)

    assert result["success"] is True
    assert not target.exists()
    assert result["summary"]["files_deleted"] == 1


def test_apply_patch_dry_run_does_not_write(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "sample.txt"
    target.write_text("before\n", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {target}
@@
-before
+after
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch, dry_run=True))

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["operations"][0]["status"] == "dry_run_ok"
    assert target.read_text(encoding="utf-8") == "before\n"


def test_apply_patch_matches_last_line_without_trailing_newline(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "sample.txt"
    target.write_text("Line 1\nLine 5 - Goodbye!", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {target}
@@
-Line 5 - Goodbye!
+Line 5 - Farewell!
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is True
    assert target.read_text(encoding="utf-8") == "Line 1\nLine 5 - Farewell!\n"


def test_apply_patch_ambiguous_context_fails(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "dup.txt"
    target.write_text("same\nsame\n", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {target}
@@
-same
+different
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_CONTEXT_AMBIGUOUS"
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


def test_apply_patch_rolls_back_on_failure(temp_dir):
    apply_patch = get_original_function("apply_patch")
    updated = temp_dir / "updated.txt"
    missing = temp_dir / "missing.txt"
    added = temp_dir / "added.txt"
    updated.write_text("old\n", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {updated}
@@
-old
+new
*** Add File: {added}
+created
*** Update File: {missing}
@@
-x
+y
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_TARGET_NOT_FOUND"
    assert updated.read_text(encoding="utf-8") == "old\n"
    assert not added.exists()


def test_apply_patch_parse_error(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "broken.txt"

    patch = f"""*** Begin Patch
*** Update File: {target}
-oops
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_PARSE_ERROR"


def test_apply_patch_rejects_invalid_path():
    apply_patch = get_original_function("apply_patch")

    patch = """*** Begin Patch
*** Add File: \0
+bad
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_INVALID_PATH"


def test_apply_patch_rejects_binary_file(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "binary.bin"
    target.write_bytes(b"\xff\xfe\x00\x01")

    patch = f"""*** Begin Patch
*** Update File: {target}
@@
-x
+y
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_BINARY_FILE_NOT_SUPPORTED"


def test_apply_patch_rejects_large_file(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "large.txt"
    target.write_text("a" * (2 * 1024 * 1024 + 1), encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {target}
@@
-aaa
+bbb
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_FILE_TOO_LARGE"


def test_apply_patch_rejects_duplicate_move_to(temp_dir):
    apply_patch = get_original_function("apply_patch")
    source = temp_dir / "move-me.txt"
    target_a = temp_dir / "a.txt"
    target_b = temp_dir / "b.txt"
    source.write_text("value\n", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {source}
*** Move to: {target_a}
*** Move to: {target_b}
@@
-value
+new-value
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_PARSE_ERROR"


def test_apply_patch_rejects_move_target_that_exists(temp_dir):
    apply_patch = get_original_function("apply_patch")
    source = temp_dir / "source.txt"
    target = temp_dir / "target.txt"
    source.write_text("value\n", encoding="utf-8")
    target.write_text("existing\n", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {source}
*** Move to: {target}
@@
-value
+new-value
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_MOVE_TARGET_EXISTS"


def test_apply_patch_rejects_update_non_file(temp_dir):
    apply_patch = get_original_function("apply_patch")
    target = temp_dir / "folder"
    target.mkdir()

    patch = f"""*** Begin Patch
*** Update File: {target}
@@
-x
+y
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_UPDATE_NON_FILE"


def test_apply_patch_rejects_conflicting_target_paths(temp_dir):
    apply_patch = get_original_function("apply_patch")
    first = temp_dir / "first.txt"
    second = temp_dir / "second.txt"
    target = temp_dir / "merged.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")

    patch = f"""*** Begin Patch
*** Update File: {first}
*** Move to: {target}
@@
-one
+ONE
*** Update File: {second}
*** Move to: {target}
@@
-two
+TWO
*** End Patch"""

    result = load_tool_result_json(apply_patch(patch))

    assert result["success"] is False
    assert result["error"]["code"] == "PATCH_INVALID_OPERATION"
