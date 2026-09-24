from __future__ import annotations

import json
from pathlib import Path

import pytest

from msb_v3.speech.realtime.aeon_config import AeonConfig, AeonConfigError, RateCaps

TOKEN = "aeon_tok_unit_test_secret"
FINGERPRINT = "AB:" * 31 + "CD"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "host": "orb.local",
        "port": 8443,
        "token": TOKEN,
        "cert_sha256": FINGERPRINT,
        "target_os": "macos",
        "screen_to_cloud": False,
        "rate_caps": {"per_turn": 10, "per_minute": 30},
        "danger_words_extra": [],
    }
    payload.update(overrides)
    return payload


def _write_config(tmp_path: Path, payload: dict[str, object], mode: int = 0o600) -> Path:
    path = tmp_path / "aeon.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(mode)
    return path


def test_loads_private_config_with_safe_defaults(tmp_path):
    path = _write_config(tmp_path, _payload())
    config = AeonConfig.load(path, environ={})

    assert config.host == "orb.local"
    assert config.port == 8443
    assert config.token == TOKEN
    assert config.cert_sha256 == ("AB:" * 31 + "CD").replace(":", "")
    assert config.target_os == "macos"
    assert config.screen_to_cloud is False
    assert config.rate_caps == RateCaps(per_turn=10, per_minute=30)
    assert config.danger_words_extra == ()
    assert config.authority == "orb.local:8443"


def test_environment_overrides_host_port_token_pin_and_privacy(tmp_path):
    path = _write_config(tmp_path, _payload())
    replacement_pin = "CD" * 32
    config = AeonConfig.load(
        path,
        environ={
            "AEON_HOST": "ORB.local",
            "AEON_PORT": "9443",
            "AEON_TOKEN": "aeon_tok_environment_override",
            "AEON_CERT_SHA256": replacement_pin,
            "AEON_SCREEN_TO_CLOUD": "true",
        },
    )

    assert config.host == "orb.local"
    assert config.port == 9443
    assert config.token == "aeon_tok_environment_override"
    assert config.cert_sha256 == replacement_pin
    assert config.screen_to_cloud is True


def test_group_or_other_readable_config_is_refused(tmp_path):
    path = _write_config(tmp_path, _payload(), mode=0o640)
    with pytest.raises(AeonConfigError, match="readable by group or others"):
        AeonConfig.load(path, environ={})


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_symlink_config_is_refused(tmp_path):
    real = _write_config(tmp_path, _payload())
    link = tmp_path / "linked.json"
    link.symlink_to(real)
    with pytest.raises(AeonConfigError, match="must not be a symlink"):
        AeonConfig.load(link, environ={})


def test_missing_config_fails_closed(tmp_path):
    with pytest.raises(AeonConfigError, match="not found"):
        AeonConfig.load(tmp_path / "missing.json", environ={})


def test_unknown_config_and_rate_keys_fail_closed(tmp_path):
    path = _write_config(tmp_path, _payload(extra_setting=True))
    with pytest.raises(AeonConfigError, match="unknown config field"):
        AeonConfig.load(path, environ={})

    path = _write_config(tmp_path, _payload(rate_caps={"per_turn": 2, "hour": 3}))
    with pytest.raises(AeonConfigError, match="unknown rate_caps field"):
        AeonConfig.load(path, environ={})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"cert_sha256": "AB:CD"}, "32 SHA-256 bytes"),
        ({"port": 0}, "port must be an integer"),
        ({"port": 65536}, "port must be an integer"),
        ({"target_os": "windows"}, "target_os must be 'macos'"),
        ({"screen_to_cloud": "false"}, "screen_to_cloud must be true or false"),
        ({"token": "not-an-aeon-token"}, "aeon_tok_ credential"),
        ({"token": "aeon_tok_bad\r\nX-Evil: 1"}, "control characters"),
        ({"rate_caps": {"per_turn": 11, "per_minute": 10}}, "at least per_turn"),
        ({"rate_caps": {"per_turn": 0, "per_minute": 30}}, "per_turn must be an integer"),
        ({"danger_words_extra": "delete"}, "must be a list"),
    ],
)
def test_invalid_fields_fail_closed(tmp_path, overrides, message):
    path = _write_config(tmp_path, _payload(**overrides))
    with pytest.raises(AeonConfigError, match=message):
        AeonConfig.load(path, environ={})


@pytest.mark.parametrize(
    "host",
    ["", "https://orb.local", "orb.local/path", "orb host", "orb@local", "orb_local"],
)
def test_unsafe_hosts_fail_closed(tmp_path, host):
    path = _write_config(tmp_path, _payload(host=host))
    with pytest.raises(AeonConfigError, match="host"):
        AeonConfig.load(path, environ={})


def test_danger_words_are_normalized_and_deduplicated(tmp_path):
    path = _write_config(
        tmp_path,
        _payload(danger_words_extra=["  Delete   Everything ", "delete everything", "Send"]),
    )
    config = AeonConfig.load(path, environ={})
    assert config.danger_words_extra == ("delete everything", "send")


def test_oversized_config_is_refused_before_json_parse(tmp_path):
    path = tmp_path / "aeon.json"
    path.write_text(" " * (64 * 1024 + 1), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(AeonConfigError, match="too large"):
        AeonConfig.load(path, environ={})


def test_direct_construction_uses_typed_fail_closed_errors():
    with pytest.raises(AeonConfigError, match="host"):
        AeonConfig(host=object(), token=TOKEN, cert_sha256=FINGERPRINT)
    with pytest.raises(AeonConfigError, match="token"):
        AeonConfig(host="orb.local", token=object(), cert_sha256=FINGERPRINT)
    with pytest.raises(AeonConfigError, match="SHA-256"):
        AeonConfig(host="orb.local", token=TOKEN, cert_sha256=object())
    with pytest.raises(AeonConfigError, match="list of strings"):
        AeonConfig(host="orb.local", token=TOKEN, cert_sha256=FINGERPRINT,
                   danger_words_extra="delete")


def test_ipv6_authority_is_bracketed(tmp_path):
    path = _write_config(tmp_path, _payload(host="2001:db8::1"))
    assert AeonConfig.load(path, environ={}).authority == "[2001:db8::1]:8443"
