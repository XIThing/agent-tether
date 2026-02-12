# agent-tether — Design Document

Tether your AI agents to human oversight through Telegram, Slack, and Discord.

Extracted from [Tether](https://github.com/xithing/tether)'s battle-tested bridge layer (~5K lines), generalized into a standalone library.

## What It Does

You're building an AI agent system. Your agent needs to:
- Send output to a human via Telegram/Slack/Discord
- Ask for approval before running tools (with inline buttons or text commands)
- Accept human input (messages, commands)
- Auto-approve repetitive tool requests with timed permissions

`agent-tether` handles all the platform plumbing. You provide callbacks for your application logic.

## What It Doesn't Do

- No session/state management (you bring your own)
- No agent/runner orchestration
- No REST API
- No database

## Install

```bash
pip install agent-tether                    # core only (no platform SDKs)
pip install agent-tether[telegram]          # + python-telegram-bot
pip install agent-tether[slack]             # + slack-sdk, slack-bolt
pip install agent-tether[discord]           # + discord.py
pip install agent-tether[all]              # everything
```

## Quick Start

```python
import asyncio
from agent_tether import TelegramBridge, Handlers

async def on_input(thread_id: str, text: str, username: str | None):
    """Human sent a message in a session thread."""
    print(f"[{thread_id}] {username}: {text}")

async def on_approval_response(thread_id: str, request_id: str, approved: bool, **kwargs):
    """Human responded to an approval request."""
    print(f"[{thread_id}] {'Approved' if approved else 'Denied'} {request_id}")

bridge = TelegramBridge(
    token="BOT_TOKEN",
    forum_group_id=123456,
    handlers=Handlers(
        on_input=on_input,
        on_approval_response=on_approval_response,
    ),
)

async def main():
    await bridge.start()
    
    # Your agent produces output → send it to a thread
    thread_id = await bridge.create_thread("My Agent Task")
    await bridge.send_output(thread_id, "Starting work on your request...")
    
    # Agent needs approval → library handles buttons/text, calls your callback
    await bridge.send_approval_request(
        thread_id,
        request_id="req_123",
        tool_name="Bash",
        description='{"command": "rm -rf /tmp/cache"}',
    )
    
    # Keep running until interrupted
    await bridge.wait_until_stopped()

asyncio.run(main())
```

## Core Concepts

### Threads

A "thread" is the library's unit of conversation. It maps to:
- Telegram: forum topic
- Slack: message thread  
- Discord: channel thread

You create threads, send messages to them, receive input from them. The library manages the platform-specific details (topic creation, thread_ts tracking, etc.).

Thread IDs are strings. The library assigns them (platform-native IDs) and you store them however you want.

### Handlers

Callbacks you provide. The library calls them when platform events arrive.

```python
@dataclass
class Handlers:
    on_input: InputHandler | None = None
    on_approval_response: ApprovalHandler | None = None
    on_command: CommandHandler | None = None
    on_status_request: StatusHandler | None = None
    on_stop_request: StopHandler | None = None
    on_usage_request: UsageHandler | None = None
```

Type signatures:

```python
InputHandler = Callable[[str, str, str | None], Awaitable[None]]
    # (thread_id, text, username)

ApprovalHandler = Callable[[str, str, bool, str | None, str | None], Awaitable[None]]
    # (thread_id, request_id, approved, reason, timer)
    # timer: None | "all" | "dir" | tool_name

CommandHandler = Callable[[str, str, str], Awaitable[str | None]]
    # (thread_id, command, args) → optional response text

StatusHandler = Callable[[], Awaitable[str]]
    # () → status text to display

StopHandler = Callable[[str], Awaitable[str | None]]
    # (thread_id) → optional confirmation text

UsageHandler = Callable[[str], Awaitable[str | None]]
    # (thread_id) → optional usage text
```

### Approval System

The library provides a complete approval flow:

**Sending requests:**
```python
await bridge.send_approval_request(
    thread_id="t_123",
    request_id="req_abc",
    tool_name="Bash",
    description='{"command": "ls -la"}',
)
```

On Telegram, this renders as an HTML message with inline keyboard buttons:
- Allow / Deny / Deny ✏️
- Allow {tool} (30m) / Allow All (30m)
- Show All (if description was truncated)

On Slack/Discord, it sends a formatted text message and accepts text replies:
- `allow`, `deny`, `deny: reason`, `allow all`, `allow bash`

**Auto-approve engine:**

Built-in timed auto-approve. When a human clicks "Allow All (30m)", subsequent approval requests for that thread auto-resolve for 30 minutes. Supports:
- Per-thread allow-all
- Per-thread per-tool
- Per-directory (group of threads sharing a directory)
- Configurable never-auto-approve list (default: "task" tools that need human judgment)

The library calls `on_approval_response` with `timer="all"` / `timer=tool_name` / `timer="dir"` so you can distinguish one-off approvals from timer-based ones.

Auto-approved requests are batched into a single notification (e.g., "✅ Auto-approved 3 tools: Bash, Read, Write") instead of flooding the chat.

**Choice requests:**

For multi-option questions (not just allow/deny):
```python
await bridge.send_choice_request(
    thread_id="t_123",
    request_id="req_abc",
    title="Select environment",
    description="Where should I deploy?",
    options=["staging", "production", "cancel"],
)
```

Renders numbered options. User replies with `1`, `2`, `3` or the label text.

### Commands

Built-in commands (all platforms):

| Command | Telegram | Slack/Discord | Behavior |
|---------|----------|---------------|----------|
| Help | `/help` | `!help` | Auto-generated command list |
| Status | `/status` | `!status` | Calls `on_status_request` handler |
| Stop | `/stop` | `!stop` | Calls `on_stop_request` handler |
| Usage | `/usage` | `!usage` | Calls `on_usage_request` handler |

Custom commands:
```python
bridge = TelegramBridge(
    ...,
    commands={
        "deploy": CommandDef(
            description="Deploy the current build",
            handler=my_deploy_handler,  # async (thread_id, args) -> str | None
        ),
        "logs": CommandDef(
            description="Show recent agent logs",
            handler=my_logs_handler,
        ),
    },
)
```

Disable built-in commands:
```python
bridge = TelegramBridge(
    ...,
    disabled_commands={"usage", "status"},
)
```

### Formatting

The library handles platform-specific formatting:

- **Tool input formatting**: JSON dicts rendered as human-readable key-value pairs with smart truncation, code blocks for commands/file content, humanized keys (`file_path` → `File path`)
- **Telegram**: Markdown → Telegram HTML conversion (bold, italic, code, pre, links, tables)
- **Slack**: Markdown passthrough (Slack supports mrkdwn natively)
- **Discord**: Markdown passthrough (Discord supports standard markdown)
- **Message chunking**: Auto-splits long messages at platform limits (4096 for Telegram, 4000 for Slack, 2000 for Discord)

Available as standalone utilities:
```python
from agent_tether.formatting import (
    format_tool_input,
    humanize_key,
    chunk_message,
)
from agent_tether.platforms.telegram.formatting import markdown_to_telegram_html
```

### State Persistence

Thread-to-name mappings are persisted to JSON files so thread names stay unique across restarts. You configure the data directory:

```python
bridge = TelegramBridge(
    ...,
    data_dir="~/.my-agent/",  # default: ~/.agent-tether/
)
```

## Architecture

```
agent_tether/
    __init__.py              # Public API exports
    models.py                # ApprovalRequest, ChoiceRequest, Handlers, CommandDef
    approval.py              # AutoApproveEngine (timers, checking, never-approve list)
    formatting.py            # format_tool_input, humanize_key, humanize_enum_value
    batching.py              # NotificationBatcher (buffer + flush-after-delay)
    debounce.py              # ErrorDebouncer
    state.py                 # ThreadState (JSON persistence for thread mappings)
    router.py                # BridgeRouter (registry, route by platform name)

    platforms/
        base.py              # BridgeBase ABC
        telegram/
            __init__.py
            bridge.py        # TelegramBridge
            formatting.py    # markdown_to_telegram_html, chunk_message, escape
        slack/
            __init__.py
            bridge.py        # SlackBridge
        discord/
            __init__.py
            bridge.py        # DiscordBridge
```

### BridgeBase

Abstract base that all platform bridges extend:

```python
class BridgeBase(ABC):
    """Base class for platform bridges."""
    
    def __init__(self, handlers: Handlers, **kwargs):
        self._handlers = handlers
        self._approval_engine = AutoApproveEngine()
        self._batcher = NotificationBatcher(flush_callback=self._send_auto_approve_batch)
        self._debouncer = ErrorDebouncer()
        self._thread_state = ThreadState(data_dir=kwargs.get("data_dir"))
        self._commands: dict[str, CommandDef] = {}
    
    # --- Public API (consumers call these) ---
    
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def wait_until_stopped(self) -> None: ...
    
    async def create_thread(self, name: str, *, directory: str | None = None) -> str: ...
    async def send_output(self, thread_id: str, text: str) -> None: ...
    async def send_approval_request(self, thread_id: str, ...) -> None: ...
    async def send_choice_request(self, thread_id: str, ...) -> None: ...
    async def send_status(self, thread_id: str, status: str) -> None: ...
    async def remove_thread(self, thread_id: str) -> None: ...
    
    # --- Internal (platforms implement these) ---
    
    @abstractmethod
    async def _platform_start(self) -> None: ...
    @abstractmethod
    async def _platform_stop(self) -> None: ...
    @abstractmethod
    async def _platform_send(self, thread_id: str, text: str, **kwargs) -> None: ...
    @abstractmethod
    async def _platform_create_thread(self, name: str) -> str: ...
```

### Thread ID Design

Thread IDs are platform-native identifiers stored as strings:
- Telegram: topic message_thread_id (e.g., "12345")
- Slack: thread_ts (e.g., "1234567890.123456")
- Discord: thread channel ID (e.g., "9876543210")

The consumer gets these from `create_thread()` and uses them for all subsequent calls. The library never generates synthetic IDs — you always work with the real platform identifier.

**Thread↔session mapping is the consumer's responsibility.** The library works exclusively with thread IDs. If your application has a concept of "sessions" (like Tether does), you maintain a `dict[str, str]` mapping thread_id↔session_id in your own code. All callbacks receive thread IDs — your handler looks up the corresponding session.

### Directory Association

Threads can optionally be associated with a directory path:

```python
thread_id = await bridge.create_thread("My Task", directory="/home/user/repo")
```

This enables directory-scoped auto-approve: when a human clicks "Allow dir (30m)", all threads sharing that directory get auto-approved. Without a directory, that button doesn't appear and auto-approve is per-thread only.

The library stores directory associations internally — the consumer just passes the path at thread creation time.

## Configuration

```python
# Telegram
bridge = TelegramBridge(
    token="BOT_TOKEN",
    forum_group_id=123456,
    handlers=handlers,
    commands={...},                   # optional custom commands
    disabled_commands={"usage"},      # optional
    data_dir="~/.my-agent/",         # optional, default ~/.agent-tether/
    auto_approve_duration=1800,      # optional, default 30 minutes
    never_auto_approve={"task"},     # optional, default {"task", "enterplanmode", "exitplanmode"}
    flush_delay=1.5,                 # optional, batching delay in seconds
)

# Slack
bridge = SlackBridge(
    bot_token="xoxb-...",
    app_token="xapp-...",           # for socket mode (real-time events)
    channel_id="C...",
    handlers=handlers,
)

# Discord
bridge = DiscordBridge(
    bot_token="...",
    channel_id=123456,
    handlers=handlers,
    require_pairing=False,          # optional, default False
    allowed_user_ids={111, 222},    # optional
)
```

## How Tether Uses This

After extraction, Tether keeps:
- `BridgeSubscriber` — consumes store events, calls `bridge.send_output()` / `bridge.send_approval_request()` etc.
- A thin adapter layer (~100–150 lines per platform) that maps thread_id↔session_id and wires handlers to Tether's API

```python
from agent_tether import TelegramBridge, Handlers, CommandDef

# Tether maintains the thread↔session mapping
thread_to_session: dict[str, str] = {}
session_to_thread: dict[str, str] = {}

async def on_input(thread_id: str, text: str, username: str | None):
    session_id = thread_to_session.get(thread_id)
    if session_id:
        await send_input_or_start_via_api(session_id, text)

async def on_approval_response(thread_id: str, request_id: str, approved: bool, **kw):
    session_id = thread_to_session.get(thread_id)
    if session_id:
        await respond_to_permission(session_id, request_id, approved)

async def on_stop(thread_id: str) -> str | None:
    session_id = thread_to_session.get(thread_id)
    if session_id:
        await interrupt_session(session_id)
        return "⏹️ Session interrupted."
    return "No session linked to this thread."

handlers = Handlers(
    on_input=on_input,
    on_approval_response=on_approval_response,
    on_status_request=fetch_sessions_and_format,
    on_stop_request=on_stop,
    on_usage_request=lambda tid: fetch_usage(thread_to_session.get(tid)),
)

bridge = TelegramBridge(
    token=settings.telegram_bot_token(),
    forum_group_id=settings.telegram_forum_group_id(),
    handlers=handlers,
    commands={
        "list": CommandDef(description="List external sessions", handler=cmd_list),
        "attach": CommandDef(description="Attach to session", handler=cmd_attach),
        "new": CommandDef(description="Start new session", handler=cmd_new),
    },
)
```

The Tether-specific commands (`list`, `attach`, `new`) become custom commands. The generic ones (`help`, `stop`, `status`, `usage`) are built-in.

The `BridgeSubscriber` (stays in Tether) translates the other direction:
```python
# Tether subscriber consumes store events and pushes to agent-tether bridge
async def _consume(session_id, queue):
    thread_id = session_to_thread.get(session_id)
    while True:
        event = await queue.get()
        if event["type"] == "output" and event["data"].get("final"):
            await bridge.send_output(thread_id, event["data"]["text"])
        elif event["type"] == "permission_request":
            await bridge.send_approval_request(thread_id, ...)
        # etc.
```

## Migration Path

1. Build `agent-tether` as a new package in this repo (or separate repo)
2. Write tests against the new API
3. Refactor Tether to use `agent-tether` as a dependency
4. Publish to PyPI
5. Remove the old bridge code from Tether

## Open Design Decisions

### 1. Sync callbacks or async-only?
Current design: all callbacks are async. Sync users would need `async def handler(...): return sync_fn(...)`. This is fine — the library is inherently async (bot SDKs are all async).

### 2. Multiple bridges simultaneously?
Support running Telegram + Slack + Discord in the same process? Yes — `BridgeRouter` handles this, same pattern as Tether's `BridgeManager`. Each bridge is independent.

### 3. Reply semantics
When a human sends input in a thread with a pending approval request, should the library:
- (a) Always try to parse as approval first, fall through to `on_input` if not recognized
- (b) Let the consumer decide via a flag

Current design: (a), matching Tether's behavior. This is the right default — if someone types "allow" in a thread with a pending approval, they mean to approve.

### 4. Logging
Use `structlog` (like Tether) or stdlib `logging`? 

Recommendation: stdlib `logging` with a named logger (`logging.getLogger("agent_tether")`). Lower dependency count, consumers configure their own logging. structlog users can still capture it.

---

## Runner Protocol (v0.2.0)

### Overview

The `agent_tether.runner` module provides protocol definitions and a registry for AI agent backend adapters. It defines the contract between the control plane (Tether) and the execution layer (Claude, Codex, etc.).

### Core Components

#### `Runner` Protocol

Interface that all runner adapters must implement:

```python
class Runner(Protocol):
    runner_type: str  # e.g., "codex", "claude", "litellm"
    
    async def start(session_id: str, prompt: str, approval_choice: int) -> None:
        """Start a new session with the given prompt."""
    
    async def send_input(session_id: str, text: str) -> None:
        """Send user input to an active session."""
    
    async def stop(session_id: str) -> int | None:
        """Stop a session and return exit code."""
    
    def update_permission_mode(session_id: str, approval_choice: int) -> None:
        """Update permission mode for an active session."""
```

#### `RunnerEvents` Protocol

Callbacks that runners invoke to report activity:

```python
class RunnerEvents(Protocol):
    async def on_output(session_id, stream, text, *, kind="final", is_final=None):
        """Report output (stdout/stderr/thinking)."""
    
    async def on_error(session_id, code, message):
        """Report an error."""
    
    async def on_exit(session_id, exit_code):
        """Report session exit."""
    
    async def on_awaiting_input(session_id):
        """Signal waiting for user input."""
    
    async def on_metadata(session_id, key, value, raw):
        """Report metadata (usage stats, etc.)."""
    
    async def on_heartbeat(session_id, elapsed_s, done):
        """Periodic heartbeat."""
    
    async def on_header(session_id, *, title, model=None, provider=None, ...):
        """Report session header info."""
    
    async def on_permission_request(session_id, request_id, tool_name, tool_input, suggestions=None):
        """Request user permission for tool use."""
    
    async def on_permission_resolved(session_id, request_id, resolved_by, allowed, message=None):
        """Report permission resolution."""
```

#### `RunnerRegistry`

Factory registry for discovering and creating runners:

```python
registry = RunnerRegistry()

# Register a runner factory
def claude_factory(events: RunnerEvents, config: dict) -> Runner:
    return ClaudeSubprocessRunner(
        events=events,
        api_key=config.get("api_key"),
        model=config.get("model", "claude-3-5-sonnet-20241022"),
    )

registry.register("claude-subprocess", claude_factory)

# Create runner instance
events = MyEventHandler()
runner = registry.create("claude-subprocess", events, api_key="sk-...")

# List available runners
print(registry.list())  # ["claude-subprocess"]
```

### Implementing a Custom Runner

```python
from agent_tether.runner import Runner, RunnerEvents

class MyCustomRunner:
    """Example custom runner implementation."""
    
    runner_type = "my-runner"
    
    def __init__(self, events: RunnerEvents, *, api_key: str, model: str):
        self.events = events
        self.api_key = api_key
        self.model = model
        self.sessions = {}
    
    async def start(self, session_id: str, prompt: str, approval_choice: int):
        # Initialize session
        self.sessions[session_id] = {"prompt": prompt, "approval": approval_choice}
        
        # Emit header
        await self.events.on_header(
            session_id,
            title=f"Session {session_id}",
            model=self.model,
        )
        
        # Start processing
        await self.events.on_output(session_id, "stdout", "Starting...\n")
        # ... actual runner logic
    
    async def send_input(self, session_id: str, text: str):
        await self.events.on_output(session_id, "stdout", f"Received: {text}\n")
        # ... process input
    
    async def stop(self, session_id: str) -> int | None:
        session = self.sessions.pop(session_id, None)
        if session:
            await self.events.on_exit(session_id, 0)
            return 0
        return None
    
    def update_permission_mode(self, session_id: str, approval_choice: int):
        if session_id in self.sessions:
            self.sessions[session_id]["approval"] = approval_choice
```

### Adapter Patterns

#### Subprocess Runner

For runners that wrap a CLI tool (e.g., `claude` command):

```python
class SubprocessRunner:
    async def start(self, session_id: str, prompt: str, approval_choice: int):
        # Spawn subprocess
        proc = await asyncio.create_subprocess_exec(
            "claude", "--prompt", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Stream output
        async for line in proc.stdout:
            await self.events.on_output(session_id, "stdout", line.decode())
        
        exit_code = await proc.wait()
        await self.events.on_exit(session_id, exit_code)
```

#### API Runner

For runners that call an HTTP/WS API:

```python
class ApiRunner:
    async def start(self, session_id: str, prompt: str, approval_choice: int):
        async with aiohttp.ClientSession() as http:
            async with http.post(
                "https://api.example.com/sessions",
                json={"prompt": prompt},
            ) as resp:
                async for line in resp.content:
                    data = json.loads(line)
                    if data["type"] == "output":
                        await self.events.on_output(
                            session_id, "stdout", data["text"]
                        )
```

### Built-in Adapters (Coming in Future Releases)

The following adapters will be available in future versions:

#### `ClaudeSubprocessRunner`

Wraps the Claude CLI tool, spawning a subprocess per session.

```python
# pip install agent-tether[claude]
from agent_tether.runner.adapters import ClaudeSubprocessRunner

runner = ClaudeSubprocessRunner(
    events=my_events,
    api_key="sk-ant-...",
    model="claude-3-5-sonnet-20241022",
)
```

#### `ClaudeAPIRunner`

Direct Anthropic API integration using the Python SDK.

```python
# pip install agent-tether[claude]
from agent_tether.runner.adapters import ClaudeAPIRunner

runner = ClaudeAPIRunner(
    events=my_events,
    api_key="sk-ant-...",
    model="claude-3-5-sonnet-20241022",
)
```

#### `CodexSidecarRunner`

Connects to a Codex SDK sidecar process via HTTP/WebSocket.

```python
# pip install agent-tether[codex]
from agent_tether.runner.adapters import CodexSidecarRunner

runner = CodexSidecarRunner(
    events=my_events,
    base_url="http://localhost:8081",
)
```

#### `PiRPCRunner`

Connects to a Pi RPC endpoint for session control.

```python
# pip install agent-tether[codex]
from agent_tether.runner.adapters import PiRPCRunner

runner = PiRPCRunner(
    events=my_events,
    endpoint="http://localhost:8765/rpc",
)
```

#### `LiteLLMRunner`

Generic runner using LiteLLM for multi-provider support.

```python
# pip install agent-tether[litellm]
from agent_tether.runner.adapters import LiteLLMRunner

runner = LiteLLMRunner(
    events=my_events,
    model="gpt-4",
    api_key="sk-...",
)
```

### Design Notes

1. **Protocol-based** — No base class to inherit from, just implement the protocol
2. **Event-driven** — Runners push events, don't poll for state
3. **Session-scoped** — One runner instance can manage multiple sessions
4. **No store dependency** — Runners don't directly access SessionStore (passed via events)
5. **Approval modes**:
   - 0 = Never ask (auto-approve all)
   - 1 = Ask first time per tool
   - 2 = Ask always

### Testing Patterns

```python
# Mock event handler for testing
class MockEvents:
    def __init__(self):
        self.events = []
    
    async def on_output(self, session_id, stream, text, **kwargs):
        self.events.append({"type": "output", "text": text})
    
    # ... other callbacks

# Test runner
@pytest.mark.asyncio
async def test_runner():
    events = MockEvents()
    runner = MyRunner(events, config={"api_key": "test"})
    
    await runner.start("sess_1", "Hello", approval_choice=1)
    await runner.send_input("sess_1", "Continue")
    await runner.stop("sess_1")
    
    assert len(events.events) > 0
    assert events.events[0]["type"] == "output"
```

### Integration with Session Store

Tether wires runners to the session store via an event handler:

```python
class TetherRunnerEvents:
    def __init__(self, store: SessionStore):
        self.store = store
    
    async def on_output(self, session_id, stream, text, **kwargs):
        # Update session state
        session = self.store.get_session(session_id)
        if session:
            session.last_activity_at = datetime.now().isoformat()
            self.store.update_session(session)
        
        # Emit event to subscribers
        await self.store.emit(session_id, {
            "type": "output",
            "stream": stream,
            "text": text,
            "seq": self.store.next_seq(session_id),
        })
```

### Future Enhancements

- Async permission resolution (runners block on permission requests)
- Session resume/reconnect after restart
- Multi-step tool execution pipelines
- Resource limits (CPU, memory, time)
- Session migration between runners
