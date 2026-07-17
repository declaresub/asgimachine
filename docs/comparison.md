# vs other frameworks

Honest positioning, because it's the fastest way to save you time. asgimachine sits at
a different point from the mainstream Python web frameworks: it owns **HTTP semantics
as defaults** and rents everything else from [Starlette](https://www.starlette.io/).
It is not trying to be a general-purpose framework, and for most of the axes below the
right answer is "use the other one." That's the point of the page.

If you haven't yet decided the *shape* of your app fits, read [When to use
it](when-to-use.md) first — this page assumes it does and asks *which* tool.

## At a glance

| Axis | asgimachine | FastAPI | Flask | Django |
|---|---|---|---|---|
| **HTTP semantics** (caching, conditional requests, negotiation, `405`/`406`/`412`/`428`) | **Correct by construction** | Hand-wired per route | Hand-wired per route | Hand-wired per view |
| Model | Webmachine decision graph | Path operations (RPC-ish) | View functions | Views + ORM + templates |
| Async | Async-only | Async-native | Sync-first (async in a threadpool) | Sync-first (async growing) |
| Request validation | Parse into a typed model at the boundary | Pydantic, first-class | Manual / extensions | Forms / DRF serializers |
| OpenAPI | Derived from behavior | Derived from signatures | Extension | Extension (DRF) |
| Routing / server / middleware | Rented from Starlette | Rented from Starlette | Werkzeug, built-in | Built-in |
| ORM / migrations / admin | **None** | None (bring your own) | None (bring your own) | **Batteries included** |
| Templating / sessions / auth | Rent from Starlette; write your own | Bring your own | Jinja + extensions | Built-in |
| Ecosystem / maturity | **Experimental, tiny** | Large, mature | Very large, very mature | Very large, very mature |

The single row that justifies asgimachine's existence is the first one. Everything
below it, the incumbents do as well or better. (The table stays at the four most
widely recognized frameworks; **Litestar** and **Falcon** — two more that regularly
come up — are covered in the prose below.)

## vs FastAPI — where most arrive from

FastAPI is where most people arrive from, and the honest comparison is narrow. Both
are async and build on Starlette; both generate OpenAPI; both lean on typed models at
the boundary (asgimachine uses Pydantic only in examples — it isn't a core
dependency).

The difference is **what's a default versus what you add**. FastAPI is schema-first
and RPC-friendly: you write a path operation, and HTTP semantics — `ETag`,
`If-None-Match`, `304`, content negotiation beyond the response model, `405 + Allow`,
optimistic concurrency — are things you reach for and wire by hand, correctly, on
every route that needs them. asgimachine makes those the default and derives the
OpenAPI document *from the behavior* rather than from the function signature.

- **Use FastAPI if** you want a huge ecosystem, first-class dependency injection
  (`Depends`), a proven production track record, and endpoints that are comfortably
  RPC-shaped. It is the safe, capable default and will not surprise you.
- **Use asgimachine if** HTTP correctness-by-construction is the actual goal —
  cache-heavy or conditional-request-heavy resource APIs, hypermedia frontends — and
  you'd rather not re-earn `304`/`Vary`/`412` on each endpoint.

The same positioning applies to **Litestar** (formerly Starlite), FastAPI's
fast-growing modern alternative: async-native, typed (msgspec / Pydantic),
OpenAPI-generating, and *more* batteries-included than FastAPI — built-in dependency
injection, a SQLAlchemy plugin, auth guards. It's controller/handler-shaped rather
than resource-graph-shaped, so asgimachine relates to it exactly as it does to
FastAPI: the HTTP-semantics-by-default axis is the whole difference, and Litestar's
richer batteries are one more reason to pick it when you want them.

For a large fraction of APIs, **FastAPI (or Litestar) is the right call.** asgimachine
wins on a specific axis; make sure that axis is your problem.

## vs Falcon — the closest in spirit

Falcon is the framework asgimachine most resembles *philosophically*: resource-
oriented, minimalist, fast, and deliberately un-batteried — no ORM, no admin, bring
your own everything. You write a resource class with responder methods (`on_get`,
`on_post`, …), which is a short step from asgimachine's resource-with-callbacks.

