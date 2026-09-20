"""Secret redaction — the belt that every output channel wears.

Two mechanisms, deliberately different in kind:

1. **Resolved values** — every value the broker hands out is registered here and
   masked by exact match. This is the strong one: it does not depend on guessing
   what a secret looks like, so a 64-character hex credential is covered even
   though no pattern would recognize it.
2. **Narrow shapes** — a fixed list of well-known credential prefixes
   (``sk-``, ``apikey_``, ``ghp_``, ``xoxb-``, ``AKIA``, ``Bearer …``). This
   catches a key that never passed through the broker at all — pasted into a
   prompt, a tool result, or a memory write.

Deliberate constraints, each of which explains why the code looks like this:

- **The mask is ``[REDACTED]``** — no quote, backslash or newline — so redacting
  an already-serialised JSON document cannot break its syntax.
- **Values shorter than ``_MIN_VALUE_LEN`` are not registered.** Masking every
  occurrence of a 3-character value would corrupt ordinary prose. A resolved
  value below the floor is still returned by the broker; it is simply not a
  redaction target. Real credentials are far longer than the floor.
- **A fixed shape list, not an entropy heuristic.** Masking anything
  high-entropy would rewrite legitimate memory and RAG content — hashes,
  content ids, base64 blobs — and silently corrupt the user's own data. The
  cost of that choice is stated in the module's *Known gap* below.
- **Nothing here raises.** A redactor that throws on an odd input would take a
  memory write or a log line down with it. Every function is total and falls
  back to its input.

**Known gaps (FACT, not aspiration):**

1. A long high-entropy secret that never passed through the broker *and* does
   not match a known shape is not masked — for example a hand-pasted 64-hex
   string. Closing that needs either the entropy heuristic (rejected: it
   corrupts real content) or resolving the value through the broker first (the
   intended path).
2. When redacting an already-serialised document (a log line, a JSON body), the
   match runs against the *escaped* text, so a secret containing a character
   the serialiser escapes (a quote, a newline) will not match its raw form.
   Credentials are hex/base64-shaped in practice; this is stated rather than
   papered over.
"""

from __future__ import annotations

import re
import threading
from typing import Any, overload

MASK = "[REDACTED]"

# Below this length a "secret" is more likely to be ordinary prose than a
# credential; masking it would corrupt content rather than protect it.
_MIN_VALUE_LEN = 8

_MAX_DEPTH = 12

_SHAPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # OpenAI-style API key.
    re.compile(r"(?<![\w-])sk-[A-Za-z0-9_-]{16,}"),
    # TypeSafe AI (Jev) — the format observed in JOB-024: apikey_<hex>_<hex>.
    re.compile(r"(?<![\w-])apikey_[A-Za-z0-9_-]{16,}"),
    # GitHub personal access token, classic and fine-grained.
    re.compile(r"(?<![\w-])ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![\w-])github_pat_[A-Za-z0-9_]{20,}"),
    # Slack bot/user/app/refresh tokens.
    re.compile(r"(?<![\w-])xox[baprs]-[A-Za-z0-9-]{10,}"),
    # AWS access key id (the secret half is not prefix-shaped; resolved values
    # and the broker cover it).
    re.compile(r"(?<![\w-])AKIA[0-9A-Z]{16}"),
    # Authorization header. The lookbehind keeps the scheme word and masks only
    # the token, so `Bearer [REDACTED]` stays readable.
    re.compile(r"(?i)(?<=\bBearer )[A-Za-z0-9._-]{16,}"),
)

_lock = threading.Lock()
_values: set[str] = set()


def register_secret_value(value: object) -> bool:
    """Register a resolved secret value as a redaction target.

    Returns True when the value is now masked on every channel, False when it
    was ignored (not a string, empty, or shorter than ``_MIN_VALUE_LEN``).
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if len(candidate) < _MIN_VALUE_LEN:
        return False
    with _lock:
        _values.add(candidate)
    return True


def registered_values() -> frozenset[str]:
    """The current redaction targets — exposed for tests and diagnostics.

    Values are returned as-is because this is the one function whose whole
    purpose is to enumerate them; nothing that logs should call it.
    """
    with _lock:
        return frozenset(_values)


def clear_registry() -> None:
    """Drop every registered value. For tests and for process teardown."""
    with _lock:
        _values.clear()


def _sorted_values() -> tuple[str, ...]:
    # Longest first: if one registered value contains another, masking the
    # longer one first avoids leaving a recognisable fragment behind.
    with _lock:
        return tuple(sorted(_values, key=len, reverse=True))


def contains_secret(text: object) -> bool:
    """True when ``text`` carries a registered value or a known credential shape.

    This is the cheap gate used by the response middleware: when it returns
    False, the caller can pass a body through untouched instead of rebuilding
    it.
    """
    if not isinstance(text, str) or not text:
        return False
    try:
        values = _sorted_values()
        if any(value in text for value in values):
            return True
        return any(pattern.search(text) is not None for pattern in _SHAPE_PATTERNS)
    except Exception:  # noqa: BLE001 — a diagnostic must never raise
        return False


@overload
def redact(text: str) -> str: ...


@overload
def redact(text: object) -> object: ...


def redact(text: object) -> object:
    """Return ``text`` with every known secret masked.

    Non-strings and empty strings are returned unchanged, and any internal
    failure returns the input rather than raising: this function sits on the
    log, memory, audit and model paths.
    """
    if not isinstance(text, str) or not text:
        return text
    try:
        out = text
        for value in _sorted_values():
            if value in out:
                out = out.replace(value, MASK)
        for pattern in _SHAPE_PATTERNS:
            out = pattern.sub(MASK, out)
        return out
    except Exception:  # noqa: BLE001
        return text


def redact_obj(obj: Any, _depth: int = 0) -> Any:
    """Redact every string inside a nested structure (dict/list/tuple).

    Structure and types are preserved, so a redacted payload is still the same
    shape to its consumer. Depth is bounded so a self-referential structure
    cannot spin.
    """
    if _depth > _MAX_DEPTH:
        return obj
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, _depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(v, _depth + 1) for v in obj)
    return obj
