"""Event subscriber that routes events from an async queue to a bridge.

This is the glue between your event source (agent runtime, SSE stream,
message queue, etc.) and an agent-tether bridge. It consumes events
from an ``asyncio.Queue`` and dispatches them to the appropriate bridge
methods.

Typical usage::

    from agent_tether import TelegramBridge, Handlers
    from agent_tether.subscriber import EventSubscriber

    bridge = TelegramBridge(token="...", forum_group_id=123, handlers=handlers)
    await bridge.start()

    subscriber = EventSubscriber(bridge)
    thread_id = await bridge.create_thread("My Task")

    # Your event source pushes events to the queue
    queue = subscriber.subscribe(thread_id)
    queue.put_nowait({"type": "output", "data": {"text": "Hello!", "final": True}})

Event format::

    {
        "type": "output" | "permission_request" | "state" | "error",
        "data": { ... }
    }

Supported event types:

- ``output`` — Agent output text. Only forwarded when ``data["final"]`` is True.
- ``permission_request`` — Tool approval request. Requires ``data["request_id"]``,
  ``data["tool_name"]``, and ``data["tool_input"]``.
- ``state`` — Agent state change. ``data["state"]`` can be ``"running"``,
  ``"waiting"``, ``"error"``, etc.
- ``error`` — Error notification. ``data["message"]`` is the error text.

The subscriber also handles Codex-style ``AskUserQuestion`` tool calls,
converting them to choice requests automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging

from agent_tether.platforms.base import BridgeBase

logger = logging.getLogger("agent_tether.subscriber")


class EventSubscriber:
    """Routes events from async queues to a bridge.

    Each thread gets its own queue. A background task per thread
    consumes events and dispatches to the bridge.

    Args:
        bridge: The bridge to dispatch events to.
    """

    def __init__(self, bridge: BridgeBase) -> None:
        self._bridge = bridge
        self._tasks: dict[str, asyncio.Task] = {}
        self._queues: dict[str, asyncio.Queue] = {}

    def subscribe(self, thread_id: str, queue: asyncio.Queue | None = None) -> asyncio.Queue:
        """Start consuming events for a thread.

        Args:
            thread_id: The thread to subscribe.
            queue: Optional pre-existing queue. If None, a new one is created.

        Returns:
            The queue that events should be pushed to.
        """
        if thread_id in self._tasks:
            return self._queues[thread_id]

        if queue is None:
            queue = asyncio.Queue()
        self._queues[thread_id] = queue

        task = asyncio.create_task(self._consume(thread_id, queue))
        self._tasks[thread_id] = task
        logger.info("Subscriber started for thread %s", thread_id)
        return queue

    async def unsubscribe(self, thread_id: str) -> None:
        """Stop consuming events for a thread."""
        task = self._tasks.pop(thread_id, None)
        self._queues.pop(thread_id, None)
        if task:
            task.cancel()
            logger.info("Subscriber stopped for thread %s", thread_id)

    async def unsubscribe_all(self) -> None:
        """Stop all subscribers."""
        for thread_id in list(self._tasks):
            await self.unsubscribe(thread_id)

    async def _consume(self, thread_id: str, queue: asyncio.Queue) -> None:
        """Background task: read events from queue, dispatch to bridge."""
        try:
            while True:
                event = await queue.get()
                event_type = event.get("type")
                data = event.get("data", {})

                # Skip history replays (common in SSE-based sources)
                if data.get("is_history"):
                    continue

                try:
                    await self._dispatch(thread_id, event_type, data)
                except Exception:
                    logger.exception(
                        "Failed to dispatch event (thread=%s, type=%s)",
                        thread_id,
                        event_type,
                    )
        except asyncio.CancelledError:
            pass

    async def _dispatch(self, thread_id: str, event_type: str | None, data: dict) -> None:
        """Dispatch a single event to the bridge."""
        if event_type == "output":
            if data.get("final"):
                text = data.get("text", "")
                if text:
                    await self._bridge.send_output(thread_id, text)

        elif event_type == "output_final":
            # Accumulated blob — skip if using per-step finals above
            pass

        elif event_type == "permission_request":
            await self._dispatch_permission(thread_id, data)

        elif event_type in ("state", "session_state"):
            state = data.get("state", "")
            if state.upper() == "RUNNING":
                await self._bridge.send_typing(thread_id)
            elif state.upper() == "AWAITING_INPUT":
                await self._bridge.send_typing_stopped(thread_id)
            elif state.upper() == "ERROR":
                await self._bridge.send_typing_stopped(thread_id)
                await self._bridge.send_status(thread_id, "error")

        elif event_type == "error":
            msg = data.get("message", "Unknown error")
            await self._bridge.send_status(thread_id, "error")

    async def _dispatch_permission(self, thread_id: str, data: dict) -> None:
        """Dispatch a permission_request event, handling choice questions."""
        tool_input = data.get("tool_input", {})
        tool_name = data.get("tool_name", "Permission request")
        request_id = data.get("request_id", "")

        # Detect Codex-style AskUserQuestion (multi-choice)
        if (
            isinstance(tool_input, dict)
            and str(tool_name).startswith("AskUserQuestion")
            and isinstance(tool_input.get("questions"), list)
            and tool_input["questions"]
            and isinstance(tool_input["questions"][0], dict)
        ):
            q = tool_input["questions"][0]
            header = str(q.get("header") or "Question")
            question = str(q.get("question") or "")
            options_raw = q.get("options") or []
            labels: list[str] = []
            lines: list[str] = [question.strip()] if question else []
            for i, opt in enumerate(options_raw, start=1):
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label") or "").strip()
                desc = str(opt.get("description") or "").strip()
                if not label:
                    continue
                labels.append(label)
                if desc:
                    lines.append(f"{i}. {label} - {desc}")
                else:
                    lines.append(f"{i}. {label}")

            await self._bridge.send_choice_request(
                thread_id,
                request_id=request_id,
                title=header,
                description="\n".join(line for line in lines if line).strip(),
                options=labels,
            )
        else:
            description = (
                json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input)
            )
            await self._bridge.send_approval_request(
                thread_id,
                request_id=request_id,
                tool_name=tool_name,
                description=description,
            )
