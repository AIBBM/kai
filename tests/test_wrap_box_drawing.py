import pytest
from widgets.utils import _wrap_box_drawing


def test_plain_text_unchanged():
    text = "Hello world\nNo box here."
    assert _wrap_box_drawing(text) == text


def test_empty_string():
    assert _wrap_box_drawing("") == ""


def test_three_box_chars_triggers_wrap():
    line = "───"
    assert _wrap_box_drawing(line) == f"```\n{line}\n```"


def test_two_box_chars_not_wrapped():
    line = "──"
    assert _wrap_box_drawing(line) == line


def test_existing_code_fence_not_rewrapped():
    text = "```\n─────\n```"
    assert _wrap_box_drawing(text) == text


def test_multiline_block_wrapped_as_one():
    block = "┌──────┐\n│ cell │\n└──────┘"
    assert _wrap_box_drawing(block) == f"```\n{block}\n```"


def test_box_block_followed_by_plain_text():
    text = "┌──┐\n└──┘\nnormal line"
    result = _wrap_box_drawing(text)
    assert result.startswith("```\n┌──┐\n└──┘\n```")
    assert result.endswith("normal line")


def test_trailing_blank_lines_outside_fence():
    # blank lines at end of block are pushed outside the closing fence
    text = "┌──┐\n└──┘\n\n"
    result = _wrap_box_drawing(text)
    lines = result.split("\n")
    assert "```" in lines
    fence_positions = [i for i, l in enumerate(lines) if l == "```"]
    assert len(fence_positions) == 2
    closing = fence_positions[1]
    # everything after closing fence should be blank
    after = lines[closing + 1 :]
    assert all(l.strip() == "" for l in after)


def test_inline_code_not_matched_as_fence():
    # `─────` (inline code) should protect the box chars inside
    text = "`─────`"
    assert _wrap_box_drawing(text) == text


def test_box_block_mixed_with_text_before():
    text = "intro line\n┌──┐\n└──┘"
    result = _wrap_box_drawing(text)
    assert result.startswith("intro line\n")
    assert "```\n┌──┐\n└──┘\n```" in result
