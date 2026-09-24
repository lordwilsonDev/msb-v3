"""Private, fail-closed configuration for the Aeon Orb voice client."""

from __future__ import annotations

import errno
import ipaddress
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

DEFAULT_CONFIG_PATH = Path("~/.msb/aeon.json").expanduser()
DEFAULT_PORT = 8443
DEFAULT_TARGET_OS = "macos"
_MAX_CONFIG_BYTES = 64 * 1024
_FINGERPRINT_RE = re.compile(r"^[0-9A-F]{64}$")
_CONFIG_KEYS = {
    "host",
    "port",
    "token",
    "cert_sha256",
    "target_os",
    "screen_to_cloud",
    "rate_caps",
    "danger_words_extra",
}
_RATE_KEYS = {"per_turn", "per_minute"}


class AeonConfigError(ValueError):
    """The Orb configuration is missing, malformed, or unsafe."""


@dataclass(frozen=True, slots=True)
class RateCaps:
    per_turn: int = 10
    per_minute: int = 30

    def __post_init__(self) -> None:
        _validate_positive_int(self.per_turn, "rate_caps.per_turn", maximum=100)
        _validate_positive_int(self.per_minute, "rate_caps.per_minute", maximum=600)
        if self.per_minute < self.per_turn:
            raise AeonConfigError("rate_caps.per_minute must be at least per_turn")


