"""The decision-graph walk (PLAN.md §6).

``run(resource, request)`` executes the v0 subset of the webmachine diagram as a
straight-line function with labeled sections (readability over a data-driven
interpreter — §6). Node labels match the canonical flowchart so this is
diff-able against the spec (§2.4). Each node records to ``ctx.trace`` before it
decides; any node may raise :class:`HaltResponse` to short-circuit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast, get_type_hints

import anyio

from .codec import DEFAULT_CODECS
from .conditional import (
    http_date,
    if_match_matches,
    if_none_match_matches,
    modified_since,
    not_modified_since,
)
from .http import (
    BodyMalformed,
    BodyTooLarge,
    HaltResponse,
    HttpResponse,
    Status,
    serialize,
)
from .negotiation import choose_media_type, parse_content_type
from .resource import Ctx
from .trace import TRACE_HEADER

if TYPE_CHECKING:
    from .codec import Codec
    from .http import HttpRequest
    from .resource import Resource

SAFE_METHODS = frozenset({"GET", "HEAD"})
# Methods that carry a request body; the body-validation nodes (B9/B6/B5/B4) are
# traversed only for these (a §2.4 pruning — bodyless requests fall through).
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


async def run(
    resource: Resource[Any],
    request: HttpRequest,
    *,
    debug: bool = False,
    codecs: dict[str, Codec] | None = None,
) -> HttpResponse:
    """Walk the graph for one request and return an HttpResponse value object.

    In ``debug`` mode the ordered node path is attached as the
    ``X-Asgimachine-Trace`` response header on every exit path (§9). ``codecs``
    is the media-type -> Codec registry (defaults to JSON only).
    """

    ctx = resource.context_class(
        # Copy per request: an injected registry is shared by reference across
        # every request through the endpoint, so ctx must get its own dict (as the
        # default branch already does) — else one request mutating ctx.codecs
        # would corrupt others in flight.
        request=request,
        codecs=dict(codecs) if codecs is not None else dict(DEFAULT_CODECS),
    )
    # The resource's per-request lifespan wraps the entire walk. It is a plain
    # async generator (no @asynccontextmanager on the override — §5); the core
    # owns the wrapping so it can also own guaranteed, cancellation-safe teardown.
    lifespan = asynccontextmanager(resource.lifespan)(ctx)
    await lifespan.__aenter__()
    streaming = False
    torn_down = False
    try:
        try:
            response = await _walk(resource, ctx)
        except HaltResponse as halt:
            response = halt.response
            await _apply_error_body(resource, ctx, response)
        if debug:
            response.headers[TRACE_HEADER] = ctx.trace.header_value
        if response.is_stream:
            # The streamed body outlives this call; hand teardown to the wrapper,
            # which releases when the body drains, errors, or is closed — even if
            # the substrate never starts iterating it (a pre-first-chunk
            # disconnect). Ownership transfers, so this scope must not release.
            stream = cast("AsyncIterator[bytes]", response.body)
            response.body = _ClosingStream(stream, lifespan)
            streaming = True
        return response
    except BaseException as exc:
        # A real error or a cancellation (client disconnect): tear down with the
        # exception in flight so a lifespan transaction rolls back, then re-raise.
        torn_down = True
        await _teardown(lifespan, exc)
        raise
    finally:
        # Non-streaming success/halt: release here, exactly once — in a finally so
        # a throw anywhere above (debug header, stream wrap) can't skip it. The
        # streaming path transferred ownership; the error path already released.
        if not streaming and not torn_down:
            await _teardown(lifespan, None)


# Cap on the shielded (otherwise uninterruptible) teardown window, so a lifespan
# whose release blocks forever — e.g. a rollback on a half-open socket — cannot
# hang the request task or stall shutdown. On timeout the release is abandoned.
_TEARDOWN_TIMEOUT_S = 30.0


async def _teardown(
    lifespan: AbstractAsyncContextManager[None], exc: BaseException | None
) -> None:
    """Close the lifespan, shielded so a client disconnect cannot interrupt
    resource release, but bounded by ``_TEARDOWN_TIMEOUT_S`` so a release that
    blocks forever cannot hang the task. An in-flight ``exc`` is fed in (rollback);
    a lifespan that *suppresses* it is a misuse — the outcome is still propagated."""

    with anyio.CancelScope(shield=True), anyio.move_on_after(_TEARDOWN_TIMEOUT_S):
        if exc is None:
            await lifespan.__aexit__(None, None, None)
        else:
            await lifespan.__aexit__(type(exc), exc, exc.__traceback__)


class _ClosingStream:
    """A streamed response body that guarantees the resource lifespan is released
    exactly once — on exhaustion, error, or close — *including* when the substrate
    never starts iterating it (a disconnect before the first chunk).

    An async generator's ``finally`` cannot guarantee this: it never runs if the
    generator was never started, and Starlette's ``StreamingResponse`` does not
    ``aclose`` the body on the ASGI spec>=2.4 path (it leaves finalization to GC).
    So teardown lives in ``aclose``/exhaustion behind a one-shot guard, and the
    substrate calls ``aclose`` unconditionally after the response completes.
    """

    __slots__ = ("_closed", "_inner", "_lifespan")

    def __init__(
        self,
        inner: AsyncIterator[bytes],
        lifespan: AbstractAsyncContextManager[None],
    ) -> None:
        self._inner = inner
        self._lifespan = lifespan
        self._closed = False

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._inner.__anext__()
        except StopAsyncIteration:
            await self._release(None)  # drained cleanly -> commit
            raise
        except BaseException as exc:
            await self._release(exc)  # mid-stream failure -> rollback
            raise

    async def aclose(self) -> None:
        # Called by the substrate after the response finishes or the client
        # disconnects (and by GC as a backstop). Close the inner stream, then
        # release — feeding GeneratorExit so an incomplete response rolls back.
        inner_aclose = getattr(self._inner, "aclose", None)
        try:
            if inner_aclose is not None:
                await inner_aclose()
        finally:
            await self._release(GeneratorExit())

    async def _release(self, exc: BaseException | None) -> None:
        if self._closed:
            return
        self._closed = True
        await _teardown(self._lifespan, exc)


def _halt(
    ctx: Ctx, node: str, status: Status, headers: dict[str, str] | None = None
) -> HaltResponse:
    ctx.trace.record(node, int(status))
    return HaltResponse(HttpResponse(status=int(status), headers=headers or {}))


async def _apply_error_body(
    resource: Resource[Any], ctx: Ctx, response: HttpResponse
) -> None:
    """Give a 4xx/5xx halt an error body (default: RFC 9457 problem+json), §4 v4.

    The error representation is negotiated over ``ERROR_PRODUCES`` — separately
    from the main C3/C4 pass, which may have failed (406) or not run (401) — with
    a serve-anyway fallback, since an error must always carry a body. Redirects and
    304 (status < 400) keep empty bodies; HEAD sends headers only.
    """

    if response.status < 400 or response.body:
        return
    offered = list(resource.ERROR_PRODUCES)
    if not offered:
        return
    media = choose_media_type(ctx.request.headers.get("accept"), offered) or offered[0]
    value = await resource.error_body(ctx, response.status, media)
    if value is None:
        return
    response.headers.setdefault("Content-Type", media)
    if ctx.request.method != "HEAD":
        codec = ctx.codecs.get(media)
        response.body = codec.encode(value) if codec is not None else serialize(value)


def _allow_header(methods: frozenset[str]) -> str:
    # OPTIONS is always available (answered at B3); sorted for a deterministic
    # header. Allow has no required order (RFC 9110), so a set is the right shape.
    return ", ".join(sorted(methods | {"OPTIONS"}))


async def _walk(resource: Resource[Any], ctx: Ctx) -> HttpResponse:
    request = ctx.request
    method = request.method

    # B13 service_available? -> 503
    if not await resource.service_available(ctx):
        raise _halt(ctx, "B13", Status.SERVICE_UNAVAILABLE)
    ctx.trace.record("B13", True)

    # B12 known_method? -> 501
    if method not in resource.KNOWN_METHODS:
        raise _halt(ctx, "B12", Status.NOT_IMPLEMENTED)
    ctx.trace.record("B12", True)

    # B10 method_allowed? -> 405 + Allow
    allowed = await resource.allowed_methods(ctx)
    ctx.allowed_methods = allowed
    allow = _allow_header(allowed)
    if method not in allowed and method != "OPTIONS":
        raise _halt(ctx, "B10", Status.METHOD_NOT_ALLOWED, {"Allow": allow})
    ctx.trace.record("B10", True)

    # B9 malformed_request? -> 400 (body-bearing methods only).
    if method in BODY_METHODS:
        if await resource.malformed_request(ctx):
            raise _halt(ctx, "B9", Status.BAD_REQUEST)
        ctx.trace.record("B9", True)

    # B8 is_authorized? -> 401 (+ WWW-Authenticate when a challenge is given)
    auth = await resource.is_authorized(ctx)
    if auth is not True:
        headers = {"WWW-Authenticate": auth} if isinstance(auth, str) else {}
        raise _halt(ctx, "B8", Status.UNAUTHORIZED, headers)
    ctx.trace.record("B8", True)

    # B7 forbidden? -> 403
    if await resource.forbidden(ctx):
        raise _halt(ctx, "B7", Status.FORBIDDEN)
    ctx.trace.record("B7", True)

    # B7a legally restricted? -> 451 (v4, RFC 7725) — denied for legal reasons.
    # Recorded only when it fires (like the precondition nodes), so the canonical
    # trace of a resource that doesn't use this extension is unchanged.
    if await resource.is_legally_restricted(ctx):
        raise _halt(ctx, "B7a", Status.UNAVAILABLE_FOR_LEGAL_REASONS)

    # Request-body validation (body-bearing methods only), in canonical order:
    # B6 valid_content_headers? -> 501, B5 known_content_type? -> 415,
    # B4 valid_entity_length? -> 413.
    if method in BODY_METHODS:
        if not await resource.valid_content_headers(ctx):
            raise _halt(ctx, "B6", Status.NOT_IMPLEMENTED)
        ctx.trace.record("B6", True)
        if not await resource.known_content_type(ctx):
            raise _halt(ctx, "B5", Status.UNSUPPORTED_MEDIA_TYPE)
        ctx.trace.record("B5", True)
        if not await resource.valid_entity_length(ctx):
            raise _halt(ctx, "B4", Status.REQUEST_ENTITY_TOO_LARGE)
        ctx.trace.record("B4", True)

    # B3 OPTIONS? -> 200 with Allow (no body). Canonical order places B3 ahead of
    # content negotiation, so OPTIONS is not subject to Accept (never 406).
    if method == "OPTIONS":
        ctx.trace.record("B3", True)
        return HttpResponse(status=int(Status.OK), headers={"Allow": allow})

    # C3/C4 Accept -> media type -> 406
    offered = list(resource.PRODUCES)
    chosen = choose_media_type(request.headers.get("accept"), offered)
    if chosen is not None:
        ctx.trace.record("C4", chosen)
    elif offered and await resource.ignore_unacceptable(ctx):
        # C4a (v4, RFC 9110 §12.1): disregard an unsatisfiable Accept and serve
        # the default representation instead of 406.
        chosen = offered[0]
        ctx.trace.record("C4a", chosen)
    else:
        raise _halt(ctx, "C4", Status.NOT_ACCEPTABLE)
    ctx.chosen_media_type = chosen

    # G7 resource_exists? -> the missing-resource branch (create / redirect / 404)
    if not await resource.resource_exists(ctx):
        ctx.trace.record("G7", False)
        return await _handle_missing(resource, ctx, method, chosen)
    ctx.trace.record("G7", True)

    # Vary: the resource's declared variances, plus Accept whenever more than one
    # media type is offered (this response was content-negotiated). Emitted on
    # cacheable responses (200 and 304) so intermediaries key correctly.
    vary_headers = await _vary_headers(resource, ctx, offered)

    # Conditional requests (G8-L17): compute validators once, reuse for the
    # precondition checks and the final response headers.
    etag = await resource.generate_etag(ctx)
    last_modified = await resource.last_modified(ctx)
    validator_headers: dict[str, str] = {}
    if etag is not None:
        validator_headers["ETag"] = etag
    if last_modified is not None:
        validator_headers["Last-Modified"] = http_date(last_modified)

    # Caching (v3): Expires / Cache-Control, emitted on cacheable responses.
    cache_headers = await _cache_headers(resource, ctx)
    # Everything an intermediary keys/validates on, for both 200 and 304.
    cacheable_headers = {**validator_headers, **vary_headers, **cache_headers}

    await _check_preconditions(
        ctx, method, etag, last_modified, validator_headers, cacheable_headers
    )

    # Method dispatch (M/N/O). GET/HEAD build a representation; write methods run
    # their processing nodes.
    if method in SAFE_METHODS:
        headers = {"Content-Type": chosen, **cacheable_headers}
        # O18 multiple representations? -> 300 with the list of offered types.
        if await resource.multiple_choices(ctx):
            ctx.trace.record("O18", int(Status.MULTIPLE_CHOICES))
            choices: object = {"choices": list(resource.PRODUCES)}
            return HttpResponse(
                status=int(Status.MULTIPLE_CHOICES),
                headers=headers,
                body=_body(choices, head=method == "HEAD", ctx=ctx),
            )
        # Otherwise build the representation and encode it via the negotiated
        # codec (HEAD suppresses the body; an async iterator streams — §8).
        value = await resource.represent(ctx)
        ctx.trace.record("O18", int(Status.OK))
        return HttpResponse(
            status=int(Status.OK),
            headers=headers,
            body=_body(value, head=method == "HEAD", ctx=ctx),
        )

    if method == "DELETE":
        return await _delete(resource, ctx, vary_headers)
    if method == "POST":
        return await _post(resource, ctx, chosen, vary_headers)
    # PUT / PATCH: update an existing resource.
    return await _write(resource, ctx, chosen, vary_headers)


# --- write path (§4 v2) ----------------------------------------------------


def _parse_body(structured: object, model_type: object) -> object:
    """Semantic parse: structured -> a typed model (Pydantic ``model_validate``
    when the type supports it); otherwise pass the decoded structure through."""

    if model_type is None or model_type is object:
        return structured
    model_validate = getattr(model_type, "model_validate", None)
    if callable(model_validate):
        return model_validate(structured)  # raises ValueError on a bad body
    return structured


def _apply_body_type(resource: Resource[Any]) -> object | None:
    """The declared type of apply()'s ``body`` param (for the semantic parse)."""

    try:
        return get_type_hints(type(resource).apply).get("body")
    except Exception:  # noqa: BLE001 — unresolvable annotation -> no model parse
        return None


