"""PyAudio microphone stream and speaker for the realtime path (live-tested)."""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, AsyncIterator, Optional

from msb_v3.speech.realtime.audio import MIC_RATE, PROVIDER_OUT_RATE


class MicStream:
    """16 kHz mono PCM16 capture, opened per turn."""

    def __init__(self, rate: int = MIC_RATE, chunk_frames: int = 1600) -> None:
        self.rate, self.chunk_frames = rate, chunk_frames
        self._pa: Any = None
        self._stream: Any = None

    def start(self) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=self.rate,
                                     input=True, frames_per_buffer=self.chunk_frames)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
        if self._pa is not None:
            self._pa.terminate()
        self._stream = self._pa = None

    async def frames_until(self, stop: asyncio.Event) -> AsyncIterator[bytes]:
        """Yield chunks until ``stop`` is set, then close the device."""
        loop = asyncio.get_running_loop()
        try:
            while not stop.is_set():
                stream = self._stream
                data = await loop.run_in_executor(
                    None, lambda: stream.read(self.chunk_frames, exception_on_overflow=False))
                yield data
        finally:
            self.stop()


class Speaker:
    """Plays PCM16 24 kHz on a background thread; ``stop()`` drops queued audio."""

    def __init__(self, rate: int = PROVIDER_OUT_RATE) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=rate, output=True)
        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            data = self._q.get()
            if data is None:
                return
            self._stream.write(data)

    def play(self, pcm16_24k: bytes) -> None:
        self._q.put(pcm16_24k)

    def stop(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=2)
        self._stream.close()
        self._pa.terminate()
