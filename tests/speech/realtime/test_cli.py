"""CLI defaults: `voice` should need no arguments."""

from __future__ import annotations

from pathlib import Path

from msb_v3.speech.realtime.cli import (
    DEFAULT_ENROLLMENTS,
    build_parser,
    needs_enrollment,
)


def test_no_arguments_needed():
    args = build_parser().parse_args([])
    assert args.provider == "gemini"
    assert args.button == "enter"
    assert Path(args.enrollments) == DEFAULT_ENROLLMENTS
    assert args.command == "talk"


def test_enroll_subcommand():
    assert build_parser().parse_args(["enroll"]).command == "enroll"


def test_default_enrollment_lives_in_msb_home():
    assert DEFAULT_ENROLLMENTS == Path("~/.msb/voice-enrollment.json").expanduser()


def test_needs_enrollment_when_missing(tmp_path):
    assert needs_enrollment(tmp_path / "none.json") is True


def test_needs_enrollment_false_when_present(tmp_path):
    f = tmp_path / "e.json"
    f.write_text('{"wilson": [[0.1]]}')
    assert needs_enrollment(f) is False


def test_needs_enrollment_when_empty_json(tmp_path):
    f = tmp_path / "e.json"
    f.write_text("{}")
    assert needs_enrollment(f) is True
