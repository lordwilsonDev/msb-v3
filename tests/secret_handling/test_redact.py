"""Unit tests for the redactor — shapes, registered values, and the invariants
the seven output channels depend on.

The properties worth pinning are the ones a caller would be hurt by if they
silently changed: the mask must be JSON-safe, redaction must be idempotent and
total (it sits on the log, memory, audit and model paths), and a value below the
registration floor must be *left alone* rather than mangled.
"""

from __future__ import annotations

import json

import pytest

from msb_v3.secrets.redact import (
    MASK,
    clear_registry,
    contains_secret,
    redact,
    redact_obj,
    register_secret_value,
    registered_values,
)

# Deliberately does not match any known shape: these tests are about the
# value-based half, which is the half that catches credentials no pattern knows.
SECRET = "TESTONLY_value_9f3a1c7e2b4d"


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def test_a_registered_value_is_masked_everywhere_it_appears():
    assert register_secret_value(SECRET) is True
    out = redact(f"before {SECRET} middle {SECRET} after")
    assert SECRET not in out
    assert out == f"before {MASK} middle {MASK} after"


def test_a_value_below_the_floor_is_not_registered_or_mangled():
    assert register_secret_value("abc") is False
    assert "abc" in redact("abcdef and abc should survive")
    assert registered_values() == frozenset()


def test_a_longer_value_is_masked_before_a_shorter_one_it_contains():
    """Otherwise masking the short one leaves a recognisable tail behind."""
    register_secret_value("TESTONLYshort123")
    register_secret_value("TESTONLYshort12345678")
    assert redact("TESTONLYshort12345678") == MASK


@pytest.mark.parametrize(
    "sample",
    [
        "sk-abcdefghijklmnopqrstuvwx",
        "apikey_0123456789abcdef0123456789abcdef",
        "ghp_0123456789abcdefghijklmnopqrstuvwx",
        "github_pat_0123456789abcdefghijklmnop",
        "xox" + "b-0123456789-abcdefghijklmnop",  # fake Slack-shaped token, split so secret scanners ignore the fixture
        "AKIA" + "IOSFODNN7EXAMPLE",  # AWS docs example key, split so the tree scan does not flag the fixture
    ],
)
def test_known_credential_shapes_are_masked_without_registration(sample):
    """The shape half catches a key that never went through the broker."""
    assert contains_secret(f"token={sample}") is True
    assert sample not in redact(f"token={sample}")


def test_the_bearer_scheme_word_survives_so_the_line_stays_readable():
    out = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345")
    assert out == f"Authorization: Bearer {MASK}"


def test_masking_a_serialised_document_keeps_it_valid_json():
    """The mask contains no quote or backslash, so redacting JSON text cannot
    break its syntax — this is why the formatters redact the rendered line."""
    register_secret_value(SECRET)
    document = json.dumps({"a": [SECRET], "b": {"c": f"x {SECRET}"}})
    redacted = redact(document)

    assert SECRET not in redacted
    assert json.loads(redacted) == {"a": [MASK], "b": {"c": f"x {MASK}"}}


def test_redaction_is_idempotent():
    register_secret_value(SECRET)
    once = redact(f"{SECRET} and sk-abcdefghijklmnopqrstuvwx")
    assert redact(once) == once


def test_non_strings_and_empties_pass_through_untouched():
    register_secret_value(SECRET)
    assert redact("") == ""
    assert redact(None) is None
    assert redact(42) == 42
    assert redact(b"bytes are left alone") == b"bytes are left alone"


def test_redact_never_raises_on_odd_input():
    """It sits on the log, memory, audit and model paths: an exception here
    would take one of those down rather than protect it."""
    register_secret_value(SECRET)
    assert redact(object()) is not None
    assert contains_secret(object()) is False


def test_redact_obj_preserves_structure_and_types():
    register_secret_value(SECRET)
    payload = {
        "list": [SECRET, 1, None],
        "tuple": (SECRET,),
        "nested": {"deep": [{"k": SECRET}]},
    }
    out = redact_obj(payload)

    assert isinstance(out, dict)
    assert isinstance(out["list"], list)
    assert isinstance(out["tuple"], tuple)
    assert isinstance(out["nested"]["deep"], list)
    assert SECRET not in json.dumps(out)
    assert out["list"][1] == 1 and out["list"][2] is None


def test_contains_secret_is_the_cheap_gate_the_middleware_relies_on():
    register_secret_value(SECRET)
    assert contains_secret(f"has {SECRET}") is True
    assert contains_secret("nothing to see") is False
    assert contains_secret("") is False
