# Quickstart

## Install

Not yet on PyPI. With [uv](https://docs.astral.sh/uv/):

```bash
uv add "git+https://github.com/declaresub/asgimachine"
# or: pip install "git+https://github.com/declaresub/asgimachine"
```

asgimachine requires **Python 3.13+**.

## A first resource

```python title="hello.py"
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

```bash
uvicorn hello:app
```

## What you get for free

That handful of callbacks already gets you the full HTTP contract — none of it
hand-written:

```console
$ curl -isS localhost:8000/hello/charles
HTTP/1.1 200 OK
etag: "charles"
content-type: application/json
{"hello": "charles"}

$ curl -isS localhost:8000/hello/charles -H 'If-None-Match: "charles"'
HTTP/1.1 304 Not Modified          # conditional GET, for free

$ curl -isS -X POST localhost:8000/hello/charles
HTTP/1.1 405 Method Not Allowed
allow: GET, HEAD, OPTIONS          # the Allow header, for free

$ curl -isS localhost:8000/hello/charles -H 'Accept: text/csv'
HTTP/1.1 406 Not Acceptable        # content negotiation, for free
```

## Where to go next

- **[The decision graph](concepts/decision-graph.md)** — how the walk works and
  why the defaults are correct.
- **[Resources & callbacks](concepts/resources.md)** — the full callback surface
  and the declaration-vs-callback rule.
- **[The two lanes](concepts/two-lanes.md)** — when to use a `Command` instead.
- **[Examples](examples.md)** — runnable apps across the resource gradient.
