# agent-tether

Connect to your AI coding agents through Telegram, Slack, and Discord. Control them from your phone while they work on your laptop.

A Python library that handles the chat-platform integration for AI agent supervision: thread management, approval flows with inline buttons, auto-approve timers, message formatting, and command handling. You provide callbacks for your application logic.

**Use Cases:**
- Monitor Claude/Codex/Aider from your phone while agents run locally
- Get approval requests as Telegram notifications with one-tap approve/deny
- Set auto-approve timers for trusted operations
- Send additional input or stop agents remotely

## Install

```bash
pip install agent-tether[telegram]   # Telegram support
pip install agent-tether[slack]      # Slack support
pip install agent-tether[discord]    # Discord support
pip install agent-tether[all]        # All platforms
```

## Quick Start

```python
import asyncio
from agent_tether import TelegramBridge, Handlers

async def on_input(thread_id: str, text: str, username: str | None):
    print(f"[{thread_id}] {username}: {text}")

async def on_approval_response(thread_id: str, request_id: str, approved: bool, **kwargs):
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

    thread_id = await bridge.create_thread("My Agent Task")
    await bridge.send_output(thread_id, "Starting work on your request...")

    await bridge.send_approval_request(
        thread_id,
        request_id="req_123",
        tool_name="Bash",
        description='{"command": "rm -rf /tmp/cache"}',
    )

    await bridge.wait_until_stopped()

asyncio.run(main())
```

### Runner Protocol Example

```python
from agent_tether.runner import Runner, RunnerEvents, RunnerRegistry

# Implement event callbacks
class MyEventHandler:
    async def on_output(self, session_id, stream, text, **kwargs):
        print(f"[{session_id}] {text}", end="")
    
    async def on_error(self, session_id, code, message):
        print(f"ERROR: {message}")
    
    async def on_exit(self, session_id, exit_code):
        print(f"Session {session_id} exited with code {exit_code}")
    
    async def on_permission_request(self, session_id, request_id, tool_name, tool_input, **kwargs):
        print(f"Permission requested for {tool_name}")
    
    # ... other event callbacks

# Register runners
registry = RunnerRegistry()

def my_runner_factory(events, config):
    # Return a Runner implementation
    return MyCustomRunner(events, **config)

registry.register("my-runner", my_runner_factory)

# Create and use runner
events = MyEventHandler()
runner = registry.create("my-runner", events, api_key="...", model="...")

await runner.start("sess_1", "Build a web app", approval_choice=1)
await runner.send_input("sess_1", "Add a login page")
await runner.stop("sess_1")
```

## Features

### Chat Platform Bridges
- **Telegram** — Forum topics, inline keyboard approval buttons, typing indicators, HTML formatting
- **Slack** — Socket mode, threaded conversations, text-based approval commands
- **Discord** — Channel threads, pairing/authorization system, text-based approvals
- **Approval engine** — Auto-approve timers (per-thread, per-tool, per-directory), batched notifications
- **Commands** — Built-in `/help`, `/stop`, `/status`, `/usage` + custom command registry
- **Formatting** — Tool input JSON → readable text, markdown conversion, message chunking

### Runner Protocol
- **Protocol definitions** — `Runner` and `RunnerEvents` interfaces for agent backends
- **RunnerRegistry** — Factory pattern for discovering and creating runners
- **Pluggable adapters** — Clean protocol for implementing custom agent backends
- **Event-driven** — Runners report progress via callbacks (output, errors, permissions, etc.)

## Documentation

- **[DESIGN.md](DESIGN.md)** — Full architecture, API reference, and configuration options
- **[CHANGELOG.md](CHANGELOG.md)** — Version history and changes

## Contributing

Contributions welcome! Please feel free to submit a Pull Request.

## Related Projects

This library is extracted from [Tether](https://github.com/xithing/tether), a full-featured control plane for supervising AI coding agents.

## License

MIT - see [LICENSE](LICENSE) for details

