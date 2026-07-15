# Serve OpenAPI

**Goal:** a live OpenAPI 3.1 document generated *from your resources* — no separate
spec file to write and keep in sync.

The trick is that the decision graph is already a schema of outcomes. You declare
only what the framework *can't* infer — the success bodies and summaries — and it
derives the rest (methods, the error surface, media types, path parameters, models)
from the resource's own callbacks. Override `forbidden`, and `403` shows up in the
document; there's nothing to keep in sync, because it's read from the behavior.

```python
from pydantic import BaseModel

from asgimachine.command import Command, json_response
from asgimachine.http import HttpRequest, HttpResponse
from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, command_route, resource_route


class WidgetIn(BaseModel):
    name: str
    quantity: int = 1


class Widgets(Resource):
    ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
    CONSUMES = ("application/json",)

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        return True  # (real check here) — overriding this puts 401 in the schema

    async def post_is_create(self, ctx: Ctx) -> bool:
        return True

    async def create_path(self, ctx: Ctx) -> str:
        return "/widgets/1"

    async def apply(self, ctx: Ctx, body: WidgetIn) -> None:
        ...

    async def represent(self, ctx: Ctx) -> object:
        return {"widgets": []}

    def describe(self) -> ResourceDescription:      # opt in; declare only successes
        return ResourceDescription(
            get=Operation(summary="List widgets", responses={200: {"type": "object"}}),
            post=Operation(summary="Create a widget", request=WidgetIn, responses={201: None}),
        )
```

Serve it from a plain [command](../concepts/two-lanes.md) — the OpenAPI endpoint
isn't a graph resource:

```python
class OpenApi(Command):
    def __init__(self, pairs: list[tuple[str, Resource]]) -> None:
        self._pairs = pairs

    async def handle(self, request: HttpRequest) -> HttpResponse:
        return json_response(
            generate_openapi(
                title="Widgets API",
                version="1.0.0",
                routes=self._pairs,
                security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}},
                security=["bearerAuth"],           # document-level default
            )
        )


pairs = [("/widgets", Widgets())]
app = build_app([
    resource_route("/widgets", Widgets()),
    command_route("/openapi.json", OpenApi(pairs), methods=["GET"]),
])
```

## What you get without declaring it

`describe()` above only names the `200` and `201` bodies. Look at what the generator
fills in from the resource's callbacks and declarations:

```
GET  /widgets  responses -> 200, 401, 406
POST /widgets  responses -> 201, 400, 401, 415
```

- **`401`** on both, because `is_authorized` is overridden.
- **`406`** on `GET`, because a representation is content-negotiated.
- **`400` and `415`** on `POST`, because `CONSUMES` is declared (a bad or wrong-typed
  body).
- The `POST` request body is `WidgetIn`, hoisted into `components.schemas` and
  referenced by `$ref` (shared models are emitted once).

The mapping, all derived — you never restate it:

| Override / declaration | Adds to the schema |
|---|---|
| `is_authorized` | `401` |
| `forbidden` | `403` |
| `is_legally_restricted` | `451` |
| `resource_exists` | `404` |
| `CONSUMES` | `415`, `400` |
| `generate_etag` / `last_modified` | `412`, and `304` on reads |
| `is_conflict` | `409` |
| `require_conditional_write` | `428` |
| `uri_too_long` | `414` |
| `ALLOWED_METHODS`, `PRODUCES` | the methods, the `406`, the media types |
| the route path | the `path` parameters |

So the document tracks behavior by construction: it can't claim a resource is public
when `is_authorized` says otherwise.

## Security

Pass `security_schemes` (→ `components.securitySchemes`) and a document-level
`security` default. Override it per operation with `Operation.security` — `[]` marks
an operation **public**:

```python
get=Operation(summary="Health", responses={200: {"type": "object"}}, security=[])
```

## Models

A Pydantic `BaseModel` (anything with `model_json_schema()`) is converted, hoisted
into `components.schemas`, and `$ref`-erenced — so a model shared across operations
or nested in another is emitted once. A raw JSON-Schema `dict` (like the `{"type":
"object"}` above) is used as-is, no Pydantic required.

## Adopt it incrementally

A resource that returns no `describe()` (the default) is simply **absent** from the
document — so you can add `describe()` one resource at a time without an
all-or-nothing cutover.

!!! note "The command lane isn't in the schema"
    `generate_openapi` documents graph resources (the `routes` you pass it).
    Command-lane endpoints — including the OpenAPI endpoint itself — aren't part of
    it; document those by hand if you need them in the spec.
