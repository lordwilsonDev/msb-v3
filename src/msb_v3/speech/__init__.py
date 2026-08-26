"""Speech pipeline — capture → transcribe → verify → intent → governed execution.

This package implements the Voice Identity Workstation's core pipeline:

1. **Capture** — microphone audio buffer (PyAudio)
2. **Transcribe** — speech-to-text (mlx-whisper primary, faster-whisper fallback)
3. **Verify** — speaker identification (resemblyzer embeddings)
4. **Intent** — command extraction (pattern matching + LLM fallback)
5. **Authorize** — fail-closed gate (unknown speaker → denied)
6. **Execute** — governed action via MSB ActionGate
"""
