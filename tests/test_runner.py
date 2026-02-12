"""Tests for runner protocol and registry."""

import pytest

from agent_tether.runner import (
    Runner,
    RunnerEvents,
    RunnerRegistry,
    RunnerUnavailableError,
)

# Mock implementations for testing


class MockEvents:
    """Mock RunnerEvents implementation for testing."""

    def __init__(self):
        self.events = []

    async def on_output(
        self, session_id: str, stream: str, text: str, *, kind="final", is_final=None
    ):
        self.events.append(
            {
                "type": "output",
                "session_id": session_id,
                "stream": stream,
                "text": text,
                "kind": kind,
                "is_final": is_final,
            }
        )

    async def on_error(self, session_id: str, code: str, message: str):
        self.events.append(
            {
                "type": "error",
                "session_id": session_id,
                "code": code,
                "message": message,
            }
        )

    async def on_exit(self, session_id: str, exit_code: int | None):
        self.events.append({"type": "exit", "session_id": session_id, "exit_code": exit_code})

    async def on_awaiting_input(self, session_id: str):
        self.events.append({"type": "awaiting_input", "session_id": session_id})

    async def on_metadata(self, session_id: str, key: str, value: object, raw: str):
        self.events.append(
            {
                "type": "metadata",
                "session_id": session_id,
                "key": key,
                "value": value,
                "raw": raw,
            }
        )

    async def on_heartbeat(self, session_id: str, elapsed_s: float, done: bool):
        self.events.append(
            {
                "type": "heartbeat",
                "session_id": session_id,
                "elapsed_s": elapsed_s,
                "done": done,
            }
        )

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
    ):
        self.events.append(
            {
                "type": "header",
                "session_id": session_id,
                "title": title,
                "model": model,
                "provider": provider,
                "sandbox": sandbox,
                "approval": approval,
                "thread_id": thread_id,
            }
        )

    async def on_permission_request(
        self,
        session_id: str,
        request_id: str,
        tool_name: str,
        tool_input: dict,
        suggestions: list | None = None,
    ):
        self.events.append(
            {
                "type": "permission_request",
                "session_id": session_id,
                "request_id": request_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "suggestions": suggestions,
            }
        )

    async def on_permission_resolved(
        self,
        session_id: str,
        request_id: str,
        resolved_by: str,
        allowed: bool,
        message: str | None = None,
    ):
        self.events.append(
            {
                "type": "permission_resolved",
                "session_id": session_id,
                "request_id": request_id,
                "resolved_by": resolved_by,
                "allowed": allowed,
                "message": message,
            }
        )


class MockRunner:
    """Mock Runner implementation for testing."""

    runner_type = "mock"

    def __init__(self, events: RunnerEvents, config: dict):
        self.events = events
        self.config = config
        self.started_sessions = []
        self.inputs = []
        self.stopped_sessions = []
        self.permission_updates = []

    async def start(self, session_id: str, prompt: str, approval_choice: int):
        self.started_sessions.append((session_id, prompt, approval_choice))

    async def send_input(self, session_id: str, text: str):
        self.inputs.append((session_id, text))

    async def stop(self, session_id: str) -> int | None:
        self.stopped_sessions.append(session_id)
        return 0

    def update_permission_mode(self, session_id: str, approval_choice: int):
        self.permission_updates.append((session_id, approval_choice))


# Tests


@pytest.mark.asyncio
async def test_mock_events():
    """Test MockEvents records all event types."""
    events = MockEvents()

    await events.on_output("sess_1", "stdout", "Hello\n")
    await events.on_error("sess_1", "E001", "Something went wrong")
    await events.on_exit("sess_1", 0)
    await events.on_awaiting_input("sess_1")
    await events.on_metadata("sess_1", "model", "claude-3", "model=claude-3")
    await events.on_heartbeat("sess_1", 5.0, False)
    await events.on_header("sess_1", title="Test Session", model="claude-3")
    await events.on_permission_request(
        "sess_1", "req_1", "bash", {"command": "ls"}, ["allow", "deny"]
    )
    await events.on_permission_resolved("sess_1", "req_1", "user", True, "Approved")

    assert len(events.events) == 9
    assert events.events[0]["type"] == "output"
    assert events.events[1]["type"] == "error"
    assert events.events[2]["type"] == "exit"
    assert events.events[3]["type"] == "awaiting_input"
    assert events.events[4]["type"] == "metadata"
    assert events.events[5]["type"] == "heartbeat"
    assert events.events[6]["type"] == "header"
    assert events.events[7]["type"] == "permission_request"
    assert events.events[8]["type"] == "permission_resolved"


