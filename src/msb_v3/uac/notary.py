"""Off-box append-only notary (security-hardening #2).

The notary's job is to keep signed chain-tip snapshots somewhere the audit-DB
attacker cannot rewrite together with the DB (T7 whole-DB replacement). The
original primitive — one local JSONL pushed with ``rclone copyto`` to a single
remote file — had three holes:

  1. verification read the LOCAL last line, so a same-box rollback of the log
     passed verification;
  2. ``copyto`` to ONE remote file means a local rollback + re-push silently
     SHRINKS the remote log (overwritable, not append-only);
  3. no enforcement that the remote actually has what the local claims.

This module closes those holes with an append-only-per-object remote pattern:

  * Every notarization appends one line to the local JSONL (unchanged format,
    so the existing ``--verify-notary`` path still works) AND pushes that line
    as its OWN immutable object (``{seq:06d}-{ts}.line``) to the remote
    directory. Deleting an object is detectable (a missing seq); overwriting
    one is detectable (the signature no longer verifies). Rollback of the
    local log is detectable (the remote knows entries the local no longer
    has). That is as close to WORM as a mutable remote gets — for true
    immutability point ``MSB_NOTARY_REMOTE`` at object-lock / WORM storage.
  * ``verify()`` reads the REMOTE head (never the local last line) and cross-
    checks seq sets both ways: remote ⊂ local ⇒ push failed or remote was
    rolled back (REMOTE_BEHIND); remote ⊃ local ⇒ the local log was rolled
    back (DIVERGED, the T7 notary case); remote unreachable ⇒ fail-closed
    REMOTE_UNREACHABLE (absence of off-box proof is never treated as health).
  * Each entry can carry an RFC 3161 ``timestamp`` proof (see
    ``uac/timestamping.py``) so WHEN is attested by a third party, not by the
    box that may be owned.

CLI (used by scripts/notarize_chain_anchor.sh and verify_chain_anchor.sh):

    python -m msb_v3.uac.notary --notarize <audit.db>   # append local + push remote + stamp
    python -m msb_v3.uac.notary --verify <audit.db>     # local + remote integrity, reads REMOTE head

Env:
    MSB_NOTARY_LOG        local append-only JSONL (default ~/msb-backups/chain-anchor-notary.jsonl)
    MSB_NOTARY_REMOTE     rclone remote DIRECTORY prefix (default gdrive:msb-v3/chain-anchor-notary);
                          empty/"none" disables the remote push
    MSB_NOTARY_RCLONE     rclone binary (default rclone)
    MSB_TSA_URL           RFC 3161 TSA endpoint (empty = receive-time only)
    MSB_TSA_ALLOW_LOCAL_FALLBACK=1   degrade to receive-time when the TSA fails
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.chain_anchor import ChainAnchor
from msb_v3.uac.timestamping import Timestamper, timestamper_from_env

ENTRY_NAME_RE = re.compile(r"^(\d{6})-([0-9T:.+Z-]+)\.line$")

DEFAULT_NOTARY_LOG = "~/msb-backups/chain-anchor-notary.jsonl"
DEFAULT_NOTARY_REMOTE = "gdrive:msb-v3/chain-anchor-notary"


class NotaryError(RuntimeError):
    """Base class for notary failures."""


class NotaryRemoteError(NotaryError):
    """The remote sink could not be reached or refused a write."""


class NotaryDiverged(NotaryError):
    """The remote notary knows entries the local log lacks (local rollback).
    Refusing to write into a divergent state: reconcile before notarizing."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── sinks ───────────────────────────────────────────────────────────────────


class NotarySink(ABC):
    """Where notary entry objects live. ``append`` must be all-or-nothing:
    a partial write is a failure."""

    @abstractmethod
    def append(self, name: str, data: bytes) -> None: ...

    @abstractmethod
    def read(self, name: str) -> Optional[bytes]: ...

    @abstractmethod
    def list(self) -> list[str]: ...

    def stat(self, name: str) -> Optional[str]:
        """Best-effort server-side modtime of one object (independent receive
        time when the sink is genuinely off-box). None when unavailable."""
        return None

    def describe(self) -> str:
        return self.__class__.__name__


