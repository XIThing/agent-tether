"""Tests for runner protocol definitions."""

from __future__ import annotations

import pytest

from agent_tether.runner import Runner, RunnerEvents, RunnerUnavailableError


class TestRunnerUnavailableError:
    def test_is_runtime_error(self):
        err = RunnerUnavailableError("backend down")
        assert isinstance(err, RuntimeError)

    def test_message(self):
        err = RunnerUnavailableError("codex not running")
        assert str(err) == "codex not running"

    def test_raise_catch(self):
        with pytest.raises(RunnerUnavailableError, match="not reachable"):
            raise RunnerUnavailableError("not reachable")


class FakeEvents:
    """Concrete implementation of RunnerEvents for testing."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    async def on_output(self, session_id, stream, text, *, kind="final", is_final=None):
        self.calls.append(("on_output", (session_id, stream, text), {"kind": kind, "is_final": is_final}))

    async def on_error(self, session_id, code, message):
        self.calls.append(("on_error", (session_id, code, message), {}))

    async def on_exit(self, session_id, exit_code):
        self.calls.append(("on_exit", (session_id, exit_code), {}))

    async def on_awaiting_input(self, session_id):
        self.calls.append(("on_awaiting_input", (session_id,), {}))

    async def on_metadata(self, session_id, key, value, raw):
        self.calls.append(("on_metadata", (session_id, key, value, raw), {}))

    async def on_heartbeat(self, session_id, elapsed_s, done):
        self.calls.append(("on_heartbeat", (session_id, elapsed_s, done), {}))

    async def on_header(self, session_id, *, title, model=None, provider=None, sandbox=None, approval=None, thread_id=None):
        self.calls.append(("on_header", (session_id,), {"title": title, "model": model}))

    async def on_permission_request(self, session_id, request_id, tool_name, tool_input, suggestions=None):
        self.calls.append(("on_permission_request", (session_id, request_id, tool_name), {}))

    async def on_permission_resolved(self, session_id, request_id, resolved_by, allowed, message=None):
        self.calls.append(("on_permission_resolved", (session_id, request_id, resolved_by, allowed), {}))


class FakeRunner:
    """Concrete implementation of Runner for testing."""

    runner_type = "fake"

    def __init__(self):
        self.started: list[tuple] = []
        self.inputs: list[tuple] = []
        self.stopped: list[str] = []

    async def start(self, session_id, prompt, approval_choice):
        self.started.append((session_id, prompt, approval_choice))

    async def send_input(self, session_id, text):
        self.inputs.append((session_id, text))

    async def stop(self, session_id):
        self.stopped.append(session_id)
        return 0

    def update_permission_mode(self, session_id, approval_choice):
        pass


class TestRunnerEventsProtocol:
    """Verify that a concrete class satisfying RunnerEvents works correctly."""

    @pytest.mark.anyio
    async def test_on_output(self):
        events = FakeEvents()
        await events.on_output("s1", "combined", "hello", kind="final", is_final=True)
        assert events.calls[-1] == ("on_output", ("s1", "combined", "hello"), {"kind": "final", "is_final": True})

    @pytest.mark.anyio
    async def test_on_output_step(self):
        events = FakeEvents()
        await events.on_output("s1", "combined", "[tool: Read]", kind="step", is_final=False)
        assert events.calls[-1][2]["kind"] == "step"

    @pytest.mark.anyio
    async def test_on_error(self):
        events = FakeEvents()
        await events.on_error("s1", "CRASH", "segfault")
        assert events.calls[-1] == ("on_error", ("s1", "CRASH", "segfault"), {})

    @pytest.mark.anyio
    async def test_on_exit(self):
        events = FakeEvents()
        await events.on_exit("s1", 0)
        assert events.calls[-1] == ("on_exit", ("s1", 0), {})

    @pytest.mark.anyio
    async def test_on_exit_none(self):
        events = FakeEvents()
        await events.on_exit("s1", None)
        assert events.calls[-1] == ("on_exit", ("s1", None), {})

    @pytest.mark.anyio
    async def test_on_awaiting_input(self):
        events = FakeEvents()
        await events.on_awaiting_input("s1")
        assert events.calls[-1] == ("on_awaiting_input", ("s1",), {})

    @pytest.mark.anyio
    async def test_on_metadata(self):
        events = FakeEvents()
        await events.on_metadata("s1", "tokens", {"input": 100}, "input: 100")
        assert events.calls[-1] == ("on_metadata", ("s1", "tokens", {"input": 100}, "input: 100"), {})

    @pytest.mark.anyio
    async def test_on_heartbeat(self):
        events = FakeEvents()
        await events.on_heartbeat("s1", 5.0, False)
        assert events.calls[-1] == ("on_heartbeat", ("s1", 5.0, False), {})

    @pytest.mark.anyio
    async def test_on_heartbeat_done(self):
        events = FakeEvents()
        await events.on_heartbeat("s1", 30.0, True)
        assert events.calls[-1][1][2] is True

    @pytest.mark.anyio
    async def test_on_header(self):
        events = FakeEvents()
        await events.on_header("s1", title="Claude Code", model="claude-4")
        assert events.calls[-1] == ("on_header", ("s1",), {"title": "Claude Code", "model": "claude-4"})

    @pytest.mark.anyio
    async def test_on_permission_request(self):
        events = FakeEvents()
        await events.on_permission_request("s1", "req_1", "Write", {"path": "/tmp/x"})
        assert events.calls[-1] == ("on_permission_request", ("s1", "req_1", "Write"), {})

    @pytest.mark.anyio
    async def test_on_permission_resolved(self):
        events = FakeEvents()
        await events.on_permission_resolved("s1", "req_1", "user", True, message="ok")
        assert events.calls[-1] == ("on_permission_resolved", ("s1", "req_1", "user", True), {})


class TestRunnerProtocol:
    """Verify that a concrete class satisfying Runner works correctly."""

    @pytest.mark.anyio
    async def test_start(self):
        runner = FakeRunner()
        await runner.start("s1", "fix the bug", 0)
        assert runner.started == [("s1", "fix the bug", 0)]

    @pytest.mark.anyio
    async def test_send_input(self):
        runner = FakeRunner()
        await runner.send_input("s1", "try a different approach")
        assert runner.inputs == [("s1", "try a different approach")]

    @pytest.mark.anyio
    async def test_stop(self):
        runner = FakeRunner()
        code = await runner.stop("s1")
        assert code == 0
        assert runner.stopped == ["s1"]

    def test_runner_type(self):
        runner = FakeRunner()
        assert runner.runner_type == "fake"

    def test_update_permission_mode(self):
        runner = FakeRunner()
        runner.update_permission_mode("s1", 2)  # Should not raise

    @pytest.mark.anyio
    async def test_isinstance_check_structural(self):
        """Protocol is structural; isinstance won't work without runtime_checkable."""
        runner = FakeRunner()
        # We verify it has the right attributes, not isinstance
        assert hasattr(runner, "start")
        assert hasattr(runner, "send_input")
        assert hasattr(runner, "stop")
        assert hasattr(runner, "runner_type")
        assert hasattr(runner, "update_permission_mode")
