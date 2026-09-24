"""Talk-button sources: Enter key (no permissions) or a headset media key.

Each button runs one background thread that pushes presses into an asyncio
queue, so ``wait_press()`` can be cancelled without losing a press.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional, Tuple

PLAY_PAUSE_KEYCODE = 16
_NS_SYSTEM_DEFINED = 14
_MEDIA_KEY_SUBTYPE = 8


def parse_media_key(data1: int) -> Tuple[int, bool]:
    """Decode NSEvent.data1 of a system-defined media-key event."""
    key_code = (data1 & 0xFFFF0000) >> 16
    is_down = ((data1 & 0xFF00) >> 8) == 0xA
    return key_code, is_down


class _QueuedButton:
    def __init__(self) -> None:
        self._queue: "asyncio.Queue[None]" = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    def _press(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def _run(self) -> None:
        raise NotImplementedError

    async def wait_press(self) -> None:
        if self._thread is None:
            self._loop = asyncio.get_running_loop()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        await self._queue.get()


class EnterKeyButton(_QueuedButton):
    """Press Enter in the terminal. Needs no macOS permission."""

    def _run(self) -> None:
        while True:
            try:
                input()
            except EOFError:
                return
            self._press()


class MediaKeyButton(_QueuedButton):
    """Headset play/pause via an AppKit global monitor (needs Input Monitoring)."""

    def __init__(self, key_code: int = PLAY_PAUSE_KEYCODE) -> None:
        super().__init__()
        self.key_code = key_code

    def _run(self) -> None:
        # pyobjc ships no type stubs; kept inline so pyproject.toml stays untouched.
        from AppKit import (  # type: ignore[import-untyped]
            NSApplication,
            NSEvent,
            NSSystemDefinedMask,
        )
        from PyObjCTools import AppHelper  # type: ignore[import-untyped]

        def handler(event):  # type: ignore[no-untyped-def]
            if event.type() != _NS_SYSTEM_DEFINED or event.subtype() != _MEDIA_KEY_SUBTYPE:
                return
            code, down = parse_media_key(event.data1())
            if code == self.key_code and down:
                self._press()

        NSApplication.sharedApplication()
        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSSystemDefinedMask, handler)
        AppHelper.runConsoleEventLoop()
