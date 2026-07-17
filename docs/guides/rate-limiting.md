# Rate-limit an auth endpoint

**Goal:** cap how often a client may hit an endpoint — the classic case being a
login route you want to protect from credential-stuffing — and answer an over-limit
request with `429 Too Many Requests` + `Retry-After`.

That's a first-class node. `within_rate_limit` (**B13a**) runs right after
`service_available` and *before* method, auth, and body checks, so an over-limit
request is shed at the cheapest possible point — before a rejected request touches
your database or your password hasher. Return `True` to proceed, or a `Retry-After`
hint (`int` seconds, or a `datetime`) to reject with `429`.

```python
import math
import time
from dataclasses import dataclass, field

from asgimachine.resource import Ctx, Resource


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    """A per-key token bucket. `check` returns None if allowed, else the number of
    seconds until the next token — the Retry-After value."""

    capacity: float = 5.0          # burst allowance
    refill_per_sec: float = 1.0    # sustained rate
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def check(self, key: str) -> int | None:
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = _Bucket(self.capacity, now)
        bucket.tokens = min(
            self.capacity, bucket.tokens + (now - bucket.updated) * self.refill_per_sec
        )
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None                                   # allowed
        return math.ceil((1.0 - bucket.tokens) / self.refill_per_sec)


def client_key(ctx: Ctx) -> str:
    # Key by the caller. Behind a trusted proxy, the left-most X-Forwarded-For hop;
    # otherwise fall back to something you *do* trust (see the note on keys below).
    forwarded = ctx.request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else "anon"


class Login(Resource):
    ALLOWED_METHODS = frozenset({"POST"})

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    async def within_rate_limit(self, ctx: Ctx) -> bool | int:
        retry_after = self._limiter.check(client_key(ctx))
        return True if retry_after is None else retry_after   # int -> 429 + Retry-After

    async def process_post(self, ctx: Ctx) -> object:
        ...  # verify credentials, mint a session — reached only within the limit
        return {"token": "…"}
```

Wire the limiter at the composition root and share the one instance — its buckets
*are* the state:

```python
from asgimachine.substrate.starlette import build_app, resource_route

limiter = RateLimiter(capacity=3.0, refill_per_sec=1.0)   # burst 3, then 1/sec
app = build_app([resource_route("/login", Login(limiter))])
```

A burst from one caller drains the bucket; a different caller is untouched:

```
$ for i in $(seq 5); do
    curl -s -o /dev/null -w '%{http_code} %header{retry-after}\n' \
      -X POST localhost:8000/login -H 'x-forwarded-for: 1.2.3.4'
  done
200
200
200
429 1
429 1
```

## `429` vs `503`

`within_rate_limit` is the sibling of `service_available` (**B13**), one node earlier.
Same return shape — `bool` or a `Retry-After` hint — but a different meaning, and the
graph gives each its own status:

- **`503`** (`service_available`) — *the service* can't take this right now, for
  anyone: a maintenance window, shedding load under overload.
- **`429`** (`within_rate_limit`) — *this client* is over its own quota; everyone else
  is fine.

Both carry `Retry-After` (RFC 9110 §10.2.3), so a well-behaved client backs off either
way — but returning `503` to a rate-limited user would tell your monitoring the service
is *down* when it isn't. Reach for the node that matches the reason.

Because it's an additive node, `B13a` lands in `X-Asgimachine-Trace` **only when it
fires** — a within-limit request's trace is unchanged:

```
X-Asgimachine-Trace: B13,B13a          # the 429 path
```

## Choosing the key

`client_key` decides *who* a bucket belongs to, and it's the part that actually
matters. B13a runs before authentication, so the principal isn't known yet — key on
what's in the request:

- **By IP** — the default above. Only trust `X-Forwarded-For` behind a proxy you
  control that *overwrites* it; a raw client can forge the header, so never key on it
  when directly exposed. Fall back to the ASGI peer address otherwise.
- **By username** — for login, throttling a specific account needs the *submitted*
  username, which lives in the body (read after B13a). Do that second check in
  `process_post` — an IP bucket at B13a sheds the flood; a per-username counter inside
  the handler stops a slow drip against one account.
- **By credential** — for token endpoints, key on the API key or client id.

## Scope: this bucket is per-process

`RateLimiter` holds its state in a dict, so each worker process has its own buckets —
run four workers and the effective limit is roughly four times what you set. That's
fine for a single instance and dead simple. When you scale out, swap the dict for a
shared store (Redis's `INCR`/`EXPIRE`, or a sliding-window script): the seam is the
same — `check(key) -> int | None` — so only `RateLimiter` changes, not `Login`.

!!! tip "Rate-limit the endpoints that need it, not everything"
    `within_rate_limit` defaults to *no limit*, so it costs nothing until you override
    it. Add it to the routes an attacker abuses — login, token issuance, password
    reset, anything that gates a secret or does expensive work — and leave the rest
    alone. It's a per-resource callback precisely so you can be selective. See
    [Build, model, or rent](../concepts/build-model-rent.md) for why this isn't a
    framework-wide middleware.

## See also

- [Authorization](authorization.md) — the `is_authorized` / `forbidden` callbacks the
  limited endpoint sits in front of.
- [Return a specific outcome](response-outcomes.md) — the `503` / `429` gates and the
  full outcome map.
- [Coverage vs. webmachine](../concepts/webmachine-coverage.md) — B13a among the
  additive nodes beyond the canonical v3 graph.
