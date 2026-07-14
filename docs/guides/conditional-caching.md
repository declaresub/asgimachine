# Conditional GET & caching

**Goal:** let clients and CDNs skip a re-download when nothing changed — a `304 Not
Modified` — and tell them how long a response stays fresh. You implement one or two
callbacks; the graph runs the entire precondition suite (RFC 9110 §13) for you.

```python
from datetime import UTC, datetime

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route


class Note(Resource):
    def __init__(self, note: dict) -> None:
        self._note = note

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return f'"{self._note["version"]}"'          # a validator for this state

    async def last_modified(self, ctx: Ctx) -> datetime | None:
        return self._note["updated"]                 # a tz-aware datetime

    async def cache_control(self, ctx: Ctx) -> str | None:
        return "private, max-age=60"                 # fresh for 60s, then revalidate

    async def represent(self, ctx: Ctx) -> object:
        return {"text": self._note["text"]}


note = {"version": "v1", "updated": datetime(2026, 1, 1, 12, tzinfo=UTC), "text": "hello"}
app = build_app([resource_route("/note", Note(note))])
```

```
$ curl -is localhost:8000/note
HTTP/1.1 200 OK
etag: "v1"
last-modified: Thu, 01 Jan 2026 12:00:00 GMT
cache-control: private, max-age=60
{"text":"hello"}

$ curl -is localhost:8000/note -H 'if-none-match: "v1"'
HTTP/1.1 304 Not Modified
etag: "v1"
cache-control: private, max-age=60
        # empty body — the client keeps what it had
```

## What the two validators do

- **`generate_etag`** emits an `ETag`. A GET carrying `If-None-Match` with a matching
  value is a **`304`**; a non-match is a normal `200`.
- **`last_modified`** emits `Last-Modified`. A GET carrying `If-Modified-Since` that
  isn't older than it is a **`304`**.

Implement either, or both — the graph evaluates them in the RFC's precedence order.
You write **no** `if request.headers.get("if-none-match")` logic; the precondition
handling is the graph's.

Crucially, the `304` carries the **validators, `Vary`, and cache headers** — not just
an empty status — so an intermediary revalidates and re-serves correctly.

## Freshness: `Cache-Control` and `Expires`

`cache_control` sets the directive; `expires` sets an absolute `Expires` date. They
land on the cacheable responses (`200` and `304`):

```python
async def cache_control(self, ctx: Ctx) -> str | None:
    return "public, max-age=300"        # a shared cache may hold it 5 min
```

The headline case is an **immutable** archived resource — a feed page that will
never change:

```python
return "public, max-age=31536000, immutable"   # a CDN can keep it forever
```

Pair that with a stable `ETag` and the resource is `304`-friendly and
CDN-cacheable — see the `feed` [example](../examples.md).

## The write side comes free too

The same validators guard *writes*. A `PUT`/`PATCH`/`DELETE` with `If-Match` (or
`If-Unmodified-Since`) that doesn't match is a **`412 Precondition Failed`** — the
lost-update guard, no code required:

```
$ curl -isX PUT localhost:8000/note -H 'if-match: "v1"'   -d ...   # matches -> 204
$ curl -isX PUT localhost:8000/note -H 'if-match: "old"'  -d ...   # stale   -> 412
```

To *require* clients to send a precondition on writes (so nobody blind-overwrites),
add `require_conditional_write` → an unconditional write becomes a **`428`**. See
[Return a specific outcome](response-outcomes.md) and the
[coverage page](../concepts/webmachine-coverage.md).

!!! note "Strong vs. weak ETags"
    `If-None-Match` (GET caching) accepts a **weak** validator — `W/"v1"` — which just
    asserts the representations are equivalent. `If-Match` (writes) requires **strong**
    comparison, and a weak ETag never matches it. If your resource handles conditional
    *writes*, emit a **strong** ETag (`"v1"`, no `W/`).
