"""Protocol definitions for runner adapters and event callbacks."""

from __future__ import annotations

from typing import Protocol


class RunnerUnavailableError(RuntimeError):
    """Raised when a configured runner backend is not reachable.

    This is used to return a clean 503 error to clients instead of a 500 + stack trace.
    """


class RunnerEvents(Protocol):
    """Callbacks invoked by runners to report process activity and terminal state."""

    async def on_output(
        self,
        session_id: str,
        stream: str,
        text: str,
        *,
        kind: str = "final",
        is_final: bool | None = None,
    ) -> None:
        """Report output from the runner.

        Args:
            session_id: Session identifier.
            stream: Stream name (e.g., "stdout", "stderr").
            text: Output text.
            kind: Output kind ("final", "thinking", "partial", etc.).
            is_final: Whether this is the final output chunk.
        """
        ...

    async def on_error(self, session_id: str, code: str, message: str) -> None:
        """Report an error from the runner.

        Args:
            session_id: Session identifier.
            code: Error code.
            message: Human-readable error message.
        """
        ...

    async def on_exit(self, session_id: str, exit_code: int | None) -> None:
        """Report that the runner has exited.

        Args:
            session_id: Session identifier.
            exit_code: Process exit code, or None if not applicable.
        """
        ...

    async def on_awaiting_input(self, session_id: str) -> None:
        """Signal that the agent has finished a turn and is waiting for user input.

        Args:
            session_id: Session identifier.
        """
        ...

    async def on_metadata(self, session_id: str, key: str, value: object, raw: str) -> None:
        """Report metadata from the runner.

        Args:
            session_id: Session identifier.
            key: Metadata key.
            value: Parsed metadata value.
            raw: Raw metadata string.
        """
        ...

    async def on_heartbeat(self, session_id: str, elapsed_s: float, done: bool) -> None:
        """Report a heartbeat from the runner.

        Args:
            session_id: Session identifier.
            elapsed_s: Elapsed time in seconds.
            done: Whether the runner has completed.
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
        """Report header information from the runner.

        Args:
            session_id: Session identifier.
            title: Session title.
            model: Model name (e.g., "claude-3-5-sonnet-20241022").
            provider: Provider name (e.g., "anthropic", "openai").
            sandbox: Sandbox type (e.g., "docker", "none").
            approval: Approval mode string.
            thread_id: External thread identifier.
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
        """Report a permission request from the runner.

        Args:
            session_id: Session identifier.
            request_id: Unique request identifier.
            tool_name: Name of the tool requiring permission.
            tool_input: Tool input parameters.
            suggestions: Optional list of suggested actions.
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
        """Report that a permission request has been resolved.

        Args:
            session_id: Session identifier.
            request_id: Unique request identifier.
            resolved_by: Who resolved the request (e.g., "user", "auto-approve").
            allowed: Whether the request was approved.
            message: Optional resolution message.
        """
        ...


class Runner(Protocol):
    """Adapter interface for agent backends (Codex CLI, Claude API, etc.)."""

    runner_type: str
    """High-level agent type identifier (e.g., 'codex', 'claude')."""

    async def start(self, session_id: str, prompt: str, approval_choice: int) -> None:
        """Start a new runner session.

        Args:
            session_id: Session identifier.
            prompt: Initial prompt text.
            approval_choice: Approval mode (0=never, 1=first-time, 2=always).
        """
        ...

    async def send_input(self, session_id: str, text: str) -> None:
        """Send user input to the runner.

        Args:
            session_id: Session identifier.
            text: User input text.
        """
        ...

    async def stop(self, session_id: str) -> int | None:
        """Stop the runner session.

        Args:
            session_id: Session identifier.

        Returns:
            Exit code if available, or None.
        """
        ...

    def update_permission_mode(self, session_id: str, approval_choice: int) -> None:
        """Update permission mode for an active session.

        Args:
            session_id: Session identifier.
            approval_choice: New approval mode (0=never, 1=first-time, 2=always).
        """
        ...
