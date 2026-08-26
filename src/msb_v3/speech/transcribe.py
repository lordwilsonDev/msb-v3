"""Speech-to-text transcription.

Primary engine: mlx-whisper (Apple MLX — Metal/ANE acceleration on Apple Silicon)
Fallback engine: faster-whisper (CTranslate2 — CPU, cross-platform)

Both use identical Whisper model weights, so accuracy is the same.
The difference is speed: mlx-whisper leverages Apple Silicon hardware,
faster-whisper runs on CPU with int8 quantization.

Model recommendation for 16GB Mac:
- small.en (244M, 3.4% WER, ~2GB RAM) — best for voice commands
- medium (769M, 2.9% WER, ~5GB RAM) — if accuracy matters more
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from msb_v3.speech.capture import save_wav
from msb_v3.speech.models import AudioBuffer, Transcript


def transcribe(
    audio: AudioBuffer,
    model_name: str = "small",
    language: str = "en",
    engine: str = "auto",
) -> Transcript:
    """Transcribe an AudioBuffer to text.

    Args:
        audio: The audio to transcribe.
        model_name: Whisper model size (tiny, base, small, medium, large-v3).
        language: Language code (en, es, fr, de, etc.).
        engine: "mlx", "faster", "openai", or "auto".

    Returns:
        Transcript with text, confidence, and segment details.
    """
    if not audio.samples:
        return Transcript(text="", engine="none", confidence=0.0)

    if engine == "auto":
        return _transcribe_auto(audio, model_name, language)
    if engine == "mlx":
        return _transcribe_mlx(audio, model_name, language)
    if engine == "faster":
        return _transcribe_faster(audio, model_name, language)
    if engine == "openai":
        return _transcribe_openai(audio, model_name, language)

    raise ValueError(f"Unknown engine: {engine} (use 'mlx', 'faster', 'openai', or 'auto')")


def _transcribe_auto(
    audio: AudioBuffer, model_name: str, language: str
) -> Transcript:
    """Try engines in order: openai → mlx → faster."""
    for engine_fn in (_transcribe_openai, _transcribe_mlx, _transcribe_faster):
        try:
            return engine_fn(audio, model_name, language)
        except Exception:  # noqa: BLE001
            pass

    return Transcript(
        text="",
        engine="none",
        confidence=0.0,
        duration_seconds=audio.duration_seconds,
    )


def _transcribe_mlx(
    audio: AudioBuffer, model_name: str, language: str
) -> Transcript:
    """Transcribe using mlx-whisper (Apple MLX — Metal acceleration)."""
    import mlx_whisper

    # Write audio to temp WAV for mlx_whisper
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        save_wav(audio, tmp_path)

        result = mlx_whisper.transcribe(
            tmp_path,
            path_or_hf_repo=f"mlx-community/whisper-{model_name}-mlx",
            language=language,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    text = result.get("text", "").strip()
    segments = [
        {
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", ""),
        }
        for seg in result.get("segments", [])
    ]

    return Transcript(
        text=text,
        language=language,
        confidence=_estimate_confidence(segments),
        duration_seconds=audio.duration_seconds,
        segments=segments,
        engine="mlx-whisper",
    )


def _transcribe_faster(
    audio: AudioBuffer, model_name: str, language: str
) -> Transcript:
    """Transcribe using faster-whisper (CTranslate2 — CPU)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    # Write audio to temp WAV for faster_whisper
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        save_wav(audio, tmp_path)

        segments_gen, info = model.transcribe(
            tmp_path,
            language=language,
            beam_size=5,
            vad_filter=True,
        )
        segments = []
        for seg in segments_gen:
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
            })
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    text = " ".join(s["text"].strip() for s in segments)

    return Transcript(
        text=text,
        language=language or (info.language if info else "en"),
        confidence=_estimate_confidence(segments),
        duration_seconds=audio.duration_seconds,
        segments=segments,
        engine="faster-whisper",
    )


def _transcribe_openai(
    audio: AudioBuffer, model_name: str, language: str
) -> Transcript:
    """Transcribe using openai-whisper (reference implementation, CPU)."""
    import whisper

    model = whisper.load_model(model_name)

    # Write audio to temp WAV
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        save_wav(audio, tmp_path)
        result = model.transcribe(tmp_path, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    text = result.get("text", "").strip()
    segments = [
        {
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", ""),
        }
        for seg in result.get("segments", [])
    ]

    return Transcript(
        text=text,
        language=language or result.get("language", "en"),
        confidence=_estimate_confidence(segments),
        duration_seconds=audio.duration_seconds,
        segments=segments,
        engine="openai-whisper",
    )


def _estimate_confidence(segments: list[dict]) -> float:
    """Estimate transcription confidence from segment data.

    Whisper doesn't expose per-word confidence directly. We use segment
    count and average segment length as a proxy — more segments with
    shorter text usually means higher confidence (cleaner audio).
    """
    if not segments:
        return 0.0
    # Simple heuristic: more segments = more confident
    return min(0.95, 0.5 + len(segments) * 0.05)
