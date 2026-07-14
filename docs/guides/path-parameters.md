# Read a path parameter

**Goal:** a resource at `/notes/{id}` that reads `id` from the URL and serves that
one note — with a `404` for free when it doesn't exist.

Put placeholders in the route, and read them from `ctx.request.path_params` in any
callback.

```python
# notes.py
from dataclasses import dataclass

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route


@dataclass(slots=True)
class NoteCtx(Ctx):
    note: str | None = None          # loaded once, in resource_exists


class Note(Resource[NoteCtx]):
    context_class = NoteCtx

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    async def resource_exists(self, ctx: NoteCtx) -> bool:
        note_id = ctx.request.path_params["id"]     # <- the path argument
        ctx.note = self._store.get(note_id)
        return ctx.note is not None                 # False -> 404, for free

    async def represent(self, ctx: NoteCtx) -> object:
        return {"id": ctx.request.path_params["id"], "text": ctx.note}


app = build_app([resource_route("/notes/{id}", Note({"1": "hello"}))])
```

```
$ curl -s localhost:8000/notes/1
{"id":"1","text":"hello"}

$ curl -s localhost:8000/notes/999
{"type":"about:blank","title":"Not Found","status":404}
```

## The two parts

- **Declare** the parameter in the route with `{name}`:
  `resource_route("/notes/{id}", ...)`. This is Starlette's routing syntax — the
  graph doesn't parse URLs, the substrate does.
- **Read** it anywhere via `ctx.request.path_params["id"]` — in `resource_exists`,
  `is_authorized`, `apply`, `represent`, any callback. It's the same `HttpRequest`
  the whole walk shares.

## The idiomatic pattern: load once, 404 for free

The example does the load in `resource_exists` and stashes the note on a typed
[`Ctx`](../concepts/resources.md), rather than re-fetching in every method. That's
the asgimachine way: `resource_exists` returning `False` **is** the `404` — you
never write `if note is None: return 404`. And because the note is on `ctx`,
`represent` (and `generate_etag`, `apply`, …) just use it.

## Typed parameters

The route can convert the value for you (Starlette's converters). Note that
`path_params` is declared `Mapping[str, str]`, but a converter puts the *converted
type* in it:

| Route | `path_params` value | Python type |
|---|---|---|
| `/widgets/{n}` | `"7"` | `str` |
| `/widgets/{n:int}` | `7` | `int` |
| `/prices/{p:float}` | `9.99` | `float` |
| `/things/{u:uuid}` | `UUID(...)` | `uuid.UUID` |
| `/files/{p:path}` | `"a/b/c.txt"` | `str` (captures `/`) |

Use `{p:path}` when the value itself contains slashes; plain `{p}` stops at the
next `/`.

!!! tip "Custom converters for your own id types"
    The built-in converters aren't the limit. Starlette lets you **register your own**
    with `register_url_convertor` (see
    [its path-parameters docs](https://starlette.dev/routing/#path-parameters) for how
    to write one), and asgimachine's routes use it unchanged — handy for opaque,
    encoded identifiers, such as the base62 ids from the `resource-id` package. Once a
    converter named, say, `rid` is registered:

    ```python
    resource_route("/notes/{id:rid}", Note(store))
    ```

    `path_params["id"]` arrives already decoded into your id type. Put the validation
    in the converter's `regex`, and a malformed segment isn't routed here at all — a
    **404 at routing, before the graph runs** — so `resource_exists` only ever sees a
    well-formed id.

## Several parameters

Name each one; they all land in `path_params`:

```python
resource_route("/users/{user_id}/notes/{note_id}", NoteInUser(store))
```

```python
async def resource_exists(self, ctx: NoteCtx) -> bool:
    user_id = ctx.request.path_params["user_id"]
    note_id = ctx.request.path_params["note_id"]
    ...
```

!!! note "Path parameters vs. the query string"
    `path_params` is only the `{…}` segments of the *route*. Query-string arguments
    (`?limit=20`) are a different input, read from `ctx.request.query_params` — see
    [Read a query argument](query-string.md).
