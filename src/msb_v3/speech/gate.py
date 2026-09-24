"""Speaker-verified stream gate — who may command, sleep, or wake the agent.

Pure decision logic for always-on voice mode: no microphone, no
transcription, no TTS. The gate takes a transcript plus the audio it came
from and returns a :class:`GateDecision`. :class:`~msb_v3.speech.stream.VoiceStream`
does the capture and the acting; this module decides.

The gate is fail-closed and orders its checks deliberately:

1. **Wake phrase** — without ``hey`` + a wake word, nothing else is even
   considered, so the verifier is never consulted on ambient speech.
2. **Speaker verification** — a valid wake phrase from an unverified speaker
   is ignored silently and logged at INFO.
3. **Sleep / wake** — exact-match phrases, so "sleep through the alarm"
   stays a command.
4. **Command** — anything left over.

Usage::

    from msb_v3.speech.gate import StreamGate
    from msb_v3.speech.speaker import SpeakerVerifier

    gate = StreamGate(SpeakerVerifier())
    decision = gate.decide("hey sovereign, system status", audio)
    if decision.action is GateAction.COMMAND:
        run(decision.command_text)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional, Protocol, Sequence

from msb_v3.speech.models import AudioBuffer, SpeakerIdentity
from msb_v3.speech.wakeword import WakeWordDetector

_log = logging.getLogger(__name__)

#: Punctuation stripped before an exact-match phrase comparison.
_TRAILING_PUNCTUATION = ".,!?;: \t"

#: Words accepted when a caller does not configure its own.
DEFAULT_WAKE_WORDS: tuple[str, ...] = ("sovereign", "msb")

#: Default phrases, compared after normalization (lowercase, no trailing
#: punctuation) so they must be spoken on their own — never as a substring.
DEFAULT_SLEEP_PHRASES: tuple[str, ...] = ("sleep", "go to sleep")
DEFAULT_WAKE_PHRASES: tuple[str, ...] = ("wake up", "wake")


class NotEnrolledError(RuntimeError):
    """Raised when always-on mode starts with nobody enrolled.

    An unenrolled gate cannot verify anyone, so every utterance would be
    ignored. Refusing to start is the honest failure mode — it says so
    instead of appearing to listen.
    """


class GateAction(str, Enum):
    """What the stream should do with one utterance."""

    IGNORE = "IGNORE"
    SLEEP = "SLEEP"
    WAKE = "WAKE"
    COMMAND = "COMMAND"


class SpeakerVerifying(Protocol):
    """The slice of ``SpeakerVerifier`` the gate depends on.

    Structural, so tests (and alternative backends) can stand in without
    importing resemblyzer.
    """

    def verify(self, audio: AudioBuffer) -> SpeakerIdentity:
        """Identify the speaker in ``audio``."""

    def list_speakers(self) -> List[str]:
        """Return the IDs of every enrolled speaker."""


@dataclass
class GateDecision:
    """One gate outcome.

    ``speaker_id`` / ``speaker_confidence`` are populated once verification
    has run. The raw voice embedding is never carried here — it stays in the
    verifier.
    """

    action: GateAction
    command_text: str = ""
    reason: str = ""
    speaker_id: str = "unknown"
    speaker_confidence: float = 0.0


def _normalize(text: str) -> str:
    """Lowercase and strip trailing punctuation for exact-match comparison."""
    return text.strip().lower().rstrip(_TRAILING_PUNCTUATION)


class StreamGate:
    """Decide whether one utterance is a command, sleep, wake, or nothing.

    Stateful only in ``asleep``. The caller owns the audio loop.
    """

    def __init__(
        self,
        verifier: SpeakerVerifying,
        wake_words: Optional[Iterable[str]] = None,
        sleep_phrases: Sequence[str] = DEFAULT_SLEEP_PHRASES,
        wake_phrases: Sequence[str] = DEFAULT_WAKE_PHRASES,
    ) -> None:
        self.verifier = verifier
        self.wake_words = list(
            DEFAULT_WAKE_WORDS if wake_words is None else wake_words
        )
        # Exact match, so normalize the configured phrases the same way the
        # incoming text is normalized.
        self.sleep_phrases = frozenset(_normalize(p) for p in sleep_phrases)
        self.wake_phrases = frozenset(_normalize(p) for p in wake_phrases)
        # require_prefix: a bare "msb" must not activate the agent.
        self.detector = WakeWordDetector(
            wake_words=self.wake_words,
            require_prefix=True,
        )
        self._asleep = False

    @property
    def asleep(self) -> bool:
        """Whether the gate is currently asleep (in-memory only)."""
        return self._asleep

    def decide(self, text: str, audio: AudioBuffer) -> GateDecision:
        """Classify one utterance.

        Step 1 — wake phrase. Missing → ``IGNORE``; the verifier is not
        called, so ambient conversation costs nothing.
        """
        wake = self.detector.detect(text)
        if not wake.detected:
            return GateDecision(action=GateAction.IGNORE, reason="no wake word")

        # Step 2 — speaker verification. Fail closed: an exception or an
        # unenrolled speaker is treated the same as a stranger.
        identity = self._verify(audio)
        if identity is None or not identity.is_enrolled:
            confidence = 0.0 if identity is None else identity.confidence
            _log.info(
                "stream gate ignored an unverified speaker "
                "(confidence %.2f, wake word %r)",
                confidence,
                wake.wake_word,
            )
            return GateDecision(
                action=GateAction.IGNORE,
                reason="unverified speaker",
                speaker_id="unknown" if identity is None else identity.speaker_id,
                speaker_confidence=confidence,
            )

        command = _normalize(wake.command_text)
        decision = GateDecision(
            action=GateAction.IGNORE,
            speaker_id=identity.speaker_id,
            speaker_confidence=identity.confidence,
        )

        # Step 3 — sleep phrase. Checked before the asleep branch so that
        # sleeping again is idempotent rather than "asleep" noise.
        if command in self.sleep_phrases:
            self._asleep = True
            decision.action = GateAction.SLEEP
            decision.reason = "sleep phrase"
            return decision

        # Step 4 — asleep: only a wake phrase gets out.
        if self._asleep:
            if command in self.wake_phrases:
                self._asleep = False
                decision.action = GateAction.WAKE
                decision.reason = "wake phrase"
                return decision
            decision.action = GateAction.IGNORE
            decision.reason = "asleep"
            return decision

        # Step 5 — the wake word on its own is not a command.
        if not command:
            decision.action = GateAction.IGNORE
            decision.reason = "wake word without command"
            return decision

        # Step 6 — a verified owner with something to say.
        decision.action = GateAction.COMMAND
        decision.command_text = command
        return decision

    # ── Internal ───────────────────────────────────────────────────────

    def _verify(self, audio: AudioBuffer) -> Optional[SpeakerIdentity]:
        """Run the verifier, mapping any failure to ``None`` (fail closed)."""
        try:
            return self.verifier.verify(audio)
        except Exception as exc:  # noqa: BLE001 — fail closed, never raise mid-loop
            _log.info("stream gate verification failed (%s) — ignoring", exc)
            return None
