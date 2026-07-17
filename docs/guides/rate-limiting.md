# Rate-limit an auth endpoint

**Goal:** cap how often a client may hit an endpoint — the classic case being a
login route you want to protect from credential-stuffing — and answer an over-limit
request with `429 Too Many Requests` + `Retry-After`.

There's no rate-limit *node* in the graph, and that's deliberate: rate-limiting is a
[collaborator you wire in](../concepts/build-model-rent.md), not a resource property
the graph decides. But it has a natural home. `service_available` (node **B13**) is
the **first** thing the graph evaluates — before authentication, before the body is
even read — so a limiter there sheds the flood at the cheapest possible point, before
a rejected request touches your database or your password hasher.

```python
import math
import time
from dataclasses import dataclass, field

from asgimachine.http import HaltResponse, HttpResponse
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

    async def service_available(self, ctx: Ctx) -> bool:
        retry_after = self._limiter.check(client_key(ctx))
        if retry_after is not None:
            raise HaltResponse(
                HttpResponse(status=429, headers={"Retry-After": str(retry_after)})
            )
        return True

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

## Why `429`, not `503`

`service_available` can *return* a `Retry-After` hint directly — an `int` or a
`datetime` — and the graph turns that into a **`503 Service Unavailable`** (see
[Return a specific outcome](response-outcomes.md)). So why raise `HaltResponse` for a
`429` instead?

Because the two statuses mean different things, and the distinction is worth keeping:

- **`503`** — *the service* can't take this right now, for anyone: a maintenance
  window, shedding load under overload. Return the hint from `service_available`; it's
  on-graph and lands in the trace as **B13**.
- **`429`** — *this client* is over its own quota; everyone else is fine. That's a
  per-caller verdict, so raise `HaltResponse` with a `429`.

Both carry `Retry-After` (RFC 9110 §10.2.3), so a well-behaved client backs off either
way — but returning `503` to a rate-limited user tells your monitoring the service is
*down* when it isn't. Reach for `429`.

!!! note "The 429 is an explicit halt, so it's off-graph"
    Raising `HaltResponse` short-circuits the walk before B13 is recorded, so a
    rate-limited request adds **no node** to `X-Asgimachine-Trace` — it never became a
    graph decision. That's the trade for a status the graph doesn't model. If you'd
    rather stay on-graph, the `503` path above is fully traced.

## Choosing the key

`client_key` decides *who* a bucket belongs to, and it's the part that actually
matters:

- **By IP** — the default above. Only trust `X-Forwarded-For` behind a proxy you
  control that *overwrites* it; a raw client can forge the header, so never key on it
  when directly exposed. Fall back to the ASGI peer address otherwise.
- **By username** — for login, keying on the *submitted* username (once the body is
  read) throttles an attacker working one account, regardless of IP. You can combine
  both: an IP bucket at B13, plus a per-username check inside `process_post`.
- **By credential** — for token endpoints, key on the API key or client id.

## Scope: this bucket is per-process

`RateLimiter` holds its state in a dict, so each worker process has its own buckets —
run four workers and the effective limit is roughly four times what you set. That's
fine for a single instance and dead simple. When you scale out, swap the dict for a
shared store (Redis's `INCR`/`EXPIRE`, or a sliding-window script): the seam is the
same — `check(key) -> int | None` — so only `RateLimiter` changes, not `Login`.

!!! tip "Rate-limit the endpoints that need it, not everything"
    Rate-limiting belongs on the routes an attacker abuses — login, token issuance,
    password reset, anything that gates a secret or does expensive work. Give those
    resources a `service_available` check; leave the rest alone. It's a per-resource
    collaborator precisely so you can be selective. See
    [Build, model, or rent](../concepts/build-model-rent.md) for why this isn't a
    framework-wide middleware.

## See also

- [Authorization](authorization.md) — the `is_authorized` / `forbidden` callbacks the
  limited endpoint sits in front of.
- [Return a specific outcome](response-outcomes.md) — the `503` + `Retry-After` path,
  and the full outcome map.
