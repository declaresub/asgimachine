# When to use it

asgimachine is a sharp tool with a narrow grain. It's built for one thing — HTTP
**resources** whose protocol semantics carry real weight — and it's honest enough to
tell you when that isn't your problem. This page helps you decide before you invest;
the [framework comparison](comparison.md) is the companion once you've decided the
*shape* fits.

## The grain of the tool

asgimachine earns its keep when **HTTP itself is doing work** in your application:
caching, conditional requests, content negotiation, method semantics, optimistic
concurrency. You describe a resource as a set of small callbacks, and correct
behavior for all of that falls out of the decision graph — by default, not by
memory.

The flip side: if your app treats HTTP as a dumb pipe for JSON blobs — every endpoint
a remote procedure call, no caching, no ETags, no negotiation — then most of the graph
is scaffolding you're paying for and not using. That's not a knock on your app; it
just isn't this tool's problem.

## Good fit

- **RESTful resource APIs.** Collections and items with genuine `GET`/`PUT`/`PATCH`/
  `DELETE` semantics — where `ETag` + `If-Match` (optimistic concurrency, `428`),
  `405 + Allow`, and `412` should be *right*, not approximated. The graph makes them
  the default.
- **Hypermedia frontends** — [Unpoly](https://unpoly.com), htmx, Turbo. Conditional
  `GET` drives a polling fragment for one round-trip and no body when nothing changed;
  content negotiation serves a fragment to the library and a full page to a plain
  navigation *from the same URL*. This is asgimachine's sweet spot — see the
  [Unpoly example](examples.md).
- **Cache-sensitive read APIs.** Anywhere `304` / `ETag` / `Vary` / `Cache-Control`
  save real bandwidth and a CDN or shared cache has to key correctly. Getting `Vary`
  wrong is a silent cache-poisoning bug; the graph gets it right.
- **Correctness-sensitive public HTTP.** APIs where a reviewer or a spec actually
  cares that `406`, `415`, `412`, `428`, `451`, and conditional requests behave to the
  letter — and you'd rather not re-derive that on every endpoint.
- **Content-negotiated APIs.** One URL, several representations (JSON / CSV / HTML),
  or language / encoding axes — proactive negotiation with automatic `Vary`.

## Poor fit

- **Action / RPC-heavy APIs.** If your endpoints are verbs — *send the email*, *run
  the report*, *exchange the token* — they're commands, not resources. asgimachine
  gives you a [second lane](concepts/two-lanes.md) (`Command`) for exactly these, so a
  *few* alongside your resources are fine. But if the *whole* app is commands, the
  graph buys you nothing → reach for **FastAPI** or **Flask**.
- **Batteries-included database apps.** You want an ORM, migrations, an admin, a
  template engine, sessions, and auth in the box → **Django**. asgimachine has none of
  that and isn't trying to.
- **GraphQL, WebSockets, real-time.** The graph models request/response HTTP. It does
  not model a single GraphQL endpoint or a subscription/socket lifecycle. Use
  **Strawberry** / **Ariadne** for GraphQL, or **Starlette** / **Channels** directly
  for sockets. (asgimachine *does* stream responses — SSE — see
  [Stream a response](guides/streaming.md).)
- **Throwaway scripts and tiny internal tools.** For three endpoints that return
  static JSON, the resource-callback ceremony isn't worth it. **Flask** or **FastAPI**
  stands up faster.
- **Projects that need a big ecosystem or a stability guarantee.** asgimachine is
  experimental, small, and single-maintainer (see the [Status](index.md) note). For
  something business-critical where hiring, plugins, and a long support track record
  matter, that's a real risk — weigh it honestly.

## Signs you're fighting the framework

A quick self-check. If, on most resources, you find yourself:

- overriding `resource_exists` to `return True` and never touching `generate_etag`,
  `last_modified`, or negotiation, and
- reading the request body and dispatching on some field like a procedure call,

…then you're routing around the graph, not using it. That's a signal your endpoints
are command-shaped — use the [`Command` lane](concepts/two-lanes.md) for them, or, if
it's the whole app, a framework built for RPC. Using the wrong lane isn't a moral
failing; forcing every endpoint through a graph that does nothing for it is just extra
code.

## You can mix

The two lanes exist so you don't have to choose all-or-nothing. A resource-shaped API
with a handful of genuinely command-shaped endpoints (a webhook receiver, a token
exchange) is a normal, healthy asgimachine app: graph resources for the resources,
`Command` handlers for the commands. The question isn't "is my *whole* app a fit" —
it's "are my *resources* resources."

Decided the shape fits? The [framework comparison](comparison.md) is the next read.
