"""The Starlette substrate — the ONLY module that imports Starlette (PLAN.md §2.6).

It adapts Starlette's ``Request`` to the core's :class:`HttpRequest` protocol,
turns the core's :class:`HttpResponse` back into a Starlette response, and builds
a ``Starlette`` app that routes to resources through :func:`core.run`.
"""

from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from ..core import run
from ..event import emit_event, outcome
from ..http import (
    DEFAULT_MAX_BODY_BYTES,
    BodyMalformed,
    BodyTooLarge,
    HttpResponse,
    Status,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from starlette.middleware import Middleware
    from starlette.routing import BaseRoute
    from starlette.types import Receive, Scope, Send

    from ..codec import Codec
    from ..command import Command
    from ..event import EventSink
    from ..resource import OnException, Resource


class _StarletteRequest:
    """Adapts a Starlette ``Request`` to the core's ``HttpRequest`` protocol."""

    __slots__ = ("_cached_body", "_max_bytes", "_request", "_route")

    def __init__(
        self, request: Request, max_bytes: int, route: str | None = None
    ) -> None:
        self._request = request
        self._max_bytes = max_bytes
        self._cached_body: bytes | None = None
        self._route = route

    @property
    def route(self) -> str | None:
        # The route template, threaded from resource_route (Starlette doesn't put
        # it in the scope). None for a request built outside a route (a test).
        return self._route

    @property
    def method(self) -> str:
        return self._request.method

    @property
    def path(self) -> str:
        return self._request.url.path

    @property
    def headers(self) -> Mapping[str, str]:
        # Starlette's Headers is case-insensitive, satisfying the protocol.
        return self._request.headers

    @property
    def path_params(self) -> Mapping[str, str]:
        return self._request.path_params

    @property
    def query_params(self) -> Mapping[str, str]:
        # Starlette's QueryParams is a case-sensitive multidict; as a Mapping it
        # yields the last value per key, and offers .getlist() for repeats.
        return self._request.query_params

    def _declared_length(self) -> int | None:
        raw = self._request.headers.get("content-length")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None  # unparseable -> h11 already rejected it; treat as absent

    async def body(self) -> bytes:
        """Read the body, bounded at ``max_bytes`` (§6). Reads via ``stream`` so a
        chunked or lying Content-Length can't blow past the cap, and verifies the
        bytes read match a declared Content-Length (a mismatch is a framing error).
        """

        if self._cached_body is not None:
            return self._cached_body
        declared = self._declared_length()
        # Fast reject an honest Content-Length over the cap, before reading.
        if declared is not None and declared > self._max_bytes:
            raise BodyTooLarge
        chunks: list[bytes] = []
        total = 0
        async for chunk in self._request.stream():
            total += len(chunk)
            if total > self._max_bytes:  # cap the actual read
                raise BodyTooLarge
            chunks.append(chunk)
        if declared is not None and total != declared:
            raise BodyMalformed  # length disagrees with Content-Length -> 400
        self._cached_body = b"".join(chunks)
        return self._cached_body


class _ClosingStreamingResponse(StreamingResponse):
    """A ``StreamingResponse`` that ``aclose``s its body iterator after the
    response completes or the client disconnects.

    Starlette's ``StreamingResponse`` never ``aclose``s the body on the ASGI
    spec>=2.4 path — it leaves finalization to GC. The core wraps a streamed body
    in ``_ClosingStream``, whose ``aclose`` releases the per-request lifespan; this
    subclass guarantees that ``aclose`` is actually called (even on a disconnect,
    which raises out of ``super().__call__``), so teardown is deterministic rather
    than GC-timed.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                await aclose()


def _to_starlette(response: HttpResponse) -> Response:
    if response.is_stream:
        return _ClosingStreamingResponse(
            response.body,  # type: ignore[arg-type]
            status_code=response.status,
            headers=response.headers,
        )
    body = response.body if isinstance(response.body, (bytes, bytearray)) else b""
    return Response(content=body, status_code=response.status, headers=response.headers)


class _ResourceEndpoint:
    """An ASGI endpoint that runs a resource through the graph.

    Registered as a *class* (not a function) so Starlette treats it as raw ASGI
    and leaves the route method-unrestricted — the graph, not the router, owns
    405/501/OPTIONS/HEAD (PLAN.md §2.3). A function endpoint would be forced to
    ``methods=["GET"]``.
    """

    __slots__ = ("_codecs", "_resource", "_route")

    def __init__(
        self,
        resource: Resource[Any],
        codecs: dict[str, Codec] | None,
        route: str | None = None,
    ) -> None:
        self._resource = resource
        self._codecs = codecs
        self._route = route

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        app = scope.get("app")
        # Tie the decision-trace header to Starlette's own debug flag; read the
        # app-wide on_exception handler the same way (both live on the app).
        debug = bool(getattr(app, "debug", False))
        state = getattr(app, "state", None)
        response = await run(
            self._resource,
            _StarletteRequest(request, self._resource.MAX_BODY_BYTES, self._route),
            debug=debug,
            codecs=self._codecs,
            on_exception=getattr(state, "on_exception", None),
            event_sink=getattr(state, "event_sink", None),
        )
        await _to_starlette(response)(scope, receive, send)


def resource_route(
    path: str, resource: Resource[Any], *, codecs: dict[str, Codec] | None = None
) -> Route:
    """Build a Starlette ``Route`` that runs ``resource`` through the graph.

    ``codecs`` injects a media-type -> Codec registry (default: JSON only).
    """

    return Route(
        path,
        _ResourceEndpoint(resource, codecs, route=path),
        name=type(resource).__name__,
    )


def command_route(
    path: str,
    command: Command,
    *,
    methods: Sequence[str] = ("POST",),
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> Route:
    """Build a ``Route`` for a command (plain-handler lane, §2.5).

    Unlike a resource, a command does not walk the graph, so the router owns
    method restriction here (405 for an unlisted method) — that's fine for a
    command-shaped endpoint. ``max_body_bytes`` bounds the request body (the graph
    lane gets this from the resource's ``MAX_BODY_BYTES``; a command has no
    resource, so it is set here) — an over-cap body is 413, a Content-Length
    mismatch 400.
    """

    async def endpoint(request: Request) -> Response:
        started = perf_counter()
        wrapped = _StarletteRequest(request, max_body_bytes)
        sink = getattr(getattr(request, "app", None), "state", None)
        sink = getattr(sink, "event_sink", None)
        status: int | None = None
        exc: BaseException | None = None
        try:
            response = _to_starlette(await command.handle(wrapped))
            status = response.status_code
            return response
        except BodyTooLarge:
            status = int(Status.REQUEST_ENTITY_TOO_LARGE)
            return Response(status_code=status)
        except BodyMalformed:
            status = int(Status.BAD_REQUEST)
            return Response(status_code=status)
        except BaseException as raised:  # emit, then propagate (no graph 500 here)
            exc = raised
            raise
        finally:
            _emit_command_event(sink, command, request, path, status, exc, started)

    endpoint.__name__ = type(command).__name__
    return Route(path, endpoint, methods=list(methods))


def _emit_command_event(
    sink: EventSink | None,
    command: Command,
    request: Request,
    route: str,
    status: int | None,
    exc: BaseException | None,
    started: float,
) -> None:
    """The command lane's thin wide event — no graph, so no decision path, but the
    same sink, OTel field names, and outcomes as the resource lane."""

    if sink is None:
        return
    event: dict[str, object] = {
        "http.request.method": request.method,
        "url.path": request.url.path,
        "http.route": route,
        "asgm.lane": "command",
        "asgm.command": type(command).__name__,
        "asgm.outcome": outcome(status, exc),
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }
    if status is not None:
        event["http.response.status_code"] = status
    if exc is not None:
        event["exception.type"] = type(exc).__qualname__
        event["exception.message"] = str(exc)
        event["error.type"] = type(exc).__qualname__
    emit_event(sink, event)


def build_app(
    routes: Sequence[BaseRoute],
    *,
    debug: bool = False,
    middleware: Sequence[Middleware] | None = None,
    on_exception: OnException | None = None,
    event_sink: EventSink | None = None,
) -> Starlette:
    """Assemble the composition root into an ASGI application.

    ``middleware`` is passed straight to Starlette, so cross-cutting concerns are
    rented rather than baked into the graph (PLAN.md §2.1). CORS in particular is
    its own decision machine — mount Starlette's ``CORSMiddleware`` here and true
    preflights are answered before a request ever reaches the graph, while actual
    responses get their ``Access-Control-*`` headers on the way out::

        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware

        build_app(
            routes,
            middleware=[Middleware(CORSMiddleware, allow_origins=["https://app.example"])],
        )

    ``on_exception`` is the app-wide catch-all for an unexpected ``Exception`` raised
    during a resource's walk (a resource may override its own). It defaults to
    re-raising — so a bug propagates to Starlette's ``ServerErrorMiddleware`` (or an
    ASGI error reporter) as before — but a handler may report the error, enrich the
    request context, and return to have the graph own a negotiated 500 instead.

    ``event_sink``, when given, receives one wide event per request (``ctx.event``)
    — the canonical-log-line seam. None by default (nothing is emitted);
    :class:`asgimachine.event.LoggingEventSink` is the reference sink.
    """

    app = Starlette(debug=debug, routes=routes, middleware=middleware)
    # Carried on the app so each endpoint can read them from the ASGI scope at
    # request time (the same way it reads ``debug``), not close over every route.
    app.state.on_exception = on_exception
    app.state.event_sink = event_sink
    return app
