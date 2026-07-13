# Per-request lifespan

A resource that needs a per-request resource — the classic case being a **database
connection** from a pool — overrides `lifespan`, which wraps the entire graph
walk.

```python
from collections.abc import AsyncGenerator
from asgimachine.resource import Ctx, Resource


@dataclass(slots=True)
class WidgetsCtx(Ctx):
    conn: Connection | None = None


class WidgetsResource(Resource[WidgetsCtx]):
    context_class = WidgetsCtx

    async def lifespan(self, ctx: WidgetsCtx) -> AsyncGenerator[None]:
        async with self._pool.acquire() as conn:   # setup
            ctx.conn = conn
            yield                                    # ...the walk runs here...
        # teardown (releasing conn) runs on every exit

    async def represent(self, ctx: WidgetsCtx) -> object:
        assert ctx.conn is not None                  # opened before the walk
        return {"widgets": await ctx.conn.fetch_widgets()}
```

## A plain async generator — no decorator

You override `lifespan` as a **plain async generator**: acquire, `yield` once,
release. **No `@asynccontextmanager`** — the core owns the wrapping. And because
the declared return type is `AsyncGenerator[None]`, *forgetting the `yield`* is a
**type error**, where forgetting a decorator would be a runtime surprise.

## The core owns release

The reason the core owns the wrapping is so it can own the hard part — *release*:

- **Where it opens:** before the graph starts, so `is_authorized` /
  `resource_exists` can already query. The connection is on `ctx`, visible to every
  callback.
- **Where it releases:** on **every** exit — a normal response, a halt
  (404/401/…), a raised error, or a client disconnect. A `HaltResponse` closes
  cleanly; a real exception is fed into the generator, so
  `async with conn.transaction()` **rolls back**.
- **Cancellation-shielded.** Teardown runs inside a shielded, timeout-bounded
  scope, so a disconnect mid-request can't interrupt the release await — and a
  release that blocks forever can't hang the task.
- **Streaming-aware.** For a streamed body (which outlives the walk), teardown is
  deferred until the stream drains or is abandoned, so the connection stays alive
  for the life of the stream.

!!! warning "Don't hold a transaction across a stream"
    Keeping a DB transaction open for the whole duration of an SSE/feed stream is
    how you exhaust a pool. Read eagerly, or release before yielding the iterator.
