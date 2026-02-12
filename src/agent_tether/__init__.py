"""agent-tether: Tether your AI agents to human oversight through chat platforms."""

from agent_tether.formatting import format_tool_input, humanize_key, humanize_enum_value
from agent_tether.models import ApprovalRequest, CommandDef, Handlers
from agent_tether.router import BridgeRouter
from agent_tether.subscriber import EventSubscriber

__all__ = [
    # Core bridge components
    "ApprovalRequest",
    "CommandDef",
    "EventSubscriber",
    "Handlers",
    "BridgeRouter",
    # Platform bridges (lazy loaded)
    "TelegramBridge",
    "SlackBridge",
    "DiscordBridge",
    # Formatting utilities
    "format_tool_input",
    "humanize_key",
    "humanize_enum_value",
    # Runner module (lazy loaded)
    "runner",
]


def __getattr__(name: str):
    """Lazy imports for platform bridges and session module — avoids requiring optional deps at import time."""
    if name == "TelegramBridge":
        try:
            from agent_tether.platforms.telegram.bridge import TelegramBridge

            return TelegramBridge
        except ImportError:
            raise ImportError(
                "TelegramBridge requires python-telegram-bot. "
                "Install with: pip install agent-tether[telegram]"
            ) from None
    if name == "SlackBridge":
        try:
            from agent_tether.platforms.slack.bridge import SlackBridge

            return SlackBridge
        except ImportError:
            raise ImportError(
                "SlackBridge requires slack-sdk and slack-bolt. "
                "Install with: pip install agent-tether[slack]"
            ) from None
    if name == "DiscordBridge":
        try:
            from agent_tether.platforms.discord.bridge import DiscordBridge

            return DiscordBridge
        except ImportError:
            raise ImportError(
                "DiscordBridge requires discord.py. "
                "Install with: pip install agent-tether[discord]"
            ) from None
    if name == "runner":
        from agent_tether import runner as runner_module

        return runner_module
    raise AttributeError(f"module 'agent_tether' has no attribute {name!r}")
