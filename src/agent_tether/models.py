"""Core data models for agent-tether."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Handler type aliases
# ---------------------------------------------------------------------------

InputHandler = Callable[[str, str, str | None], Awaitable[None]]
"""(thread_id, text, username) → None"""

ApprovalHandler = Callable[..., Awaitable[None]]
"""(thread_id, request_id, approved, reason=None, timer=None) → None

timer: None | "all" | "dir" | tool_name
"""

CommandHandler = Callable[[str, str, str], Awaitable[str | None]]
"""(thread_id, command, args) → optional response text"""

StatusHandler = Callable[[], Awaitable[str]]
"""() → status text to display"""

StopHandler = Callable[[str], Awaitable[str | None]]
"""(thread_id) → optional confirmation text"""

UsageHandler = Callable[[str], Awaitable[str | None]]
"""(thread_id) → optional usage text"""


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@dataclass
class Handlers:
    """Callbacks the consumer provides to handle platform events.

    All handlers are optional. If a handler is not set, the corresponding
    event is silently ignored.
    """

    on_input: InputHandler | None = None
    """Human sent a text message in a thread."""

    on_approval_response: ApprovalHandler | None = None
    """Human responded to an approval/permission request."""

    on_command: CommandHandler | None = None
    """Catch-all for unrecognized commands. Return text to reply, or None."""

    on_status_request: StatusHandler | None = None
    """Handle /status or !status. Return text to display."""

    on_stop_request: StopHandler | None = None
    """Handle /stop or !stop. Return text to confirm, or None."""

    on_usage_request: UsageHandler | None = None
    """Handle /usage or !usage. Return text to display, or None."""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@dataclass
class CommandDef:
    """Definition of a custom command.

    Attributes:
        description: Short help text shown in /help output.
        handler: Async function ``(thread_id, args) → str | None``.
            Return text to reply, or None for no reply.
    """

    description: str
    handler: Callable[[str, str], Awaitable[str | None]]


# ---------------------------------------------------------------------------
# Approval / Choice requests
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    """An approval or choice request sent to a human via a chat thread.

    For permission requests (kind="permission"), the human is asked to
    Allow or Deny a tool invocation.

    For choice requests (kind="choice"), the human picks from a list of
    options (e.g., selecting an environment, confirming a plan).
    """

    kind: Literal["permission", "choice"] = "permission"
    request_id: str
    title: str
    description: str
    options: list[str] = field(default_factory=lambda: ["Allow", "Deny"])
    timeout_s: int = 300
