"""The *errors* channel — secret redaction on the way out of the API.

Why a response middleware rather than redacting where errors are raised: the
leaks this closes are not exceptions that escape, they are *handled* errors
whose text is already built. ``HTTPException(status_code=404, detail=str(exc))``
appears across the routers, and by the time a handler sees it the exception
string — which can quote a URL, a header, a config repr — is the response
body. Redacting at the boundary is the only place that covers every route,
including ones written later.

Ordering (this is load-bearing): middleware added *later* wraps middleware
added *earlier*, so this class must be added **before** ``GZipMiddleware`` to
sit inside it. Outside it, the body being inspected would be compressed bytes —
redaction would be both useless and unsafe.

Two body paths, chosen by content type, because buffering is a behaviour
change:

- ``application/json`` is buffered and redacted whole. JSON responses here are
  not streamed, and a whole-body match is the only way to catch a secret that
  straddles a chunk boundary.
- ``text/*`` (notably ``text/event-stream``) is redacted **per chunk** and
  streamed through untouched otherwise. Buffering an SSE response would turn a
  live stream into a batch — the middleware must not be why ``/v1/...``
  streaming stopped working. The cost is stated: a secret split across two SSE
  chunks is not caught by shape or value matching on either half.
- Anything else (files, images, octet-stream) is passed through byte-for-byte:
  decoding arbitrary binary as UTF-8 to redact it would corrupt it.

The middleware never raises. If anything goes wrong mid-redaction it returns
the original response — the remaining six channels and the model-context hooks
are the primary control, and a response that cannot be redacted should still
reach its caller rather than becoming a 500.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse

from msb_v3.secrets.redact import contains_secret, redact

logger = logging.getLogger(__name__)

_HOP_HEADERS = frozenset({"content-length", "content-encoding"})


class SecretRedactionMiddleware(BaseHTTPMiddleware):
    """Redact known secrets out of response bodies before they leave."""

    async def dispatch(self, request: Any, call_next: Any) -> Response:
        response = await call_next(request)
        try:
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return await self._redact_json(response)
            if content_type.startswith("text/"):
                return self._redact_stream(response)
            return response
        except Exception:  # noqa: BLE001 — availability beats this channel alone
            logger.warning("secret-redaction middleware failed; body passed through")
            return response

    async def _redact_json(self, response: Any) -> Response:
        chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode() for chunk in chunks)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return self._rebuild(response, body)

        if not contains_secret(text):
            return self._rebuild(response, body)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in _HOP_HEADERS}
        return Response(
            content=redact(text),
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )

    def _redact_stream(self, response: Any) -> Response:
        original = response.body_iterator

        async def redacted_chunks() -> AsyncIterator[Any]:
            async for chunk in original:
                if isinstance(chunk, bytes):
                    try:
                        text = chunk.decode("utf-8")
                    except UnicodeDecodeError:
                        yield chunk
                        continue
                    yield redact(text) if contains_secret(text) else chunk
                else:
                    yield redact(chunk) if contains_secret(chunk) else chunk

        headers = {k: v for k, v in response.headers.items() if k.lower() not in _HOP_HEADERS}
        return StreamingResponse(
            redacted_chunks(),
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )

    @staticmethod
    def _rebuild(response: Response, body: bytes) -> Response:
        headers = {k: v for k, v in response.headers.items() if k.lower() not in _HOP_HEADERS}
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
