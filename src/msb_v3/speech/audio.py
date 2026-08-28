"""Interchangeable audio rendering seam for speech responses.

The speech/governance layers produce text and remain independent of playback.
Renderers consume normalized audio requests and report structured outcomes.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol, Sequence


class AudioError(RuntimeError):
    """Base error for audio rendering failures."""


class AudioDeviceError(AudioError):
    """The selected renderer/device is unavailable."""


class AudioFormatError(AudioError):
    """The payload cannot be rendered safely."""


class AudioPlaybackError(AudioError):
    """Playback failed after the renderer was selected."""


class RenderStatus(str, Enum):
    PLAYED = "PLAYED"
    SAFE_FAILURE = "SAFE_FAILURE"
    EXPECTED_SKIP = "EXPECTED_SKIP"


@dataclass(frozen=True)
class AudioPayload:
    """Canonical audio representation passed to a renderer."""

    pcm_s16le: bytes
    sample_rate: int = 24000
    channels: int = 1

    def validate(self) -> None:
        if self.sample_rate <= 0:
            raise AudioFormatError("sample_rate must be positive")
        if self.channels <= 0:
            raise AudioFormatError("channels must be positive")
        if len(self.pcm_s16le) % (2 * self.channels) != 0:
            raise AudioFormatError("PCM payload is not aligned to s16le frames")


@dataclass(frozen=True)
class RenderContext:
    """Non-machine-specific context for one rendering operation."""

    operation_id: str = ""
    device: str = "default"
    timeout_s: float = 30.0


@dataclass(frozen=True)
class RenderResult:
    """Structured, auditable renderer outcome."""

    status: RenderStatus
    renderer_id: str
    duration_ms: float = 0.0
    error_class: str = ""
    message: str = ""
    diagnostics: Dict[str, str] = field(default_factory=dict)


class AudioRenderer(Protocol):
    """Definition implemented by every audio renderer."""

    renderer_id: str
    capabilities: frozenset[str]

    def available(self) -> bool:
        """Return whether this renderer can be selected in this environment."""

    def unavailable_reason(self) -> str:
        """Explain why the renderer cannot currently be selected."""

    def render(self, audio: AudioPayload, context: RenderContext) -> RenderResult:
        """Render canonical audio and return a structured result."""


class MacOSSayRenderer:
    """macOS ``say`` adapter.

    ``say`` is text-oriented, so this adapter is intended for the existing
    speech response path while the canonical PCM contract remains available
    for native renderers. It never leaks the binary above this boundary.
    """

    renderer_id = "macos_say"
    capabilities = frozenset({"text_to_audio", "local_playback"})

    def __init__(self, text_provider: Optional[Callable[[], str]] = None) -> None:
        self._text_provider = text_provider

    def available(self) -> bool:
        return shutil.which("say") is not None

    def unavailable_reason(self) -> str:
        return "macOS say executable is not available"

    def render(self, audio: AudioPayload, context: RenderContext) -> RenderResult:
        audio.validate()
        if not self.available():
            return RenderResult(
                RenderStatus.EXPECTED_SKIP,
                self.renderer_id,
                error_class="AUDIO_RENDERER_UNAVAILABLE",
                message=self.unavailable_reason(),
            )
        if self._text_provider is None:
            return RenderResult(
                RenderStatus.SAFE_FAILURE,
                self.renderer_id,
                error_class="AUDIO_TEXT_BRIDGE_UNAVAILABLE",
                message="macOS say requires text from the TTS adapter",
            )
        try:
            subprocess.run(
                ["say", self._text_provider()],
                check=True,
                timeout=context.timeout_s,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return RenderResult(
                RenderStatus.SAFE_FAILURE,
                self.renderer_id,
                error_class="AUDIO_PLAYBACK_ERROR",
                message=str(exc),
            )
        return RenderResult(RenderStatus.PLAYED, self.renderer_id)


class UnavailableAudioRenderer:
    """Deterministic provider used when no hardware renderer is configured."""

    renderer_id = "unavailable"
    capabilities: frozenset[str] = frozenset()

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "no audio renderer configured"

    def render(self, audio: AudioPayload, context: RenderContext) -> RenderResult:
        audio.validate()
        return RenderResult(
            RenderStatus.EXPECTED_SKIP,
            self.renderer_id,
            error_class="AUDIO_RENDERER_UNAVAILABLE",
            message=self.unavailable_reason(),
        )


class AudioRendererRegistry:
    """Deterministic, fail-closed renderer selection."""

    def __init__(self, renderers: Optional[Sequence[AudioRenderer]] = None) -> None:
        self._renderers: List[AudioRenderer] = list(renderers or [])

    def register(self, renderer: AudioRenderer) -> None:
        self._renderers.append(renderer)

    def has_available_renderer(self, required_capabilities: frozenset[str] = frozenset()) -> bool:
        return any(
            required_capabilities.issubset(renderer.capabilities) and renderer.available()
            for renderer in self._renderers
        )

    def select(self, required_capabilities: frozenset[str] = frozenset()) -> AudioRenderer:
        for renderer in self._renderers:
            if required_capabilities.issubset(renderer.capabilities) and renderer.available():
                return renderer
        reasons = "; ".join(
            f"{renderer.renderer_id}: {renderer.unavailable_reason()}"
            for renderer in self._renderers
        )
        raise AudioDeviceError(reasons or "no audio renderers registered")

    def render(
        self,
        audio: AudioPayload,
        context: RenderContext,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> RenderResult:
        try:
            renderer = self.select(required_capabilities)
        except AudioDeviceError as exc:
            return RenderResult(
                RenderStatus.SAFE_FAILURE,
                "registry",
                error_class="AUDIO_DEVICE_UNAVAILABLE",
                message=str(exc),
            )
        return renderer.render(audio, context)


def silent_payload() -> AudioPayload:
    """Return a valid zero-length canonical payload for text-only adapters."""

    return AudioPayload(pcm_s16le=b"")
