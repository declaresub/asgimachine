"""Response-header validation: a malformed field-value is un-constructable (#11).

Every HttpResponse (both lanes) validates its header values against the RFC 9110
field-value grammar at construction. A CR/LF-bearing value — a response-splitting
vector, e.g. a client-influenced path param echoed into ETag/Location — fails
closed with 500 rather than reaching the wire.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from asgimachine.command import Command
from asgimachine.http import HttpRequest, HttpResponse, MalformedHeader, Status
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, command_route, resource_route


# --- unit: HttpResponse self-validates --------------------------------------


def test_httpresponse_rejects_crlf_value() -> None:
    with pytest.raises(MalformedHeader):
        HttpResponse(status=200, headers={"ETag": '"x"\r\nX-Injected: evil'})


def test_httpresponse_accepts_valid_values() -> None:
    resp = HttpResponse(status=200, headers={"ETag": '"v1"', "Location": "/x"})
    assert resp.status == 200


def test_malformed_header_message_omits_the_value() -> None:
    # The offending value is untrusted (may itself carry CRLF) -> keep it out of
    # the message to avoid log injection; the header name is enough to debug.
    with pytest.raises(MalformedHeader) as exc:
        HttpResponse(status=200, headers={"Location": "ok\r\nevil: 1"})
    assert "Location" in str(exc.value)
    assert "evil" not in str(exc.value)


# --- end to end: both lanes fail closed with no injected header --------------


def _client(app: Starlette) -> TestClient:
    # raise_server_exceptions=False so we observe the 500 the client would get,
    # rather than re-raising MalformedHeader into the test.
    return TestClient(app, raise_server_exceptions=False)


class InjectingResource(Resource):
    async def generate_etag(self, ctx: Ctx) -> str | None:
        return '"x"\r\nX-Injected: evil'

    async def represent(self, ctx: Ctx) -> object:
        return {}


def test_graph_crlf_header_is_500_without_injection() -> None:
    resp = _client(build_app([resource_route("/i", InjectingResource())])).get("/i")
    assert resp.status_code == 500
    assert "x-injected" not in resp.headers


class EchoEtagResource(Resource):
    async def generate_etag(self, ctx: Ctx) -> str | None:
        return f'"{ctx.request.path_params["id"]}"'

    async def represent(self, ctx: Ctx) -> object:
        return {}


def test_crlf_path_param_echoed_into_etag_is_rejected() -> None:
    # The realistic vector: %0D%0A in the path decodes to CRLF in the path param,
    # echoed into ETag -> rejected at construction, never emitted.
    app = build_app([resource_route("/e/{id}", EchoEtagResource())])
    resp = _client(app).get("/e/n1%0D%0AX-Injected:%20evil")
    assert resp.status_code == 500
    assert "x-injected" not in resp.headers


class InjectingCommand(Command):
    async def handle(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse(
            status=int(Status.OK), headers={"Location": "ok\r\nX-Injected: evil"}
        )


def test_command_crlf_header_is_500() -> None:
    resp = _client(build_app([command_route("/c", InjectingCommand())])).post("/c")
    assert resp.status_code == 500
    assert "x-injected" not in resp.headers
