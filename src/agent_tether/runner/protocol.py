"""Protocol definitions for runner adapters and event callbacks.

These protocols define the contract between a supervisor (like Tether) and
the agent backends it manages (Claude Code, Codex, Pi, etc.).

A ``Runner`` drives an agent process: start, send input, stop.
A ``RunnerEvents`` sink receives structured callbacks as the agent works:
output, errors, permission requests, heartbeats, etc.
"""

from __future__ import annotations

from typing import Protocol


class RunnerUnavailableError(RuntimeError):
    """Raised when a configured runner backend is not reachable.

    Supervisors can catch this to return a clean "service unavailable"
    response instead of an opaque internal error.
    """


class RunnerEvents(Protocol):
    """Callbacks invoked by runners to report process activity.

    The supervisor implements this protocol and passes it to the runner
    at construction time. The runner calls these methods as the agent
    produces output, requests permissions, encounters errors, etc.
    """

    async def on_output(
        self,
        session_id: str,
        stream: str,
        text: str,
        *,
        kind: str = "final",
        is_final: bool | None = None,
    ) -> None:
        """Agent produced text output.

        Args:
            session_id: Session identifier.
            stream: Output stream name (e.g. "combined", "stdout", "stderr").
            text: The output text.
            kind: "final" for completed assistant turns, "step" for intermediate output.
            is_final: Explicit flag; when True this is the last text block of the turn.
        """
        ...

    async def on_error(self, session_id: str, code: str, message: str) -> None:
        """Agent encountered an error.

        Args:
            session_id: Session identifier.
            code: Machine-readable error code (e.g. "SUBPROCESS_ERROR").
            message: Human-readable error description.
        """
        ...

    async def on_exit(self, session_id: str, exit_code: int | None) -> None:
        """Agent process exited.

        Args:
            session_id: Session identifier.
            exit_code: Process exit code, or None if unknown.
        """
        ...

    async def on_awaiting_input(self, session_id: str) -> None:
        """Agent finished a turn and is waiting for user input.

        Args:
            session_id: Session identifier.
        """
        ...

    async def on_metadata(self, session_id: str, key: str, value: object, raw: str) -> None:
        """Agent reported metadata (tokens, cost, etc.).

        Args:
            session_id: Session identifier.
            key: Metadata key (e.g. "tokens", "cost").
            value: Structured value.
            raw: Human-readable string representation.
        """
        ...

    async def on_heartbeat(self, session_id: str, elapsed_s: float, done: bool) -> None:
        """Periodic heartbeat from the runner.

        Args:
            session_id: Session identifier.
            elapsed_s: Seconds since the session turn started.
            done: True if this is the final heartbeat (turn complete).
        """
        ...

    async def on_header(
        self,
        session_id: str,
        *,
        title: str,
        model: str | None = None,
        provider: str | None = None,
        sandbox: str | None = None,
        approval: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Agent session initialized with metadata.

        Args:
            session_id: Session identifier.
            title: Display title (e.g. "Claude Code v1.2").
            model: Model name.
            provider: Provider description.
            sandbox: Sandbox identifier, if applicable.
            approval: Approval mode description.
            thread_id: Backend thread/session identifier.
        """
        ...

    async def on_permission_request(
        self,
        session_id: str,
        request_id: str,
        tool_name: str,
        tool_input: dict,
        suggestions: list | None = None,
    ) -> None:
        """Agent requests permission to use a tool.

        Args:
            session_id: Session identifier.
            request_id: Unique request identifier for correlating the response.
            tool_name: Name of the tool (e.g. "Write", "Bash").
            tool_input: Tool arguments.
            suggestions: Optional suggested responses.
        """
        ...

    async def on_permission_resolved(
        self,
        session_id: str,
        request_id: str,
        resolved_by: str,
        allowed: bool,
        message: str | None = None,
    ) -> None:
        """A permission request was resolved.

        Args:
            session_id: Session identifier.
            request_id: The request that was resolved.
            resolved_by: Who resolved it ("user", "auto", "timeout").
            allowed: Whether the tool use was permitted.
            message: Optional message from the resolver.
        """
        ...


class Runner(Protocol):
    """Adapter interface for agent backends.

    Each backend (Claude Code subprocess, Claude API, Codex SDK, Pi, etc.)
    implements this protocol. The supervisor calls these methods to drive
    the agent lifecycle.
    """

    runner_type: str
    """Identifier for the agent type (e.g. "claude-subprocess", "codex")."""

    async def start(self, session_id: str, prompt: str, approval_choice: int) -> None:
        """Start a new agent turn.

        Args:
            session_id: Session identifier.
            prompt: The user's prompt text.
            approval_choice: Permission mode (0=ask, 1=accept edits, 2=bypass).
        """
        ...

    async def send_input(self, session_id: str, text: str) -> None:
        """Send follow-up input to a running session.

        Args:
            session_id: Session identifier.
            text: The user's input text.
        """
        ...

    async def stop(self, session_id: str) -> int | None:
        """Stop/interrupt the agent.

        Args:
            session_id: Session identifier.

        Returns:
            Exit code, or None if not applicable.
        """
        ...

    def update_permission_mode(self, session_id: str, approval_choice: int) -> None:
        """Update the permission mode for an active session.

        Args:
            session_id: Session identifier.
            approval_choice: New permission mode (0=ask, 1=accept edits, 2=bypass).
        """
        ...
