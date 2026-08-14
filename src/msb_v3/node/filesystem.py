"""Scoped filesystem capability providers."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


class CapabilityViolation(ValueError):
    pass


class FileReader:
    def __init__(self, root: str | Path, max_bytes: int = 1_048_576) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, requested: str) -> Path:
        if not requested or "\x00" in requested:
            raise CapabilityViolation("invalid file path")
        candidate = Path(requested)
        candidate = candidate if candidate.is_absolute() else self.root / candidate
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.root)
        except FileNotFoundError as exc:
            raise CapabilityViolation("file does not exist") from exc
        except ValueError as exc:
            raise CapabilityViolation("file path outside capability scope") from exc
        if not resolved.is_file():
            raise CapabilityViolation("target is not a regular file")
        return resolved

    def read(self, requested: str) -> Dict[str, object]:
        path = self._safe_path(requested)
        size = path.stat().st_size
        if size > self.max_bytes:
            raise CapabilityViolation(f"file exceeds read limit ({self.max_bytes} bytes)")
        data = path.read_bytes()
        try:
            content: object = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            import base64

            content = base64.b64encode(data).decode("ascii")
            encoding = "base64"
        return {
            "path": str(path.relative_to(self.root)),
            "size": size,
            "sha256": hashlib.sha256(data).hexdigest(),
            "encoding": encoding,
            "content": content,
        }


@dataclass(frozen=True)
class FileWriteReceipt:
    path: str
    existed: bool
    before_sha256: Optional[str]
    after_sha256: str
    size: int
    _before: Optional[bytes]

    def public(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "existed": self.existed,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "size": self.size,
        }


class FileWriter:
    """Write only inside a resolved root, atomically, with reversible receipts."""

    def __init__(self, root: str | Path, max_bytes: int = 1_048_576) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_target(self, requested: str) -> Path:
        if not requested or "\x00" in requested:
            raise CapabilityViolation("invalid file path")
        candidate = Path(requested)
        candidate = candidate if candidate.is_absolute() else self.root / candidate
        try:
            parent = candidate.parent.resolve(strict=True)
            parent.relative_to(self.root)
        except (FileNotFoundError, ValueError) as exc:
            raise CapabilityViolation("file path outside capability scope or missing parent") from exc
        if candidate.exists() or candidate.is_symlink():
            if candidate.is_symlink():
                raise CapabilityViolation("symlink targets are not writable")
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self.root)
            except (FileNotFoundError, ValueError) as exc:
                raise CapabilityViolation("file path outside capability scope") from exc
            if not resolved.is_file():
                raise CapabilityViolation("target is not a regular file")
            return resolved
        return parent / candidate.name

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def write(self, requested: str, content: bytes, expected_sha256: Optional[str] = None) -> FileWriteReceipt:
        if len(content) > self.max_bytes:
            raise CapabilityViolation(f"write exceeds limit ({self.max_bytes} bytes)")
        path = self._safe_target(requested)
        before = path.read_bytes() if path.exists() else None
        before_sha256 = self._sha256(before) if before is not None else None
        if expected_sha256 is not None and before_sha256 != expected_sha256:
            raise CapabilityViolation("precondition hash does not match target")
        after_sha256 = self._sha256(content)
        temporary_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as handle:
                temporary_name = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
        observed = path.read_bytes()
        if self._sha256(observed) != after_sha256:
            raise CapabilityViolation("postcondition hash does not match written content")
        return FileWriteReceipt(
            path=str(path.relative_to(self.root)),
            existed=before is not None,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            size=len(content),
            _before=before,
        )

    def rollback(self, receipt: FileWriteReceipt) -> Dict[str, object]:
        path = self._safe_target(receipt.path)
        if receipt.existed:
            if receipt._before is None:
                raise CapabilityViolation("rollback snapshot is missing")
            self.write(receipt.path, receipt._before)
            restored = path.read_bytes()
            ok = self._sha256(restored) == receipt.before_sha256
        else:
            if path.exists():
                path.unlink()
            ok = not path.exists()
        return {"ok": ok, "path": receipt.path, "restored_sha256": receipt.before_sha256}
