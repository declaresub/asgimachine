# asgimachine

A **webmachine-style HTTP decision-graph framework** for Python.

You write a *resource* — a class of small `async` callbacks like `is_authorized`,
`resource_exists`, `generate_etag`, `represent` — and asgimachine walks the
webmachine v3 decision graph over them. Content negotiation, conditional requests
(ETag / `If-None-Match` / `If-Modified-Since`), `405 + Allow`, `406`, `304`,
caching headers, and the POST/PUT/PATCH/DELETE write path all fall out of the
graph. You override only the callbacks your resource actually cares about; every
one ships a correct HTTP default.

!!! warning "Status"
    Experimental. Requires **Python 3.13+** (PEP 695 generics, PEP 696
    type-parameter defaults). The graph implements the v0–v3 subset of webmachine
    plus a slice of RFC-completeness extensions.

## The design in one breath

- **Own Layer 1, rent Layer 2.** asgimachine owns the decision graph and the
  resource conventions; it rents routing, the server, CORS, and middleware from
  [Starlette](https://www.starlette.io/). The core is provably Starlette-free — the
  substrate lives behind one adapter module.
- **Correctness by construction.** The right HTTP behavior is the *default*, not
  something you remember to add. A resource that overrides only `represent`
  already answers HEAD, OPTIONS, 405, 406, and 501 correctly.
- **Two lanes, no cosplay.** Resource-shaped endpoints walk the graph;
  genuinely command-shaped endpoints (a token exchange, a webhook) use a plain
  `Command` handler instead of being forced through a graph that does nothing for
  them.
- **Parse, don't validate.** A request body is parsed into a typed model at the
  boundary (a bad body is a `400`); your write handler receives a value it can
  trust.

## A taste

```python
from dataclasses import dataclass

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route


@dataclass(slots=True)
class GreetCtx(Ctx):           # typed per-request state (optional)
    name: str = "world"


class Greeting(Resource[GreetCtx]):
    context_class = GreetCtx
    ALLOWED_METHODS = frozenset({"GET", "HEAD"})

    async def resource_exists(self, ctx: GreetCtx) -> bool:
        ctx.name = ctx.request.path_params.get("name", "world")
        return True

    async def generate_etag(self, ctx: GreetCtx) -> str | None:
        return f'"{ctx.name}"'

    async def represent(self, ctx: GreetCtx) -> object:
        return {"hello": ctx.name}


app = build_app([resource_route("/hello/{name}", Greeting())])
```

Those few callbacks already answer `200` + `ETag`, a conditional `304`, `405` with
an `Allow` header, and `406` on a failed negotiation — for free. See
[Quickstart](quickstart.md) to run it, then [Concepts](concepts/decision-graph.md)
for how the graph works.

!!! question "Is this the right tool for you?"
    asgimachine has a narrow grain and says so plainly. Before you invest, read
    [When to use it](when-to-use.md) (what fits and what doesn't) and
    [vs other frameworks](comparison.md) (a blunt comparison with FastAPI, Flask,
    and Django) — they'll tell you honestly when to reach for something else.
