"""The decision-graph walk (PLAN.md §6).

``run(resource, request)`` executes the v0 subset of the webmachine diagram as a
straight-line function with labeled sections (readability over a data-driven
interpreter — §6). Node labels match the canonical flowchart so this is
diff-able against the spec (§2.4). Each node records to ``ctx.trace`` before it
decides; any node may raise :class:`HaltResponse` to short-circuit.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .conditional import http_date, if_none_match_matches, not_modified_since
from .http import HaltResponse, HttpResponse, Status
from .negotiation import choose_media_type
from .resource import Ctx
from .trace import TRACE_HEADER

if TYPE_CHECKING:
    from .http import HttpRequest
    from .resource import Producer, Resource

SAFE_METHODS = frozenset({"GET", "HEAD"})


async def run(
    resource: Resource, request: HttpRequest, *, debug: bool = False
) -> HttpResponse:
    """Walk the graph for one request and return an HttpResponse value object.

    In ``debug`` mode the ordered node path is attached as the
    ``X-Asgimachine-Trace`` response header on every exit path (§9).
    """

    ctx = Ctx(request=request)
    try:
        response = await _walk(resource, ctx)
    except HaltResponse as halt:
        response = halt.response
    if debug:
        response.headers[TRACE_HEADER] = ctx.trace.header_value
    return response


def _halt(
    ctx: Ctx, node: str, status: Status, headers: dict[str, str] | None = None
) -> HaltResponse:
    ctx.trace.record(node, int(status))
    return HaltResponse(HttpResponse(status=int(status), headers=headers or {}))


def _allow_header(methods: list[str]) -> str:
    seen: list[str] = []
    for method in [*methods, "OPTIONS"]:
        if method not in seen:
            seen.append(method)
    return ", ".join(seen)


async def _walk(resource: Resource, ctx: Ctx) -> HttpResponse:
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

    # B3 OPTIONS? -> 200 with Allow (no body). Canonical order places B3 ahead of
    # content negotiation, so OPTIONS is not subject to Accept (never 406).
    if method == "OPTIONS":
        ctx.trace.record("B3", True)
        return HttpResponse(status=int(Status.OK), headers={"Allow": allow})

    # C3/C4 Accept -> media type -> 406
    provided = await resource.content_types_provided(ctx)
    offered = [mtype for mtype, _ in provided]
    chosen = choose_media_type(request.headers.get("accept"), offered)
    if chosen is None:
        raise _halt(ctx, "C4", Status.NOT_ACCEPTABLE)
    ctx.chosen_media_type = chosen
    ctx.trace.record("C4", chosen)
    producer: Producer = dict(provided)[chosen]

    # G7 resource_exists? -> 404
    if not await resource.resource_exists(ctx):
        raise _halt(ctx, "G7", Status.NOT_FOUND)
    ctx.trace.record("G7", True)

    # Conditional GET (G8-L17 subset): compute validators once, reuse for both
    # the precondition check and the final response headers.
    etag = await resource.generate_etag(ctx)
    last_modified = await resource.last_modified(ctx)
    validator_headers: dict[str, str] = {}
    if etag is not None:
        validator_headers["ETag"] = etag
    if last_modified is not None:
        validator_headers["Last-Modified"] = http_date(last_modified)

    if method in SAFE_METHODS:
        inm = request.headers.get("if-none-match")
        if inm is not None and if_none_match_matches(inm, etag):
            ctx.trace.record("K13", Status.NOT_MODIFIED)
            raise HaltResponse(
                HttpResponse(
                    status=int(Status.NOT_MODIFIED), headers=validator_headers
                ),
            )
        ims = request.headers.get("if-modified-since")
        # If-Modified-Since applies only when If-None-Match is absent.
        if inm is None and ims is not None and not_modified_since(ims, last_modified):
            ctx.trace.record("L17", Status.NOT_MODIFIED)
            raise HaltResponse(
                HttpResponse(
                    status=int(Status.NOT_MODIFIED), headers=validator_headers
                ),
            )

    # O18 build representation (HEAD suppresses the body).
    value = await producer(ctx)
    body = b"" if method == "HEAD" else _serialize(value)
    headers = {"Content-Type": chosen, **validator_headers}
    ctx.trace.record("O18", int(Status.OK))
    return HttpResponse(status=int(Status.OK), headers=headers, body=body)


def _serialize(value: Any) -> bytes:
    """Turn a producer's return value into bytes (PLAN.md §6).

    bytes/str pass through; Pydantic models via ``model_dump_json`` (no hard
    dependency on Pydantic in the core); everything else via ``json.dumps``.
    """

    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    model_dump_json = getattr(value, "model_dump_json", None)
    if callable(model_dump_json):
        result = model_dump_json()  # Pydantic returns str
        return result.encode() if isinstance(result, str) else str(result).encode()
    return json.dumps(value).encode()
