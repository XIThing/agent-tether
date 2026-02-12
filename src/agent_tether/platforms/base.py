"""Abstract base class for platform bridges.

BridgeBase provides the shared machinery for all platform bridges:
approval flow, auto-approve engine, command dispatch, notification
batching, error debouncing, and thread state. Platform implementations
override the ``_platform_*`` methods.
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

from agent_tether.approval import AutoApproveEngine
from agent_tether.batching import NotificationBatcher
from agent_tether.debounce import ErrorDebouncer
from agent_tether.formatting import format_tool_input
from agent_tether.models import ApprovalRequest, CommandDef, Handlers
from agent_tether.state import ThreadState

logger = logging.getLogger("agent_tether.bridge")

_STATE_EMOJI: dict[str, str] = {
    "running": "🔄",
    "waiting": "📝",
    "error": "❌",
    "done": "✅",
    "thinking": "💭",
    "executing": "⚙️",
}


class BridgeBase(ABC):
    """Abstract base class for platform bridges.

    Consumers interact through the public API (``create_thread``,
    ``send_output``, ``send_approval_request``, etc.). Platform events
    (human messages, button clicks) are routed through the handlers.

    Subclasses implement the ``_platform_*`` methods for
    platform-specific behavior.

    Args:
        handlers: Callback handlers for platform events.
        commands: Custom command definitions (name → CommandDef).
        disabled_commands: Built-in command names to disable.
        data_dir: Directory for persistent state files.
        auto_approve_duration: Auto-approve timer duration in seconds.
        never_auto_approve: Tool name prefixes that are never auto-approved.
        flush_delay: Seconds before flushing batched notifications.
        error_debounce_seconds: Minimum seconds between error notifications.
        command_prefix: Command prefix for this platform (default "!").
    """

    def __init__(
        self,
        handlers: Handlers,
        *,
        commands: dict[str, CommandDef] | None = None,
        disabled_commands: set[str] | None = None,
        data_dir: str | Path | None = None,
        auto_approve_duration: int = 30 * 60,
        never_auto_approve: set[str] | frozenset[str] | None = None,
        flush_delay: float = 1.5,
        error_debounce_seconds: int = 0,
        command_prefix: str = "!",
    ) -> None:
        self._handlers = handlers
        self._command_prefix = command_prefix
        self._disabled_commands = disabled_commands or set()
        self._stopped = asyncio.Event()

        # Resolve data dir
        if data_dir:
            self._data_dir = Path(data_dir)
        else:
            self._data_dir = Path.home() / ".agent-tether"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Core components
        self._approval = AutoApproveEngine(
            duration_s=auto_approve_duration,
            never_auto_approve=never_auto_approve,
        )
        self._batcher = NotificationBatcher(
            self._send_auto_approve_batch,
            flush_delay=flush_delay,
        )
        self._debouncer = ErrorDebouncer(debounce_seconds=error_debounce_seconds)

        # Pending permission requests: thread_id → ApprovalRequest
        self._pending: dict[str, ApprovalRequest] = {}

        # Command registry: built-in + custom
        self._commands: dict[str, CommandDef] = {}
        self._register_builtins()
        if commands:
            self._commands.update(commands)

    # ------------------------------------------------------------------
    # Built-in commands
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        """Register built-in commands (unless disabled)."""
        builtins: dict[str, CommandDef] = {
            "help": CommandDef(
                description="Show available commands",
                handler=self._builtin_help,
            ),
            "status": CommandDef(
                description="Show status",
                handler=self._builtin_status,
            ),
            "stop": CommandDef(
                description="Stop / interrupt the agent",
                handler=self._builtin_stop,
            ),
            "usage": CommandDef(
                description="Show token usage and cost",
                handler=self._builtin_usage,
            ),
        }
        for name, cmd in builtins.items():
            if name not in self._disabled_commands:
                self._commands[name] = cmd

    async def _builtin_help(self, thread_id: str, args: str) -> str | None:
        """Auto-generate help text from the command registry."""
        prefix = self._command_prefix
        lines = ["Available commands:\n"]
        for name, cmd in sorted(self._commands.items()):
            lines.append(f"  {prefix}{name} — {cmd.description}")
        lines.append(f"\nSend a text message in a thread to forward it as input.")
        return "\n".join(lines)

    async def _builtin_status(self, thread_id: str, args: str) -> str | None:
        if self._handlers.on_status_request:
            return await self._handlers.on_status_request()
        return None

    async def _builtin_stop(self, thread_id: str, args: str) -> str | None:
        if self._handlers.on_stop_request:
            return await self._handlers.on_stop_request(thread_id)
        return None

    async def _builtin_usage(self, thread_id: str, args: str) -> str | None:
        if self._handlers.on_usage_request:
            return await self._handlers.on_usage_request(thread_id)
        return None

    # ------------------------------------------------------------------
    # Public API — Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the bridge (connects to the platform)."""
        self._stopped.clear()
        await self._platform_start()
        logger.info("Bridge started: %s", type(self).__name__)

    async def stop(self) -> None:
        """Stop the bridge."""
        await self._platform_stop()
        self._stopped.set()
        logger.info("Bridge stopped: %s", type(self).__name__)

    async def wait_until_stopped(self) -> None:
        """Block until ``stop()`` is called."""
        await self._stopped.wait()

    # ------------------------------------------------------------------
    # Public API — Threads
    # ------------------------------------------------------------------

    async def create_thread(self, name: str, *, directory: str | None = None) -> str:
        """Create a new thread on the platform.

        Args:
            name: Display name for the thread.
            directory: Optional directory path for directory-scoped auto-approve.

        Returns:
            Platform-native thread ID as a string.
        """
        thread_id = await self._platform_create_thread(name)
        self._thread_state.register(thread_id, name)
        if directory:
            self._approval.associate_directory(thread_id, directory)
        logger.info("Thread created: %s (id=%s)", name, thread_id)
        return thread_id

    async def remove_thread(self, thread_id: str) -> None:
        """Clean up all state for a thread."""
        self._pending.pop(thread_id, None)
        self._approval.remove_thread(thread_id)
        self._batcher.remove_thread(thread_id)
        self._debouncer.remove_thread(thread_id)
        self._thread_state.unregister(thread_id)
        logger.info("Thread removed: %s", thread_id)

    # ------------------------------------------------------------------
    # Public API — Output
    # ------------------------------------------------------------------

    async def send_output(self, thread_id: str, text: str) -> None:
        """Send output text to a thread."""
        await self._platform_send(thread_id, text)

    async def send_status(self, thread_id: str, status: str) -> None:
        """Send a status notification to a thread.

        Respects error debouncing.
        """
        if status == "error" and not self._debouncer.should_send(thread_id):
            return
        emoji = _STATE_EMOJI.get(status, "ℹ️")
        await self._platform_send(thread_id, f"{emoji} Status: {status}")

    async def send_typing(self, thread_id: str) -> None:
        """Show a typing indicator (if the platform supports it)."""
        await self._platform_typing_start(thread_id)

    async def send_typing_stopped(self, thread_id: str) -> None:
        """Stop the typing indicator."""
        await self._platform_typing_stop(thread_id)

    # ------------------------------------------------------------------
    # Public API — Approvals
    # ------------------------------------------------------------------

    async def send_approval_request(
        self,
        thread_id: str,
        *,
        request_id: str,
        tool_name: str,
        description: str,
    ) -> None:
        """Send an approval request to a thread.

        If an auto-approve timer is active, the request is resolved
        automatically and a batched notification is sent instead.

        Args:
            thread_id: Target thread.
            request_id: Unique request identifier.
            tool_name: Name of the tool requesting approval.
            description: JSON string or text describing the tool input.
        """
        request = ApprovalRequest(
            kind="permission",
            request_id=request_id,
            title=tool_name,
            description=description,
            options=["Allow", "Deny"],
        )

        # Check auto-approve
        reason = self._approval.check(thread_id, tool_name)
        if reason:
            await self._auto_approve(thread_id, request, reason)
            return

        await self._platform_typing_stop(thread_id)
        self._pending[thread_id] = request
        formatted = format_tool_input(description)
        await self._platform_send_approval(thread_id, request, formatted)

    async def send_choice_request(
        self,
        thread_id: str,
        *,
        request_id: str,
        title: str,
        description: str,
        options: list[str],
    ) -> None:
        """Send a choice request (multi-option question) to a thread.

        Args:
            thread_id: Target thread.
            request_id: Unique request identifier.
            title: Question title / header.
            description: Question body text.
            options: List of option labels.
        """
        request = ApprovalRequest(
            kind="choice",
            request_id=request_id,
            title=title,
            description=description,
            options=options,
        )
        await self._platform_typing_stop(thread_id)
        self._pending[thread_id] = request
        await self._platform_send_choice(thread_id, request)

    # ------------------------------------------------------------------
    # Auto-approve internals
    # ------------------------------------------------------------------

    async def _auto_approve(self, thread_id: str, request: ApprovalRequest, reason: str) -> None:
        """Handle an auto-approved request: notify handler + batch notification."""
        if self._handlers.on_approval_response:
            await self._handlers.on_approval_response(
                thread_id, request.request_id, True, None, reason
            )
        self._batcher.add(thread_id, request.title, reason)

    async def _send_auto_approve_batch(self, thread_id: str, items: list[tuple[str, str]]) -> None:
        """Default auto-approve batch notification. Platforms may override."""
        if len(items) == 1:
            tool_name, reason = items[0]
            text = f"✅ {tool_name} — auto-approved ({reason})"
        else:
            lines = [f"✅ Auto-approved {len(items)} tools:"]
            for tool_name, _reason in items:
                lines.append(f"  • {tool_name}")
            lines.append(f"({items[0][1]})")
            text = "\n".join(lines)
        await self._platform_send(thread_id, text)

    # ------------------------------------------------------------------
    # Incoming message dispatch (platforms call this)
    # ------------------------------------------------------------------

    async def _dispatch_message(
        self, thread_id: str, text: str, username: str | None = None
    ) -> None:
        """Route an incoming human message.

        Called by platform implementations when a message arrives in a thread.
        Handles: pending approvals/choices → commands → plain input.
        """
        stripped = text.strip()
        if not stripped:
            return

        # 1. Check pending choice request
        pending = self._pending.get(thread_id)
        if pending and pending.kind == "choice":
            selected = self._parse_choice_text(thread_id, stripped)
            if selected:
                self._pending.pop(thread_id, None)
                if self._handlers.on_approval_response:
                    await self._handlers.on_approval_response(
                        thread_id, pending.request_id, True, selected, None
                    )
                await self._platform_send(thread_id, f"✅ Selected: {selected}")
                return

        # 2. Check pending approval request
        if pending and pending.kind == "permission":
            parsed = self._parse_approval_text(stripped)
            if parsed is not None:
                await self._handle_approval_parsed(thread_id, pending, parsed, username)
                return

        # 3. Check for commands
        if stripped.startswith(self._command_prefix):
            await self._dispatch_command(thread_id, stripped, username)
            return

        # 4. Plain input
        if self._handlers.on_input:
            await self._handlers.on_input(thread_id, stripped, username)

    async def _dispatch_command(
        self, thread_id: str, text: str, username: str | None = None
    ) -> None:
        """Parse and dispatch a command."""
        # Strip prefix
        without_prefix = text[len(self._command_prefix) :].strip()
        parts = without_prefix.split(None, 1)
        if not parts:
            return
        cmd_name = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        cmd = self._commands.get(cmd_name)
        if cmd:
            try:
                reply = await cmd.handler(thread_id, args)
                if reply:
                    await self._platform_send(thread_id, reply)
            except Exception:
                logger.exception("Command %s failed", cmd_name)
                await self._platform_send(thread_id, f"Command failed: {cmd_name}")
            return

        # Catch-all handler
        if self._handlers.on_command:
            try:
                reply = await self._handlers.on_command(thread_id, cmd_name, args)
                if reply:
                    await self._platform_send(thread_id, reply)
            except Exception:
                logger.exception("Command handler failed for %s", cmd_name)
            return

        await self._platform_send(
            thread_id,
            f"Unknown command: {self._command_prefix}{cmd_name}\n"
            f"Use {self._command_prefix}help for available commands.",
        )

    # ------------------------------------------------------------------
    # Approval text parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_approval_text(text: str) -> dict | None:
        """Parse a text message as an approval response.

        Returns a dict with keys: allow (bool), reason (str|None), timer (str|None)
        or None if the text is not an approval command.
        """
        stripped = text.strip()
        lower = stripped.lower()

        # Common synonyms
        if lower in ("proceed", "continue", "start", "go", "ok", "okay"):
            return {"allow": True, "reason": None, "timer": None}
        if lower in ("cancel", "stop", "abort"):
            return {"allow": False, "reason": None, "timer": None}

        # "allow all"
        if lower == "allow all":
            return {"allow": True, "reason": None, "timer": "all"}

        # "allow dir"
        if lower == "allow dir":
            return {"allow": True, "reason": None, "timer": "dir"}

        # "allow <tool>" (but not bare "allow")
        if lower.startswith("allow ") and lower != "allow all":
            rest = stripped[6:].strip()
            if rest:
                return {"allow": True, "reason": None, "timer": rest}

        # Bare allow/approve/yes
        if lower in ("allow", "approve", "yes"):
            return {"allow": True, "reason": None, "timer": None}

        # "deny: reason" or "reject: reason"
        if lower.startswith(("deny:", "reject:", "no:")):
            sep = stripped.index(":")
            reason = stripped[sep + 1 :].strip()
            return {"allow": False, "reason": reason or None, "timer": None}

        # "deny reason" (multi-word)
        if lower.startswith(("deny ", "reject ")):
            first_space = stripped.index(" ")
            reason = stripped[first_space + 1 :].strip()
            if reason:
                return {"allow": False, "reason": reason, "timer": None}

        # Bare deny/reject/no
        if lower in ("deny", "reject", "no"):
            return {"allow": False, "reason": None, "timer": None}

        return None

    def _parse_choice_text(self, thread_id: str, text: str) -> str | None:
        """Parse a text message as a choice selection.

        Supports numeric (1-indexed) or exact label match.
        """
        pending = self._pending.get(thread_id)
        if not pending or pending.kind != "choice":
            return None

        stripped = text.strip()
        if not stripped:
            return None

        # Numeric selection
        if stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(pending.options):
                return pending.options[idx]
            return None

        # Label match (case-insensitive)
        lowered = stripped.casefold()
        for opt in pending.options:
            if opt.casefold() == lowered:
                return opt
        return None

    async def _handle_approval_parsed(
        self,
        thread_id: str,
        request: ApprovalRequest,
        parsed: dict,
        username: str | None = None,
    ) -> None:
        """Handle a parsed approval text response."""
        allow = parsed["allow"]
        reason = parsed.get("reason")
        timer = parsed.get("timer")

        if allow:
            if timer == "all":
                self._approval.set_allow_all(thread_id)
            elif timer == "dir":
                directory = self._approval.get_directory(thread_id)
                if directory:
                    self._approval.set_allow_directory(directory)
                else:
                    self._approval.set_allow_all(thread_id)
            elif timer:
                self._approval.set_allow_tool(thread_id, timer)

        self._pending.pop(thread_id, None)

        if self._handlers.on_approval_response:
            await self._handlers.on_approval_response(
                thread_id, request.request_id, allow, reason, timer
            )

        # Send confirmation
        if allow:
            msg = "Approved"
            if timer == "all":
                msg = "Allow All (30m)"
            elif timer == "dir":
                msg = "Allow dir (30m)"
            elif timer:
                msg = f"Allow {timer} (30m)"
            display = f"✅ {msg}"
            if username:
                display += f" by {username}"
            await self._platform_send(thread_id, display)
        else:
            msg = f"Denied: {reason}" if reason else "Denied"
            display = f"❌ {msg}"
            if username:
                display += f" by {username}"
            await self._platform_send(thread_id, display)

    # ------------------------------------------------------------------
    # Thread state (initialized lazily by subclass or in start)
    # ------------------------------------------------------------------

    @property
    def _thread_state(self) -> ThreadState:
        """Thread state instance. Subclasses should set ``_thread_state_instance``."""
        if not hasattr(self, "_thread_state_instance") or self._thread_state_instance is None:
            platform_name = type(self).__name__.lower().replace("bridge", "")
            path = self._data_dir / f"{platform_name}_threads.json"
            self._thread_state_instance = ThreadState(path)
            self._thread_state_instance.load()
        return self._thread_state_instance

    # ------------------------------------------------------------------
    # Platform abstract methods
    # ------------------------------------------------------------------

    @abstractmethod
    async def _platform_start(self) -> None:
        """Start the platform client/bot."""

    @abstractmethod
    async def _platform_stop(self) -> None:
        """Stop the platform client/bot."""

    @abstractmethod
    async def _platform_send(self, thread_id: str, text: str, **kwargs) -> None:
        """Send a text message to a thread."""

    @abstractmethod
    async def _platform_create_thread(self, name: str) -> str:
        """Create a thread on the platform. Return the platform-native thread ID."""

    @abstractmethod
    async def _platform_send_approval(
        self, thread_id: str, request: ApprovalRequest, formatted_description: str
    ) -> None:
        """Send an approval request with platform-specific UI (buttons, text, etc.)."""

    @abstractmethod
    async def _platform_send_choice(self, thread_id: str, request: ApprovalRequest) -> None:
        """Send a choice request with platform-specific UI."""

    # ------------------------------------------------------------------
    # Platform optional methods (override as needed)
    # ------------------------------------------------------------------

    async def _platform_typing_start(self, thread_id: str) -> None:
        """Show a typing indicator. Override if the platform supports it."""

    async def _platform_typing_stop(self, thread_id: str) -> None:
        """Stop the typing indicator. Override if the platform supports it."""