@dataclass(frozen=True, slots=True)
class AeonConfig:
    host: str
    token: str
    cert_sha256: str
    port: int = DEFAULT_PORT
    target_os: str = DEFAULT_TARGET_OS
    screen_to_cloud: bool = False
    rate_caps: RateCaps = field(default_factory=RateCaps)
    danger_words_extra: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", _validate_host(self.host))
        _validate_token(self.token)
        object.__setattr__(self, "cert_sha256", _validate_fingerprint(self.cert_sha256))
        _validate_positive_int(self.port, "port", maximum=65535)
        if self.target_os != DEFAULT_TARGET_OS:
            raise AeonConfigError("target_os must be 'macos' for the first Orb target")
        if not isinstance(self.screen_to_cloud, bool):
            raise AeonConfigError("screen_to_cloud must be a boolean")
        if not isinstance(self.rate_caps, RateCaps):
            raise AeonConfigError("rate_caps must be a RateCaps value")
        object.__setattr__(self, "danger_words_extra", _validate_danger_words(self.danger_words_extra))

    @property
    def authority(self) -> str:
        """Return an HTTP Host authority safe for IPv4, IPv6, or a DNS name."""
        return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"

    @classmethod
    def load(
        cls,
        path: Path | str = DEFAULT_CONFIG_PATH,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "AeonConfig":
        """Load a private config file, then apply explicit environment overrides."""
        raw = _read_private_json(Path(path).expanduser())
        env = os.environ if environ is None else environ
        values = dict(raw)
        for field_name, env_name in (
            ("host", "AEON_HOST"),
            ("port", "AEON_PORT"),
            ("token", "AEON_TOKEN"),
            ("cert_sha256", "AEON_CERT_SHA256"),
            ("target_os", "AEON_TARGET_OS"),
        ):
            if env_name in env:
                values[field_name] = env[env_name]
        if "AEON_SCREEN_TO_CLOUD" in env:
            values["screen_to_cloud"] = _parse_bool(env["AEON_SCREEN_TO_CLOUD"], "AEON_SCREEN_TO_CLOUD")

        missing = [name for name in ("host", "token", "cert_sha256") if not values.get(name)]
        if missing:
            raise AeonConfigError(f"missing required config field(s): {', '.join(missing)}")

        rate_raw = values.get("rate_caps", {})
        if not isinstance(rate_raw, dict):
            raise AeonConfigError("rate_caps must be an object")
        unknown_rates = set(rate_raw) - _RATE_KEYS
        if unknown_rates:
            raise AeonConfigError(f"unknown rate_caps field(s): {', '.join(sorted(unknown_rates))}")

        danger_raw = values.get("danger_words_extra", [])
        if not isinstance(danger_raw, list):
            raise AeonConfigError("danger_words_extra must be a list")

        try:
            return cls(
                host=_require_str(values["host"], "host"),
                port=_parse_port(values["port"]),
                token=_require_str(values["token"], "token"),
                cert_sha256=_require_str(values["cert_sha256"], "cert_sha256"),
                target_os=_require_str(values.get("target_os", DEFAULT_TARGET_OS), "target_os"),
                screen_to_cloud=_require_bool(values.get("screen_to_cloud", False), "screen_to_cloud"),
                rate_caps=RateCaps(
                    per_turn=_parse_int(rate_raw.get("per_turn", 10), "rate_caps.per_turn"),
                    per_minute=_parse_int(rate_raw.get("per_minute", 30), "rate_caps.per_minute"),
                ),
                danger_words_extra=tuple(danger_raw),
            )
        except AeonConfigError:
            raise
        except (TypeError, ValueError) as exc:
            raise AeonConfigError(f"invalid Orb config: {type(exc).__name__}") from None


def _read_private_json(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise AeonConfigError("Orb config must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise AeonConfigError(f"Orb config not found: {path}") from None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            raise AeonConfigError("Orb config path is unsafe") from None
        raise AeonConfigError(f"cannot open Orb config: {type(exc).__name__}") from None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise AeonConfigError("Orb config must be a regular file")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise AeonConfigError("Orb config must not be readable by group or others")
        if metadata.st_size > _MAX_CONFIG_BYTES:
            raise AeonConfigError("Orb config is too large")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            payload = handle.read(_MAX_CONFIG_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(payload) > _MAX_CONFIG_BYTES:
        raise AeonConfigError("Orb config is too large")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AeonConfigError("Orb config is not valid JSON") from None
    if not isinstance(raw, dict):
        raise AeonConfigError("Orb config root must be an object")
    unknown = set(raw) - _CONFIG_KEYS
    if unknown:
        raise AeonConfigError(f"unknown config field(s): {', '.join(sorted(unknown))}")
    return raw


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise AeonConfigError(f"{name} must be a string")
    return value


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise AeonConfigError(f"{name} must be true or false")
    return value


def _parse_bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    raise AeonConfigError(f"{name} must be true or false")


def _parse_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise AeonConfigError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise AeonConfigError(f"{name} must be an integer")


def _parse_port(value: object) -> int:
    port = _parse_int(value, "port")
    _validate_positive_int(port, "port", maximum=65535)
    return port


def _validate_positive_int(value: int, name: str, *, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise AeonConfigError(f"{name} must be an integer from 1 to {maximum}")


def _validate_host(value: str) -> str:
    if not isinstance(value, str):
        raise AeonConfigError("host must be a non-empty hostname or IP address")
    host = value.strip()
    if not host or host != value or any(ch.isspace() for ch in host):
        raise AeonConfigError("host must be a non-empty hostname or IP address")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if any(ch in host for ch in "/\\?#@[]%"):
        raise AeonConfigError("host must not contain a URL, path, credential, or scope marker")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise AeonConfigError("host is not a valid hostname") from None
    if len(ascii_host) > 253 or any(not label or len(label) > 63 for label in ascii_host.split(".")):
        raise AeonConfigError("host is not a valid hostname")
    if any(not all(ch.isalnum() or ch == "-" for ch in label) for label in ascii_host.split(".")):
        raise AeonConfigError("host is not a valid hostname")
    if any(label.startswith("-") or label.endswith("-") for label in ascii_host.split(".")):
        raise AeonConfigError("host is not a valid hostname")
    return ascii_host


def _validate_token(token: str) -> None:
    if not isinstance(token, str):
        raise AeonConfigError("token must be a non-empty aeon_tok_ credential")
    if not token or token != token.strip() or len(token) > 4096:
        raise AeonConfigError("token is empty, padded, or too long")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in token):
        raise AeonConfigError("token contains control characters")
    if not token.startswith("aeon_tok_") or len(token) <= len("aeon_tok_"):
        raise AeonConfigError("token must be a non-empty aeon_tok_ credential")


def _validate_fingerprint(value: str) -> str:
    if not isinstance(value, str):
        raise AeonConfigError("cert_sha256 must contain exactly 32 SHA-256 bytes")
    normalized = value.replace(":", "").replace(" ", "").upper()
    if not _FINGERPRINT_RE.fullmatch(normalized):
        raise AeonConfigError("cert_sha256 must contain exactly 32 SHA-256 bytes")
    return normalized


def _validate_danger_words(words: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(words, (tuple, list)):
        raise AeonConfigError("danger_words_extra must be a list of strings")
    if len(words) > 128:
        raise AeonConfigError("danger_words_extra may contain at most 128 entries")
    normalized: list[str] = []
    for word in words:
        if not isinstance(word, str):
            raise AeonConfigError("danger_words_extra entries must be strings")
        cleaned = " ".join(word.casefold().split())
        if not cleaned or len(cleaned) > 64 or any(ord(ch) < 0x20 for ch in cleaned):
            raise AeonConfigError("danger words must contain 1 to 64 printable characters")
        if cleaned not in normalized:
            normalized.append(cleaned)
    return tuple(normalized)
