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
    IGNORE_UNACCEPTABLE = True   # serve PRODUCES[0] instead of 406
```

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
