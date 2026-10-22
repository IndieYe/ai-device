"""Reusable operations and a built-wheel installation with no MCP dependencies."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from mcp_server_fs import core


def test_local_text_operations_and_plain_results(tmp_path):
    target = tmp_path / "nested/file.txt"
    written = core.write_file(str(target), "alpha\nbeta\n")
    assert isinstance(written, core.FileResult)
    assert written.data["success"] is True
    assert written.metadata["path"] == str(target)
    assert (
        core.read_file(str(target), type="lines", offset=1, limit=1).data["content"]
        == "beta\n"
    )
    assert (
        core.read_file(str(target), direction="backward", limit=5).data["content"]
        == "beta\n"
    )
    assert core.read_file(str(target), max_chars=3).data["truncated"] is True
    preview = core.edit_file(
        str(target),
        [{"oldText": "alpha", "newText": "first"}],
        dry_run=True,
        include_diff=True,
    )
    assert preview.data["dry_run"] is True
    assert "-alpha" in preview.data["diff"]["content"]
    assert target.read_text() == "alpha\nbeta\n"
    edited = core.edit_file(str(target), [{"oldText": "alpha", "newText": "first"}])
    assert edited.data["summary"]["edits_applied"] == 1
    inserted = core.insert_file(str(target), "last\n", at_end=True)
    assert inserted.data["success"] is True
    assert target.read_text() == "first\nbeta\nlast\n"
    patched = core.apply_patch(
        f"*** Begin Patch\n*** Update File: {target}\n@@\n-first\n+changed\n*** End Patch"
    )
    assert patched.data["success"] is True
    assert patched.metadata["summary"] == "Update file.txt"
    assert target.read_text().startswith("changed\n")
    deleted = core.apply_patch(
        f"*** Begin Patch\n*** Delete File: {target}\n*** End Patch"
    )
    assert deleted.data["success"] is True
    assert not target.exists()


def test_local_errors_and_atomic_write_failure(tmp_path):
    target = tmp_path / "file.txt"
    assert core.read_file(str(target)).data["error"] == "FileNotFoundError"
    core.write_file(str(target), "original")
    failed_edit = core.edit_file(
        str(target), [{"oldText": "absent", "newText": "other"}]
    )
    assert failed_edit.data["success"] is False
    assert target.read_text() == "original"
    failed_insert = core.insert_file(str(target), "other")
    assert failed_insert.data["success"] is False
    assert core.apply_patch("not a patch").data["success"] is False
    with patch(
        "mcp_server_fs.io_utils.os.replace", side_effect=OSError("replace failed")
    ):
        failed_write = core.write_file(str(target), "replacement")
    assert failed_write.data["error"]["code"] == "WRITE_FILE_ERROR"
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_diff_fallback_uses_explicit_directory(tmp_path):
    target = tmp_path / "large.txt"
    original = "old-" + "x" * 12000
    replacement = "new-" + "y" * 12000
    core.write_file(str(target), original)
    output_dir = tmp_path / "caller-output"
    result = core.edit_file(
        str(target),
        [{"oldText": original, "newText": replacement}],
        dry_run=True,
        include_diff=True,
        temp_dir=output_dir,
    )
    diff = result.data["diff"]
    assert diff["truncated"] is True
    full_output = Path(diff["temp_file"])
    assert full_output.parent == output_dir
    assert original in full_output.read_text()
    assert target.read_text() == original


def test_image_core_returns_original_bytes_and_typed_errors(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (2, 3), "red").save(path)
    result = core.read_image(str(path))
    assert isinstance(result, core.ImageResult)
    assert result.path == str(path)
    assert result.info.data == path.read_bytes()
    assert result.info.mime_type == "image/png"
    assert (result.info.width, result.info.height) == (2, 3)
    with pytest.raises(core.ImageReadError) as caught:
        core.read_image(str(tmp_path / "missing.png"))
    assert caught.value.code == "IMAGE_NOT_FOUND"
    with pytest.raises(core.ImageReadError) as caught:
        core.read_image(str(path), max_bytes=1)
    assert caught.value.code == "IMAGE_TOO_LARGE"


def test_built_wheel_runs_without_mcp_or_import_side_effects(tmp_path):
    """Install the actual wheel, not the source tree or a mocked import graph."""
    root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / "dist"
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    wheel = next(wheel_dir.glob("*.whl"))
    work = tmp_path / "execution"
    work.mkdir()
    (work / ".env").write_text("FS_CORE_IMPORT_MARKER=loaded\n")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("FS_CORE_IMPORT_MARKER", None)
    env["MCP_TEMP_DIR"] = str(work / "must-not-be-created")
    code = textwrap.dedent("""
        import importlib.util
        import os
        from pathlib import Path
        import sys
        import tempfile
        import PIL.Image

        for name in ("fastmcp", "mcp", "pydantic", "dotenv"):
            assert importlib.util.find_spec(name) is None, name
        before = set(Path.cwd().iterdir())
        from mcp_server_fs import core
        assert set(Path.cwd().iterdir()) == before
        assert "mcp_server_fs.config" not in sys.modules
        assert "mcp_server_fs.server" not in sys.modules
        assert "FS_CORE_IMPORT_MARKER" not in os.environ
        assert not Path(os.environ["MCP_TEMP_DIR"]).exists()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "file.txt"
            assert core.write_file(str(path), "first").data["success"]
            assert core.read_file(str(path)).data["content"] == "first"
            assert core.edit_file(str(path), [{"oldText":"first","newText":"second"}]).data["success"]
            assert core.insert_file(str(path), "!", at_end=True).data["success"]
            assert path.read_text() == "second!"
            patch = f"*** Begin Patch\\n*** Add File: {root / 'added.txt'}\\n+added\\n*** End Patch"
            assert core.apply_patch(patch).data["success"]
            image = root / "image.png"
            PIL.Image.new("RGB", (1, 1)).save(image)
            assert core.read_image(str(image)).info.data == image.read_bytes()
        print("standalone wheel: six operations passed")
    """)
    executed = subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--with",
            str(wheel),
            "python",
            "-I",
            "-c",
            code,
        ],
        cwd=work,
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert "six operations passed" in executed.stdout


def test_mcp_diff_preserves_configured_output_directory(tmp_path, monkeypatch):
    """Configuration is consumed by the adapter, never by the core import."""
    import importlib

    adapter = importlib.import_module("mcp_server_fs.tools.edit_file")
    directory = tmp_path / "mcp-output"
    monkeypatch.setattr(adapter, "TEMP_DIR", directory)
    path = tmp_path / "file.txt"
    path.write_text("x" * 12000)
    result = adapter.edit_file(
        str(path),
        [{"oldText": "x" * 12000, "newText": "y" * 12000}],
        dry_run=True,
        include_diff=True,
    )
    payload = json.loads(result.content[0].text)
    assert Path(payload["diff"]["temp_file"]).parent == directory


@pytest.mark.parametrize("failure_point", ["stat", "open"])
def test_image_access_failure_is_a_typed_core_error(tmp_path, failure_point):
    from types import SimpleNamespace
    from unittest.mock import Mock

    source = Mock()
    source.exists.return_value = True
    source.is_file.return_value = True
    source.stat.return_value = SimpleNamespace(st_size=10)
    getattr(source, failure_point).side_effect = PermissionError("access denied")
    with (
        patch.object(importlib.import_module("mcp_server_fs.core.read_image"), "validate_path", return_value=source),
        pytest.raises(core.ImageReadError) as caught,
    ):
        core.read_image(str(tmp_path / "image.png"))
    assert caught.value.code == "IMAGE_READ_ERROR"
    assert "access denied" in caught.value.details["reason"]


def test_insert_diff_fallback_uses_explicit_directory(tmp_path):
    target = tmp_path / "large.txt"
    target.write_text("first\n")
    directory = tmp_path / "insert-output"
    result = core.insert_file(
        str(target),
        "added\n" * 3000,
        at_end=True,
        dry_run=True,
        include_diff=True,
        temp_dir=directory,
    )
    assert result.data["diff"]["truncated"] is True
    assert Path(result.data["diff"]["temp_file"]).parent == directory
    assert target.read_text() == "first\n"


def test_inspector_enables_mcp_extra_without_network(tmp_path):
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "pnpm"
    executable.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    executable.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    result = subprocess.run(
        ["bash", str(root / "scripts/inspector.sh"), "--transport", "stdio"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "dlx",
        "@modelcontextprotocol/inspector",
        "uv",
        "run",
        "--directory",
        str(root),
        "--extra",
        "mcp",
        "mcp-server-fs",
        "--transport",
        "stdio",
    ]