The difference is what happens *inside* those responders. Falcon hands you the resource
and the **hooks** — media handlers for (de)serialization, `req.if_match` / ETag
helpers, `resp.cache_control` — but the HTTP decision logic is yours to write: you
check the preconditions, run the negotiation, and choose the status code in each
responder, on each resource. asgimachine turns that logic into the graph, so the same
conditional-request, negotiation, and method handling are **defaults derived from your
callbacks**, not code you repeat per responder.

- **Use Falcon if** you want a mature, high-performance, resource-oriented
  microframework (WSGI *or* ASGI) and you're content owning the HTTP semantics
  explicitly — or you need synchronous operation, which asgimachine doesn't offer.
- **Use asgimachine if** you want those semantics correct by construction rather than
  hand-written per responder — the decision graph is exactly the part Falcon leaves to
  you.

Of the established frameworks, Falcon's users are the most likely to *recognize* what
asgimachine is doing: it's the same instinct — HTTP resources, no magic — carried one
layer further, into the graph.

## vs Flask — the microframework

Flask is a mature, sync-first microframework: minimal core, assemble the rest from a
deep bench of extensions. asgimachine is async-only and opinionated where Flask is
unopinionated.

- **Use Flask if** you want maximum flexibility, a vast ecosystem, synchronous code,
  and the freedom to structure the app however you like.
- **Use asgimachine if** you specifically want the decision graph and correct HTTP
  defaults, and async fits your stack. You're trading Flask's flexibility and maturity
  for opinionated correctness on one axis.

## vs Django — a different universe

Django is batteries-included: ORM, migrations, admin, templating, sessions, auth,
forms — a full stack for database-backed web applications, sync-first with async
support growing. There's barely an overlap to compare.

- **Use Django** (or Django + DRF for APIs) if you're building a database-backed app
  and want the admin, the ORM, and the ecosystem. This is the overwhelmingly common,
  sensible choice for that shape of project.
- **asgimachine has none of it** — no ORM, no migrations, no admin, no template
  engine, no built-in auth — and doesn't want it. If those are on your requirements
  list, this is the wrong tool, full stop.

## vs Starlette — not a competitor

asgimachine is *built on* Starlette; it rents routing, the ASGI server integration,
middleware, and background tasks from it. So this isn't "versus" — it's a layer above.

- **Use Starlette directly** if you want raw ASGI control and a thin toolkit, and the
  decision graph would only be in your way.
- **Use asgimachine** when you want that graph on top of Starlette. The core is
  provably Starlette-free (the substrate is one adapter module), so you keep Starlette's
  middleware ecosystem — `CORSMiddleware`, `SessionMiddleware`, and friends are rented,
  not reimplemented.

## What asgimachine deliberately lacks

Stated plainly, so nothing is a surprise:

- **No ORM, no migrations, no admin.** Bring your own data layer (the docs use raw
  asyncpg in the [database guide](guides/database-connection.md)).
- **No template engine or built-in auth.** You render HTML yourself if you want it
  (the [Unpoly example](examples.md) does), and you implement `is_authorized` /
  `forbidden` yourself (`asgimachine.auth` only *parses* the `Authorization` header).
- **No dependency-injection system.** Wiring is plain constructor injection at the
  composition root, plus typed per-request state on `Ctx`.
- **Experimental and small.** Python **3.13+** only, one maintainer, a young API that
  may still shift, and a niche mental model (webmachine) that has a learning curve.

None of these are on a roadmap to be filled in — they're the deliberate cost of
"[own Layer 1, rent Layer 2](concepts/build-model-rent.md)."

## The honest summary

**Pick asgimachine if** your resources are genuinely resources, HTTP semantics carry
weight, and you value correctness-by-construction over ecosystem size — and you can
live with an experimental, single-maintainer library.

**Pick something else if** you need batteries (Django), an RPC-shaped API with a proven
ecosystem (FastAPI), synchronous simplicity (Flask), or a stability and support
guarantee for a business-critical system. There's no shame in it — using the tool that
fits the grain of the work is the whole idea.
