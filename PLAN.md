# asgimachine — implementation plan

A webmachine-style web framework for Python, built as a decision core that
walks the HTTP decision diagram and dispatches to explicit resource callbacks.
It sits on top of Starlette (for now — see §2.6) and is deliberately *not* a
dependency-injection framework.

> **Design tenets.** asgimachine is a standalone, general-purpose framework. It
> is designed and validated on its own terms, against its own example app and
> conformance suite — not shaped to any particular application. Four constraints
> are load-bearing throughout this plan:
> - **Rent Layer 2, own Layer 1.** ASGI plumbing, routing, streaming, and the
>   test client are commodity, battle-tested, and easy to get subtly wrong.
>   Rent them from Starlette. Own only the decision graph + resource conventions.
> - **No DI.** Collaborators (stores, policies, clients) are wired into resources
>   at a composition root. Tests pass fakes to constructors. There is no
>   dependency-injection container, no override registry, no request-scoped
>   resolver.
> - **HTTP correctness by construction.** Status/header logic (405/406/304/401/
>   409/412/415/501/503, `Allow`, `WWW-Authenticate`, conditional requests) comes
>   from the core, not from handlers.
> - **Hybrid lanes.** Resource-shaped endpoints run through the graph. Genuinely
>   command-shaped endpoints (credential exchange, webhook receivers) run as plain
>   handlers on the same substrate. Do not dress commands up as resources.
>
> A throwaway spike has validated the core walk (a simple read resource returning
> 200/304/405/401/406 + HEAD from ~5 callbacks). M0 reimplements that shape
> cleanly from this plan; the spike is not carried into the codebase.

---

