"""The Starlette substrate — the ONLY module that imports Starlette (PLAN.md §2.6).

It adapts Starlette's ``Request`` to the core's :class:`HttpRequest` protocol,
turns the core's :class:`HttpResponse` back into a Starlette response, and builds
a ``Starlette`` app that routes to resources through :func:`core.run`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from ..core import run
from ..http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Sequence

    from starlette.middleware import Middleware
    from starlette.routing import BaseRoute
    from starlette.types import Receive, Scope, Send

    from ..codec import Codec
    from ..command import Command
    from ..resource import Resource


class _StarletteRequest:
    """Adapts a Starlette ``Request`` to the core's ``HttpRequest`` protocol."""

    __slots__ = ("_request",)

    def __init__(self, request: Request) -> None:
        self._request = request

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

    async def body(self) -> bytes:
        return await self._request.body()


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

    __slots__ = ("_codecs", "_resource")

    def __init__(
        self, resource: Resource[Any], codecs: dict[str, Codec] | None
    ) -> None:
        self._resource = resource
        self._codecs = codecs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        # Tie the decision-trace header to Starlette's own debug flag.
        debug = bool(getattr(scope.get("app"), "debug", False))
        response = await run(
            self._resource,
            _StarletteRequest(request),
            debug=debug,
            codecs=self._codecs,
        )
        await _to_starlette(response)(scope, receive, send)


def resource_route(
    path: str, resource: Resource[Any], *, codecs: dict[str, Codec] | None = None
) -> Route:
    """Build a Starlette ``Route`` that runs ``resource`` through the graph.

    ``codecs`` injects a media-type -> Codec registry (default: JSON only).
    """

    return Route(
        path, _ResourceEndpoint(resource, codecs), name=type(resource).__name__
    )


def command_route(
    path: str, command: Command, *, methods: Sequence[str] = ("POST",)
) -> Route:
    """Build a ``Route`` for a command (plain-handler lane, §2.5).

    Unlike a resource, a command does not walk the graph, so the router owns
    method restriction here (405 for an unlisted method) — that's fine for a
    command-shaped endpoint.
    """

    async def endpoint(request: Request) -> Response:
        return _to_starlette(await command.handle(_StarletteRequest(request)))

    endpoint.__name__ = type(command).__name__
    return Route(path, endpoint, methods=list(methods))


def build_app(
    routes: Sequence[BaseRoute],
    *,
    debug: bool = False,
    middleware: Sequence[Middleware] | None = None,
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
    """

    return Starlette(debug=debug, routes=routes, middleware=middleware)
