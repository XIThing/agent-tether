"""Tests for EventSubscriber."""

import asyncio

import pytest

from agent_tether.models import ApprovalRequest, Handlers
from agent_tether.platforms.base import BridgeBase
from agent_tether.subscriber import EventSubscriber


class RecordingBridge(BridgeBase):
    """Bridge that records all calls for testing."""

    def __init__(self):
        super().__init__(Handlers())
        self.outputs: list[tuple[str, str]] = []
        self.approvals: list[tuple[str, str, str]] = []
        self.choices: list[tuple[str, str, list[str]]] = []
        self.statuses: list[tuple[str, str]] = []
        self.typing_started: list[str] = []
        self.typing_stopped: list[str] = []

    async def _platform_start(self):
        pass

    async def _platform_stop(self):
        pass

    async def _platform_send(self, thread_id, text, **kwargs):
        self.outputs.append((thread_id, text))

    async def _platform_create_thread(self, name):
        return "mock_thread"

    async def _platform_send_approval(self, thread_id, request, formatted):
        self.approvals.append((thread_id, request.request_id, request.title))

    async def _platform_send_choice(self, thread_id, request):
        self.choices.append((thread_id, request.request_id, request.options))

    async def _platform_typing_start(self, thread_id):
        self.typing_started.append(thread_id)

    async def _platform_typing_stop(self, thread_id):
        self.typing_stopped.append(thread_id)


@pytest.mark.asyncio
async def test_output_event():
    bridge = RecordingBridge()
    sub = EventSubscriber(bridge)
    queue = sub.subscribe("t1")

    queue.put_nowait({"type": "output", "data": {"text": "Hello!", "final": True}})
    queue.put_nowait({"type": "output", "data": {"text": "Ignored", "final": False}})

    await asyncio.sleep(0.05)
    await sub.unsubscribe("t1")

    assert ("t1", "Hello!") in bridge.outputs
    assert ("t1", "Ignored") not in bridge.outputs


@pytest.mark.asyncio
async def test_permission_request_event():
    bridge = RecordingBridge()
    sub = EventSubscriber(bridge)
    queue = sub.subscribe("t1")

    queue.put_nowait(
        {
            "type": "permission_request",
            "data": {
                "request_id": "req_1",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            },
        }
    )

    await asyncio.sleep(0.05)
    await sub.unsubscribe("t1")

    assert len(bridge.approvals) == 1
    assert bridge.approvals[0] == ("t1", "req_1", "Bash")


@pytest.mark.asyncio
async def test_codex_choice_event():
    bridge = RecordingBridge()
    sub = EventSubscriber(bridge)
    queue = sub.subscribe("t1")

    queue.put_nowait(
        {
            "type": "permission_request",
            "data": {
                "request_id": "req_2",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "header": "Select env",
                            "question": "Where to deploy?",
                            "options": [
                                {"label": "staging", "description": "Test env"},
                                {"label": "production", "description": "Live env"},
                            ],
                        }
                    ]
                },
            },
        }
    )

    await asyncio.sleep(0.05)
    await sub.unsubscribe("t1")

    assert len(bridge.choices) == 1
    assert bridge.choices[0][0] == "t1"
    assert bridge.choices[0][1] == "req_2"
    assert bridge.choices[0][2] == ["staging", "production"]


@pytest.mark.asyncio
async def test_state_events():
    bridge = RecordingBridge()
    sub = EventSubscriber(bridge)
    queue = sub.subscribe("t1")

    queue.put_nowait({"type": "session_state", "data": {"state": "RUNNING"}})
    queue.put_nowait({"type": "session_state", "data": {"state": "AWAITING_INPUT"}})

    await asyncio.sleep(0.05)
    await sub.unsubscribe("t1")

    assert "t1" in bridge.typing_started
    assert "t1" in bridge.typing_stopped


@pytest.mark.asyncio
async def test_history_events_skipped():
    bridge = RecordingBridge()
    sub = EventSubscriber(bridge)
    queue = sub.subscribe("t1")

    queue.put_nowait(
        {
            "type": "output",
            "data": {"text": "Old message", "final": True, "is_history": True},
        }
    )
    queue.put_nowait(
        {
            "type": "output",
            "data": {"text": "New message", "final": True},
        }
    )

    await asyncio.sleep(0.05)
    await sub.unsubscribe("t1")

    assert ("t1", "Old message") not in bridge.outputs
    assert ("t1", "New message") in bridge.outputs


@pytest.mark.asyncio
async def test_unsubscribe_all():
    bridge = RecordingBridge()
    sub = EventSubscriber(bridge)
    sub.subscribe("t1")
    sub.subscribe("t2")

    assert len(sub._tasks) == 2
    await sub.unsubscribe_all()
    assert len(sub._tasks) == 0