async def _apply(resource: Resource[Any], ctx: Ctx) -> object:
    """Decode + parse the request body (parse, don't validate) and run apply().

    The body is decoded by the negotiated codec and parsed into apply()'s
    declared ``body`` type; a failure at either step is a 400, recorded at P0.
    """

    media = parse_content_type(ctx.request.headers.get("content-type"))
    codec = ctx.codecs.get(media or "")
    if codec is None:
        raise _halt(ctx, "B5", Status.UNSUPPORTED_MEDIA_TYPE)
    try:
        raw = await ctx.request.body()
    except BodyTooLarge:
        # The read exceeded the resource's MAX_BODY_BYTES (a chunked/lying
        # Content-Length that B4's header check couldn't catch up front).
        raise _halt(ctx, "B4", Status.REQUEST_ENTITY_TOO_LARGE) from None
    except BodyMalformed:
        # Bytes read disagree with Content-Length — a framing error.
        raise _halt(ctx, "B9", Status.BAD_REQUEST) from None
    try:
        structured = codec.decode(raw)
        body = _parse_body(structured, _apply_body_type(resource))
    except ValueError, TypeError, UnicodeDecodeError, RecursionError:
        # RecursionError: deeply nested input (json.loads / a recursive model)
        # exceeds the interpreter's depth — a malformed body, not a server fault,
        # so 400 rather than an escaped 500.
        raise _halt(ctx, "P0", Status.BAD_REQUEST) from None
    ctx.trace.record("P0", True)
    return await resource.apply(ctx, body)


