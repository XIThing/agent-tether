"""agent-tether: Chat platform bridges for AI agent supervision."""

from agent_tether.base import (
    ApprovalRequest,
    ApprovalResponse,
    BridgeConfig,
    BridgeInterface,
    GetSessionDirectory,
    GetSessionInfo,
    HumanInput,
    OnSessionBound,
)
from agent_tether.manager import BridgeManager
from agent_tether.subscriber import BridgeSubscriber

__all__ = [
    # Core types
    "ApprovalRequest",
    "ApprovalResponse",
    "BridgeConfig",
    "BridgeInterface",
    "GetSessionDirectory",
    "GetSessionInfo",
    "HumanInput",
    "OnSessionBound",
    # Manager and subscriber
    "BridgeManager",
    "BridgeSubscriber",
    # Platform bridges (lazy loaded)
    "TelegramBridge",
    "SlackBridge",
    "DiscordBridge",
]


def __getattr__(name: str):
    """Lazy imports for platform bridges."""
    if name == "TelegramBridge":
        try:
            from agent_tether.telegram.bot import TelegramBridge

            return TelegramBridge
        except ImportError:
            raise ImportError(
                "TelegramBridge requires python-telegram-bot. "
                "Install with: pip install agent-tether[telegram]"
            ) from None
    if name == "SlackBridge":
        try:
            from agent_tether.slack.bot import SlackBridge

            return SlackBridge
        except ImportError:
            raise ImportError(
                "SlackBridge requires slack-sdk and slack-bolt. "
                "Install with: pip install agent-tether[slack]"
            ) from None
    if name == "DiscordBridge":
        try:
            from agent_tether.discord.bot import DiscordBridge

            return DiscordBridge
        except ImportError:
            raise ImportError(
                "DiscordBridge requires discord.py. "
                "Install with: pip install agent-tether[discord]"
            ) from None
    raise AttributeError(f"module 'agent_tether' has no attribute {name!r}")
