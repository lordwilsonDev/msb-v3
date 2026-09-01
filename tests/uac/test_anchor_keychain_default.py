from __future__ import annotations

import msb_ledger.chain_anchor as ca


def test_from_env_falls_back_to_default_keychain_service(monkeypatch, tmp_path):
    """A process with no MSB_CHAIN_ANCHOR_* env vars still resolves a seed
    stored under the canonical service name (the gateway/cron case)."""
    monkeypatch.delenv("MSB_CHAIN_ANCHOR_KEY", raising=False)
    monkeypatch.delenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", raising=False)
    monkeypatch.setattr(ca, "_default_key_path", lambda: tmp_path / "absent")

    seen: list[str | None] = []
    fake_seed = ca.generate_seed().hex()

    def fake_keychain(service=None):
        seen.append(service)
        return fake_seed if service == ca.DEFAULT_KEYCHAIN_SERVICE else None

    monkeypatch.setattr(ca, "_seed_from_keychain", fake_keychain)
    anchor = ca.ChainAnchor.from_env()
    assert anchor.public_key_hex()  # constructed fine
    assert ca.DEFAULT_KEYCHAIN_SERVICE in seen  # the last-resort lookup ran


def test_from_env_still_raises_when_nothing_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("MSB_CHAIN_ANCHOR_KEY", raising=False)
    monkeypatch.delenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", raising=False)
    monkeypatch.setattr(ca, "_default_key_path", lambda: tmp_path / "absent")
    monkeypatch.setattr(ca, "_seed_from_keychain", lambda service=None: None)
    try:
        ca.ChainAnchor.from_env()
    except ValueError as exc:
        assert "no chain anchor key configured" in str(exc)
    else:
        raise AssertionError("expected ValueError")
