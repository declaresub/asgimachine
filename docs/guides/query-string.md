# Read a query argument

**Goal:** read `?q=note&limit=20` in a resource — for search, filtering, or
pagination.

The parsed query string is on `ctx.request.query_params`, a mapping you read in any
callback.

```python
# search.py
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route

_NOTES = [f"note {i}" for i in range(1, 51)]


class Search(Resource):
    async def represent(self, ctx: Ctx) -> object:
        q = ctx.request.query_params
        term = q.get("q", "")
        limit = int(q.get("limit", "10"))          # values are strings — coerce
        hits = [n for n in _NOTES if term in n][:limit]
        return {"query": term, "count": len(hits), "results": hits}


app = build_app([resource_route("/search", Search())])
```

```
$ curl -s 'localhost:8000/search?q=note%201&limit=3'
{"query":"note 1","count":3,"results":["note 1","note 10","note 11"]}

$ curl -s localhost:8000/search
{"query":"","count":10,"results":["note 1","note 2","note 3", ...]}
```

## Things to know

- **Values are strings.** `query_params.get("limit")` is `"20"`, not `20` — coerce
  and validate it yourself (a bad `?limit=abc` would raise, so guard it or clamp to a
  default).
- **Absent is empty.** No query string means an empty mapping, so `.get(key,
  default)` is the idiom.
- **Repeated keys.** `?tag=a&tag=b` yields the *last* value (`"b"`) through the
  mapping. To read all of them, use the substrate's concrete `getlist`:

    ```python
    tags = ctx.request.query_params.getlist("tag")   # ["a", "b"] (Starlette)
    ```

## Query arguments vs. path parameters

Two different inputs, two different accessors:

| | Where | Accessor | Routed on? |
|---|---|---|---|
| **Path parameter** | a `{…}` segment of the route | `path_params["id"]` | yes — a bad one is a 404 |
| **Query argument** | after the `?` | `query_params["q"]` | no — the graph ignores it |

Because the query string is part of the URL, a different query is already a
different cache key — you don't add it to `Vary` the way you would a negotiated
header. See [Read a path parameter](path-parameters.md) for the routed side.

!!! note "Not in the wide event"
    Query arguments are *not* recorded in the [wide event](../concepts/observability.md)
    by default — query strings routinely carry tokens and PII, so logging them
    wholesale is a leak. Add the specific field you want (`ctx.event["search.term"] =
    term`) if it's safe.
