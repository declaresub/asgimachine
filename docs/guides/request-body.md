# Accept a request body

**Goal:** a `POST` (or `PUT`/`PATCH`) that takes a JSON body and hands your code a
*typed, validated* object — with a `400` for a bad body, a `415` for the wrong
content type, and a `413` for an oversized one, all for free.

Three pieces do it: declare **`CONSUMES`**, define a **model**, and annotate
**`apply`**'s `body` parameter with that model.

```python
# widgets.py
from pydantic import BaseModel

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route


class WidgetInput(BaseModel):          # 1. the model
    name: str
    quantity: int = 1


class Widgets(Resource):
    ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
    CONSUMES = ("application/json",)   # 2. the content types you accept

    def __init__(self, store: dict[str, dict]) -> None:
        self._store = store

    async def post_is_create(self, ctx: Ctx) -> bool:
        return True

    async def create_path(self, ctx: Ctx) -> str:
        ctx.extra["id"] = str(len(self._store) + 1)
        return f"/widgets/{ctx.extra['id']}"

    # 3. annotate `body` with the model — the framework reads, decodes, and
    #    validates the request into a WidgetInput before calling you.
    async def apply(self, ctx: Ctx, body: WidgetInput) -> object:
        wid = ctx.extra["id"]
        self._store[wid] = {"name": body.name, "quantity": body.quantity}
        return {"id": wid, **self._store[wid]}

    async def represent(self, ctx: Ctx) -> object:
        return {"widgets": self._store}


app = build_app([resource_route("/widgets", Widgets({}))])
```

```
$ pip install pydantic     # the example's model library — optional (see below)
$ uvicorn widgets:app
```

That's the whole thing. `apply` receives a `WidgetInput` you can trust — `body.name`
is a `str`, `body.quantity` is an `int` with its default applied.

## What each piece does

- **`CONSUMES = ("application/json",)`** — the media types this resource accepts on
  a write. A request whose `Content-Type` isn't one of them is a **`415`** before
  your code runs. (Leave it empty and any content type is accepted.)
- **`WidgetInput`** — the **model**: the shape the body must have. Any Pydantic
  `BaseModel` works. asgimachine does not depend on Pydantic — see
  [Other models](#other-models) below.
- **`apply(self, ctx, body: WidgetInput)`** — the **reader** is the annotation. The
  framework reads `apply`'s `body` type hint and parses the decoded body into it. A
  loosely-typed `body: dict` skips validation and hands you the raw structure.

## What you get for free

You wrote no parsing, no validation, no error handling — yet:

| Situation | Response | Why |
|---|---|---|
| valid body | `201` + `Location` + the created widget | the happy path |
| malformed JSON | `400` | the codec can't decode it |
| valid JSON, wrong shape (`name` missing, `quantity` a string) | `400` | `model_validate` rejects it |
| `Content-Type: text/plain` | `415` | not in `CONSUMES` |
| body over `MAX_BODY_BYTES` (default 1 MiB) | `413` | the size cap |

This is *parse, don't validate*: because the body is parsed into `WidgetInput` at
the boundary, your `apply` never sees a malformed one — so you don't write (and
can't forget) a `malformed_request` check.

```
$ curl -isX POST localhost:8000/widgets -H content-type:application/json -d '{"name":"cog","quantity":3}'
HTTP/1.1 201 Created
location: /widgets/1
content-type: application/json
{"id":"1","name":"cog","quantity":3}

$ curl -sX POST localhost:8000/widgets -H content-type:application/json -d '{"quantity":3}'
{"type":"about:blank","title":"Bad Request","status":400}

$ curl -sX POST localhost:8000/widgets -H content-type:text/plain -d 'cog'
{"type":"about:blank","title":"Unsupported Media Type","status":415}
```

## How the reader works

The read happens in two steps, both replaceable:

1. **Decode** — a media-type-keyed [`Codec`](../concepts/negotiation.md) turns the
   raw bytes into a structure. The default registry is JSON (`bytes` → `dict`).
2. **Parse** — the framework looks at `apply`'s `body` annotation and, if the type
   has a `model_validate` classmethod, calls it (`dict` → `WidgetInput`). This is
   the node **P0** in the [trace](../concepts/decision-graph.md); a failure at
   either step is a `400`.

The parse is duck-typed on `model_validate`, which is why Pydantic isn't required.

## Variations

### The raw structure, no validation

Annotate `body` loosely to receive exactly what the codec decoded:

```python
async def apply(self, ctx: Ctx, body: dict) -> object:
    ...  # body is the decoded JSON dict, unvalidated
```

### Other models

Any type with a `model_validate(data)` classmethod is a valid model —
`msgspec.Struct` (via a small shim), an `attrs` class with a validator, or your own:

```python
class WidgetInput:
    def __init__(self, name: str, quantity: int = 1) -> None:
        self.name, self.quantity = name, quantity

    @classmethod
    def model_validate(cls, data: object) -> "WidgetInput":
        if not isinstance(data, dict) or "name" not in data:
            raise ValueError("name is required")   # -> 400
        return cls(**data)
```

### PUT / PATCH instead of POST

The same `apply` handles them — add the method to `ALLOWED_METHODS` and the body is
parsed identically. (A `POST` that's an *action* rather than a create uses
`process_post`, which reads the body itself; the typed-body reader is the `apply`
path — create and update.)

### A non-JSON body

Register a [`Codec`](../concepts/negotiation.md) for the media type at the
composition root and add it to `CONSUMES`; the decode step then uses it.

### Cap the size

`MAX_BODY_BYTES` (default 1 MiB) bounds the read; raise it for an upload resource. An
over-cap declared `Content-Length` — or a chunked body that trips the running total
— is a `413`.
