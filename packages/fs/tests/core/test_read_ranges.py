"""Direct regressions for zero limits, reverse offsets and truthful line ranges."""

import pytest

from mcp_server_fs.core import read_file
from mcp_server_fs.defaults import HARD_MAX_CHARS


@pytest.mark.parametrize("type", ["chars", "lines"])
@pytest.mark.parametrize("direction", ["forward", "backward"])
def test_zero_limit_and_out_of_range_are_empty(tmp_path, type, direction):
    file = tmp_path / "range.txt"
    file.write_text("a\nb\nc\n")
    assert (
        read_file(str(file), type=type, direction=direction, limit=0).data["content"]
        == ""
    )
    assert (
        read_file(str(file), type=type, direction=direction, offset=20, limit=1).data[
            "content"
        ]
        == ""
    )
    assert (
        read_file(str(file), type=type, direction=direction, offset=20).data["content"]
        == ""
    )


def test_reverse_line_range_and_empty_metadata(tmp_path):
    file = tmp_path / "中文.txt"
    file.write_text("一\n二\n三\n")
    tail = read_file(str(file), type="lines", direction="backward", limit=1).data
    assert tail["content"] == "三\n"
    assert tail["line_range"] == "3-3"
    middle = read_file(
        str(file), type="lines", direction="backward", offset=1, limit=1
    ).data
    assert middle["content"] == "二\n"
    assert middle["line_range"] == "2-2"
    empty = read_file(str(file), type="lines", limit=0).data
    assert empty["lines_read"] == 0 and empty["line_range"] is None


@pytest.mark.parametrize("args", [{"offset": -1}, {"limit": -1}])
def test_negative_ranges_fail(tmp_path, args):
    file = tmp_path / "range.txt"
    file.write_text("abc")
    assert read_file(str(file), **args).data["error"] == "ValueError"


@pytest.mark.parametrize(
    "max_chars",
    [0, -1, True, False, 1.5, "100", HARD_MAX_CHARS + 1],
)
def test_invalid_max_chars_fails_before_reading(tmp_path, max_chars):
    missing = tmp_path / "does-not-exist.txt"
    result = read_file(str(missing), max_chars=max_chars).data
    assert result["error"] == "ValueError"
    assert "max_chars" in result["message"]


def test_max_chars_accepts_inclusive_bounds(tmp_path):
    file = tmp_path / "bounds.txt"
    file.write_text("a" * (HARD_MAX_CHARS + 10), encoding="utf-8")

    one_char = read_file(str(file), max_chars=1).data
    assert one_char["content"] == "a"
    assert one_char["truncated"] is True
    assert one_char["returned_chars"] == 1

    at_limit = read_file(str(file), max_chars=HARD_MAX_CHARS).data
    assert len(at_limit["content"]) == HARD_MAX_CHARS
    assert at_limit["truncated"] is True
    assert at_limit["returned_chars"] == HARD_MAX_CHARS
