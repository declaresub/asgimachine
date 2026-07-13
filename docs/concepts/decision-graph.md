# The decision graph

asgimachine transcribes the **webmachine v3 HTTP decision diagram** — a flowchart
of ~50 nodes that RFC-correctly maps a request to a status code and headers. Each
node is a yes/no question; the edges lead to the next question or to a terminal
response.

The core, [`run(resource, request)`][asgimachine.core.run], walks that graph as a
straight-line async function with labelled sections. Node labels match the
canonical flowchart (`B13`, `C4`, `G7`, `O18`, …) so the implementation is
diff-able against the spec.

## Correctness by construction

Every node calls a resource callback that ships a **correct HTTP default**. The
practical consequence: a resource that overrides only `represent` still answers,
correctly and for free:

| Situation | Response | Node |
|---|---|---|
| service unavailable | `503` | B13 |
| unknown method | `501` | B12 |
| method not allowed | `405` + `Allow` | B10 |
| unauthorized | `401` (+ `WWW-Authenticate`) | B8 |
| forbidden | `403` | B7 |
| unacceptable `Accept` | `406` | C4 |
| missing resource | `404` | G7/L7 |
| conditional GET matches | `304` | K13/L17 |
| `HEAD` / `OPTIONS` | correct empty responses | — |

You override a callback only to change one of these decisions; everything you
don't touch keeps its correct default.

## Short-circuiting: `HaltResponse`

Any node (or callback) can terminate the walk immediately by raising
[`HaltResponse`][asgimachine.http.HaltResponse] with an explicit response. That's
how the graph emits a 404/401/406/…: the node records its label to the trace and
halts. The core catches it in `run()`.

## The phases

The graph was implemented subset-first — each phase is independently useful and
faithful to the diagram's structure:

- **v0 — read resources.** Availability, method/auth/existence, negotiation,
  conditional GET, `HEAD`/`OPTIONS`. (200/304/404/405/406/501/503.)
- **v1 — conditional + tracing.** Full `If-Match`/`If-None-Match`/
  `If-Modified-Since`/`If-Unmodified-Since` (→ 304/412), `Vary`, and the decision
  trace.
- **v2 — the write path.** POST/PUT/PATCH/DELETE, body validation (400/415/413),
  201 + `Location`, 409, delete (204/202), redirects (301/307/410).
- **v3 — feeds & caching.** `Cache-Control`/`Expires`, `multiple_choices` (300),
  immutable feed pages.
- **v4 — RFC-completeness slice.** 451 (legally restricted), 308 (permanent
  redirect), serve-anyway negotiation, and negotiated error bodies (RFC 9457).

## The trace

Run the app in debug mode and every response carries an `X-Asgimachine-Trace`
header listing the exact node path the request walked — so the graph explains
itself. Reading a `403`? The trace shows which node returned it (and, with the
policy engine, which rule fired). See [`Trace`][asgimachine.trace.Trace].
