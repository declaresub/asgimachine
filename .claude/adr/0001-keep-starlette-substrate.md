# ADR 0001 — Keep Starlette as the substrate (revisit at M5)

**Status:** Proposed (awaiting maintainer ratification)
**Date:** 2026-07-11
**Context:** PLAN.md §2.6 (replaceable substrate) and §14 (open question). The plan
deferred the "can we beat Starlette?" question behind a narrow seam and committed
to revisiting it at M5 *with data in hand*. M0–M5 are now built; here is the data
and the recommendation.

## Decision

**Keep Starlette. Do not build a second substrate now.** The replaceable-substrate
seam is validated and stays reversible; revisit only on a concrete trigger (below).

## The data

The seam held exactly as §2.6 intended:

- **One module imports Starlette:** `substrate/starlette.py` (140 LOC), plus the
  `testing.py` test-client helper. Every core module — `core`, `resource`, `http`,
  `negotiation`, `conditional`, `trace`, `streaming`, `policy`, `command`,
  `schema` — is Starlette-free, and a test (`test_import_isolation`) fails the
  build if that ever regresses.
- **What we actually rent** from Starlette: ASGI request parsing (`Request`:
  scope/receive, headers, body, `path_params`), routing (`Route` path matching +
  params), the response/streaming ASGI send-protocol (`Response`,
  `StreamingResponse` — including **client-disconnect cancellation** via its anyio
  task group), the `TestClient`, and the middleware ecosystem (we mount
  `CORSMiddleware` rather than implementing CORS — §2.1).
- Every one of those is Layer-2 commodity the plan explicitly said to rent (§2.1).

## Friction encountered across M0–M5

Honest accounting of every place Starlette pushed back:

1. **A function endpoint is forced to `methods=["GET"]`.** To let the *graph*
   (not the router) own 405/501/OPTIONS/HEAD, resources register as an ASGI
   *class* endpoint. Minor, resolved once, documented.
2. **Disconnect on ASGI ≥ 2.4 is detected only on the next `send`.** A producer
   stuck in a long `await` between yields isn't cancelled promptly on that path
   (the older-spec path races it immediately). A home-grown substrate could
   expose a cooperative disconnect signal to producers — but Starlette's
   cancellation covers the common case, so this is logged as optional, not a
   forcing function. (See the `anyio-disconnect-handling` note.)
3. **`path_params` is loosely typed** (`Any`) upstream. Trivial.

None of these is a reason to replace Starlette. The one thing a bespoke substrate
would let us do better (item 2) is precisely the ASGI streaming/disconnect
plumbing that §2.1 warns is "commodity and error-prone" — a strong argument to
keep renting it, not reimplement it.

## Performance

Target workloads are I/O-bound (§1); Starlette's per-request overhead is
negligible against a DB round trip, and nothing measured suggests a bottleneck.
"Beat Starlette on throughput" is not a goal (§1 non-goals). No data supports a
rewrite on performance grounds.

## Consequences

- The framework ships on Starlette. The seam stays real: the core is provably
  substrate-free, so a future swap is a new `substrate/*` module, not a rewrite.
- **Triggers that would reopen this** (build a second substrate only then, per
  YAGNI):
  - a concrete streaming/disconnect requirement Starlette structurally can't
    meet (e.g. prompt cooperative cancellation across all ASGI versions);
  - a *measured* per-request overhead that matters for a real workload;
  - a Starlette API break or maintenance lapse that makes renting costlier than
    owning.
- **If we swap:** implement `substrate/<name>.py` providing (a) an `HttpRequest`
  adapter over raw ASGI scope/receive, (b) response + streaming send with its own
  disconnect handling, and (c) route registration. The core and all resources are
  unchanged; `test_import_isolation` guards the boundary. The hardest ~third of
  that work is the streaming/disconnect sender — budget accordingly.

## Recommendation

Ratify "keep Starlette." The "own Layer 1, rent Layer 2" bet paid off: Layer 1
(the decision graph + resource/command/policy conventions, ~830 LOC across the
core modules) is entirely ours and Starlette-agnostic; Layer 2 is 140 lines of
adapter we can replace if we ever must.