## Contents

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [Design principles](#2-design-principles)
3. [Architecture](#3-architecture)
4. [The decision graph](#4-the-decision-graph)
5. [The Resource API](#5-the-resource-api)
6. [The core walk](#6-the-core-walk)
7. [Auth as callbacks (porting a rule engine)](#7-auth-as-callbacks)
8. [Streaming / SSE](#8-streaming--sse)
9. [Observability: the decision trace](#9-observability-the-decision-trace)
10. [OpenAPI / schema generation](#10-openapi--schema-generation)
11. [Testing strategy](#11-testing-strategy)
12. [Milestones](#12-milestones)
13. [Adoption in an existing ASGI app](#13-adoption-in-an-existing-asgi-app)
14. [Risks and open questions](#14-risks-and-open-questions)
15. [References](#15-references)

---

## 1. Goals and non-goals

### Goals
- A small, **finite, faithful** transcription of the webmachine HTTP decision
  diagram as an async state machine. The graph is a *known artifact*; we are
  converging on a spec, not inventing one.
- Resources are plain classes with **explicit async callbacks** and sane HTTP
  defaults; you override only what you care about.
- **Correct HTTP semantics for free**: conditional requests, content
  negotiation, method/existence handling, and status selection are the core's
  job.
- **Zero magic dependency injection.** Wiring is explicit and lexical.
- **First-class decision tracing** — you can always answer "why did I get a 403."
- Runs any ASGI server; interoperates with existing Starlette middleware.

### Non-goals
- Not an ORM, not a settings system, not a validation library. Bring your own
  (Pydantic v2 recommended, but the core does not require it).
- Not a general "do anything" framework. Command-shaped endpoints are supported
  but are explicitly a *second lane*, not the headline.
- Not chasing raw throughput. Target workloads are I/O-bound; the graph's
  per-request overhead (a handful of awaits) is negligible against a DB round
  trip. We will not trade clarity for microbenchmarks.
- Not (initially) a substrate of our own. Starlette is the substrate. See §2.6
  for how we keep that a *reversible* decision.

---

## 2. Design principles

### 2.1 Rent Layer 2, own Layer 1
Two layers hide inside "a framework": (1) conventions/opinions — routing
composition, handler signature, how auth/errors/validation are wired; (2) ASGI
protocol correctness — the `scope`/`receive`/`send` dance, streaming bodies,
disconnect detection, a working test client. Layer 2 is commodity and
error-prone; we import it from Starlette. Layer 1 is ours.

### 2.2 No dependency injection
A resource receives its collaborators as constructor arguments. A composition
root (the app-builder function) wires the real ones; tests wire fakes. This is
the entire "testability" story — no override registry, no request-scoped
resolver, no shim.

### 2.3 Correctness by construction
Every default callback returns a *correct HTTP default*. A resource that
implements only `content_types_provided` already gets 405/406/304/501/503
behavior. Handlers never hand-write status codes for protocol concerns.

### 2.4 Subset-first, faithful
Implement the diagram in phases (§4), but keep node labels identical to the
canonical flowchart so the implementation is diff-able against the spec. Unused
branches must fall through to correct defaults — a partial graph is *legitimate*,
not half-baked, because that's how the diagram is designed to degrade.

### 2.5 Hybrid lanes, no cosplay
`Resource` (graph lane) and `Command` (plain-handler lane) are both first-class
and share the substrate. The tell that an endpoint belongs in the command lane:
you're inventing an unaddressable noun and faking `resource_exists` /
`content_types_provided` to satisfy the model. Let commands be commands.

### 2.6 Starlette is a *replaceable substrate*
The maintainer is unconvinced Starlette is the best possible base. We honor that
without paying for it now, via a **narrow seam**:

- The core (`asgimachine.core`, `asgimachine.resource`) must not import Starlette.
  It talks to a thin `HttpRequest` protocol and returns an `HttpResponse`
  value object (status, headers, body-or-stream).
- `asgimachine.substrate.starlette` is the *only* module that imports Starlette;
  it adapts Starlette's `Request`/`Response`/`Router`/`TestClient` to those
  protocols.
- Swapping Starlette later (raw ASGI, or a home-grown substrate) becomes a new
  `substrate.*` module, not a rewrite.

Pragmatic caveat to avoid premature abstraction: in **M0** we may let the
adapter be thin to the point of leaky (pass Starlette's `Request` through inside
`Ctx`) to move fast — but the *core's* imports stay clean from day one, so the
seam is real even before it's pretty. Formalize the protocol in M1. Do not build
a second substrate until there is a concrete reason to (YAGNI); design so it's
cheap when the reason arrives.

### 2.7 Deliberate divergences from the canonical model
"Canonical model" = the webmachine v3 decision diagram (§15) *and* its resource
interface convention, in which everything a resource exposes to the graph is a
per-request **callback** (`allowed_methods`, `content_types_provided`,
`content_types_accepted`, …). We transcribe the **graph** faithfully — node
labels and edges match the flowchart (§2.4) — but knowingly break from the
all-callbacks convention in a few places. All the breaks follow one rule:

> **Static resource *shape* is a declaration; per-request *behavior* is a
> callback.** A thing that cannot legitimately vary per request is a class
> attribute, not an `async def`.

The callbacks that remain (`resource_exists`, `is_authorized`, `forbidden`,
`generate_etag`, `is_conflict`, …) are genuine per-request behavior. What moved
to declarations:

- **`ALLOWED_METHODS`** (was `allowed_methods`) — the supported methods. 405 is a
  property of the target resource (RFC 9110 §15.5.6); per-principal gating is
  `forbidden`/403, not a varying method set. A `frozenset`, mirroring
  `KNOWN_METHODS`.
- **`PRODUCES`** (was `content_types_provided`) — the offered media types, in
  preference order. Encoding is split out to a media-type-keyed **codec** (§10);
  the resource builds one representation via `represent()` and the codec encodes
  it N ways.
- **`CONSUMES`** (was `content_types_accepted`) — the accepted request media
  types. The write handler is a single `apply(ctx, body)`.

Two payoffs justify the divergence: it's more RFC-correct (methods/media are
resource properties), and it makes the surface **statically introspectable** —
negotiation offers, the `Allow` header, 300 `multiple_choices`, and OpenAPI
generation all read the declarations without calling a callback.

One more break, on the request body: **parse, don't validate**. The core decodes
the body via the negotiated codec and *parses* it into `apply`'s declared `body`
type (a Pydantic `model_validate`; a failure is a 400 at node P0). `apply`
receives a typed value and is total over it, rather than re-parsing a loose dict.
`malformed_request` survives only as an escape hatch for untyped handlers and
cross-field checks the type can't express.

---

## 3. Architecture

```
asgimachine/
  core.py            # the decision-graph walk (imports only .resource, .http)
  resource.py        # Resource base class, callback surface, defaults
  command.py         # Command base class (plain-handler lane)
  http.py            # HttpRequest protocol, HttpResponse value object, status enum
  negotiation.py     # Accept / Accept-* parsing and selection
  conditional.py     # ETag / Last-Modified evaluation helpers
  trace.py           # decision-trace record + formatting
  streaming.py       # streaming producers, SSE event helpers
  policy.py          # optional Policy protocol for auth rule engines
  substrate/
    __init__.py
    starlette.py     # THE ONLY module that imports starlette
  schema/            # (v2) OpenAPI generation from resource declarations
  testing.py         # test helpers (client factory, trace assertions)
```

Dependency direction: `substrate.starlette → core → resource/command → http`.
Nothing in `core`/`resource`/`http` imports `starlette`.

Tooling: Python 3.14, `uv`, `ruff` (rule set F,E,UP,ASYNC,BLE,COM,C4,T20,RUF,ISC;
E501 off), `pytest` + `pytest-asyncio`, `pytest-cov`. Hardened CI (pinned
actions, minimal permissions). Packaged for PyPI; released when it graduates past
its own example app + conformance suite.

---

## 4. The decision graph

Phased by the node clusters of the canonical diagram. Labels match the flowchart.

### Phase v0 — read resources (the accounts/budgets/transactions shape)
| Node(s) | Question | Failure status |
|---|---|---|
| B13 | `service_available?` | 503 |
| B12 | `known_method?` | 501 |
| B10 | `method_allowed?` | 405 + `Allow` |
| B8  | `is_authorized?` | 401 + `WWW-Authenticate` |
| B7  | `forbidden?` | 403 |
| C3/C4 | Accept → media type (minimal: offer JSON) | 406 |
| G7  | `resource_exists?` | 404 |
| G8–L17 (subset) | conditional GET: `generate_etag`/`If-None-Match`, `last_modified`/`If-Modified-Since` | 304 |
| O18/O20 | build representation; HEAD/OPTIONS handling | — |

Acceptance: the PoC's 6 behaviors + explicit HEAD and OPTIONS.

### Phase v1 — conditional requests (full) + negotiation + tracing
Full `If-Match`/`If-None-Match`/`If-Modified-Since`/`If-Unmodified-Since`
(nodes G/H/I/K/L → 304/412), real `Accept` negotiation over multiple offered
types, `Vary` via `variances`, and the trace facility (§9).

### Phase v2 — write path (POST/PUT/PATCH/DELETE)
Node labels follow the canonical webmachine v3 flowchart (checked
content-type-before-entity-length). The body-validation nodes are traversed only
for body-bearing methods (POST/PUT/PATCH) — a §2.4 pruning matching cowboy_rest;
bodyless requests fall through the inert branch.
| Node(s) | Question | Status |
|---|---|---|
| B6 | `valid_content_headers?` | 501 |
| B5 | `known_content_type?` | 415 |
| B4 | `valid_entity_length?` | 413 |
| B9 | `malformed_request?` (Pydantic body parse) | 400 |
| — | `content_types_accepted` (acceptor callbacks) | — |
| O14/P3 | `is_conflict?` | 409 |
| N11 | `post_is_create?` / `create_path` | 201 + `Location` |
| M20/M16 | `delete_resource` / `delete_completed` | 204 / 202 |
| K5/L5/M5 | `moved_permanently?` / `moved_temporarily?` / `previously_existed?` | 301 / 307 / 410 |

### Phase v3 — feeds, caching, advanced negotiation
Full `Accept-Language`/`Charset`/`Encoding` (D/E/F nodes), `expires`/
`Cache-Control`, `multiple_choices` (300), and **immutable feed pages** — the
outbox/event-feed case. This is where the caching nodes pay off hardest:
archived feed pages are `Cache-Control: immutable`, stable-ETag, 304-friendly,
CDN-cacheable. (This is the endpoint class that most rewards the whole exercise.)

---

## 5. The Resource API

All callbacks are `async def callback(self, ctx: Ctx) -> ...`, each with a
correct default. Override only what a given resource needs.

```python
class Resource:
    KNOWN_METHODS = {"GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"}

    async def service_available(self, ctx) -> bool: ...          # -> 503
    async def allowed_methods(self, ctx) -> list[str]: ...       # -> 405 + Allow
    async def is_authorized(self, ctx) -> bool | str: ...        # str = WWW-Authenticate -> 401
    async def forbidden(self, ctx) -> bool: ...                  # -> 403
    async def known_content_type(self, ctx) -> bool: ...         # -> 415  (write path)
    async def malformed_request(self, ctx) -> bool: ...          # -> 400  (write path)
    async def resource_exists(self, ctx) -> bool: ...            # -> 404
    async def generate_etag(self, ctx) -> str | None: ...        # -> 304 / 412
    async def last_modified(self, ctx) -> datetime | None: ...   # -> 304 / 412
    async def content_types_provided(self, ctx) -> list[tuple[str, Producer]]: ...  # -> 406
    async def content_types_accepted(self, ctx) -> list[tuple[str, Acceptor]]: ...  # write path
    async def is_conflict(self, ctx) -> bool: ...                # -> 409
    async def delete_resource(self, ctx) -> bool: ...            # -> 204
```

`Ctx` is per-request scratch state (webmachine's ReqData + Context). It carries
the request, holds what callbacks compute (`ctx.user`, `ctx.entity`,
`ctx.chosen_media_type`), and accumulates the decision trace. **Per-request
state lives on `Ctx`, never on the shared resource instance** — resources hold
only their wired collaborators.

Example (the `accounts` resource, no DI):

```python
class AccountsResource(Resource):
    def __init__(self, retrieve_accounts_for_user, authenticate):
        self._retrieve = retrieve_accounts_for_user   # store, wired at construction
        self._authenticate = authenticate             # auth, wired at construction

    async def allowed_methods(self, ctx):  return ["GET", "HEAD"]
    async def is_authorized(self, ctx):
        user = await self._authenticate(ctx.request)
        if user is None: return "Bearer"              # -> 401 WWW-Authenticate: Bearer
        ctx.user = user; return True
    async def resource_exists(self, ctx):
        ctx.entity = await self._retrieve(ctx.user.id); return True
    async def generate_etag(self, ctx):
        return f'W/"accounts-{ctx.user.id}-{len(ctx.entity)}"'
    async def to_json(self, ctx):
        return GetDataResponse[list[Account]](data=ctx.entity)
```

---

## 6. The core walk

`async def run(resource, request) -> HttpResponse` executes the graph, mutating a
`Ctx`, and returns an `HttpResponse` value object. Design notes:

- **Short-circuit via a `HaltResponse` exception** carrying a response — any
  callback can bail out with an explicit status.
- **The producer node is the streaming seam.** `content_types_provided` yields
  `(media_type, producer)`. A producer returns either a serializable value
  (normal response) or an async iterator (streaming response). The graph decides
  status + headers, then hands the iterator to the substrate. See §8.
- **Every node appends to `ctx.trace`** before/after evaluating (§9).
- **Serialization is pluggable.** Default producer serializes Pydantic models via
  `model_dump_json`; the core only requires "producer returns something the
  substrate can turn into bytes." Non-Pydantic users register a serializer.
- The walk is a straight-line function with labeled sections, not a data-driven
  interpreter, in v0 — readability over cleverness. If the graph later warrants
  it, we can express edges as data; not before.

---

## 7. Auth as callbacks

Authorization is not baked into the core. Instead, the `is_authorized`/
`forbidden` nodes delegate to a **`Policy` collaborator** — a plain object wired
into resources at the composition root. This keeps authorization logic
centralized, testable in isolation, and free of any request-scoped resolver.

The framework ships one recommended `Policy` implementation: an **ordered
`Allow`/`Deny` rule engine** (each rule inspects the request + authenticated
principal and matches/denies, first match wins). This is itself a small,
hand-built fragment of the decision graph, and it's a natural fit for apps whose
authorization is more than "is there a valid token."

- Resources delegate: `is_authorized`/`forbidden` call `self._policy.evaluate(ctx)`.
- The policy's own rule trace merges into the decision trace (§9), so "which rule
  denied me" and "which node returned 403" are one story.
- Apps with trivial auth skip the engine entirely and implement `is_authorized`
  directly on the resource; apps with a custom scheme provide their own `Policy`.

---

## 8. Streaming / SSE

Streaming is *inside* the graph, at the producer node — not a special case
bolted beside it. The graph decides the envelope (auth, method, body validation
for POST-streams, negotiation → `200 text/event-stream`); after the first flush
the producer owns the connection.

- `content_types_provided` may return a producer that yields an async iterator.
- `streaming.sse_event(event, data)` formats SSE frames.
- **Document the post-commit boundary explicitly:** once streaming starts, the
  status line is on the wire. A mid-stream failure cannot become a 500 — it
  becomes an SSE `error` event. The graph (and any exception-handler mapping)
  has no say after commit. This is inherent to HTTP, not a framework limit; we
  surface it in docs and provide an `sse_error()` helper + a recommended
  pattern for wrapping producer bodies.

Proof target (in `examples/`): a self-contained `text/event-stream` endpoint —
e.g. a POST that validates a body, authenticates, then hands off to an async
generator producing server-sent events. The point is to demonstrate that the
graph fully governs the envelope (validation/auth/negotiation → `200
text/event-stream`) and then cleanly hands the untouched generator to the
substrate for the open-ended part.

---

## 9. Observability: the decision trace

webmachine's signature debugging feature, made first-class:

- `ctx.trace` records every node visited and its outcome.
- In dev (`asgimachine` debug mode), emit `X-Asgimachine-Trace` response header
  and/or a structured log line with the ordered node path + terminal node.
- A test helper asserts the node path for a request (`assert_trace(resp, [...])`)
  — this both documents and pins the graph wiring.

This subsumes the app's existing `rule_trace` logging and answers the perennial
"why did this request get that status" without a debugger.

---

## 10. OpenAPI / schema generation

Honest hard part. FastAPI derives schema from handler *signatures*;
asgimachine's callbacks don't carry request/response types in signatures. Plan:

- Resources **declare** their typed surface: `content_types_provided` producers
  and `content_types_accepted` acceptors reference Pydantic models; resources may
  expose a small `describe()` returning method → (request model, response model,
  status codes).
- A `schema/` generator walks registered resources + declarations to emit
  OpenAPI. More explicit than FastAPI's introspection, but tractable.
- **Deferred to v2/v3.** Until then, graph routes are simply absent from any
  generated schema — acceptable while a resource is being adopted incrementally,
  and adopters can keep their existing schema mechanism for other routes.
- Flagged as an open question (§14) because it's the least-designed piece.

---

## 11. Testing strategy

Three tiers:

1. **Unit** — Starlette `TestClient` over the substrate adapter; collaborators are
   constructor-injected fakes. No override registry, no app monkeypatching.
   The test seam is `TestClient(build_app(fake_store, fake_auth))`.
2. **Conformance** — a table-driven suite of `(request → expected status +
   headers)` encoding HTTP semantics from the RFCs/diagram. This is the executable
   spec and the regression net; it is what makes a bespoke core *trustworthy*.
   Covers each node's success/failure edge.
3. **Trace assertions** — assert the exact node path for representative requests
   (verifies graph wiring, catches accidental short-circuits).

A high coverage bar. The conformance suite is a hard gate: the core does not
ship a phase until that phase's node table is green.

---

## 12. Milestones

Each milestone is independently useful and independently abandonable.

- **M0 — Seed.** Repo scaffold + tooling + CI. Implement the core walk (shape
  validated by the spike): `core.run`, `Resource`, `Ctx`, Starlette adapter,
  `HttpRequest`/`HttpResponse`, plus one read-resource example in `examples/`.
  *Acceptance:* v0 node table green (200/304/405/401/406/404/501/503 + HEAD +
  OPTIONS); conformance suite seeded.
- **M1 — Conditional + negotiation + trace.** Full conditional-request nodes,
  real `Accept` negotiation, `Vary`, the trace facility + debug header, and the
  formalized substrate protocol (core fully Starlette-free). *Acceptance:* 412s
  and multi-type 406/negotiation cases green; `assert_trace` helper working.
- **M2 — Write path.** POST/PUT/PATCH/DELETE, body validation (400/415/413),
  `content_types_accepted`, 201+`Location`, 409, delete (204/202), redirects
  (301/307/410). *Acceptance:* v2 node table green.
- **M3 — Streaming.** Producer-returns-iterator, SSE helpers, post-commit-error
  pattern. The `examples/` SSE endpoint (§8) as proof. *Acceptance:* streamed
  `text/event-stream` response walks the graph; mid-stream error emits an SSE
  event.
- **M4 — Auth policy + dogfood example app.** The `Allow`/`Deny` `Policy` engine
  (§7), and a small-but-realistic **example application** in `examples/` — several
  resources across the gradient (a collection, a member, a read-only resource), a
  command-lane endpoint (credential exchange or a webhook receiver), and an auth
  policy — serving as the framework's own end-to-end validation harness.
  *Acceptance:* the example app passes an end-to-end HTTP behavior suite; decision
  traces observable; the command lane and resource lane coexist without cosplay.
- **M5 — Feeds + schema + (optional) second substrate.** Immutable feed/caching
  resource (the outbox case); OpenAPI generation; revisit "can we beat Starlette"
  with data now in hand (§2.6, §14). *Acceptance:* a cacheable feed resource with
  immutable pages + working conditional GET; schema emitted for graph routes.

---

## 13. Adoption in an existing ASGI app

Written for any future adopter; asgimachine itself has no dependency on, or
knowledge of, the apps that use it.

- asgimachine mounts as an ordinary ASGI app. An existing app (FastAPI,
  Starlette, etc.) can `mount()` an asgimachine `Router` at a subtree, or
  vice-versa — both are ASGI, so they compose without a rewrite.
- **Adopt one resource first** — the best fit is a pure-read resource where
  conditional GET is genuinely useful. Everything else stays where it is.
- Expand resource-by-resource across the genuinely resource-shaped surface. This
  **empirically answers "how much of this app is resource-shaped"** as you go —
  the adoption is its own measurement.
- Command-shaped endpoints (credential exchange, webhook receivers) stay on the
  incumbent framework or move to asgimachine's `Command` lane — no rush, no
  cosplay.
- **Fully reversible.** If it doesn't pay off after a resource or two, the
  adopter has touched a small, isolated surface. Sunk cost stays small by
  construction.

---

## 14. Risks and open questions

- **Schema/OpenAPI without signatures** (§10) — the least-designed piece; the
  `describe()` approach is plausible but unproven. Biggest technical unknown.
- **Bus-factor of a bespoke core** — mitigated by: it's a *documented* diagram
  (new contributors learn the model from the canonical flowchart, not just our
  code), the conformance suite is executable spec, and the trace facility makes
  behavior self-explaining. Still a real cost to weigh vs. adopting an
  established framework.
- **Substrate seam vs premature abstraction** (§2.6) — resolved as: clean core
  imports from day one, formalize the protocol at M1, build a second substrate
  only on concrete need. The "beat Starlette" question is explicitly *deferred*
  behind the seam and revisited at M5 with real data — not litigated up front.
  **M5 verdict:** keep Starlette; the seam held (140-LOC adapter, provably
  Starlette-free core). See [docs/adr/0001-keep-starlette-substrate.md](docs/adr/0001-keep-starlette-substrate.md).
- **Graph inertness on commands** — accepted: commands use the second lane; don't
  force them through the graph for a dividend that isn't there.
- **Per-request overhead** — many small awaits per request. Negligible vs. a DB
  round trip for this I/O-bound workload; if ever measured to matter, sync-detect
  callbacks that needn't be coroutines. Not a v0 concern.
- **The framework's meta-risk** — over-building the graph before proving it end
  to end: elaborate node coverage with no realistic app exercising it. Hedged by
  subset-first phasing (§4) and the M4 dogfood example app as the go/no-go
  checkpoint — the graph does not advance a phase the example app doesn't use.
- **The adopter's meta-risk** — half-migration: a lovely core, a few resources
  moved, and the long tail never justifies running two systems. §13's
  one-resource-first, fully-reversible sequencing is the hedge.

---

## 15. References

- **The webmachine decision diagram** — the canonical flowchart this core
  transcribes (node labels B/C/D/E/F/G/H/I/K/L/M/N/O/P).
- **Basho webmachine** (Erlang) — the original.
- **`cowboy_rest`** (Erlang) — the living, pragmatic subset most people run;
  best reference for "which nodes actually matter in practice."
- **Liberator** (Clojure) — the clearest modern docs + decision-graph
  visualization; study this for the model.
- **Airship** (Haskell) — another faithful port.
- **Fielding, *Architectural Styles and the Design of Network-based Software
  Architectures*** — REST, the resource concept.
- **Webber, Parastatidis, Robinson, *REST in Practice*** — event feeds,
  cacheable immutable feed pages (the v3 feed case).
- **Transactional outbox pattern** — the "POST appends to a list" storage step
  done correctly.
- **ASGI specification** and **Starlette documentation** — the rented Layer 2.
