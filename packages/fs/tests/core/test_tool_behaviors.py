"""测试 MCP Filesystem Server - 更新版本"""

import pytest
import tempfile
import json
import base64
import stat
from pathlib import Path
import main


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


class TestNewReadFile:
    """测试新的 read_file 功能"""

    def test_read_by_chars(self, sample_files):
        """测试按字符读取"""
        read_file = get_original_function('read_file')
        result_json = read_file(str(sample_files["text_file"]), type="chars")
        result = load_tool_result_payload(result_json)

        assert "content" in result
        assert "Hello World" in result["content"]
        assert result["truncated"] == False
        assert result_json.meta["summary"] == "read test.txt"

    def test_read_by_lines(self, sample_files):
        """测试按行读取"""
        read_file = get_original_function('read_file')
        result_json = read_file(str(sample_files["text_file"]), type="lines", limit=2)
        result = load_tool_result_payload(result_json)

        assert result["lines_read"] == 2
        assert "Hello World" in result["content"]
        assert result["total_lines"] == 3

    def test_read_backward(self, sample_files):
        """测试从后往前读取"""
        read_file = get_original_function('read_file')
        result_json = read_file(
            str(sample_files["text_file"]),
            type="lines",
            limit=1,
            direction="backward"
        )
        result = load_tool_result_payload(result_json)

        assert "Line 3" in result["content"]

    def test_read_with_offset(self, sample_files):
        """测试带偏移量读取"""
        read_file = get_original_function('read_file')
        result_json = read_file(
            str(sample_files["text_file"]),
            type="lines",
            offset=1,
            limit=1
        )
        result = load_tool_result_payload(result_json)

        assert "Line 2" in result["content"]
        assert result["lines_read"] == 1

    def test_read_file_error_path_keeps_type_parameter_compatible(self):
        """type 参数不应遮蔽内置 type() 导致错误路径二次异常"""
        read_file = get_original_function('read_file')
        result_json = read_file("\0", type="chars")
        result = load_tool_result_payload(result_json)

        assert result["error"] == "ValueError"
        assert "embedded null" in result["message"]


class TestJSONReturns:
    """测试 JSON 返回格式"""

    def test_write_file_json(self, temp_dir):
        """测试 write_file 返回 JSON"""
        write_file = get_original_function('write_file')
        test_file = temp_dir / "new.txt"
        result_json = write_file(str(test_file), "test content")
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert result["summary"]["bytes_written"] == len("test content".encode("utf-8"))
        assert result["operations"][0]["type"] == "write_file"
        assert test_file.exists()


class TestTextEditingTools:
    """测试 insert_file"""

    def test_insert_file_by_line(self, sample_files):
        """按行号插入文本"""
        insert_file = get_original_function('insert_file')
        target = sample_files["text_file"]

        result_json = insert_file(str(target), "Inserted\n", line=2)
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert result["operations"][0]["mode"] == "line"
        assert "diff" not in result
        assert target.read_text(encoding="utf-8") == "Hello World\nInserted\nLine 2\nLine 3\n"
        assert result_json.meta["summary"] == "insert test.txt"

    def test_insert_file_before_anchor(self, sample_files):
        """在锚点文本前插入"""
        insert_file = get_original_function('insert_file')
        target = sample_files["text_file"]

        result_json = insert_file(str(target), ">>", before="Line 2")
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert result["operations"][0]["mode"] == "before"
        assert ">>Line 2" in target.read_text(encoding="utf-8")

    def test_insert_file_dry_run(self, sample_files):
        """insert_file dry_run 不落盘"""
        insert_file = get_original_function('insert_file')
        target = sample_files["text_file"]
        original = target.read_text(encoding="utf-8")

        result_json = insert_file(str(target), "tail\n", after="Line 3\n", dry_run=True)
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert result["dry_run"] == True
        assert result["operations"][0]["status"] == "dry_run_ok"
        assert "diff" not in result
        assert target.read_text(encoding="utf-8") == original

    def test_insert_file_after_anchor_inserts_immediately_after_match(self, temp_dir):
        """after 锚点按字符串匹配末尾插入，不会自动跳到下一行"""
        insert_file = get_original_function('insert_file')
        target = temp_dir / "after-anchor.txt"
        target.write_text("line 2: foo bar\nline 3: baz qux", encoding="utf-8")

        result_json = insert_file(str(target), "new line\n", after="line 2: foo bar")
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert result["operations"][0]["mode"] == "after"
        assert target.read_text(encoding="utf-8") == "line 2: foo barnew line\n\nline 3: baz qux"

    def test_insert_file_include_diff(self, sample_files):
        """insert_file include_diff 时返回 diff"""
        insert_file = get_original_function('insert_file')
        target = sample_files["text_file"]

        result_json = insert_file(
            str(target),
            "tail\n",
            after="Line 3\n",
            dry_run=True,
            include_diff=True,
        )
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert result["diff"]["truncated"] == False
        assert "tail" in result["diff"]["content"]

    def test_insert_file_error_returns_json(self, sample_files):
        """insert_file 失败时返回结构化 JSON"""
        insert_file = get_original_function('insert_file')
        target = sample_files["text_file"]

        result_json = insert_file(str(target), "tail\n", before="missing anchor")
        result = load_tool_result_payload(result_json)

        assert result["success"] == False
        assert result["applied"] == False
        assert result["error"]["code"] == "INSERT_FILE_ERROR"
        assert result["error"]["details"]["before"] == "missing anchor"

    def test_insert_file_at_end(self, sample_files):
        """直接追加到文件末尾"""
        insert_file = get_original_function('insert_file')
        target = sample_files["text_file"]

        result_json = insert_file(str(target), "Tail\n", at_end=True)
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert result["operations"][0]["mode"] == "at_end"
        assert target.read_text(encoding="utf-8") == "Hello World\nLine 2\nLine 3\nTail\n"

    def test_insert_file_preserves_executable_bit(self, temp_dir):
        """插入已有文件时保留可执行权限"""
        insert_file = get_original_function('insert_file')
        target = temp_dir / "script.sh"
        target.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        target.chmod(0o755)

        result_json = insert_file(str(target), "echo newer\n", at_end=True)
        result = load_tool_result_payload(result_json)

        assert result["success"] == True
        assert stat.S_IMODE(target.stat().st_mode) == 0o755


class TestLargeFileHandling:
    """测试大文件处理"""

    def test_large_content_truncation(self, temp_dir):
        """测试大内容截断"""
        # 创建一个大文件
        large_file = temp_dir / "large.txt"
        large_content = "x" * 20000  # 超过默认的 10000 字符限制
        large_file.write_text(large_content)

        read_file = get_original_function('read_file')
        result_json = read_file(str(large_file), max_chars=5000)
        result = load_tool_result_payload(result_json)

        assert result["truncated"] == True
        assert "temp_file" not in result  # read_file 不保存到临时文件
        assert result["total_content_chars"] == 20000
        assert result["returned_chars"] == 5000
        assert len(result["content"]) == 5000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
