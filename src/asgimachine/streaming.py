"""Streaming / SSE helpers (PLAN.md §8).

Streaming lives *inside* the graph, at the producer node: a producer (or a POST's
``process_post``) returns an async iterator of bytes instead of a value, and the
core hands it to the substrate untouched. These helpers format Server-Sent Events
frames and wrap a stream so a mid-stream failure becomes an SSE ``error`` event.

**Post-commit boundary.** Once streaming starts, the status line is on the wire;
a later failure cannot become a 500. :func:`guard_sse` is the recommended pattern
— it converts an exception raised after commit into an ``event: error`` frame.

**Client disconnect.** Disconnect handling is rented from the substrate (§2.1):
Starlette cancels the streaming task (an anyio task group) when the client goes
away. Cancellation propagates into the producer as ``CancelledError`` /
``GeneratorExit``, so producers should release resources in ``try/finally``.
:func:`guard_sse` catches only :class:`Exception`, never the cancellation
:class:`BaseException`\\ s, so a disconnect stops the stream and runs cleanup
rather than being swallowed into an error frame.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable


def sse_event(
    data: object,
    *,
    event: str | None = None,
    event_id: str | None = None,
    retry: int | None = None,
    comment: str | None = None,
) -> bytes:
    """Format one Server-Sent Events frame as bytes.

    ``data`` is emitted verbatim if it is a ``str``, otherwise JSON-encoded.
    Multi-line data becomes multiple ``data:`` lines, per the SSE spec.
    """

    lines: list[str] = []
    if comment is not None:
        lines.append(f": {comment}")
    if event is not None:
        lines.append(f"event: {event}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if retry is not None:
        lines.append(f"retry: {retry}")
    text = data if isinstance(data, str) else json.dumps(data)
    lines.extend(f"data: {line}" for line in text.split("\n"))
    return ("\n".join(lines) + "\n\n").encode()


def sse_error(data: object, *, event: str = "error") -> bytes:
    """Format an SSE frame carrying an error (the post-commit failure channel)."""

    return sse_event(data, event=event)


async def guard_sse(
    source: AsyncIterator[bytes],
    *,
    format_error: Callable[[Exception], object] | None = None,
) -> AsyncIterator[bytes]:
    """Yield from ``source``; on a post-commit exception, emit an SSE error frame.

    Wrap a producer's event generator with this so a failure after the response
    has committed surfaces as an ``event: error`` frame instead of tearing down
    the connection. ``format_error`` maps the exception to the error payload.
    """

    try:
        async for chunk in source:
            yield chunk
    except Exception as exc:  # noqa: BLE001 — post-commit: app failures become a frame
        # Only Exception: cancellation (CancelledError/GeneratorExit on client
        # disconnect) is a BaseException and must propagate so the stream stops.
        payload = format_error(exc) if format_error is not None else "internal error"
        yield sse_error(payload)
