"""Thread state persistence (JSON-backed thread↔name mappings).

Provides persistent tracking of thread names so that unique naming
(e.g., "Repo", "Repo 2", "Repo 3") survives restarts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("agent_tether.state")

_DEFAULT_MAX_LEN = 64


class ThreadState:
    """Persistent thread name registry.

    Tracks thread_id → name mappings on disk so unique name allocation
    is consistent across restarts.

    Args:
        path: Path to the JSON state file.
        max_name_len: Maximum thread name length (default 64).
    """

    def __init__(self, path: str | Path, *, max_name_len: int = _DEFAULT_MAX_LEN) -> None:
        self._path = Path(path)
        self._max_len = max_name_len
        self._names: dict[str, str] = {}  # thread_id → name
        self._used_names: set[str] = set()

    def load(self) -> None:
        """Load state from disk."""
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if not isinstance(raw, dict):
                return
            for k, v in raw.items():
                ks = str(k).strip()
                vs = str(v).strip()
                if ks and vs:
                    self._names[ks] = vs
                    self._used_names.add(vs)
            logger.debug("Loaded thread state: %d entries", len(self._names))
        except Exception:
            logger.exception("Failed to load thread state from %s", self._path)

    def save(self) -> None:
        """Save state to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(dict(sorted(self._names.items())), indent=2, sort_keys=True)
            self._path.write_text(data + "\n", "utf-8")
        except Exception:
            logger.exception("Failed to save thread state to %s", self._path)

    def allocate_name(self, base_name: str) -> str:
        """Allocate a unique thread name based on ``base_name``.

        If ``base_name`` is already in use, appends " 2", " 3", etc.
        Returns the allocated name.
        """
        base = (base_name or "Thread").strip() or "Thread"
        base = base[: self._max_len]

        if base not in self._used_names:
            return base

        for i in range(2, 100):
            suffix = f" {i}"
            avail = max(1, self._max_len - len(suffix))
            candidate = (base[:avail] + suffix)[: self._max_len]
            if candidate not in self._used_names:
                return candidate

        return base

    def register(self, thread_id: str, name: str) -> None:
        """Register a thread_id → name mapping and persist."""
        self._names[thread_id] = name
        self._used_names.add(name)
        self.save()

    def unregister(self, thread_id: str) -> None:
        """Remove a thread mapping and persist."""
        name = self._names.pop(thread_id, None)
        if name:
            self._used_names.discard(name)
            self.save()

    def get_name(self, thread_id: str) -> str | None:
        """Get the name for a thread, or None."""
        return self._names.get(thread_id)

    def get_thread_id(self, name: str) -> str | None:
        """Reverse lookup: find thread_id by name."""
        for tid, n in self._names.items():
            if n == name:
                return tid
        return None
