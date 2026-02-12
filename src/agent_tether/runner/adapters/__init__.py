"""Runner adapter implementations.

Adapters are available based on installed optional dependencies:

- claude-subprocess: Requires anthropic SDK (pip install agent-tether[claude])
- claude-api: Requires anthropic SDK (pip install agent-tether[claude])
- codex-sidecar: Requires aiohttp (pip install agent-tether[codex])
- pi-rpc: Requires aiohttp (pip install agent-tether[codex])
- litellm: Requires litellm (pip install agent-tether[litellm])

Example:
    from agent_tether.runner.adapters import ClaudeSubprocessRunner
"""

__all__ = []

# Note: Actual adapter implementations will be added in future commits
# For now, this module serves as a placeholder for the adapter namespace