class LocalDirSink(NotarySink):
    """A plain local directory (same-box). Used for tests/dev — the remote
    push is what makes the notary genuinely off-box."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, name: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        tmp = target.with_name(f".{target.name}.{os.urandom(4).hex()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def read(self, name: str) -> Optional[bytes]:
        path = self.root / name
        if not path.exists():
            return None
        return path.read_bytes()

    def list(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if ENTRY_NAME_RE.match(p.name))

    def stat(self, name: str) -> Optional[str]:
        path = self.root / name
        if not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

    def describe(self) -> str:
        return f"local-dir:{self.root}"


class RcloneSink(NotarySink):
    """Append-only-per-object storage behind an rclone remote (gdrive, s3,
    b2, local network share, ...). Each entry is pushed as its OWN object so a
    remote cannot be silently shrunk by re-pushing: deleting an object leaves
    a hole (detectable seq gap) and overwriting one breaks its signature.
    Point ``remote`` at object-lock / WORM storage for true immutability."""

    def __init__(self, remote: str, *, rclone: str = "rclone", timeout: float = 60.0) -> None:
        self.remote = remote.rstrip("/")
        self.rclone = rclone
        self.timeout = timeout

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        cmd = [self.rclone, *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise NotaryRemoteError(f"rclone failed to run ({' '.join(cmd)}): {exc}") from exc
        return proc

    def append(self, name: str, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(prefix="notary-", suffix=".line") as handle:
            handle.write(data)
            handle.flush()
            proc = self._run("copyto", handle.name, f"{self.remote}/{name}")
            if proc.returncode != 0:
                raise NotaryRemoteError(
                    f"remote push FAILED for {name}: {proc.stderr.strip() or proc.stdout.strip()}"
                )

    def read(self, name: str) -> Optional[bytes]:
        proc = self._run("cat", f"{self.remote}/{name}")
        if proc.returncode != 0:
            return None
        return proc.stdout.encode()

    def list(self) -> list[str]:
        proc = self._run("lsf", self.remote, "--format", "p")
        if proc.returncode != 0:
            return []
        return sorted(
            line.strip() for line in proc.stdout.splitlines() if ENTRY_NAME_RE.match(line.strip())
        )

    def stat(self, name: str) -> Optional[str]:
        """Best-effort server-side modtime of one object (independent receive
        time when the remote is genuinely off-box). None when unavailable."""
        proc = self._run("lsl", f"{self.remote}/{name}")
        if proc.returncode != 0:
            return None
        parts = proc.stdout.split()
        if len(parts) >= 4:
            return " ".join(parts[1:4])  # date time.tz offset
        return None

    def describe(self) -> str:
        return f"rclone:{self.remote}"


# ── service ─────────────────────────────────────────────────────────────────


class NotaryService:
    """Composes the local JSONL + optional remote sink + optional timestamper
    into one notarize/verify flow."""

    def __init__(
        self,
        chain: AuditChain,
        anchor: ChainAnchor,
        local_log: str | Path,
        remote: Optional[NotarySink] = None,
        timestamper: Optional[Timestamper] = None,
    ) -> None:
        self.chain = chain
        self.anchor = anchor
        self.local_log = Path(local_log)
        self.remote = remote
        self.timestamper = timestamper

    # -- local log ------------------------------------------------------------
    def _read_local_entries(self) -> list[dict]:
        if not self.local_log.exists():
            return []
        lines = [line for line in self.local_log.read_text().splitlines() if line.strip()]
        entries: list[dict] = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise NotaryError(f"local notary log corrupted at line {len(entries) + 1}: {exc}") from exc
        return entries

    def _append_local(self, line: str) -> None:
        self.local_log.parent.mkdir(parents=True, exist_ok=True)
        with self.local_log.open("a") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # -- notarize ---------------------------------------------------------------
    def notarize(self) -> dict:
        """Append a signed snapshot locally, stamp it, push it off-box, and
        backfill any older local entries the remote is missing (a previously
        failed push converges on the next run). Raises NotaryDiverged if the
        local log was rolled back behind the remote."""
        try:
            entries = self._read_local_entries()
        except NotaryError as exc:
            raise NotaryError(f"cannot notarize into a corrupt local log: {exc}") from exc
        seq = len(entries) + 1
        if self.remote is not None:
            remote_seqs = self._remote_seqs()
            if any(s > len(entries) for s in remote_seqs):
                raise NotaryDiverged(
                    f"remote notary knows entries (max seq {max(remote_seqs)}) the local log "
                    f"({len(entries)} entries) lacks — local rollback detected; reconcile before notarizing"
                )
        entry = self.anchor.build_notary_entry(self.chain)
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        if self.timestamper is not None:
            entry["timestamp"] = self.timestamper.stamp(canonical).to_dict()
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        self._append_local(line)  # local append MUST succeed (fail-closed)

        result: dict = {
            "ok": True,
            "seq": seq,
            "local_entries": seq,
            "tip_hash": entry["anchor"]["snapshot"]["tip_hash"],
            "notarized_at": entry.get("notarized_at"),
            "remote_configured": self.remote is not None,
            "remote_push_ok": None,
            "timestamp": entry.get("timestamp"),
        }
        if self.remote is not None:
            name = f"{seq:06d}-{entry['notarized_at'].replace(':', '').replace('+00:00', 'Z')}.line"
            try:
                self.remote.append(name, (line + "\n").encode())
                result["remote_push_ok"] = True
                result["remote_entry"] = name
            except NotaryRemoteError as exc:
                result["remote_push_ok"] = False
                result["remote_error"] = str(exc)
                result["ok"] = False  # loud: off-box guarantee not met

            # Backfill: converge the remote to the full local history. A
            # remote push that failed on an earlier run leaves older seqs
            # missing (verify => REMOTE_BEHIND); the next notarize re-pushes
            # them so the remote self-heals without operator action. Never
            # overwrites: only seqs the remote lacks are pushed, and the
            # rollback guard above already ruled out remote-ahead-of-local.
            backfilled: list[int] = []
            backfill_errors: list[str] = []
            for s in sorted(set(range(1, seq)) - remote_seqs):
                old = entries[s - 1]
                old_line = json.dumps(old, sort_keys=True, separators=(",", ":"))
                old_name = f"{s:06d}-{old['notarized_at'].replace(':', '').replace('+00:00', 'Z')}.line"
                try:
                    self.remote.append(old_name, (old_line + "\n").encode())
                    backfilled.append(s)
                except NotaryRemoteError as exc:
                    backfill_errors.append(f"{s}: {exc}")
            if backfilled:
                result["backfilled"] = backfilled
            if backfill_errors:
                result["backfill_errors"] = backfill_errors
                result["ok"] = False  # remote still behind => verify would alert
        return result

    # -- verify ------------------------------------------------------------------
    def verify(self) -> dict:
        """Local + remote integrity. Never trusts the local last line alone."""
        try:
            local = self._read_local_entries()
        except NotaryError as exc:
            return {"verdict": "LOCAL_INVALID", "detail": f"local notary log corrupt: {exc}", "local_entries": None}
        if not local:
            return {
                "verdict": "LOCAL_INVALID",
                "detail": "notary log is empty — notarization has never run",
                "local_entries": 0,
            }
        # Every local entry must parse (done above); the last must be a valid
        # signed anchor covering a tip in the live chain.
        head = self.anchor.verify_notary_entry(local[-1], self.chain)
        if not head.get("valid"):
            return {
                "verdict": "LOCAL_INVALID",
                "detail": f"last local notary entry invalid: {head.get('reason')}",
                "local_entries": len(local),
                "head": head,
            }
        local_seqs = set(range(1, len(local) + 1))
        result: dict = {
            "verdict": None,
            "local_entries": len(local),
            "head": head,
            "timestamp": local[-1].get("timestamp"),
        }

        if self.remote is None:
            result["verdict"] = "LOCAL_ONLY"
            result["detail"] = "no remote sink configured — notary is same-box only (not off-box)"
            return result

        try:
            names = self.remote.list()
        except NotaryRemoteError as exc:
            result["verdict"] = "REMOTE_UNREACHABLE"
            result["detail"] = f"remote notary unreachable: {exc}"
            return result

        remote_seqs: dict[int, str] = {}
        for name in names:
            match = ENTRY_NAME_RE.match(name)
            if match:
                remote_seqs[int(match.group(1))] = name
        if not remote_seqs:
            result["verdict"] = "REMOTE_UNREACHABLE"
            result["detail"] = "remote notary is empty — off-box protection absent"
            return result

        # Every remote entry must be a valid signed anchor with its tip in the chain.
        invalid = []
        for seq, name in sorted(remote_seqs.items()):
            data = self.remote.read(name)
            if data is None:
                invalid.append(f"{name}: unreadable")
                continue
            try:
                entry = json.loads(data.decode())
            except json.JSONDecodeError:
                invalid.append(f"{name}: not JSON")
                continue
            check = self.anchor.verify_notary_entry(entry, self.chain)
            if not check.get("valid"):
                invalid.append(f"{name}: {check.get('reason')}")
        if invalid:
            result["verdict"] = "REMOTE_INVALID"
            result["detail"] = "remote notary entries failed verification: " + "; ".join(invalid[:5])
            return result

        head_seq = max(remote_seqs)
        head_name = remote_seqs[head_seq]
        remote_set = set(remote_seqs)
        missing = sorted(local_seqs - remote_set)
        extra = sorted(remote_set - local_seqs)
        result["remote_entries"] = len(remote_seqs)
        result["remote_head"] = head_name
        result["remote_head_seq"] = head_seq
        result["remote_received_at"] = self.remote.stat(head_name)
        if extra:
            result["verdict"] = "DIVERGED"
            result["detail"] = (
                f"remote notary has entries the local log lacks (seqs {extra}) — "
                "local rollback detected; the remote is the authoritative history (T7)"
            )
        elif missing:
            result["verdict"] = "REMOTE_BEHIND"
            result["detail"] = (
                f"remote notary is missing local entries (seqs {missing}) — "
                "remote push failed or the remote was rolled back"
            )
        else:
            result["verdict"] = "HEALTHY"
            result["detail"] = "local and remote notary histories agree; remote head verified"
        return result

    def _remote_seqs(self) -> set[int]:
        if self.remote is None:
            return set()
        try:
            names = self.remote.list()
        except NotaryRemoteError as exc:
            raise NotaryDiverged(f"cannot check remote before notarizing: {exc}") from exc
        seqs = set()
        for name in names:
            match = ENTRY_NAME_RE.match(name)
            if match:
                seqs.add(int(match.group(1)))
        return seqs


# ── env resolution + CLI ────────────────────────────────────────────────────


def notary_service_from_env(db_path: str | Path) -> NotaryService:
    remote_env = os.getenv("MSB_NOTARY_REMOTE", DEFAULT_NOTARY_REMOTE).strip()
    remote: Optional[NotarySink] = None
    if remote_env and remote_env.lower() not in ("none", "off"):
        remote = RcloneSink(
            remote_env,
            rclone=os.getenv("MSB_NOTARY_RCLONE", "rclone").strip() or "rclone",
        )
    return NotaryService(
        chain=AuditChain(str(db_path)),
        anchor=ChainAnchor.from_env(),
        local_log=os.getenv("MSB_NOTARY_LOG", DEFAULT_NOTARY_LOG),
        remote=remote,
        timestamper=timestamper_from_env(),
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="Off-box append-only chain-tip notary (#2)")
    parser.add_argument("--notarize", metavar="AUDIT_DB", help="append a signed + stamped snapshot locally and push it off-box")
    parser.add_argument("--verify", metavar="AUDIT_DB", help="verify local + remote notary integrity (reads the REMOTE head)")
    args = parser.parse_args()

    if args.notarize:
        service = notary_service_from_env(args.notarize)
        try:
            result = service.notarize()
        except NotaryDiverged as exc:
            print(json.dumps({"ok": False, "verdict": "DIVERGED", "detail": str(exc)}, indent=2))
            return 2
        except NotaryError as exc:
            print(json.dumps({"ok": False, "verdict": "LOCAL_INVALID", "detail": str(exc)}, indent=2))
            return 2
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1  # remote push failure is loud (local intact)
    if args.verify:
        service = notary_service_from_env(args.verify)
        result = service.verify()
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["verdict"] in ("HEALTHY", "LOCAL_ONLY") else 2
    parser.error("specify --notarize <audit.db> or --verify <audit.db>")


if __name__ == "__main__":
    raise SystemExit(_main())
