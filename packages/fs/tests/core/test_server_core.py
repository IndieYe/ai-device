"""测试 MCP Filesystem Server"""

import pytest
import tempfile
import json
import stat
from pathlib import Path
import main
from mcp_server_fs.formatting import format_size
from mcp_server_fs.path_utils import normalize_path, validate_path


# 获取原始函数（绕过 FastMCP 装饰器）
def get_original_function(func_name):
    """从 FastMCP 工具中提取原始函数"""
    tool = getattr(main, func_name)
    if hasattr(tool, 'fn'):
        return tool.fn
    return tool


def load_tool_result_payload(tool_result):
    """统一解析 FastMCP ToolResult 或旧字符串返回值"""
    if hasattr(tool_result, 'content'):
        content = tool_result.content
        if isinstance(content, list) and content:
            text_content = getattr(content[0], 'text', None)
            if isinstance(text_content, str):
                return json.loads(text_content)
        if isinstance(content, str):
            return json.loads(content)
        return content
    if isinstance(tool_result, str):
        return json.loads(tool_result)
    return tool_result


@pytest.fixture
def temp_dir():
    """创建临时测试目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir).resolve()
        yield temp_path


@pytest.fixture
def sample_files(temp_dir):
    """创建示例文件"""
    # 创建文本文件
    text_file = temp_dir / "test.txt"
    text_file.write_text("Hello World\nLine 2\nLine 3\n")

    # 创建子目录和文件
    sub_dir = temp_dir / "subdir"
    sub_dir.mkdir()
    (sub_dir / "file1.py").write_text("print('hello')")
    (sub_dir / "file2.txt").write_text("content")

    return {
        "text_file": text_file,
        "sub_dir": sub_dir
    }


class TestPathValidation:
    """测试路径验证功能"""

    def test_normalize_path(self, temp_dir):
        """测试路径规范化"""
        path = temp_dir / "test"
        normalized = normalize_path(path)
        assert normalized.is_absolute()

    def test_validate_path_success(self, temp_dir):
        """测试有效路径验证"""
        test_file = temp_dir / "test.txt"
        validated = validate_path(str(test_file))
        assert validated.parent == temp_dir


class TestUtilityFunctions:
    """测试工具函数"""

    def test_format_size(self):
        """测试文件大小格式化"""
        assert format_size(0) == "0 B"
        assert format_size(1024) == "1.00 KB"
        assert format_size(1024 * 1024) == "1.00 MB"
        assert format_size(1024 * 1024 * 1024) == "1.00 GB"


class TestNewReadFile:
    """测试新的 read_file 功能"""

    def test_read_file_full(self, sample_files):
        """测试读取完整文本文件"""
        read_file = get_original_function('read_file')
        result_json = read_file(str(sample_files["text_file"]))
        result = load_tool_result_payload(result_json)
        assert "Hello World" in result["content"]
        assert "Line 2" in result["content"]
        assert result_json.meta["summary"] == "read test.txt"

    def test_read_file_by_lines(self, sample_files):
        """测试按行读取文件"""
        read_file = get_original_function('read_file')
        result_json = read_file(str(sample_files["text_file"]), type="lines", limit=2)
        result = load_tool_result_payload(result_json)
        assert result["lines_read"] == 2
        assert "Hello World" in result["content"]


class TestFileWriting:
    """测试文件写入功能"""

    def test_write_file(self, temp_dir):
        """测试写入文件"""
        write_file = get_original_function('write_file')
        test_file = temp_dir / "new_file.txt"
        result_json = write_file(str(test_file), "Test content")
        result = load_tool_result_payload(result_json)
        assert result["success"] == True
        assert result["summary"]["files_written"] == 1
        assert result["operations"][0]["type"] == "write_file"
        assert test_file.read_text() == "Test content"
        assert result_json.meta["summary"] == "write new_file.txt"

    def test_write_file_overwrite(self, sample_files):
        """测试覆盖现有文件"""
        write_file = get_original_function('write_file')
        result_json = write_file(str(sample_files["text_file"]), "New content")
        result = load_tool_result_payload(result_json)
        assert result["success"] == True
        assert result["summary"]["bytes_written"] == len("New content".encode("utf-8"))
        assert sample_files["text_file"].read_text() == "New content"

    def test_write_file_overwrite_preserves_executable_bit(self, temp_dir):
        """覆盖已有文件时保留可执行权限"""
        write_file = get_original_function('write_file')
        target = temp_dir / "script.sh"
        target.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        target.chmod(0o755)

        result_json = write_file(str(target), "#!/bin/sh\necho new\n")
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert stat.S_IMODE(target.stat().st_mode) == 0o755

    def test_edit_file(self, sample_files):
        """测试编辑文件"""
        edit_file = get_original_function('edit_file')
        edits = [
            {"oldText": "Hello World", "newText": "Hello Python"}
        ]
        result_json = edit_file(str(sample_files["text_file"]), edits, dry_run=False)
        result = load_tool_result_payload(result_json)
        assert result["success"] == True
        assert "diff" not in result
        assert result["summary"]["edits_applied"] == 1
        assert result["operations"][0]["status"] == "applied"
        content = sample_files["text_file"].read_text()
        assert "Hello Python" in content
        assert result_json.meta["summary"] == "edit test.txt"

    def test_edit_file_dry_run(self, sample_files):
        """测试编辑文件预览模式"""
        edit_file = get_original_function('edit_file')
        original_content = sample_files["text_file"].read_text()
        edits = [
            {"oldText": "Hello World", "newText": "Hello Python"}
        ]
        result_json = edit_file(str(sample_files["text_file"]), edits, dry_run=True)
        result = load_tool_result_payload(result_json)
        assert result["success"] == True
        assert result["dry_run"] == True
        assert result["operations"][0]["status"] == "dry_run_ok"
        assert "diff" not in result
        assert sample_files["text_file"].read_text() == original_content

    def test_edit_file_include_diff(self, sample_files):
        """测试编辑文件按需返回 diff"""
        edit_file = get_original_function('edit_file')
        edits = [
            {"oldText": "Hello World", "newText": "Hello Python"}
        ]
        result_json = edit_file(
            str(sample_files["text_file"]),
            edits,
            dry_run=True,
            include_diff=True,
        )
        result = load_tool_result_payload(result_json)
        assert result["success"] == True
        assert result["diff"]["truncated"] == False
        assert "Hello Python" in result["diff"]["content"]

    def test_edit_file_error_returns_json(self, sample_files):
        """测试 edit_file 失败时返回结构化 JSON"""
        edit_file = get_original_function('edit_file')
        edits = [
            {"oldText": "missing", "newText": "Hello Python"}
        ]
        result_json = edit_file(str(sample_files["text_file"]), edits)
        result = load_tool_result_payload(result_json)
        assert result["success"] == False
        assert result["applied"] == False
        assert result["error"]["code"] == "EDIT_FILE_ERROR"
        assert result["error"]["details"]["edits_requested"] == 1

    def test_edit_file_preserves_executable_bit(self, temp_dir):
        """编辑已有文件时保留可执行权限"""
        edit_file = get_original_function('edit_file')
        target = temp_dir / "script.sh"
        target.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        target.chmod(0o755)

        result_json = edit_file(
            str(target),
            [{"oldText": "echo old", "newText": "echo new"}],
        )
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert stat.S_IMODE(target.stat().st_mode) == 0o755


class TestFileOperations:
    """测试文件操作功能"""


class TestErrorHandling:
    """测试错误处理"""

    def test_read_nonexistent_file(self, temp_dir):
        """测试读取不存在的文件"""
        read_file = get_original_function('read_file')
        result = read_file(str(temp_dir / "nonexistent.txt"))
        data = load_tool_result_payload(result)
        assert "error" in data
        assert data["error"] == "FileNotFoundError"
