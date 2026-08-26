"""Speaker verification using resemblyzer voice embeddings.

Provides enrollment (store a speaker's voice embedding) and verification
(determine if a voice matches an enrolled speaker).

The resemblyzer library computes 256-dimensional embeddings from audio.
Verification is cosine similarity against enrolled embeddings — a threshold
of 0.75+ typically gives good separation between speakers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from msb_v3.speech.models import AudioBuffer, SpeakerIdentity


class SpeakerVerifier:
    """Enroll speakers and verify voice identity.

    Usage::

        verifier = SpeakerVerifier()

        # Enroll a speaker (from audio samples)
        verifier.enroll("wilson", audio_buffer_1)
        verifier.enroll("wilson", audio_buffer_2)  # more samples = better

        # Verify an unknown voice
        identity = verifier.verify(unknown_audio)
        if identity.is_enrolled and identity.confidence > 0.8:
            # Authorized
            pass
    """

    def __init__(
        self,
        threshold: float = 0.75,
        enrollments_path: Optional[str] = None,
    ) -> None:
        self.threshold = threshold
        self._embeddings: Dict[str, List[np.ndarray]] = {}
        self._enroller = None
        self._enrollments_path = (
            Path(enrollments_path) if enrollments_path else None
        )
        if self._enrollments_path and self._enrollments_path.exists():
            self._load_enrollments()

    def _get_enroller(self):
        """Lazy-load the resemblyzer VoiceEncoder."""
        if self._enroller is None:
            from resemblyzer import VoiceEncoder

            self._enroller = VoiceEncoder()
        return self._enroller

    def enroll(self, speaker_id: str, audio: AudioBuffer) -> None:
        """Add an audio sample to a speaker's enrollment.

        Multiple samples improve accuracy. Each call adds to the existing
        enrollment for this speaker (doesn't replace).
        """
        embedding = self._compute_embedding(audio)
        if speaker_id not in self._embeddings:
            self._embeddings[speaker_id] = []
        self._embeddings[speaker_id].append(embedding)

        if self._enrollments_path:
            self._save_enrollments()

    def verify(self, audio: AudioBuffer) -> SpeakerIdentity:
        """Verify an audio sample against enrolled speakers.

        Returns a SpeakerIdentity with the best match (or "unknown" if
        no enrolled speaker exceeds the threshold).
        """
        if not self._embeddings:
            return SpeakerIdentity(
                speaker_id="unknown",
                confidence=0.0,
                is_enrolled=False,
                method="resemblyzer",
            )

        embedding = self._compute_embedding(audio)
        best_id = "unknown"
        best_score = 0.0

        for speaker_id, enrollments in self._embeddings.items():
            # Compare against all enrollment samples, take the best
            scores = [
                float(self._cosine_similarity(embedding, e))
                for e in enrollments
            ]
            max_score = max(scores) if scores else 0.0
            if max_score > best_score:
                best_score = max_score
                best_id = speaker_id

        is_enrolled = best_score >= self.threshold
        return SpeakerIdentity(
            speaker_id=best_id if is_enrolled else "unknown",
            confidence=best_score,
            is_enrolled=is_enrolled,
            embedding=embedding.tolist(),
            method="resemblyzer",
        )

    def list_speakers(self) -> List[str]:
        """Return IDs of all enrolled speakers."""
        return list(self._embeddings.keys())

    def remove_speaker(self, speaker_id: str) -> bool:
        """Remove a speaker's enrollment. Returns True if found."""
        if speaker_id in self._embeddings:
            del self._embeddings[speaker_id]
            if self._enrollments_path:
                self._save_enrollments()
            return True
        return False

    # ── Internal ───────────────────────────────────────────────────────

    def _compute_embedding(self, audio: AudioBuffer) -> np.ndarray:
        """Compute a 256-dim voice embedding from an AudioBuffer."""
        encoder = self._get_enroller()
        audio_np = np.array(audio.samples, dtype=np.float32)
        # resemblyzer expects 16kHz mono float32
        embedding = encoder.embed_utterance(audio_np)
        return embedding

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two embeddings."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))

    def _save_enrollments(self) -> None:
        """Persist enrollments to disk."""
        if not self._enrollments_path:
            return
        self._enrollments_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for speaker_id, embeddings in self._embeddings.items():
            data[speaker_id] = [e.tolist() for e in embeddings]
        self._enrollments_path.write_text(json.dumps(data))

    def _load_enrollments(self) -> None:
        """Load enrollments from disk."""
        if not self._enrollments_path or not self._enrollments_path.exists():
            return
        try:
            data = json.loads(self._enrollments_path.read_text())
            for speaker_id, embeddings in data.items():
                self._embeddings[speaker_id] = [
                    np.array(e, dtype=np.float32) for e in embeddings
                ]
        except (json.JSONDecodeError, KeyError):
            pass
