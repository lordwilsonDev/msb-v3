"""Text-to-speech engine.

Primary: macOS `say` — native, fast, many voices, no dependencies.
Fallback: pyttsx3 — cross-platform, wraps NSSpeechSynthesizer on macOS.

Usage::

    from msb_v3.speech.tts import speak, speak_to_file

    # Speak aloud
    speak("System status nominal.")

    # Save to WAV file
    speak_to_file("System status nominal.", "/tmp/response.wav")
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def speak(
    text: str,
    voice: Optional[str] = None,
    rate: int = 200,
    volume: float = 1.0,
) -> bool:
    """Speak text aloud using the system voice.

    Args:
        text: The text to speak.
        voice: Voice name (e.g., "Samantha", "Daniel"). None = default.
        rate: Words per minute (100-300). Default 200.
        volume: 0.0-1.0. Default 1.0.

    Returns:
        True if speech completed successfully.
    """
    if not text or not text.strip():
        return False

    return _speak_macos_say(text, voice, rate, volume)


def speak_to_file(
    text: str,
    output_path: str,
    voice: Optional[str] = None,
    rate: int = 200,
) -> bool:
    """Save spoken text to an AIFF file.

    Args:
        text: The text to speak.
        output_path: Path to write the AIFF file.
        voice: Voice name. None = default.
        rate: Words per minute.

    Returns:
        True if file was written successfully.
    """
    if not text or not text.strip():
        return False

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["say", "-o", str(path)]
    if voice:
        cmd.extend(["-v", voice])
    cmd.extend(["-r", str(rate)])
    cmd.append(text)

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def list_voices(language: str = "en") -> list[dict[str, str]]:
    """List available system voices.

    Args:
        language: Filter by language prefix (e.g., "en", "fr"). None = all.

    Returns:
        List of dicts with 'name', 'language', 'description' keys.
    """
    try:
        result = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    voices = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        lang = parts[1]
        desc = " ".join(parts[2:]).strip("# ").strip()
        if language and not lang.startswith(language):
            continue
        voices.append({"name": name, "language": lang, "description": desc})

    return voices


def _speak_macos_say(
    text: str, voice: Optional[str], rate: int, volume: float
) -> bool:
    """Speak using macOS `say` command."""
    cmd = ["say"]
    if voice:
        cmd.extend(["-v", voice])
    cmd.extend(["-r", str(rate)])
    if volume < 1.0:
        cmd.extend(["-v", f"{voice or 'default'}"])
    cmd.append(text)

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
