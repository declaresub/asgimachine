# Negotiation & errors

## Content negotiation

A resource declares the media types it offers in `PRODUCES` (preference order,
declared-first wins ties). The core negotiates against the request's `Accept`,
picks a type, and calls `represent(ctx)` once — a **codec** then encodes that one
representation for the chosen media type.

```python
class Widget(Resource):
    PRODUCES = ("application/json", "text/plain")

    async def represent(self, ctx: Ctx) -> object:
        return {"id": 1, "name": "sprocket"}
```

Encoding is separated from representation: `represent` builds a value; the
media-type-keyed [`Codec`][asgimachine.codec.Codec] registry (default: JSON)
encodes it. An unsatisfiable `Accept` is a `406`.

### Serve-anyway

RFC 9110 §12.1 lets a server *disregard* an unsatisfiable `Accept` and serve its
default representation instead of a `406`. Opt in with a declaration:

```python
class Api(Resource):
    IGNORE_UNACCEPTABLE = True   # serve the default instead of 406
```

It applies to every negotiated axis below, not just `Accept`.

## The other axes — language and encoding

The webmachine D and F nodes. Each is a declaration parallel to `PRODUCES`, and
each is **opt-in**: leave it empty (the default) and that axis is not negotiated —
the `Accept-*` header is ignored, nothing is added to `Vary`, and no `406` can
come from it. A resource that offers none is byte-for-byte unchanged.

```python
class Greeting(Resource):
    LANGUAGES = ("en", "fr")          # D4/D5 -> Content-Language, 406
    ENCODINGS = ("identity", "gzip")  # F6/F7 -> 406

    async def represent(self, ctx: Ctx) -> object:
        return {"hello": "bonjour" if ctx.chosen_language == "fr" else "hello"}
```

When an axis is offered, the core negotiates it against the matching request
header, `406`s an offered-but-unsatisfiable one (unless serve-anyway is set), adds
the axis to `Vary`, and exposes the choice on `ctx` (`chosen_language`,
`chosen_encoding`). Language uses RFC 4647 lookup matching (a request for `en-US`
is served by an offered `en`, and vice versa); `identity` is always an acceptable
encoding unless the client explicitly refuses it (RFC 9110 §12.5.3).

!!! note "The graph negotiates; it does not transform"
    asgimachine picks the value, decides `406`/`Vary`, and advertises the headers
    — it does **not** compress bodies (that's the substrate's or a reverse proxy's
    job — *rent Layer 2*). Read `ctx.chosen_language` in `represent` to serve the
    right translation, or `ctx.chosen_encoding` to set `Content-Encoding` after
    applying a coding.

### Why there's no `Accept-Charset`

webmachine has a matching **E node** for charset; asgimachine deliberately leaves
it out. RFC 9110 §12.5.2 **deprecates `Accept-Charset`**: UTF-8 is now nearly
universal, sending a charset list "wastes bandwidth, increases latency, and makes
passive fingerprinting far too easy," and general-purpose user agents no longer
send it. Charset today is carried by the Content-Type `charset` parameter, not a
negotiation axis — and letting a *deprecated* request header force a hard `406`
would work directly against the spec's guidance. If you genuinely need to stamp a
charset, set it on the response Content-Type in your codec or representation; the
graph won't negotiate it for you.

## Conditional requests

Implement `generate_etag` and/or `last_modified` and the graph handles the full
precondition suite — RFC 9110 §13, correctly:

- `If-None-Match` / `If-Modified-Since` → `304` for GET/HEAD.
- `If-Match` / `If-Unmodified-Since` → `412` on a failed precondition (strong
  comparison for `If-Match`; unverifiable/absent validators are *ignored*, per RFC,
  not failed).
- `If-Match` takes precedence over `If-Unmodified-Since`; `If-None-Match` over
  `If-Modified-Since`.

`ETag`, `Last-Modified`, `Vary`, and `Cache-Control` are emitted on the cacheable
responses (200 and 304) so intermediaries key and validate correctly.

## The write path

Declare `CONSUMES` and implement `apply(ctx, body)`. The core decodes the request
via the negotiated codec and **parses** it into `apply`'s declared `body` type — a
Pydantic model, say. A bad body is a `400` at the boundary; `apply` receives a
value it can trust (*parse, don't validate*). Annotate `body` loosely
(`dict`/`object`) to receive the decoded structure as-is.

The request body is bounded by `MAX_BODY_BYTES` (default 1 MiB → `413`), and a
`Content-Length` that disagrees with the bytes read is a framing error (`400`).

## Error bodies (RFC 9457)

Every `4xx`/`5xx` response carries a body — an **RFC 9457 problem detail**
(`application/problem+json`) by default:

```json
{ "type": "about:blank", "title": "Not Found", "status": 404 }
```

The error body is negotiated over `ERROR_PRODUCES` *separately* from the main
representation (which may have failed with a `406`, or never run before a `401`),
with a serve-anyway fallback. Customize it with the `error_body(ctx, status,
media_type)` hook — add `detail`/`instance`/custom members, render per media type
(declare `text/html` + a codec for browser error pages), or return `None` for an
empty body. Redirects and `304` keep empty bodies; `HEAD` sends headers only.
