"""Discord pairing state (paired user IDs + pairing code).

Lightweight, file-backed allowlist for the Discord bridge.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_pairing_code() -> str:
    """Generate an 8-digit pairing code."""
    return f"{secrets.randbelow(10**8):08d}"


@dataclass
class PairingState:
    """Persistent pairing state for Discord authorization."""

    pairing_code: str
    paired_user_ids: set[int]
    control_channel_id: int | None
    created_at: str

    def to_json(self) -> dict:
        return {
            "pairing_code": self.pairing_code,
            "paired_user_ids": sorted(self.paired_user_ids),
            "control_channel_id": self.control_channel_id,
            "created_at": self.created_at,
        }


def load_or_create(
    *,
    path: Path,
    fixed_code: str | None = None,
) -> PairingState:
    """Load pairing state from disk, or create a new one."""
    path.parent.mkdir(parents=True, exist_ok=True)

    state: PairingState | None = None
    if path.exists():
        try:
            raw = json.loads(path.read_text("utf-8"))
            code = str(raw.get("pairing_code") or "").strip()
            ids_raw = raw.get("paired_user_ids") or []
            ids = {int(x) for x in ids_raw if str(x).strip()}
            cc = raw.get("control_channel_id")
            control_channel_id = int(cc) if cc is not None and str(cc).strip() else None
            created_at = str(raw.get("created_at") or "").strip() or _now_iso()
            if code:
                state = PairingState(
                    pairing_code=code,
                    paired_user_ids=ids,
                    control_channel_id=control_channel_id,
                    created_at=created_at,
                )
        except Exception:
            state = None

    if state is None:
        state = PairingState(
            pairing_code=(fixed_code or "").strip() or generate_pairing_code(),
            paired_user_ids=set(),
            control_channel_id=None,
            created_at=_now_iso(),
        )
        save(path=path, state=state)
        return state

    if fixed_code and fixed_code.strip() and fixed_code.strip() != state.pairing_code:
        state.pairing_code = fixed_code.strip()
        save(path=path, state=state)

    return state


def save(*, path: Path, state: PairingState) -> None:
    """Save pairing state to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n", "utf-8")
