# Build, model, or rent

asgimachine's slogan is *own Layer 1, rent Layer 2* — own the decision graph and
the resource conventions, rent routing, the server, and middleware from Starlette.
But a slogan isn't a criterion. This page is the criterion: given a concern, does
the framework **build** it in, let you **model** it as a resource property, or
**rent** it to middleware? The answer turns on one question — *can it be fully
handled at a single seam?*

## The sharper definition of "cross-cutting"

The usual reason given for building something into a framework is that it's a
"cross-cutting concern." But that phrase gets used for two different things, and
only one of them earns the name.

"Cross-cutting" is not about the *scope* of a concern (does it apply to every
request?) — it's about the *shape of its implementation*. A concern is cross-cutting
when implementing it forces you to **scatter** code across many points in the code,
tangled with their logic. If a single wrapper suffices, you touched exactly one
place: there's no scatter, so it was never cross-cutting — it just looked that way
because it applied everywhere.

!!! quote "The test"
    A concern is **cross-cutting** iff its full implementation cannot be localized
    to a single seam — it intrinsically needs participation at *multiple depths* of
    the call tree. **Fully middleware-able ⟹ not cross-cutting.**

The word *fully* matters. A concern can be *touched* by middleware while its
essential part lives at depth — distributed tracing starts a root span at the seam,
but the attributes and child spans that make it useful are set deep inside. That's
still cross-cutting, because the seam only captures the trivial part. Contrast a
request-id: generated at the seam, stamped on the response, done — its inner *use*
(logging it) is a different concern borrowing the value.

## The three buckets

=== "Rent"

    **Single-seam concerns.** Fully captured at the outer ASGI boundary — transform
    the request/response, or reject. Zero inner participation. asgimachine rents
    these to Starlette middleware and does nothing special.

    CORS · compression · security headers (HSTS/CSP) · global rate limiting ·
    request-id generation.

=== "Model"

    **Resource properties.** Not cross-cutting either — but they *vary by resource*,
    as a function of what's being served. They belong on the resource as a
    declaration or a callback, not in a wrapper. (Calling these "cross-cutting" is
    the classic mistake — a symptom of a framework with no resource model to put
    them on.)

    Method handling · authorization decisions · content negotiation · conditional
    requests / caching · per-resource rate limiting · availability (503) ·
    precondition-required (428) · the async 202 hand-off.

=== "Build"

    **Genuinely cross-cutting.** Their essential implementation *cannot* be
    localized — they need participation at many depths. The framework earns its keep
    here, and "building in" means one specific thing: **providing the depth-seams**
    that let code at every level participate.

    Observability · error reporting · transactions.

## Why these three are actually cross-cutting

Each one can be *started* at a seam but not *finished* there:

| Concern | Seam can do | ...but the essential part is at depth |
|---|---|---|
| **Observability** | method/path/status/duration | `halted_at`, the negotiated media type, `account.id`, the SQL count — set inside the walk, invisible to a middleware outside `ctx` |
| **Error reporting** | catch → 500 | co-locating the report id with the log, rolling back with the exception in flight, the negotiated `problem+json` — all need `ctx` |
| **Transactions** | begin / commit at the boundary | every query in between must use *that* connection, and rollback must know an error occurred |

A middleware sits *outside* the request's context, so it structurally can't reach
any of that. Building the concern in means giving the framework the seams that can:

- **`ctx`** — per-request state every callback and the substrate share.
- **`ctx.event`** — the wide-event accumulator any depth can enrich (a resource
  callback, an instrumented DB connection); see [Observability](observability.md).
- **The `lifespan`** — wraps the whole walk and carries the connection/transaction
  to every callback, with cancellation-safe, rollback-aware teardown; see
  [Per-request lifespan](lifespan.md).
- **`on_exception`** — the catch-all that runs *inside* the walk, so an error
  reporter and the wide event can share a report id.

## The payoff

The middleware-ability test *is* the design boundary — precise and non-arbitrary:

- **Fully single-seam** → rent it. The framework should do nothing; Starlette
  already has the seam.
- **Varies by resource** → model it as a declaration/callback. This is most of what
  the decision graph *is*.
- **Needs depth-participation** → build it, by providing the depth-seams.

And the reframe that falls out: the *genuinely* cross-cutting set is **small** —
observability, error handling, transactions. Nearly everything else that gets filed
under "cross-cutting" is really a single-seam concern (rent) or a mis-filed resource
property (model) — mis-filed by frameworks that lacked the seams to model it any
other way. asgimachine's job is to *have* those seams.