def _finish(
    ctx: Ctx, status: Status, value: object, chosen: str, headers: dict[str, str]
) -> HttpResponse:
    """O20: a None entity yields 204 (or keeps 201/etc.); a value yields a body."""

    if value is None:
        final = Status.NO_CONTENT if status is Status.OK else status
        ctx.trace.record("O20", int(final))
        return HttpResponse(status=int(final), headers=headers)
    ctx.trace.record("O20", int(status))
    return HttpResponse(
        status=int(status),
        headers={"Content-Type": chosen, **headers},
        body=_body(value, head=False, ctx=ctx),
    )


async def _delete(
    resource: Resource[Any], ctx: Ctx, headers: dict[str, str]
) -> HttpResponse:
    # M16 DELETE? -> M20 delete_resource.
    if not await resource.delete_resource(ctx):
        raise _halt(ctx, "M20", Status.INTERNAL_SERVER_ERROR)
    completed = await resource.delete_completed(ctx)
    status = Status.NO_CONTENT if completed else Status.ACCEPTED
    ctx.trace.record("M20", int(status))
    return HttpResponse(status=int(status), headers=headers)


async def _post(
    resource: Resource[Any], ctx: Ctx, chosen: str, headers: dict[str, str]
) -> HttpResponse:
    # N16 POST? -> N11 post_is_create?
    if await resource.post_is_create(ctx):
        ctx.trace.record("N11", True)
        location = await resource.create_path(ctx)
        value = await _apply(resource, ctx)
        return _finish(
            ctx, Status.CREATED, value, chosen, {**headers, "Location": location}
        )
    ctx.trace.record("N11", False)
    value = await resource.process_post(ctx)
    return _finish(ctx, Status.OK, value, chosen, headers)


