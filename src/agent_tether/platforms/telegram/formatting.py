"""Telegram-specific formatting utilities.

Converts markdown to Telegram-compatible HTML and handles message chunking
for Telegram's 4096-character limit.
"""

from __future__ import annotations

import html
import re


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special_chars = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text


def _markdown_table_to_pre(text: str) -> str:
    """Convert markdown tables to <pre> blocks.

    Finds consecutive lines that look like table rows (start/end with |)
    and wraps them in <pre>, dropping the separator row (dashes).
    Must be called AFTER html.escape so content is safe.
    """

    def _format_table(match: re.Match) -> str:
        lines = match.group(0).strip().splitlines()
        rows: list[list[str]] = []
        for line in lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r"-{2,}|:?-+:?", c) for c in cells):
                continue
            rows.append(cells)
        if not rows:
            return match.group(0)
        col_count = max(len(r) for r in rows)
        widths = [0] * col_count
        for row in rows:
            for i, cell in enumerate(row):
                if i < col_count:
                    widths[i] = max(widths[i], len(cell))
        formatted: list[str] = []
        for row in rows:
            parts = []
            for i in range(col_count):
                cell = row[i] if i < len(row) else ""
                parts.append(cell.ljust(widths[i]))
            formatted.append("  ".join(parts))
        return "<pre>" + "\n".join(formatted) + "</pre>"

    return re.sub(
        r"(?:^\|.+\|$\n?){2,}",
        _format_table,
        text,
        flags=re.MULTILINE,
    )


def markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown to Telegram-compatible HTML.

    Handles: code blocks, inline code, bold, italic, links, headers, tables.
    Telegram HTML supports: <b>, <i>, <code>, <pre>, <a href="">.
    """
    text = html.escape(text)
    text = _markdown_table_to_pre(text)

    # Fenced code blocks
    text = re.sub(
        r"```(?:\w*)\n(.*?)```",
        lambda m: f"<pre>{m.group(1).rstrip()}</pre>",
        text,
        flags=re.DOTALL,
    )

    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # Italic
    text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text)

    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Headers → bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    return text


def strip_tool_markers(text: str) -> str:
    """Remove tool-use marker lines like ``[tool: Read]`` from text."""
    return re.sub(r"^\[tool:\s*\w+\]\s*$", "", text, flags=re.MULTILINE).strip()


def chunk_telegram_message(text: str, limit: int = 4096) -> list[str]:
    """Split a message into chunks at Telegram's character limit."""
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def format_tool_input_html(raw: str, *, truncate: int = 120) -> tuple[str, bool]:
    """Pretty-format tool_input as Telegram HTML.

    Returns ``(html_text, was_truncated)``.
    """
    import json as json_mod

    try:
        obj = json_mod.loads(raw) if isinstance(raw, str) else raw
    except (json_mod.JSONDecodeError, TypeError):
        obj = None

    truncated = False

    if isinstance(obj, dict):
        from agent_tether.formatting import humanize_key, humanize_enum_value

        lines: list[str] = []
        for key, value in obj.items():
            label = humanize_key(str(key))
            v = humanize_enum_value(value)
            if len(v) > truncate:
                v = v[:truncate] + "…"
                truncated = True
            v_escaped = html.escape(v)
            label_escaped = html.escape(label)
            if key in ("file_path", "path", "notebook_path"):
                lines.append(f"<b>{label_escaped}</b>: <code>{v_escaped}</code>")
            elif key in ("command", "old_string", "new_string", "content", "new_source"):
                lines.append(f"<b>{label_escaped}</b>:\n<pre>{v_escaped}</pre>")
            else:
                lines.append(f"<b>{label_escaped}</b>: {v_escaped}")
        return "\n".join(lines), truncated

    text = html.escape(str(raw))
    if len(text) > truncate * 3:
        text = text[: truncate * 3] + "…"
        truncated = True
    return text, truncated


def format_tool_input_full_html(raw: str) -> str:
    """Format tool_input as Telegram HTML without truncation."""
    import json as json_mod

    try:
        obj = json_mod.loads(raw) if isinstance(raw, str) else raw
    except (json_mod.JSONDecodeError, TypeError):
        obj = None

    if isinstance(obj, dict):
        from agent_tether.formatting import humanize_key, humanize_enum_value

        lines: list[str] = []
        for key, value in obj.items():
            label = humanize_key(str(key))
            v = humanize_enum_value(value)
            v_escaped = html.escape(v)
            label_escaped = html.escape(label)
            if key in ("file_path", "path", "notebook_path"):
                lines.append(f"<b>{label_escaped}</b>: <code>{v_escaped}</code>")
            elif key in ("command", "old_string", "new_string", "content", "new_source"):
                lines.append(f"<b>{label_escaped}</b>:\n<pre>{v_escaped}</pre>")
            else:
                lines.append(f"<b>{label_escaped}</b>: {v_escaped}")
        return "\n".join(lines)

    return html.escape(str(raw))
