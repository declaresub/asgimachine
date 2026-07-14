# Return a specific outcome

You rarely set a status code directly. You **signal intent through a callback**, and
the graph produces the status *and* the right headers — `Location`, `Allow`,
`Retry-After` — correct by construction. Here's the map, then how to reach each one.

| I want to return | How | Node |
|---|---|---|
| `200 OK` with a body | `represent` (GET), or `process_post` returns a value | O18/O20 |
| `201 Created` + `Location` | `post_is_create` → `True`, `create_path` → the URL | N11 |
| `204 No Content` | `apply` / `process_post` returns `None` | O20 |
| `202 Accepted` + `Location` | `accepted` → a status-monitor URL | O20a |
| `303 See Other` + `Location` | `see_other` → a URL (on a POST) | N11a |
| `301` / `308` / `307` + `Location` | `moved_permanently` / `permanent_redirect` / `moved_temporarily` | K5 / K5a / L5 |
| `410 Gone` | `previously_existed` → `True`, no redirect | M5 |
| `404` / `401` / `403` / `409` / `428` / … | the matching gate callback returns its "no" | — |
| anything the callbacks don't cover | `raise HaltResponse(...)` | — |

## Successful writes

`POST`/`PUT`/`PATCH` outcomes come from what the handler returns and a couple of
callbacks:

```python
class Widgets(Resource):
    ALLOWED_METHODS = frozenset({"POST"})
    CONSUMES = ("application/json",)

    def __init__(self, store: dict) -> None:
        self._store = store

    async def post_is_create(self, ctx: Ctx) -> bool:
        return True                              # -> 201

    async def create_path(self, ctx: Ctx) -> str:
        ctx.extra["id"] = str(len(self._store) + 1)
        return f"/widgets/{ctx.extra['id']}"     # -> the Location header

    async def apply(self, ctx: Ctx, body: dict) -> object:
        self._store[ctx.extra["id"]] = body
        return {"id": ctx.extra["id"], **body}   # a value -> 201 body; None -> 201 no body
```

```
$ curl -isX POST localhost:8000/widgets -H content-type:application/json -d '{"name":"cog"}'
HTTP/1.1 201 Created
location: /widgets/1
{"id":"1","name":"cog"}
```

- **`201 Created`** — set `post_is_create` and return the URL from `create_path`; the
  graph adds `Location`. `apply`'s return is the response body (or `None` for none).
- **`204 No Content`** — return `None` from `apply` (`PUT`/`PATCH`/create) or
  `process_post` (a `POST` *action*).
- **`200 OK`** — return a value from `process_post` (an action that isn't a create).

## Hand off asynchronously — `202`

When the work can't finish inside the request budget, enqueue it and point the
client at a status monitor (see [the deadline story](../concepts/webmachine-coverage.md)):

```python
async def accepted(self, ctx: Ctx) -> str | None:
    return f"/jobs/{job_id}"   # -> 202 Accepted + Location: /jobs/{job_id}
```

## Redirect after a POST — `303`

The POST-Redirect-Get pattern: run the side effect, then send the browser to the
result with `303 See Other` (empty body):

```python
async def see_other(self, ctx: Ctx) -> str | None:
    return f"/widgets/{ctx.extra['id']}"   # -> 303 See Other + Location
```

## A resource that moved — `301` / `308` / `307` / `410`

These live in the *missing-resource* branch: the resource doesn't exist **at this
URL** but says where it went. So `resource_exists` returns `False`, and:

```python
class OldWidget(Resource):
    async def resource_exists(self, ctx: Ctx) -> bool:
        return False                                       # not here

    async def previously_existed(self, ctx: Ctx) -> bool:
        return True                                        # ...but it did

    async def moved_permanently(self, ctx: Ctx) -> str | None:
        return f"/widgets/{ctx.request.path_params['id']}"  # -> 301 + Location
```

Swap the callback for the semantics you want: `permanent_redirect` → **308** (like
301 but method-preserving), `moved_temporarily` → **307**. With none of them,
`previously_existed = True` yields **410 Gone**.

!!! note "Redirecting a resource that *does* exist"
    These callbacks only fire for a missing resource. To redirect a *live* endpoint
    (say `/latest` → `/v2`), use the escape hatch below with a `307`/`308`.

## Full control — `HaltResponse`

For anything the callbacks don't model — a bespoke status, a redirect from a live
endpoint, a hand-built header set — raise a `HaltResponse` from any callback:

```python
from asgimachine.http import HaltResponse, HttpResponse

async def represent(self, ctx: Ctx) -> object:
    raise HaltResponse(
        HttpResponse(status=307, headers={"Location": "/v2"})
    )
```

Reach for this sparingly: the declarative callbacks set the correct headers for you
and route `4xx`/`5xx` through the negotiated
[`problem+json` error body](../concepts/negotiation.md);
a hand-rolled `HaltResponse` is the ad-hoc code the graph otherwise saves you.

## Error statuses

The `4xx` you *reject* with come from their gate callbacks, not a "return this
status" call — `is_authorized` → `401`, `forbidden` → `403`, `resource_exists` →
`404`, `is_conflict` → `409`, `require_conditional_write` → `428`. Each ships an
RFC 9457 `problem+json` body for free. See
[Negotiation & errors](../concepts/negotiation.md) and
[the flowchart](../concepts/flowchart.md) for the full set.
