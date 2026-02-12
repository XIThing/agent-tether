"""Formatting utilities for tool input and human-readable output.

Provides functions to convert JSON tool input dicts into readable
text suitable for chat platforms, with smart truncation and key
humanization.
"""

from __future__ import annotations

import json
import re

# ---------------------------------------------------------------------------
# Key / value humanization
# ---------------------------------------------------------------------------

_ACRONYMS = frozenset(
    {
        "id",
        "url",
        "api",
        "sdk",
        "http",
        "https",
        "cli",
        "ui",
        "sse",
        "mcp",
        "json",
    }
)


def humanize_key(key: str) -> str:
    """Convert a snake_case key into a human-friendly label.

    Examples:
        >>> humanize_key("output_mode")
        'Output mode'
        >>> humanize_key("session_id")
        'Session ID'
    """
    if not key or "_" not in key:
        return key

    parts = [p for p in key.strip().split("_") if p]
    if not parts:
        return key

    out: list[str] = []
    for i, p in enumerate(parts):
        low = p.lower()
        if low in _ACRONYMS:
            out.append(low.upper())
        elif i == 0:
            out.append(low[:1].upper() + low[1:])
        else:
            out.append(low)
    return " ".join(out)


def humanize_enum_value(value: object) -> str:
    """Humanize enum-ish snake_case values like ``files_with_matches``.

    Only touches values that look like enums; paths and commands are
    left alone.
    """
    s = str(value)
    if "_" not in s:
        return s
    if not re.fullmatch(r"[a-z0-9_]+", s):
        return s
    parts = [p for p in s.split("_") if p]
    if not parts:
        return s
    out: list[str] = []
    for i, p in enumerate(parts):
        low = p.lower()
        if low == "id":
            out.append("ID")
        elif i == 0:
            out.append(low[:1].upper() + low[1:])
        else:
            out.append(low)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Tool input formatting
# ---------------------------------------------------------------------------

_PATH_KEYS = frozenset({"file_path", "path", "notebook_path"})
_CODE_BLOCK_KEYS = frozenset(
    {
        "command",
        "old_string",
        "new_string",
        "content",
        "new_source",
    }
)


def format_tool_input(
    raw: str,
    *,
    truncate: int = 400,
    truncate_code: int = 1400,
    max_chars: int = 2000,
) -> str:
    """Format a tool_input JSON string as readable markdown for chat platforms.

    Args:
        raw: JSON string (or plain text) of the tool input.
        truncate: Max chars per value (non-code fields).
        truncate_code: Max chars for code-block fields.
        max_chars: Total output character budget.

    Returns:
        Formatted markdown string.
    """
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return str(raw)

    if not isinstance(obj, dict):
        return str(raw)

    lines: list[str] = []
    total = 0
    for key, value in obj.items():
        key_s = str(key)
        label = humanize_key(key_s)

        if isinstance(value, (dict, list)):
            v = json.dumps(value, ensure_ascii=True)
        else:
            v = humanize_enum_value(value)

        limit = truncate_code if key_s in _CODE_BLOCK_KEYS else truncate
        if len(v) > limit:
            v = v[:limit] + "..."

        # Prevent closing a code block early.
        v = v.replace("```", "``\\`")

        if key_s in _PATH_KEYS:
            part = f"{label}: `{v}`"
        elif key_s in _CODE_BLOCK_KEYS:
            part = f"{label}:\n```\n{v}\n```"
        else:
            part = f"{label}: {v}"

        if total + len(part) > max_chars and lines:
            lines.append("...(truncated)")
            break
        lines.append(part)
        total += len(part) + 1

    return "\n".join(lines).strip()


def chunk_message(text: str, limit: int = 4096) -> list[str]:
    """Split a message into chunks at a character limit.

    Args:
        text: Text to chunk.
        limit: Maximum characters per chunk.

    Returns:
        List of text chunks.
    """
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]
