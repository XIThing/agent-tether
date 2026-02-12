"""Bridge router — registry for multiple platform bridges.

Allows running Telegram + Slack + Discord simultaneously, routing
events to the correct platform by name.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_tether.platforms.base import BridgeBase

logger = logging.getLogger("agent_tether.router")


class BridgeRouter:
    """Registry of named platform bridges.

    Example::

        router = BridgeRouter()
        router.register("telegram", telegram_bridge)
        router.register("slack", slack_bridge)
        await router.start_all()
    """

    def __init__(self) -> None:
        self._bridges: dict[str, BridgeBase] = {}

    def register(self, name: str, bridge: BridgeBase) -> None:
        """Register a bridge under a platform name."""
        self._bridges[name] = bridge
        logger.info("Bridge registered: %s", name)

    def get(self, name: str) -> BridgeBase | None:
        """Get a registered bridge by name."""
        return self._bridges.get(name)

    def list(self) -> list[str]:
        """List registered platform names."""
        return list(self._bridges.keys())

    async def start_all(self) -> None:
        """Start all registered bridges."""
        for name, bridge in self._bridges.items():
            logger.info("Starting bridge: %s", name)
            await bridge.start()

    async def stop_all(self) -> None:
        """Stop all registered bridges."""
        for name, bridge in self._bridges.items():
            logger.info("Stopping bridge: %s", name)
            await bridge.stop()
