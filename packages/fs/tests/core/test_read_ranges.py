"""Direct regressions for zero limits, reverse offsets and truthful line ranges."""

import pytest

from mcp_server_fs.core import read_file


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
