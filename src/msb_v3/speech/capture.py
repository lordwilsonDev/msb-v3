"""Audio capture from microphone using PyAudio.

Provides both file-based and live capture. Live capture requires a working
microphone — on macOS this means the app has microphone permission in
System Preferences → Privacy → Microphone.
"""

from __future__ import annotations

import wave
from pathlib import Path

from msb_v3.speech.models import AudioBuffer


def capture_from_file(audio_path: str, target_rate: int = 16000) -> AudioBuffer:
    """Load audio from a file (WAV, FLAC, etc.) into an AudioBuffer.

    Uses the standard library wave module for WAV files. For other formats
    falls back to torchaudio if available.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    suffix = path.suffix.lower()
    if suffix == ".wav":
        return _load_wav(path, target_rate)
    return _load_with_torchaudio(path, target_rate)


def capture_from_microphone(
    duration_seconds: float = 5.0,
    sample_rate: int = 16000,
    channels: int = 1,
    chunk_size: int = 1024,
) -> AudioBuffer:
    """Capture audio from the default microphone for a fixed duration.

    Returns an AudioBuffer with float32 samples normalized to [-1, 1].

    Raises RuntimeError if PyAudio cannot open the input stream.
    """
    try:
        import pyaudio
    except ImportError as exc:
        raise RuntimeError("PyAudio not installed — pip install pyaudio") from exc

    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk_size,
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot open microphone: {exc}") from exc

    frames: list[bytes] = []
    total_chunks = int(sample_rate / chunk_size * duration_seconds)
    try:
        for _ in range(total_chunks):
            data = stream.read(chunk_size, exception_on_overflow=False)
            frames.append(data)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    # Convert bytes to float32 samples
    import struct

    all_bytes = b"".join(frames)
    sample_count = len(all_bytes) // 4  # 4 bytes per float32
    samples = list(struct.unpack(f"{sample_count}f", all_bytes))

    return AudioBuffer(
        samples=samples,
        sample_rate=sample_rate,
        duration_seconds=duration_seconds,
        channels=channels,
    )


def capture_intelligent(
    max_duration: float = 15.0,
    silence_after: float = 1.0,
    sample_rate: int = 16000,
) -> AudioBuffer:
    """Capture audio using VAD for variable-length recording.

    Unlike capture_from_microphone() which records for a fixed duration,
    this uses Voice Activity Detection to:
    - Start recording when speech is detected
    - Stop recording after silence_after seconds of silence
    - Return only the speech portion (trimmed)

    This reduces latency by not waiting for a fixed 5-second window.
    """
    from msb_v3.speech.vad import VoiceDetector, VADConfig, capture_until_silence

    return capture_until_silence(
        duration_seconds=max_duration,
        silence_after=silence_after,
        sample_rate=sample_rate,
        max_duration=max_duration,
    )


def save_wav(buffer: AudioBuffer, output_path: str) -> None:
    """Save an AudioBuffer to a WAV file."""
    import struct

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(buffer.channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(buffer.sample_rate)
        # Convert float32 to int16
        int16_samples = [max(-32768, min(32767, int(s * 32767))) for s in buffer.samples]
        raw = struct.pack(f"{len(int16_samples)}h", *int16_samples)
        wf.writeframes(raw)


# ── Internal helpers ──────────────────────────────────────────────────────


def _load_wav(path: Path, target_rate: int) -> AudioBuffer:
    """Load a WAV file into an AudioBuffer."""
    import struct

    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # Convert to float32
    if sampwidth == 2:
        int16_samples = struct.unpack(f"{n_frames * channels}h", raw)
        samples = [s / 32767.0 for s in int16_samples]
    elif sampwidth == 4:
        samples = list(struct.unpack(f"{n_frames * channels}f", raw))
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth} bytes")

    duration = n_frames / sample_rate
    return AudioBuffer(
        samples=samples,
        sample_rate=sample_rate,
        duration_seconds=duration,
        channels=channels,
    )


def _load_with_torchaudio(path: Path, target_rate: int) -> AudioBuffer:
    """Load audio using torchaudio (supports FLAC, MP3, OGG, etc.)."""
    try:
        import torchaudio

        waveform, sample_rate = torchaudio.load(str(path))
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot load {path.suffix} files — install torchaudio or convert to WAV"
        ) from exc

    # Convert to mono if stereo
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # Resample if needed
    if sample_rate != target_rate:
        resampler = torchaudio.transforms.Resample(sample_rate, target_rate)
        waveform = resampler(waveform)
        sample_rate = target_rate

    samples = waveform.squeeze().tolist()
    duration = len(samples) / sample_rate

    return AudioBuffer(
        samples=samples,
        sample_rate=sample_rate,
        duration_seconds=duration,
        channels=1,
    )