async def _write(
    resource: Resource[Any], ctx: Ctx, chosen: str, headers: dict[str, str]
) -> HttpResponse:
    # O16 PUT/PATCH -> O14 is_conflict? -> 409, else apply the acceptor.
    if await resource.is_conflict(ctx):
        raise _halt(ctx, "O14", Status.CONFLICT)
    ctx.trace.record("O14", False)
    value = await _apply(resource, ctx)
    return _finish(ctx, Status.OK, value, chosen, headers)


async def _handle_missing(
    resource: Resource[Any], ctx: Ctx, method: str, chosen: str
) -> HttpResponse:
    """The G7-false branch: create (PUT), redirect/gone (previously_existed), 404."""

    # H7 If-Match on a non-existent resource cannot be satisfied -> 412.
    if ctx.request.headers.get("if-match") is not None:
        raise _halt(ctx, "H7", Status.PRECONDITION_FAILED)

    # I7 PUT? -> create at the request URI (P3 is_conflict? -> 409, else 201).
    if method == "PUT":
        ctx.trace.record("I7", True)
        if await resource.is_conflict(ctx):
            raise _halt(ctx, "P3", Status.CONFLICT)
        ctx.trace.record("P3", False)
        value = await _apply(resource, ctx)
        return _finish(ctx, Status.CREATED, value, chosen, {})

    # K7 previously_existed? -> K5 moved_permanently 301 / L5 moved_temporarily
    # 307 / else 410 Gone.
    if await resource.previously_existed(ctx):
        ctx.trace.record("K7", True)
        moved = await resource.moved_permanently(ctx)
        if moved is not None:
            raise _halt(ctx, "K5", Status.MOVED_PERMANENTLY, {"Location": moved})
        permanent = await resource.permanent_redirect(ctx)
        if permanent is not None:  # K5a -> 308 (v4, RFC 7538): method-preserving
            raise _halt(ctx, "K5a", Status.PERMANENT_REDIRECT, {"Location": permanent})
        temporary = await resource.moved_temporarily(ctx)
        if temporary is not None:
            raise _halt(ctx, "L5", Status.TEMPORARY_REDIRECT, {"Location": temporary})
        raise _halt(ctx, "M5", Status.GONE)
    ctx.trace.record("K7", False)

    # L7: no create path applies -> 404.
    raise _halt(ctx, "L7", Status.NOT_FOUND)


