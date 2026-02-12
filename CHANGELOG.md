# Changelog

All notable changes to agent-tether will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-02-12

### Added
- Runner protocol and registry framework
  - `Runner` protocol for agent backends
  - `RunnerEvents` protocol with 10 event callbacks
  - `RunnerRegistry` for factory-based runner creation
  - `RunnerUnavailableError` exception
- Comprehensive test suite (41 tests)
- Full documentation in README.md and DESIGN.md

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

[0.2.0]: https://github.com/xithing/agent-tether/releases/tag/v0.2.0
[0.1.0]: https://github.com/xithing/agent-tether/releases/tag/v0.1.0
