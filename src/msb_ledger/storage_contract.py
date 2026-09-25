"""SQLite durability and fixed-disk storage-state contract for the audit chain."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

# How long an operator clear suspends the mechanical refusal before the probe
# rules again. Bounded on purpose: the override is a window, not an off switch.
_OVERRIDE_TTL_ENV = "MSB_STORAGE_OVERRIDE_TTL_S"
_DEFAULT_OVERRIDE_TTL_S = 300


class StorageState(StrEnum):
    NORMAL = "NORMAL"
    LOW_SPACE = "LOW_SPACE"
    CRITICAL_SPACE = "CRITICAL_SPACE"
    AUDIT_WRITE_BLOCKED = "AUDIT_WRITE_BLOCKED"
    SAFE_MODE = "SAFE_MODE"


class AuditWriteBlocked(RuntimeError):
    """The audit writer is not allowed to create a new authoritative record."""


@dataclass(frozen=True)
class StorageProbe:
    free_bytes: int
    total_bytes: int

    @property
    def free_ratio(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return self.free_bytes / self.total_bytes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a persisted override expiry; ``None`` when absent or malformed.

    A malformed expiry is treated as no override, never as an active one — the
    failure direction has to be the mechanical refusal, not the operator window.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _override_active(expires_at: str | None) -> bool:
    parsed = _parse_iso(expires_at)
    return parsed is not None and parsed > datetime.now(timezone.utc)


def _ttl_from_env() -> int:
    raw = os.getenv(_OVERRIDE_TTL_ENV)
    try:
        ttl = int(raw) if raw is not None else _DEFAULT_OVERRIDE_TTL_S
    except ValueError:
        return _DEFAULT_OVERRIDE_TTL_S
    return ttl if ttl > 0 else _DEFAULT_OVERRIDE_TTL_S


def _state_from_ratio(ratio: float, low: float, critical: float, blocked: float) -> StorageState:
    if ratio < blocked:
        return StorageState.AUDIT_WRITE_BLOCKED
    if ratio < critical:
        return StorageState.CRITICAL_SPACE
    if ratio < low:
        return StorageState.LOW_SPACE
    return StorageState.NORMAL


class StorageContract:
    """Own effective SQLite settings and the fail-closed storage state machine.

    State is persisted next to the database as a small control file.  The file is
    not evidence of audit authenticity; it is a local write-admission control.
    Audit authenticity remains the chain/checkpoint verifier's responsibility.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        low_ratio: float = 0.15,
        critical_ratio: float = 0.10,
        blocked_ratio: float = 0.05,
        probe: Callable[[Path], StorageProbe] | None = None,
        enabled: bool | None = None,
        override_ttl_s: int | None = None,
    ) -> None:
        if not 0 <= blocked_ratio < critical_ratio < low_ratio <= 1:
            raise ValueError("storage ratios must satisfy 0 <= blocked < critical < low <= 1")
        self.db_path = Path(db_path)
        self.low_ratio = low_ratio
        self.critical_ratio = critical_ratio
        self.blocked_ratio = blocked_ratio
        self.override_ttl_s = _ttl_from_env() if override_ttl_s is None else override_ttl_s
        if self.override_ttl_s <= 0:
            raise ValueError("override_ttl_s must be positive")
        self.enabled = (
            os.getenv("MSB_STORAGE_CONTRACT_ENABLED", "0") == "1"
            if enabled is None
            else enabled
        )
        self._probe = probe or self._default_probe
        self.state_path = self.db_path.with_name(f".{self.db_path.name}.storage-state.json")

    @staticmethod
    def _default_probe(path: Path) -> StorageProbe:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        return StorageProbe(free_bytes=usage.free, total_bytes=usage.total)

    def _read_state(self) -> tuple[StorageState, str | None]:
        """Persisted state plus any operator-override expiry, if one is recorded."""
        if not self.state_path.exists():
            return StorageState.NORMAL, None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return StorageState(data["state"]), data.get("override_expires_at")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AuditWriteBlocked(f"storage state file is unreadable: {exc}") from exc

    def _write_state(
        self,
        state: StorageState,
        reason: str,
        *,
        override_expires_at: datetime | None = None,
    ) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.tmp")
        payload: dict[str, Any] = {
            "state": state.value,
            "reason": reason,
            "updated_at": _now_iso(),
            "db_path": str(self.db_path.resolve()),
        }
        if override_expires_at is not None:
            payload["override_expires_at"] = override_expires_at.isoformat()
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _derived_state(self, probe: StorageProbe) -> StorageState:
        """What the free-space probe alone says, ignoring any operator override."""
        return _state_from_ratio(
            probe.free_ratio,
            self.low_ratio,
            self.critical_ratio,
            self.blocked_ratio,
        )

    def state(self, *, refresh: bool = True) -> StorageState:
        if not self.enabled:
            return StorageState.NORMAL
        current, override_expires_at = self._read_state()
        # An explicit latch — a failed write, or operator-selected safe mode —
        # is never relaxed by a later probe reading, and never by an override.
        if current in (StorageState.AUDIT_WRITE_BLOCKED, StorageState.SAFE_MODE):
            return current
        if _override_active(override_expires_at):
            # Bounded operator clear: probe-driven escalation is suspended for
            # the window, and the mechanical refusal returns when it lapses.
            return StorageState.NORMAL
        if refresh:
            probe = self._probe(self.db_path)
            derived = self._derived_state(probe)
            if derived != current:
                current = derived
                self._write_state(current, f"free_ratio={probe.free_ratio:.6f}")
        return current

    def assert_write_allowed(self) -> None:
        if not self.enabled:
            return
        current = self.state()
        if current in (StorageState.AUDIT_WRITE_BLOCKED, StorageState.SAFE_MODE):
            raise AuditWriteBlocked(f"audit writes are blocked in {current.value}")

    def mark_blocked(self, reason: str) -> None:
        self._write_state(StorageState.AUDIT_WRITE_BLOCKED, reason)

    def mark_safe_mode(self, reason: str) -> None:
        self._write_state(StorageState.SAFE_MODE, reason)

    def clear_block(
        self,
        reason: str = "operator cleared storage block",
        *,
        ttl_s: int | None = None,
    ) -> datetime:
        """Suspend the block for a bounded window and return when it lapses.

        This is not an off switch. The override expires; the next admission
        check after expiry re-derives the state from the probe and re-blocks if
        the disk is still full. Expiry is reported by :meth:`health`, so an
        active window is always visible rather than silent.
        """
        ttl = self.override_ttl_s if ttl_s is None else ttl_s
        if ttl <= 0:
            raise ValueError("override ttl must be positive")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        self._write_state(StorageState.NORMAL, reason, override_expires_at=expires_at)
        return expires_at

    def health(self) -> dict[str, Any]:
        """Effective state, what the probe says on its own, and any override.

        ``derived_state`` is reported even while an override is suppressing it,
        so a live override can never hide a full disk from an observer.
        """
        probe = self._probe(self.db_path)
        derived = self._derived_state(probe)
        persisted, override_expires_at = self._read_state()
        override = _override_active(override_expires_at)
        effective = persisted
        if not self.enabled:
            effective = StorageState.NORMAL
        elif persisted in (StorageState.AUDIT_WRITE_BLOCKED, StorageState.SAFE_MODE):
            effective = persisted  # an explicit latch outranks a live override
        elif override:
            effective = StorageState.NORMAL
        return {
            "enabled": self.enabled,
            "state": effective.value,
            "derived_state": derived.value,
            "override_active": override,
            "override_expires_at": override_expires_at,
            "override_ttl_s": self.override_ttl_s,
            "free_bytes": probe.free_bytes,
            "total_bytes": probe.total_bytes,
            "free_ratio": round(probe.free_ratio, 6),
            "low_ratio": self.low_ratio,
            "critical_ratio": self.critical_ratio,
            "blocked_ratio": self.blocked_ratio,
            "write_allowed": effective not in (StorageState.AUDIT_WRITE_BLOCKED, StorageState.SAFE_MODE),
        }

    @staticmethod
    def configure_connection(conn: sqlite3.Connection, *, enable_wal: bool = True) -> None:
        """Apply the explicit audit-store connection contract.

        The values are deliberately centralized.  They are not a substitute for
        measuring the target Apple NVMe device under power loss.  ``enable_wal``
        lets legacy/opt-in callers preserve the historical rollback-journal
        behavior while the production durability contract explicitly enables
        WAL and full synchronous commits.
        """
        if enable_wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        conn.execute("PRAGMA cache_size=-4096")

    @staticmethod
    def effective_settings(conn: sqlite3.Connection) -> dict[str, Any]:
        def pragma(name: str) -> Any:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            return row[0] if row else None

        return {
            "journal_mode": pragma("journal_mode"),
            "synchronous": pragma("synchronous"),
            "foreign_keys": pragma("foreign_keys"),
            "busy_timeout": pragma("busy_timeout"),
            "wal_autocheckpoint": pragma("wal_autocheckpoint"),
            "cache_size": pragma("cache_size"),
        }

    def effective_settings_for_db(self) -> dict[str, Any]:
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            return self.effective_settings(conn)