def _not_modified(headers: dict[str, str]) -> HaltResponse:
    return HaltResponse(HttpResponse(status=int(Status.NOT_MODIFIED), headers=headers))


async def _check_preconditions(
    ctx: Ctx,
    method: str,
    etag: str | None,
    last_modified: datetime | None,
    validator_headers: dict[str, str],
    not_modified_headers: dict[str, str],
) -> None:
    """Evaluate conditional headers in canonical order (G8-L17).

    ``If-Match`` / ``If-Unmodified-Since`` fail with 412. ``If-None-Match`` yields
    304 for GET/HEAD but 412 for writes. ``If-Modified-Since`` yields 304 for
    GET/HEAD. Nodes are recorded only when they fire (short-circuit).
    """

    headers = ctx.request.headers
    safe = method in SAFE_METHODS

    # G8/G11 If-Match -> 412
    ifm = headers.get("if-match")
    if ifm is not None and not if_match_matches(ifm, etag):
        raise _halt(ctx, "G11", Status.PRECONDITION_FAILED, dict(validator_headers))

    # H10/H12 If-Unmodified-Since -> 412. Ignored entirely when If-Match is
    # present (RFC 9110 §13.1.4) and treated as passing when unverifiable (no
    # Last-Modified / unparseable date) — only a *known* later modification 412s.
    ius = headers.get("if-unmodified-since")
    if ifm is None and ius is not None and modified_since(ius, last_modified):
        raise _halt(ctx, "H12", Status.PRECONDITION_FAILED, dict(validator_headers))

    # I12/K13 If-None-Match -> 304 (GET/HEAD) / 412 (writes)
    inm = headers.get("if-none-match")
    if inm is not None and if_none_match_matches(inm, etag):
        if safe:
            ctx.trace.record("K13", int(Status.NOT_MODIFIED))
            raise _not_modified(not_modified_headers)
        raise _halt(ctx, "K13", Status.PRECONDITION_FAILED, dict(validator_headers))

    # L13/L17 If-Modified-Since -> 304 (GET/HEAD only, and only if If-None-Match
    # was absent).
    if safe and inm is None:
        ims = headers.get("if-modified-since")
        if ims is not None and not_modified_since(ims, last_modified):
            ctx.trace.record("L17", int(Status.NOT_MODIFIED))
            raise _not_modified(not_modified_headers)


