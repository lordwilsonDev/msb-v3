"""End-of-turn detection: stop after speech then silence, never on leading silence."""

from __future__ import annotations

from msb_v3.speech.realtime.turn import EndOfTurnDetector

SPEECH, QUIET = b"S" * 3200, b"Q" * 3200  # 0.1 s chunks at 16 kHz


def _detector(**kw):
    return EndOfTurnDetector(lambda chunk: chunk[:1] == b"S", **kw)


def test_leading_silence_does_not_end_turn():
    d = _detector(silence_after=0.5, max_seconds=30)
    assert not any(d.update(QUIET) for _ in range(20))


def test_speech_then_silence_ends_turn():
    d = _detector(silence_after=0.5, max_seconds=30)
    for _ in range(10):
        assert d.update(SPEECH) is False
    ended = [d.update(QUIET) for _ in range(5)]
    assert ended == [False, False, False, False, True]


def test_speech_resets_silence_count():
    d = _detector(silence_after=0.5, max_seconds=30)
    d.update(SPEECH)
    for _ in range(4):
        d.update(QUIET)
    assert d.update(SPEECH) is False
    assert [d.update(QUIET) for _ in range(5)][-1] is True


def test_max_seconds_caps_turn():
    d = _detector(silence_after=0.5, max_seconds=1.0)
    ended = [d.update(SPEECH) for _ in range(10)]
    assert ended[-1] is True and not any(ended[:-1])


def test_speech_seconds_tracked():
    d = _detector(silence_after=0.5, max_seconds=30)
    for _ in range(7):
        d.update(SPEECH)
    d.update(QUIET)
    assert round(d.speech_seconds, 2) == 0.7
