# Stream a response

**Goal:** send the body incrementally — Server-Sent Events, NDJSON, a large export
— instead of buffering it. Return an **async iterator of bytes** from `represent`
(or `process_post`); the graph hands it to the substrate untouched.

Streaming is a genuine special case, because once the first byte is on the wire the
status is *committed* — a later failure can no longer become a `500`. asgimachine
handles the envelope (auth, negotiation, method) normally and then gets out of the
way for the open-ended part.

```python
from collections.abc import AsyncIterator

from asgimachine.resource import Ctx, Resource
from asgimachine.streaming import guard_sse, sse_event
from asgimachine.substrate.starlette import build_app, resource_route


class Ticks(Resource):
    PRODUCES = ("text/event-stream",)          # negotiate to SSE — part of the envelope

    async def represent(self, ctx: Ctx) -> AsyncIterator[bytes]:
        return guard_sse(self._events())       # <- an async iterator of bytes

    async def _events(self) -> AsyncIterator[bytes]:
        for n in range(3):
            yield sse_event({"n": n}, event="tick", event_id=str(n))
        yield sse_event("done", event="complete")


app = build_app([resource_route("/events", Ticks())])
```

```
$ curl -N localhost:8000/events
event: tick
id: 0
data: {"n": 0}

event: tick
id: 1
data: {"n": 1}
        ...
event: complete
data: done
```

The envelope is still the graph's: this resource gets `401`, `406`, `405`, and HEAD
handling for free — the stream is only the body.

## You own the bytes

A returned `AsyncIterator[bytes]` bypasses the codec (there's nothing to buffer and
encode), so you write the framing. `PRODUCES` sets the `Content-Type`. For SSE, the
[`streaming`](../reference.md) helpers do the formatting:

- `sse_event(data, *, event=, event_id=, retry=, comment=)` — one frame (dict data
  is JSON-encoded; a `str` is sent verbatim).
- For NDJSON, a file, etc., just `yield` your own `bytes` with a matching `PRODUCES`
  (`application/x-ndjson`, `application/octet-stream`, …).

## The post-commit boundary

This is the part that makes streaming different. Before the first byte, a failure is
a normal halt — `on_exception`, a `500`. *After* it, the `200` and headers are
already sent; you can't retract them. So a mid-stream error has to travel **in-band**.

`guard_sse` is the pattern: it yields from your producer and, on an exception,
appends an `event: error` frame instead of tearing down the connection.

```
$ curl -N localhost:8000/events    # a producer that raises after event 1
event: tick
id: 0
...
event: error
data: internal error
        # still HTTP 200 — the failure is a frame, not a status
```

Pass `format_error=` to shape the payload. Wrap every producer you stream.

## Client disconnect and cleanup

When the client goes away, Starlette cancels the streaming task; the cancellation
arrives inside your producer as `CancelledError` / `GeneratorExit`. So **release
resources in `try/finally`** in the producer:

```python
async def _events(self) -> AsyncIterator[bytes]:
    try:
        async for row in cursor:
            yield sse_event(row)
    finally:
        ...  # runs on normal end AND on disconnect
```

`guard_sse` catches only `Exception`, never the cancellation `BaseException`s — so a
disconnect *stops* the stream and runs your cleanup rather than being swallowed into
an error frame.

## Resources stay alive for the whole stream

The streamed body outlives the walk, so the core **defers the
[lifespan](../concepts/lifespan.md) teardown until the stream drains, errors, or the
client disconnects** — guaranteed, even if the substrate never reads a chunk (a
pre-first-chunk disconnect). A connection stashed on `ctx` is therefore valid for the
life of the stream.

!!! warning "Don't hold a transaction across a long stream"
    Keeping a DB transaction open for the duration of an SSE/feed stream is how you
    exhaust a connection pool. Read eagerly, or release the transaction before
    yielding the iterator — the stream can outlive any sane transaction.

`HEAD` on a streaming resource sends the headers with an empty body, as it should.
