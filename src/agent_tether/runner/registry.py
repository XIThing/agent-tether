"""Runner registry for discovering and creating runner instances."""

from __future__ import annotations

import logging
from typing import Any, Callable

from .protocol import Runner, RunnerEvents

logger = logging.getLogger(__name__)


RunnerFactory = Callable[[RunnerEvents, dict[str, Any]], Runner]
"""Factory function that creates a runner instance from events and config."""


class RunnerRegistry:
    """Registry for runner adapters."""

    def __init__(self) -> None:
        self._factories: dict[str, RunnerFactory] = {}

    def register(self, name: str, factory: RunnerFactory) -> None:
        """Register a runner factory.

        Args:
            name: Runner name (e.g., "claude-subprocess", "codex-sidecar").
            factory: Factory function that takes (events, **config) and returns a Runner.
        """
        if name in self._factories:
            logger.warning(f"Overwriting existing runner factory: {name}")
        self._factories[name] = factory
        logger.debug(f"Registered runner: {name}")

    def unregister(self, name: str) -> None:
        """Unregister a runner factory.

        Args:
            name: Runner name to unregister.
        """
        self._factories.pop(name, None)

    def create(self, name: str, events: RunnerEvents, **config: Any) -> Runner:
        """Create a runner instance.

        Args:
            name: Runner name (must be registered).
            events: Event callbacks.
            **config: Runner-specific configuration.

        Returns:
            Runner instance.

        Raises:
            KeyError: If runner name is not registered.
        """
        if name not in self._factories:
            available = ", ".join(self._factories.keys()) or "(none)"
            raise KeyError(f"Runner '{name}' not registered. Available runners: {available}")
        factory = self._factories[name]
        logger.debug(f"Creating runner: {name}", extra={"config": config})
        return factory(events, config)

    def list(self) -> list[str]:
        """Get list of registered runner names.

        Returns:
            List of runner names.
        """
        return list(self._factories.keys())

    def has(self, name: str) -> bool:
        """Check if a runner is registered.

        Args:
            name: Runner name to check.

        Returns:
            True if registered, False otherwise.
        """
        return name in self._factories
