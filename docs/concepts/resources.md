# Resources & callbacks

A resource is a plain class. Every callback is `async def callback(self, ctx) ->
…` with a correct default; you override only what your resource needs. The
resource instance holds only its wired collaborators (a store, an authenticator) —
**per-request state lives on `ctx`**, never on the shared instance.

## Declarations vs. callbacks

asgimachine transcribes the *graph* faithfully but breaks from webmachine's
"everything is a callback" convention in one deliberate way:

> **Static resource *shape* is a declaration; per-request *behavior* is a
> callback.** A thing that can't legitimately vary per request is a class
> attribute — the source of truth *and* the schema anchor — not something computed
> each time.

So the following are **declarations** (class attributes):

| Declaration | Meaning |
|---|---|
| `ALLOWED_METHODS: frozenset[str]` | permitted methods → `405 + Allow` |
| `PRODUCES: tuple[str, ...]` | offered media types → `406` |
| `CONSUMES: tuple[str, ...]` | accepted request media types (writes) |
| `MAX_BODY_BYTES: int` | request-body cap → `413` |
| `ERROR_PRODUCES: tuple[str, ...]` | error-body media types (RFC 9457) |
| `context_class: type[Ctx]` | the `Ctx` subclass the core constructs |

Where an override escape hatch is still worth keeping, the graph reads the
declaration through a *thin callback that defaults to it* — e.g. `allowed_methods(ctx)`
returns `ALLOWED_METHODS` unless you override it. The common case is a one-line
class attribute; per-request variation stays possible; the schema reads the
attribute statically.

## The callbacks you reach for

| Callback | Controls | Default outcome |
|---|---|---|
| `service_available` | availability | `503` when `False` |
| `is_authorized` | authentication | `401` (+ `WWW-Authenticate` if a `str`) |
| `forbidden` | authorization | `403` |
| `resource_exists` | existence | `404` / the create/redirect branch |
| `generate_etag` / `last_modified` | validators | `304` / `412` on conditionals |
| `represent` | the GET/HEAD body | encoded per `PRODUCES` |
| `apply` | the write body | `201`/`200`/`204`; `415`/`400` on a bad body |
| `is_conflict` | write conflict | `409` |
| `cache_control` / `expires` | caching | emitted on cacheable responses |
| `lifespan` | per-request setup/teardown | see [Lifespan](lifespan.md) |

The full surface is in the [API reference][asgimachine.resource.Resource].

## Typed per-request state: subclassing `Ctx`

Base [`Ctx`][asgimachine.resource.Ctx] is deliberately minimal and domain-agnostic
— the request, the decision trace, the negotiated media type, the codec registry,
and an `extra` bag. Domain state (a principal, the loaded entity) goes in a **`Ctx`
subclass**. A resource is generic over its context type and names the subclass via
`context_class`, which the core constructs per request:

```python
from dataclasses import dataclass, field
from asgimachine.resource import Ctx, Resource


@dataclass(slots=True)
class AccountsCtx(Ctx):                    # typed per-request state
    user: User | None = None
    accounts: list[Account] = field(default_factory=list)


class AccountsResource(Resource[AccountsCtx]):
    ALLOWED_METHODS = frozenset({"GET", "HEAD"})
    context_class = AccountsCtx

    async def is_authorized(self, ctx: AccountsCtx) -> bool | str:
        user = await self._authenticate(ctx)
        if user is None:
            return "Bearer"                # -> 401 WWW-Authenticate: Bearer
        ctx.user = user
        return True

    async def resource_exists(self, ctx: AccountsCtx) -> bool:
        assert ctx.user is not None        # set by is_authorized, which runs first
        ctx.accounts = await self._retrieve(ctx.user.id)
        return True

    async def generate_etag(self, ctx: AccountsCtx) -> str | None:
        return f'W/"accounts-{ctx.user.id}-{len(ctx.accounts)}"'

    async def represent(self, ctx: AccountsCtx) -> object:
        return {"data": [asdict(a) for a in ctx.accounts]}
```

Plain `Resource` (no type parameter) just uses base `Ctx`.

## No dependency injection

Collaborators are constructor arguments, wired at a composition root (your
app-builder function). Tests pass fakes to the constructor. There is no DI
container, no override registry, no request-scoped resolver — wiring is explicit
and lexical.
