# agent-tether

[![Tests](https://github.com/larsderidder/agent-tether/actions/workflows/test.yml/badge.svg)](https://github.com/larsderidder/agent-tether/actions/workflows/test.yml)
[![Publish to PyPI](https://github.com/larsderidder/agent-tether/actions/workflows/publish.yml/badge.svg)](https://github.com/larsderidder/agent-tether/actions/workflows/publish.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-tether)](https://pypi.org/project/agent-tether/)
[![Python Versions](https://img.shields.io/pypi/pyversions/agent-tether)](https://pypi.org/project/agent-tether/)
[![License](https://img.shields.io/github/license/larsderidder/agent-tether)](LICENSE)

Connect AI coding agents to human oversight through Telegram, Slack, and Discord.

Status: Beta. APIs are stable and open to feedback.

A Python library that provides chat platform bridges for supervising AI agents. Each bridge handles platform-specific formatting, thread management, approval flows with inline buttons, auto-approve timers, and command handling.

**Use Cases:**
- Monitor Claude Code or Codex sessions from your phone while agents run locally
- Get approval requests as Telegram notifications with one-tap approve/deny
- Set auto-approve timers (per-session, per-tool, per-directory)
- Send additional input or stop agents remotely

> Looking for a ready-made solution for connecting your agents? Check out [Tether](https://github.com/larsderidder/tether).

## Install

```bash
pip install agent-tether[telegram]   # Telegram support
pip install agent-tether[slack]      # Slack support (experimental)
pip install agent-tether[discord]    # Discord support
pip install agent-tether[all]        # All platforms
```

## Architecture

```
Your Application
    │
    ├── BridgeCallbacks       → You implement these to wire up your backend
    │
    ├── agent-tether bridges
    │     ├── TelegramBridge  → Telegram forum topics
    │     ├── SlackBridge     → Slack threads (experimental)
    │     └── DiscordBridge   → Discord threads
    │
    ├── BridgeManager         → Routes events to the right bridge
    └── BridgeSubscriber      → Consumes store events, forwards to bridges
```

### Core Components

- **`BridgeCallbacks`**: Dataclass of async callbacks that you provide, connecting bridges to your session backend
- **`BridgeInterface`**: Abstract base class with shared logic for auto-approve, approval parsing, error debouncing, and formatting
- **`BridgeManager`**: Routes output, approvals, and status changes to the correct platform bridge
- **`BridgeSubscriber`**: Consumes events from a store subscriber queue and forwards them to bridges
- **`BridgeConfig`**: Configuration (data directory, default adapter, error debounce)

## Quick Start

```python
from agent_tether import (
    BridgeCallbacks,
    BridgeConfig,
    BridgeManager,
    TelegramBridge,
)

# Implement callbacks to wire bridges to your backend
callbacks = BridgeCallbacks(
    create_session=my_create_session,
    send_input=my_send_input,
    stop_session=my_stop_session,
    respond_to_permission=my_respond_to_permission,
    list_sessions=my_list_sessions,
    get_usage=my_get_usage,
    check_directory=my_check_directory,
    list_external_sessions=my_list_external,
    get_external_history=my_get_history,
    attach_external=my_attach_external,
)

# Configure
config = BridgeConfig(data_dir="/tmp/agent-tether")

# Create a Telegram bridge
telegram = TelegramBridge(
    bot_token="BOT_TOKEN",
    forum_group_id=123456,
    config=config,
    callbacks=callbacks,
    get_session_directory=lambda sid: "/home/user/project",
)

# Register with manager
manager = BridgeManager()
manager.register_bridge("telegram", telegram)

# Route events from your backend
await manager.route_output("sess_1", "Starting work...", "telegram")
await manager.route_status("sess_1", "running", "telegram")
```

### BridgeCallbacks

The `BridgeCallbacks` dataclass defines 10 async functions that connect agent-tether to your session backend:

| Callback | Signature | Purpose |
|---|---|---|
| `create_session` | `(**kwargs) -> dict` | Create a new agent session |
| `send_input` | `(session_id, text) -> None` | Send human input to a session |
| `stop_session` | `(session_id) -> None` | Interrupt/stop a running session |
| `respond_to_permission` | `(session_id, request_id, allow, message?) -> bool` | Approve or deny a permission request |
| `list_sessions` | `() -> list[dict]` | List all active sessions |
| `get_usage` | `(session_id) -> dict` | Get token/cost usage for a session |
| `check_directory` | `(path) -> dict` | Validate a directory path |
| `list_external_sessions` | `(**kwargs) -> list[dict]` | Discover running external sessions |
| `get_external_history` | `(external_id, runner_type, limit) -> dict?` | Fetch history for an external session |
| `attach_external` | `(**kwargs) -> dict` | Attach to an external session |

All callbacks default to no-ops, so you only need to implement the ones your application uses.

### Approval Parsing

Bridges parse human text into approval responses:

```python
from agent_tether import BridgeInterface

# These are parsed by bridges when users reply in chat
bridge.parse_approval_text("allow")       # → {"allow": True, "timer": None}
bridge.parse_approval_text("deny: risky") # → {"allow": False, "reason": "risky"}
bridge.parse_approval_text("allow all")   # → {"allow": True, "timer": "all"}
bridge.parse_approval_text("allow Bash")  # → {"allow": True, "timer": "Bash"}
```

### Auto-Approve Timers

```python
# Auto-approve all tools for this session (30 min)
bridge.set_allow_all("sess_1")

# Auto-approve only Bash for this session (30 min)
bridge.set_allow_tool("sess_1", "Bash")

# Auto-approve all sessions in a directory (30 min)
bridge.set_allow_directory("/home/user/project")

# Check if a request should be auto-approved
reason = bridge.check_auto_approve("sess_1", "Bash")
# Returns "Allow All", "Allow Bash", "Allow dir project", or None
```

## Features

### Chat Platform Bridges
- **Telegram**: Forum topics, inline keyboard approval buttons, typing indicators, HTML formatting
- **Slack** *(experimental)*: Socket mode, threaded conversations, text-based approval commands
- **Discord**: Channel threads, pairing/authorization system, text-based approvals

### Shared Bridge Logic
- **Auto-approve engine**: Per-session, per-tool, and per-directory timers (30 min default)
- **Approval parsing**: Text commands (allow/deny/proceed/cancel) with tool and directory timers
- **Choice parsing**: Numeric or label-based selection for multi-option prompts
- **Error debouncing**: Suppress rapid-fire error notifications
- **Notification batching**: Collapse rapid auto-approvals into single messages
- **External session pagination**: Browse and attach to running Claude Code/Codex sessions
- **Formatting**: Tool input JSON to readable markdown, message chunking

### Commands (available in all bridges)
- `/help` or `!help`: Show available commands
- `/status` or `!status`: List all sessions
- `/list` or `!list`: List external sessions (Claude Code, Codex)
- `/attach` or `!attach`: Attach to an external session
- `/new` or `!new`: Start a new session
- `/stop` or `!stop`: Interrupt the current session
- `/usage` or `!usage`: Show token usage and cost

## Documentation

- **[CHANGELOG.md](CHANGELOG.md)**: Version history and changes

## Contributing

Contributions welcome! Please feel free to submit a Pull Request.

## License

MIT. See [LICENSE](LICENSE) for details.
