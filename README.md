# asgimachine

**webmachine for Python** — an HTTP decision-graph framework for resources that are
correct by construction.

You write a **resource** as a handful of small `async` callbacks — `is_authorized`,
`resource_exists`, `generate_etag`, `represent` — and asgimachine walks the
[webmachine v3 decision graph](https://raw.githubusercontent.com/wiki/basho/webmachine/images/http-headers-status-v3.png)
over them. Content negotiation, conditional requests (ETag / `If-None-Match` /
`If-Modified-Since`), `405 + Allow`, `406`, `304`, `Vary`, caching headers, and the
POST/PUT/PATCH/DELETE write path all fall out of the graph — each with a correct
HTTP default. You override only the callbacks your resource actually cares about.

**This is a specialist tool, not a general web framework.** It optimizes for the one
thing FastAPI, Flask, and DRF leave to you: getting HTTP semantics *right* — the
strong-vs-weak ETag comparison, the `Vary` you forgot, the `304` where you'd have
shipped a `200`. Reach for it when correctness at the HTTP layer is the point:
public REST APIs, CDN-cacheable content, feeds and outboxes, conditional-request-
heavy services. If you want the shortest path from idea to a JSON CRUD endpoint, or
a batteries-included stack with an ORM and auth, use FastAPI or Django —
asgimachine has none of that by design. It **owns the decision graph and rents
everything else** (routing, the server, middleware) from
[Starlette](https://www.starlette.io/), so it composes with that ecosystem instead
of replacing it.

> **Status:** experimental. Requires **Python 3.13+** (it uses PEP 695 generics and
> PEP 696 type-parameter defaults). The decision graph implements the v0–v3 subset
> of webmachine; see [PLAN.md](PLAN.md) for the design and roadmap.

## Design in one breath

- **Own Layer 1, rent Layer 2.** asgimachine owns the decision graph and the
  resource conventions; it rents everything else (routing, the server, CORS,
  middleware) from [Starlette](https://www.starlette.io/). The core is provably
  Starlette-free — the substrate lives behind a single adapter module, so the
  graph could sit on another ASGI substrate unchanged.
- **Correctness by construction.** The right HTTP behavior is the *default*, not
  something you remember to add. A resource that overrides only `represent` already
  answers HEAD, OPTIONS, 405, 406, and 501 correctly.
- **Two lanes, no cosplay.** Resource-shaped endpoints walk the graph; genuinely
  command-shaped endpoints (a token exchange, a webhook receiver) use a plain
  `Command` handler instead of being forced through a graph that does nothing for
  them.
- **Parse, don't validate.** A request body is parsed into a typed model at the
  boundary (a bad body is a `400`); your write handler receives a value it can
  trust.

## Install

Not yet on PyPI. With [uv](https://docs.astral.sh/uv/):

```bash
uv add "git+https://github.com/declaresub/asgimachine"
# or: pip install "git+https://github.com/declaresub/asgimachine"
```

## Quickstart

```python
# hello.py
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

That handful of callbacks already gets you the full HTTP contract:

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

## What you override

Every callback is `async def (self, ctx) -> ...` with a correct default. The ones
you reach for most:

| Callback | Controls | Default outcome |
|---|---|---|
| `service_available` | availability | `503` when `False` |
| `allowed_methods` / `ALLOWED_METHODS` | permitted methods | `405 + Allow` |
| `is_authorized` | authentication | `401` (+ `WWW-Authenticate`) |
| `forbidden` | authorization | `403` |
| `resource_exists` | existence | `404` (or the create/redirect branch) |
| `generate_etag` / `last_modified` | validators | `304` / `412` on conditional requests |
| `PRODUCES` + `represent` | the GET/HEAD body | `406` on a failed negotiation |
| `CONSUMES` + `apply` | the write body | `415` / `400`; `201`/`200`/`204` |
| `cache_control` / `expires` | caching | emitted on cacheable responses |
| `lifespan` | per-request setup/teardown | acquire a DB connection, released on every exit |

Domain state lives on a `Ctx` subclass you declare via `context_class`; resources
hold only their wired collaborators (no dependency-injection framework — you pass
collaborators to `__init__`).

## The command lane

Not everything is a resource. A credential exchange or webhook receiver is a
`Command` — a plain request→response handler that skips the graph:

```python
from asgimachine.command import Command, json_response
from asgimachine.substrate.starlette import build_app, command_route

class Token(Command):
    async def handle(self, request):
        ...
        return json_response({"token": ...}, status=201)

app = build_app([command_route("/token", Token())])
```

## More

- **OpenAPI.** `generate_openapi(...)` emits an OpenAPI 3.1 document from your
  resources — the error surface (401/403/404/406/415/…) is *derived from which
  callbacks you overrode*, and models are hoisted into `components.schemas`.
- **Streaming / SSE.** `represent` (or `process_post`) may return an async iterator
  of bytes; the graph decides status and headers, then streams the body.
- **Decision trace.** In debug mode every response carries an
  `X-Asgimachine-Trace` header listing the exact node path it walked — the graph
  explains itself.

## Examples

Runnable apps in [`examples/`](examples/) (`uvicorn examples.<name>:app --reload`):

| Example | Shows |
|---|---|
| `accounts` | a pure-read resource with conditional GET |
| `feed` | immutable, CDN-cacheable feed pages (the outbox case) |
| `events` | Server-Sent Events streaming through the graph |
| `connection` | per-request DB connection via `lifespan` |
| `notes_app` | the full gradient: collection + member resources, the command lane, an auth policy, and self-served OpenAPI |

## Development

```bash
uv sync
uv run pytest             # test suite
uv run pyright            # strict on src + examples
uv run ruff check .       # lint
uv run ruff format --check .  # style
```

Enable the pre-push gate once per clone — it runs all four checks (the same as
CI) before a push leaves your machine:

```bash
git config core.hooksPath .githooks
```

Bypass a single push with `git push --no-verify`.

## License

MIT © Charles Yeomans
