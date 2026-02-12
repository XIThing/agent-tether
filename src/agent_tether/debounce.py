"""Error notification debouncing.

Prevents flooding chat threads with repeated error messages
by enforcing a minimum interval between error notifications
per thread.
"""

from __future__ import annotations

import time


class ErrorDebouncer:
    """Debounces error notifications per thread.

    Args:
        debounce_seconds: Minimum seconds between error notifications
            for the same thread. 0 means no debouncing.
    """

    def __init__(self, *, debounce_seconds: int = 0) -> None:
        self._debounce_s = debounce_seconds
        self._last_sent: dict[str, float] = {}

    def should_send(self, thread_id: str) -> bool:
        """Return True if an error notification should be sent now."""
        if self._debounce_s <= 0:
            return True

        now = time.time()
        last = self._last_sent.get(thread_id)
        if last is not None and (now - last) < self._debounce_s:
            return False

        self._last_sent[thread_id] = now
        return True

    def remove_thread(self, thread_id: str) -> None:
        """Clean up state for a thread."""
        self._last_sent.pop(thread_id, None)
