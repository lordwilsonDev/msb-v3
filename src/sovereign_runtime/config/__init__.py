"""Sovereign Runtime — Configuration.

Loads runtime configuration from sovereign_runtime/config/runtime.yaml
plus environment overrides.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "runtime.yaml"
_config_cache: Dict[str, Any] = {}


def _defaults() -> Dict[str, Any]:
    return {
        "brain": {"framework": "motia", "enabled": True},
        "memory": {"redis": False, "sqlite": True, "vector": False},
        "models": {
            "local": {"ollama": True},
            "cloud": {"deepseek": False},
        },
        "safety": {"fail_closed": True},
        "executor": {"sandbox": True},
    }


def load_config() -> Dict[str, Any]:
    cfg = _defaults()
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _deep_merge(cfg, data)
    _deep_merge(cfg, _env_overrides())
    return cfg


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


_BOOL_STRINGS = {"true": True, "false": False}


def _coerce_env_value(value: str) -> Any:
    if value.lower() in _BOOL_STRINGS:
        return _BOOL_STRINGS[value.lower()]
    return value


def _env_overrides() -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name, value in os.environ.items():
        if not name.startswith("SOVEREIGN_"):
            continue
        raw = name[len("SOVEREIGN_"):].lower()
        if "_" not in raw:
            result[raw] = _coerce_env_value(value)
            continue
        first_underscore = raw.index("_")
        section = raw[:first_underscore]
        remainder = raw[first_underscore + 1 :]
        target = result.setdefault(section, {})
        if "." in remainder:
            sub_keys = remainder.split(".")
            for sub in sub_keys[:-1]:
                target = target.setdefault(sub, {})
            target[sub_keys[-1]] = _coerce_env_value(value)
        else:
            target[remainder] = _coerce_env_value(value)
    return result


def get(path: str, default: Any = None) -> Any:
    keys = path.split(".")
    node: Any = load_config()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
