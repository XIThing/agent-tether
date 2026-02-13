# Changelog

All notable changes to agent-tether will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-02-12

### Changed
- **BREAKING**: Complete rewrite as a standalone library.
- Replaced `Handlers` callback model with `BridgeInterface` abstract base class (push model)
- Replaced `BridgeBase` with platform-specific implementations (`TelegramBridge`, `SlackBridge`, `DiscordBridge`)
- Removed `Runner`, `RunnerEvents`, `RunnerRegistry` (runner protocol)
- Removed standalone formatting, approval, batching, debounce, and router modules. All logic is now in `BridgeInterface` or platform implementations.
- Replaced all internal HTTP calls with `BridgeCallbacks`, a dataclass of async functions provided by the host application. No more `httpx` dependency or assumption of a localhost API server.
- Removed `api_port` and `api_token` from `BridgeConfig`
- Switched from stdlib `logging` to `structlog`

### Added
- `BridgeCallbacks` dataclass with 10 async callback slots (`create_session`, `send_input`, `stop_session`, `respond_to_permission`, `list_sessions`, `get_usage`, `check_directory`, `list_external_sessions`, `get_external_history`, `attach_external`)
- `BridgeInterface` with shared helpers: auto-approve timers, approval/choice text parsing, error debouncing, notification batching, external session pagination, formatting
- `BridgeManager` for multi-platform event routing
- `BridgeSubscriber` for consuming store events and forwarding to bridges
- `BridgeConfig` for configuration (data directory, default adapter, error debounce)
- Callbacks for store integration (`GetSessionDirectory`, `GetSessionInfo`, `OnSessionBound`)
- Telegram `StateManager` for session-to-topic persistence
- Discord `DiscordPairingState` for pairing code management
- `thread_state` module for lightweight thread name persistence
- Full command handling in all three bridges (help, status, list, attach, new, stop, usage)

### Removed
- `httpx` dependency (replaced by `BridgeCallbacks`)
- `agent_tether.runner` module (Runner protocol, RunnerEvents, RunnerRegistry, RunnerUnavailableError)
- `agent_tether.models` module (Handlers, CommandDef, ApprovalRequest moved to base)
- `agent_tether.platforms` package (replaced by top-level telegram/slack/discord packages)
- `agent_tether.approval`, `agent_tether.batching`, `agent_tether.debounce`, `agent_tether.formatting`, `agent_tether.router`, `agent_tether.state` modules

## [0.2.0] - 2026-02-12

### Added
- Runner protocol and registry framework
  - `Runner` protocol for agent backends
  - `RunnerEvents` protocol with 10 event callbacks
  - `RunnerRegistry` for factory-based runner creation
  - `RunnerUnavailableError` exception
- Comprehensive test suite (41 tests)
- Full documentation in README.md

### Changed
- Simplified package scope to bridges + runner protocol only
- Updated documentation to reflect focused scope
- Improved example code in README

## [0.1.0] - 2026-02-11

### Added
- Initial release with chat platform bridges
- Telegram bridge with forum topics and inline keyboards
- Slack bridge with socket mode and threaded conversations
- Discord bridge with pairing system and thread management
- Auto-approve engine with per-thread, per-tool, and per-directory timers
- Command handling with built-in commands and custom registry
- Message formatting utilities (markdown, chunking, tool inputs)
- Thread state management with JSON persistence
- Notification batching and error debouncing
- Event subscriber system for bridge events
- Comprehensive test coverage
- MIT License
- Full documentation

[1.0.0]: https://github.com/larsderidder/agent-tether/releases/tag/v1.0.0
[0.2.0]: https://github.com/larsderidder/agent-tether/releases/tag/v0.2.0
[0.1.0]: https://github.com/larsderidder/agent-tether/releases/tag/v0.1.0
