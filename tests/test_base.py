"""Tests for BridgeBase with a mock platform implementation."""

import pytest

from agent_tether.models import ApprovalRequest, CommandDef, Handlers
from agent_tether.platforms.base import BridgeBase


class MockBridge(BridgeBase):
    """Minimal mock bridge for testing BridgeBase."""

    def __init__(self, handlers: Handlers, **kwargs):
        super().__init__(handlers, **kwargs)
        self.sent_messages: list[tuple[str, str]] = []
        self.created_threads: list[tuple[str, str]] = []

    async def _platform_start(self) -> None:
        pass

    async def _platform_stop(self) -> None:
        pass

    async def _platform_send(self, thread_id: str, text: str, **kwargs) -> None:
        self.sent_messages.append((thread_id, text))

    async def _platform_create_thread(self, name: str) -> str:
        thread_id = f"mock_{len(self.created_threads)}"
        self.created_threads.append((thread_id, name))
        return thread_id

    async def _platform_send_approval(
        self, thread_id: str, request: ApprovalRequest, formatted_description: str
    ) -> None:
        self.sent_messages.append((thread_id, f"Approval: {request.title}"))

    async def _platform_send_choice(self, thread_id: str, request: ApprovalRequest) -> None:
        self.sent_messages.append((thread_id, f"Choice: {request.title}"))


@pytest.mark.asyncio
async def test_create_thread():
    handlers = Handlers()
    bridge = MockBridge(handlers)
    await bridge.start()

    thread_id = await bridge.create_thread("Test Thread")
    assert thread_id == "mock_0"
    assert ("mock_0", "Test Thread") in bridge.created_threads

    await bridge.stop()


@pytest.mark.asyncio
async def test_send_output():
    handlers = Handlers()
    bridge = MockBridge(handlers)
    await bridge.start()

    thread_id = await bridge.create_thread("Test")
    await bridge.send_output(thread_id, "Hello, world!")

    assert any("Hello, world" in msg for _, msg in bridge.sent_messages)
    await bridge.stop()


@pytest.mark.asyncio
async def test_builtin_help_command():
    handlers = Handlers()
    bridge = MockBridge(handlers, command_prefix="!")
    await bridge.start()

    thread_id = await bridge.create_thread("Test")
    await bridge._dispatch_message(thread_id, "!help")

    # Check that help text was sent
    assert any("help" in msg.lower() for _, msg in bridge.sent_messages)
    await bridge.stop()


@pytest.mark.asyncio
async def test_custom_command():
    called = []

    async def my_handler(thread_id: str, args: str) -> str | None:
        called.append((thread_id, args))
        return "Command executed"

    handlers = Handlers()
    commands = {"test": CommandDef(description="Test command", handler=my_handler)}
    bridge = MockBridge(handlers, commands=commands, command_prefix="!")
    await bridge.start()

    thread_id = await bridge.create_thread("Test")
    await bridge._dispatch_message(thread_id, "!test arg1 arg2")

    assert called == [(thread_id, "arg1 arg2")]
    assert any("Command executed" in msg for _, msg in bridge.sent_messages)
    await bridge.stop()


@pytest.mark.asyncio
async def test_disabled_commands():
    handlers = Handlers()
    bridge = MockBridge(handlers, disabled_commands={"help"}, command_prefix="!")
    await bridge.start()

    # help should not be registered
    assert "help" not in bridge._commands

    await bridge.stop()


@pytest.mark.asyncio
async def test_approval_text_parsing():
    assert BridgeBase._parse_approval_text("allow") == {
        "allow": True,
        "reason": None,
        "timer": None,
    }
    assert BridgeBase._parse_approval_text("deny") == {
        "allow": False,
        "reason": None,
        "timer": None,
    }
    assert BridgeBase._parse_approval_text("deny: too risky") == {
        "allow": False,
        "reason": "too risky",
        "timer": None,
    }
    assert BridgeBase._parse_approval_text("allow all") == {
        "allow": True,
        "reason": None,
        "timer": "all",
    }
    assert BridgeBase._parse_approval_text("allow Bash") == {
        "allow": True,
        "reason": None,
        "timer": "Bash",
    }
    assert BridgeBase._parse_approval_text("proceed") == {
        "allow": True,
        "reason": None,
        "timer": None,
    }
    assert BridgeBase._parse_approval_text("cancel") == {
        "allow": False,
        "reason": None,
        "timer": None,
    }


@pytest.mark.asyncio
async def test_approval_request_auto_approve():
    approval_responses = []

    async def on_approval(thread_id, request_id, approved, reason=None, timer=None):
        approval_responses.append((thread_id, request_id, approved, timer))

    handlers = Handlers(on_approval_response=on_approval)
    bridge = MockBridge(handlers, auto_approve_duration=60)
    await bridge.start()

    thread_id = await bridge.create_thread("Test")

    # Set allow-all
    bridge._approval.set_allow_all(thread_id)

    # Send approval request — should auto-approve
    await bridge.send_approval_request(
        thread_id, request_id="req_1", tool_name="Bash", description='{"command": "ls"}'
    )

    # Handler should be called with approved=True
    assert approval_responses == [(thread_id, "req_1", True, "Allow All")]

    await bridge.stop()


@pytest.mark.asyncio
async def test_choice_request():
    handlers = Handlers()
    bridge = MockBridge(handlers)
    await bridge.start()

    thread_id = await bridge.create_thread("Test")
    await bridge.send_choice_request(
        thread_id,
        request_id="choice_1",
        title="Select env",
        description="Where to deploy?",
        options=["staging", "production"],
    )

    # Check that choice was sent
    assert any("Choice: Select env" in msg for _, msg in bridge.sent_messages)

    await bridge.stop()