@pytest.mark.asyncio
async def test_mock_runner():
    """Test MockRunner implements Runner protocol."""
    events = MockEvents()
    runner = MockRunner(events, {"api_key": "test"})

    assert runner.runner_type == "mock"
    assert runner.config == {"api_key": "test"}

    await runner.start("sess_1", "Hello", 1)
    await runner.send_input("sess_1", "Continue")
    await runner.stop("sess_1")
    runner.update_permission_mode("sess_1", 2)

    assert runner.started_sessions == [("sess_1", "Hello", 1)]
    assert runner.inputs == [("sess_1", "Continue")]
    assert runner.stopped_sessions == ["sess_1"]
    assert runner.permission_updates == [("sess_1", 2)]


def test_registry_basic():
    """Test basic registry operations."""
    registry = RunnerRegistry()

    # Empty registry
    assert registry.list() == []
    assert not registry.has("mock")

    # Register
    def mock_factory(events, config):
        return MockRunner(events, config)

    registry.register("mock", mock_factory)
    assert registry.list() == ["mock"]
    assert registry.has("mock")

    # Unregister
    registry.unregister("mock")
    assert registry.list() == []
    assert not registry.has("mock")


def test_registry_create():
    """Test creating runners from registry."""
    registry = RunnerRegistry()
    events = MockEvents()

    def mock_factory(events, config):
        return MockRunner(events, config)

    registry.register("mock", mock_factory)

    # Create runner
    runner = registry.create("mock", events, api_key="test", model="claude-3")
    assert isinstance(runner, MockRunner)
    assert runner.config == {"api_key": "test", "model": "claude-3"}

    # Unknown runner
    with pytest.raises(KeyError, match="not registered"):
        registry.create("unknown", events)


def test_registry_overwrite():
    """Test overwriting a registered runner."""
    registry = RunnerRegistry()

    def factory1(events, config):
        return MockRunner(events, {"version": 1, **config})

    def factory2(events, config):
        return MockRunner(events, {"version": 2, **config})

    registry.register("mock", factory1)
    registry.register("mock", factory2)  # Overwrites

    events = MockEvents()
    runner = registry.create("mock", events)
    assert runner.config["version"] == 2


def test_registry_multiple_runners():
    """Test registering multiple runners."""
    registry = RunnerRegistry()

    def mock_factory(events, config):
        return MockRunner(events, config)

    class AnotherRunner(MockRunner):
        runner_type = "another"

    def another_factory(events, config):
        return AnotherRunner(events, config)

    registry.register("mock", mock_factory)
    registry.register("another", another_factory)

    assert len(registry.list()) == 2
    assert "mock" in registry.list()
    assert "another" in registry.list()

    events = MockEvents()
    runner1 = registry.create("mock", events)
    runner2 = registry.create("another", events)

    assert runner1.runner_type == "mock"
    assert runner2.runner_type == "another"


def test_runner_unavailable_error():
    """Test RunnerUnavailableError exception."""
    error = RunnerUnavailableError("Service not reachable")
    assert isinstance(error, RuntimeError)
    assert str(error) == "Service not reachable"


@pytest.mark.asyncio
async def test_runner_full_lifecycle():
    """Test complete runner lifecycle through registry."""
    registry = RunnerRegistry()

    def mock_factory(events, config):
        return MockRunner(events, config)

    registry.register("mock", mock_factory)

    events = MockEvents()
    runner = registry.create("mock", events, timeout=30)

    # Start session
    await runner.start("sess_1", "Initial prompt", approval_choice=1)
    await runner.events.on_header("sess_1", title="Test Session")
    await runner.events.on_output("sess_1", "stdout", "Starting...\n")

    # Send input
    await runner.send_input("sess_1", "Continue with task")
    await runner.events.on_output("sess_1", "stdout", "Continuing...\n")

    # Permission request
    await runner.events.on_permission_request("sess_1", "req_1", "bash", {"command": "rm file"})
    await runner.events.on_permission_resolved("sess_1", "req_1", "user", True)

    # Stop
    exit_code = await runner.stop("sess_1")
    await runner.events.on_exit("sess_1", exit_code)

    # Verify events were recorded
    assert len(events.events) == 6
    assert events.events[0]["type"] == "header"
    assert events.events[1]["type"] == "output"
    assert events.events[2]["type"] == "output"
    assert events.events[3]["type"] == "permission_request"
    assert events.events[4]["type"] == "permission_resolved"
    assert events.events[5]["type"] == "exit"

    # Verify runner tracked calls
    assert len(runner.started_sessions) == 1
    assert len(runner.inputs) == 1
    assert len(runner.stopped_sessions) == 1