async def _vary_headers(
    resource: Resource[Any], ctx: Ctx, offered: list[str]
) -> dict[str, str]:
    """Build the ``Vary`` header from resource variances + Accept-negotiation."""

    names = list(await resource.variances(ctx))
    if len(offered) > 1 and "Accept" not in names:
        names.insert(0, "Accept")
    return {"Vary": ", ".join(names)} if names else {}


async def _cache_headers(resource: Resource[Any], ctx: Ctx) -> dict[str, str]:
    """Build Expires / Cache-Control from the resource's caching callbacks (v3)."""

    result: dict[str, str] = {}
    expires = await resource.expires(ctx)
    if expires is not None:
        result["Expires"] = http_date(expires)
    cache_control = await resource.cache_control(ctx)
    if cache_control is not None:
        result["Cache-Control"] = cache_control
    return result


def _body(value: object, *, head: bool, ctx: Ctx) -> bytes | AsyncIterator[bytes]:
    """Turn a represent()/apply() return into a response body.

    HEAD suppresses it; an async iterator streams untouched (§8); anything else
    is encoded by the negotiated codec (falling back to JSON serialization).
    """

    if head:
        return b""
    if isinstance(value, AsyncIterator):
        return cast("AsyncIterator[bytes]", value)
    codec = ctx.codecs.get(ctx.chosen_media_type or "")
    return codec.encode(value) if codec is not None else serialize(value)
