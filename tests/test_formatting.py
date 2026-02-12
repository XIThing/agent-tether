"""Tests for formatting utilities."""

from agent_tether.formatting import (
    chunk_message,
    format_tool_input,
    humanize_enum_value,
    humanize_key,
)


def test_humanize_key_no_underscores():
    assert humanize_key("command") == "command"
    assert humanize_key("path") == "path"


def test_humanize_key_with_underscores():
    assert humanize_key("file_path") == "File path"
    assert humanize_key("session_id") == "Session ID"
    assert humanize_key("api_key") == "API key"


def test_humanize_enum_value_no_underscores():
    assert humanize_enum_value("running") == "running"


def test_humanize_enum_value_with_underscores():
    assert humanize_enum_value("files_with_matches") == "Files with matches"
    assert humanize_enum_value("user_id") == "User ID"


def test_format_tool_input_string():
    result = format_tool_input("plain text")
    assert result == "plain text"


def test_format_tool_input_json_dict():
    result = format_tool_input('{"command": "ls -la", "path": "/tmp"}')
    assert "command" in result
    assert "ls -la" in result
    assert "path" in result or "Path" in result
    assert "/tmp" in result


def test_format_tool_input_truncate():
    long_value = "x" * 500
    result = format_tool_input(f'{{"data": "{long_value}"}}', truncate=100)
    assert "..." in result
    assert len(result) < 500


def test_chunk_message_short():
    text = "short message"
    assert chunk_message(text) == [text]


def test_chunk_message_long():
    text = "a" * 5000
    chunks = chunk_message(text, limit=4096)
    assert len(chunks) == 2
    assert len(chunks[0]) == 4096
    assert len(chunks[1]) == 5000 - 4096
