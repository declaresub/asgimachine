# The two lanes

Not every endpoint is a resource. asgimachine has two first-class lanes that share
the same Starlette substrate:

- **The resource lane** — walks the decision graph. For anything with a URL that
  names a *thing* you GET/PUT/DELETE, where conditional requests, negotiation, and
  method handling are genuinely useful.
- **The command lane** — a plain request→response handler that *skips* the graph.
  For genuinely command-shaped endpoints: a credential exchange, a webhook
  receiver, an RPC-ish action.

## No cosplay

The tell that an endpoint belongs in the command lane: you find yourself inventing
an unaddressable noun and faking `resource_exists` / `represent` to satisfy the
graph model. Don't. Let commands be commands.

## A command

A [`Command`][asgimachine.command.Command] implements one method:

```python
from asgimachine.command import Command, json_response
from asgimachine.substrate.starlette import build_app, command_route


class Token(Command):
    def __init__(self, store):
        self._store = store

    async def handle(self, request):
        body = json.loads(await request.body())
        if not self._store.verify(body["username"], body["password"]):
            return json_response({"error": "invalid credentials"}, status=401)
        return json_response({"token": self._store.issue(body["username"])}, status=201)


app = build_app([command_route("/token", Token(store))])
```

Because a command doesn't walk the graph, the *router* owns method restriction
(a `405` for an unlisted method) — which is fine for a command-shaped endpoint.
The request-body cap still applies: `command_route(..., max_body_bytes=…)` bounds
the read, and an over-cap body is a `413`.

## Mixing them

A single app freely composes both:

```python
app = build_app([
    resource_route("/notes", NotesCollection(store)),
    resource_route("/notes/{id}", NoteMember(store, policy)),
    command_route("/token", Token(store)),
])
```

See the `notes_app` [example](../examples.md) for the full gradient — public
resource, simple-auth collection, policy-governed member, plus the command lane —
coexisting without cosplay.
