"""Auto-approve engine with timed permissions.

Manages per-thread, per-tool, and per-directory auto-approve timers.
When a human grants a timed permission (e.g., "Allow All for 30m"),
subsequent approval requests are resolved automatically until the
timer expires.
"""

from __future__ import annotations

import os
import time

_DEFAULT_DURATION_S = 30 * 60  # 30 minutes
_DEFAULT_NEVER_AUTO_APPROVE = frozenset({"task", "enterplanmode", "exitplanmode"})


class AutoApproveEngine:
    """Timed auto-approve engine.

    Args:
        duration_s: How long timers last in seconds (default 30 minutes).
        never_auto_approve: Tool name prefixes that are never auto-approved.
    """

    def __init__(
        self,
        *,
        duration_s: int = _DEFAULT_DURATION_S,
        never_auto_approve: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._duration_s = duration_s
        self._never: frozenset[str] = (
            frozenset(never_auto_approve)
            if never_auto_approve is not None
            else _DEFAULT_NEVER_AUTO_APPROVE
        )
        # Per-thread allow-all: thread_id → expiry timestamp
        self._allow_all_until: dict[str, float] = {}
        # Per-thread per-tool: thread_id → {tool_name → expiry}
        self._allow_tool_until: dict[str, dict[str, float]] = {}
        # Per-directory: normalised_dir → expiry timestamp
        self._allow_dir_until: dict[str, float] = {}
        # Thread → directory association
        self._thread_directory: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def associate_directory(self, thread_id: str, directory: str) -> None:
        """Associate a thread with a directory path (for directory-scoped timers)."""
        self._thread_directory[thread_id] = os.path.normpath(directory)

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def check(self, thread_id: str, tool_name: str) -> str | None:
        """Check if a tool request should be auto-approved.

        Returns a reason string if auto-approved, or None if human review
        is required.
        """
        norm = (tool_name or "").strip().lower()
        if any(norm.startswith(prefix) for prefix in self._never):
            return None

        now = time.time()

        # Per-thread allow-all
        if now < self._allow_all_until.get(thread_id, 0):
            return "Allow All"

        # Per-thread per-tool
        tool_expiry = self._allow_tool_until.get(thread_id, {}).get(tool_name, 0)
        if now < tool_expiry:
            return f"Allow {tool_name}"

        # Per-directory
        return self._check_directory(thread_id, now)

    def _check_directory(self, thread_id: str, now: float) -> str | None:
        """Check directory-scoped auto-approve timers."""
        if not self._allow_dir_until:
            return None
        thread_dir = self._thread_directory.get(thread_id)
        if not thread_dir:
            return None
        for allowed_dir, expiry in self._allow_dir_until.items():
            if now >= expiry:
                continue
            if thread_dir == allowed_dir or thread_dir.startswith(allowed_dir + os.sep):
                short = os.path.basename(allowed_dir) or allowed_dir
                return f"Allow dir {short}"
        return None

    # ------------------------------------------------------------------
    # Set timers
    # ------------------------------------------------------------------

    def set_allow_all(self, thread_id: str) -> None:
        """Enable auto-approve for all tools on this thread."""
        self._allow_all_until[thread_id] = time.time() + self._duration_s

    def set_allow_tool(self, thread_id: str, tool_name: str) -> None:
        """Enable auto-approve for a specific tool on this thread."""
        self._allow_tool_until.setdefault(thread_id, {})[tool_name] = (
            time.time() + self._duration_s
        )

    def set_allow_directory(self, directory: str) -> None:
        """Enable auto-approve for all threads in a directory."""
        norm = os.path.normpath(directory)
        self._allow_dir_until[norm] = time.time() + self._duration_s

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_directory(self, thread_id: str) -> str | None:
        """Get the directory associated with a thread, or None."""
        return self._thread_directory.get(thread_id)

    def is_never_approved(self, tool_name: str) -> bool:
        """Check if a tool name matches the never-auto-approve set."""
        norm = (tool_name or "").strip().lower()
        return any(norm.startswith(prefix) for prefix in self._never)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def remove_thread(self, thread_id: str) -> None:
        """Clean up all state for a thread."""
        self._allow_all_until.pop(thread_id, None)
        self._allow_tool_until.pop(thread_id, None)
        self._thread_directory.pop(thread_id, None)
