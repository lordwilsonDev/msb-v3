"""API key loading for the realtime voice providers.

Order: environment variable, then a file. Key text never appears in a log
line or an exception message — errors name the variable or path only.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping, Optional

#: msb-v3 repo root: src/msb_v3/speech/realtime/keys.py → parents[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DOTENV = _REPO_ROOT / ".env"
DEFAULT_GEMINI_KEY_FILE = Path("~/.secrets/gemini-api-key.txt").expanduser()


class MissingKeyError(RuntimeError):
    """No usable key was found. The message names where we looked."""


class InsecureKeyFileError(MissingKeyError):
    """The key file is readable by group or others, so it is not used."""


def _dotenv_value(path: Path, name: str) -> Optional[str]:
    if not path.is_file():
        return None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if not line.startswith(f"{name}="):
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        return value or None
    return None


def load_openai_key(
    env: Optional[Mapping[str, str]] = None,
    dotenv_path: Optional[Path] = None,
) -> str:
    """Return the OpenAI key from the environment or msb-v3's ``.env``."""
    env = os.environ if env is None else env
    value = (env.get("OPENAI_API_KEY") or "").strip()
    if value:
        return value
    path = dotenv_path or DEFAULT_DOTENV
    value = _dotenv_value(path, "OPENAI_API_KEY") or ""
    if value:
        return value
    raise MissingKeyError(f"OPENAI_API_KEY not set in the environment or {path}")


def _read_key_file(path: Path) -> str:
    if not path.is_file():
        raise MissingKeyError(f"Gemini key file not found: {path}")
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise InsecureKeyFileError(
            f"Gemini key file {path} is accessible by group/others; run: chmod 600 {path}"
        )
    value = path.read_text().strip()
    if not value:
        raise MissingKeyError(f"Gemini key file is empty: {path}")
    return value


def load_gemini_key(
    env: Optional[Mapping[str, str]] = None,
    key_file: Optional[Path] = None,
) -> str:
    """Return the Gemini key from ``GEMINI_API_KEY`` or the key file."""
    env = os.environ if env is None else env
    value = (env.get("GEMINI_API_KEY") or "").strip()
    if value:
        return value
    if key_file is None:
        override = (env.get("GEMINI_API_KEY_FILE") or "").strip()
        key_file = Path(override).expanduser() if override else DEFAULT_GEMINI_KEY_FILE
    return _read_key_file(key_file)
