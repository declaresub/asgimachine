# Authorization

Two questions, two callbacks, two status codes:

- **Who are you?** — `is_authorized` (node B8). A failure is a **`401`**, optionally
  with a `WWW-Authenticate` challenge. This is *authentication*.
- **May you do this?** — `forbidden` (node B7). A failure is a **`403`**. This is
  *authorization*, of a known principal.

They run early — B8 then B7, both *before* `resource_exists` — so a request that
can't identify or isn't allowed is turned away before any work.

```python
from dataclasses import dataclass

from asgimachine.auth import bearer_token
from asgimachine.resource import Ctx, Resource


@dataclass(slots=True)
class DocCtx(Ctx):
    user: str | None = None
    role: str | None = None


class Doc(Resource[DocCtx]):
    context_class = DocCtx

    def __init__(self, users: dict[str, str], doc: dict) -> None:
        self._users, self._doc = users, doc

    async def is_authorized(self, ctx: DocCtx) -> bool | str:
        token = bearer_token(ctx.request.headers.get("authorization"))
        if token is None or token not in self._users:
            return "Bearer"                       # -> 401 + WWW-Authenticate: Bearer
        ctx.user, ctx.role = token, self._users[token]
        return True                               # authenticated

    async def forbidden(self, ctx: DocCtx) -> bool:
        return ctx.user != self._doc["owner"] and ctx.role != "admin"   # -> 403

    async def represent(self, ctx: DocCtx) -> object:
        return {"text": self._doc["text"]}
```

- `is_authorized` returns **`True`** (known), **`False`** (a bare `401`), or a
  **`str`** — the `WWW-Authenticate` challenge value, so a browser or client knows
  *how* to authenticate.
- `forbidden` returns **`True`** to deny (`403`), `False` to allow. It runs only
  after `is_authorized` passed, so `ctx.user` is set.

!!! tip "Parsing the `Authorization` header"
    `bearer_token` extracts and validates the token from `Authorization: Bearer …`
    (scheme case-insensitive, `token68` per RFC 6750) — don't hand-roll
    `removeprefix("Bearer ")`. Its siblings in `asgimachine.auth` are
    `basic_credentials` (Basic → `(user, password)`) and `parse_authorization` (the
    generic `(scheme, credentials)` split, for any scheme). They *parse*; verifying
    the credential is still your job.

```
$ curl -is localhost:8000/doc
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer

$ curl -is localhost:8000/doc -H 'authorization: Bearer bob'      # not the owner
HTTP/1.1 403 Forbidden

$ curl -is localhost:8000/doc -H 'authorization: Bearer alice'    # the owner
HTTP/1.1 200 OK
```

## A policy engine for richer rules

Hand-rolled boolean logic in `forbidden` gets unwieldy. asgimachine ships an
**ordered Allow/Deny rule engine** you delegate to — each rule fires `ALLOW`/`DENY`
or abstains (`None`); **first match wins**; the default is deny. The *decision itself*
is the graph's (B7 → 403); *how you decide* is this collaborator, wired at the
composition root.

```python
from asgimachine.policy import Effect, NamedRule, RuleEngine


async def admin(ctx: DocCtx) -> Effect | None:
    return Effect.ALLOW if ctx.role == "admin" else None       # admins pass


async def owner(ctx: DocCtx) -> Effect | None:
    return Effect.ALLOW if ctx.user == doc["owner"] else None  # so does the owner


policy = RuleEngine(
    [NamedRule("admin", admin), NamedRule("owner", owner)],
    default=Effect.DENY,                                        # everyone else: 403
)


class Doc(Resource[DocCtx]):
    def __init__(self, policy: RuleEngine[DocCtx]) -> None:
        self._policy = policy

    async def forbidden(self, ctx: DocCtx) -> bool:
        return not (await self._policy.evaluate(ctx)).allowed
```

The deciding rule lands in the [decision trace](../concepts/decision-graph.md), so
"which rule denied me" and "which node returned 403" are one story:

```
X-Asgimachine-Trace: B13,B12,B10,B8,policy:default,B7
```

## Load ownership state early

Authorization often depends on the *target* — "is this note's owner you?". But B7/B8
run **before** `resource_exists` (G7), so load the entity you authorize against in
`is_authorized`, not `resource_exists`:

```python
async def is_authorized(self, ctx: DocCtx) -> bool | str:
    ... # authenticate, set ctx.user
    ctx.note = await self._store.load(ctx.request.path_params["id"])   # for the policy
    return True
```

Then `forbidden` (and the policy) can see `ctx.note`, and `resource_exists` just
reports whether it was found.

## Should you cache parsed credentials?

Rarely — and it's worth knowing why, because the instinct is common.

- **Within a request, `ctx` is the cache.** Parse once in `is_authorized`, stash the
  principal on `ctx`, and every later callback reads it (the examples above do
  exactly this). There's nothing to re-parse.
- **Across requests, cache *verification*, not parsing.** The cost of auth is
  *checking* the credential — a session/DB lookup, a JWT signature verify — which
  dwarfs the header parse. An app that cares caches `token → principal`, and that
  cache already amortizes the parse to once per unique token: a hit skips both, a
  miss does both once.

The helpers are pure functions, so if a profile ever shows the *parse itself* is hot
(extreme RPS, cheap verification, long tokens), caching is one line:

```python
from functools import lru_cache

from asgimachine.auth import bearer_token

cached_bearer = lru_cache(maxsize=4096)(bearer_token)
```

asgimachine deliberately doesn't ship that cache. It would be a process-global store
holding **tokens** — secrets — with an unbounded-growth policy (a max size, an
eviction rule): decisions that belong to your app, not a framework default. And the
sharper lever, if the parse ever profiles hot, is a cheaper validation path — not a
cache that retains credentials.

!!! note "401 vs 403 vs 405"
    - **`401`** — *not authenticated*; the client should retry with credentials
      (hence the challenge).
    - **`403`** — *authenticated but not permitted*; retrying won't help.
    - **`405`** — the *method* isn't allowed on this resource at all — a property of
      the resource (`ALLOWED_METHODS`), not the principal. That's a different
      callback; see [Return a specific outcome](response-outcomes.md).
