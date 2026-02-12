"""Notification batcher for auto-approve events.

Collects auto-approve notifications per thread and flushes them
as a single batched message after a short delay, so rapid-fire
approvals don't flood the chat.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class NotificationBatcher:
    """Batches notifications per thread and flushes after a delay.

    Args:
        flush_callback: Async function called with ``(thread_id, items)``
            when the batch is ready. ``items`` is a list of
            ``(tool_name, reason)`` tuples.
        flush_delay: Seconds to wait before flushing (default 1.5).
    """

    def __init__(
        self,
        flush_callback: Callable[[str, list[tuple[str, str]]], Awaitable[None]],
        *,
        flush_delay: float = 1.5,
    ) -> None:
        self._flush_callback = flush_callback
        self._flush_delay = flush_delay
        self._buffer: dict[str, list[tuple[str, str]]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def add(self, thread_id: str, tool_name: str, reason: str) -> None:
        """Buffer a notification. Resets the flush timer."""
        self._buffer.setdefault(thread_id, []).append((tool_name, reason))

        # Cancel existing timer and start a new one
        existing = self._tasks.pop(thread_id, None)
        if existing:
            existing.cancel()

        self._tasks[thread_id] = asyncio.create_task(self._flush_after_delay(thread_id))

    async def _flush_after_delay(self, thread_id: str) -> None:
        """Wait then flush buffered notifications."""
        try:
            await asyncio.sleep(self._flush_delay)
        except asyncio.CancelledError:
            return
        self._tasks.pop(thread_id, None)
        items = self._buffer.pop(thread_id, [])
        if items:
            await self._flush_callback(thread_id, items)

    def remove_thread(self, thread_id: str) -> None:
        """Cancel pending flush and discard buffer for a thread."""
        task = self._tasks.pop(thread_id, None)
        if task:
            task.cancel()
        self._buffer.pop(thread_id, None)
