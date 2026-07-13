# Observability & logging

asgimachine takes the **wide-event** (canonical-log-line) view: instead of
scattering narrow log lines through a request, it accumulates one structured event
per request on `ctx.event` and emits it once at the boundary through a pluggable
sink. The [decision trace](decision-graph.md) is its spine; you slice and
aggregate the rest.

The framework owns the *event and the emission seam*; the *sink* — stdlib logging,
structlog, OpenTelemetry, an error reporter — is yours to choose. Nothing is
emitted until you wire one.

## The event

`ctx.event` is a plain, mutable dict, present on every request. Write to it from
anywhere that holds `ctx`; the core fills the owned fields and emits it once — at
the request boundary for a buffered response, or at **stream-close** for a streamed
body (so late work lands in it). Field names follow **OpenTelemetry** semantic
conventions where they exist; graph-specific fields live under `asgm.`.

| Field | When | Meaning |
|---|---|---|
| `http.request.method` | always | the method |
| `url.path` | always | the request path |
| `http.response.status_code` | if a response was produced | the status |
| `duration_ms` | always | wall time from entry to emit |
| `asgm.lane` | always | `resource` or `command` |
| `asgm.resource` / `asgm.command` | always | the class name |
| `asgm.decision_path` | resource lane | the trace node path (`B13,B12,…,O18`) |
| `asgm.halted_at` | 4xx/5xx | the last node — *where* it ended |
| `asgm.outcome` | always | `ok` / `halt` / `error` / `propagated` |
| `asgm.media_type` / `asgm.language` / `asgm.encoding` | if negotiated | the chosen variant |
| `exception.type` / `exception.message` / `error.type` | on an exception | the failure |

Those namespaces (`http.`, `url.`, `error.`, `exception.`, `asgm.`) are reserved
for the core — put your domain fields in your own.

## Wiring a sink

No sink is configured by default. The reference sink logs via the stdlib:

```python
from asgimachine.event import LoggingEventSink
from asgimachine.substrate.starlette import build_app

app = build_app(routes, event_sink=LoggingEventSink())
```

A sink is one synchronous method (`emit(event)`) — sync so it's safe to call on
the exit path even under cancellation. Swap in your backend at the composition
root:

```python
# structlog
class StructlogSink:
    def __init__(self) -> None:
        self._log = structlog.get_logger("request")

    def emit(self, event):
        self._log.info("request", **event)

# OpenTelemetry — decorate the active server span with the (already OTel-named) fields
class SpanSink:
    def emit(self, event):
        span = trace.get_current_span()
        for key, value in event.items():
            span.set_attribute(key, value)
```

A sink that raises is swallowed (logged to `asgimachine.event`) — an observability
failure never becomes a request failure.

## Enriching from a resource

Any callback can add domain fields — high-cardinality is the point:

```python
class Account(Resource[AccountCtx]):
    async def resource_exists(self, ctx: AccountCtx) -> bool:
        ctx.account = await self._store.load(ctx.request.path_params["id"])
        if ctx.account is not None:
            ctx.event["account.id"] = ctx.account.id       # a wide dimension
            ctx.event["account.plan"] = ctx.account.plan
        return ctx.account is not None
```

Now every request event carries the account — so "p95 latency by plan" or "404s
by account" is a query, not a code change.

## Enriching from deep code — the database

The hard part of wide events is contributing from code that doesn't hold `ctx` — a
database layer three calls down. asgimachine's answer for the common case needs no
ambient globals: the [lifespan](lifespan.md) already puts the connection on `ctx`,
and DB code always has the connection in hand. So carry a per-connection
accumulator and **merge it in the lifespan** — where `ctx` and the connection meet.

This example uses `asyncpg`'s per-connection query logger:

```python
import asyncpg


class QueryLog:
    """Aggregates, not enumerates — a request doing 200 queries is three numbers,
    not a 200-element array. Never records bound parameters (a PII/fingerprint
    leak); log the parameterized statement shape only if you must."""

    __slots__ = ("count", "total_ms", "max_ms")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.count, self.total_ms, self.max_ms = 0, 0.0, 0.0

    def record(self, q: asyncpg.LoggedQuery) -> None:
        ms = q.elapsed * 1000
        self.count += 1
        self.total_ms += ms
        self.max_ms = max(self.max_ms, ms)

    def merge_into(self, event: dict) -> None:
        if self.count:
            event["db.query_count"] = self.count
            event["db.total_ms"] = round(self.total_ms, 3)
            event["db.max_ms"] = round(self.max_ms, 3)


class LoggingConnection(asyncpg.Connection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.qlog = QueryLog()
        self.add_query_logger(self.qlog.record)  # asyncpg >= 0.29


pool = await asyncpg.create_pool(dsn, connection_class=LoggingConnection)
```

Then merge in the resource's lifespan:

```python
class AccountsResource(Resource[AccountCtx]):
    async def lifespan(self, ctx: AccountCtx) -> AsyncGenerator[None]:
        async with self._pool.acquire() as conn:
            conn.qlog.reset()          # (!) pooled connections are reused
            ctx.conn = conn
            try:
                yield
            finally:
                conn.qlog.merge_into(ctx.event)   # lands before the event emits
```

Two things this gets right, both easy to get wrong:

!!! warning "Reset on acquire, not just merge on release"
    A pooled connection outlives one request. If you only merge at release, request
    *N+1* inherits *N*'s counters. Reset **after acquire** (or in the pool's
    `setup=` hook, which runs on every checkout).

The merge lands because the core emits the event **after** lifespan teardown —
including the deferred teardown of a streamed response, so a feed that queries
while streaming still reports its DB work.

## Errors and report ids

The [`on_exception`](resources.md) handler runs *inside* the walk with `ctx` in
scope — which is exactly what lets an error report and the wide event share a
report id. Report, stash the id, return, and the graph owns a clean 500 whose event
carries the id:

```python
from honeybadger import honeybadger

async def report(ctx, exc):
    ctx.event["honeybadger.notice_id"] = honeybadger.notify(exc)
    return None  # graph-owned 500 (negotiated problem+json), event carries the id

app = build_app(routes, on_exception=report, event_sink=LoggingEventSink())
```

This is why the catch-all lives in the core rather than an outer ASGI middleware: a
middleware only sees the exception *after* the walk returns, so the report id is
born too late to reach an event emitted inside `run()`. Co-locating the reporter
with the event is the whole point. (Re-raise instead of returning to hand the
exception to your outer reporter unchanged — but then the id won't be in the event.)

A crash that isn't handled still emits a `propagated` event (statusless) before it
leaves the graph, so it's never invisible.

## The command lane

Commands skip the graph, so they emit a thinner event (no decision path) through
the same sink — the same OTel fields and outcomes, with `asgm.lane = "command"`.
Filter on `asgm.lane` to separate the two, or union them for a single request
stream.

## What the graph does — and doesn't

The graph owns the *event*: the fields only it knows (the decision path, the halt
node, the negotiated variant, why a 406 happened). It does **not** own the
*backend* — shipping to a log aggregator, a tracing system, or an APM is Layer 2,
rented through the sink. And it doesn't turn RED metrics into a separate path:
rate, errors, and duration all fall out of the one event stream at your backend, so
there's a single source of truth per request.
