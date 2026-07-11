"""The Starlette substrate — the ONLY module that imports Starlette (PLAN.md §2.6).

It adapts Starlette's ``Request`` to the core's :class:`HttpRequest` protocol,
turns the core's :class:`HttpResponse` back into a Starlette response, and builds
a ``Starlette`` app that routes to resources through :func:`core.run`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from ..core import run
from ..http import HttpResponse

if TYPE_CHECKING:
    from starlette.routing import BaseRoute
    from starlette.types import Receive, Scope, Send

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

    async def body(self) -> bytes:
        return await self._request.body()


def _to_starlette(response: HttpResponse) -> Response:
    if response.is_stream:
        return StreamingResponse(
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

    __slots__ = ("_resource",)

    def __init__(self, resource: Resource) -> None:
        self._resource = resource

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        response = await run(self._resource, _StarletteRequest(request))
        await _to_starlette(response)(scope, receive, send)


def resource_route(path: str, resource: Resource) -> Route:
    """Build a Starlette ``Route`` that runs ``resource`` through the graph."""

    return Route(path, _ResourceEndpoint(resource), name=type(resource).__name__)


def build_app(routes: list[BaseRoute], *, debug: bool = False) -> Starlette:
    """Assemble the composition root into an ASGI application."""

    return Starlette(debug=debug, routes=routes)
