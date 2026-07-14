"""Wide-event logging: one structured event per request through an EventSink.

The core fills owned fields (OTel conventions + the asgm.* namespace) and emits
``ctx.event`` once at the boundary — after lifespan teardown, or at stream-close
for a streamed body. Resources and instrumented code enrich it in place.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Mapping

from starlette.testclient import TestClient

from asgimachine.command import Command, json_response
from asgimachine.event import Event
from asgimachine.http import HttpResponse, Status
from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, command_route, resource_route


class CaptureSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


def _client(resource, *, sink=None, on_exception=None, rse=True):
    app = build_app(
        [resource_route("/r", resource)],
        debug=True,
        event_sink=sink,
        on_exception=on_exception,
    )
    return TestClient(app, raise_server_exceptions=rse)


class Ok(Resource):
    async def represent(self, ctx: Ctx) -> object:
        return {"ok": True}


# --- owned fields + one-per-request -----------------------------------------


def test_emits_one_event_with_owned_fields() -> None:
    sink = CaptureSink()
    resp = _client(Ok(), sink=sink).get("/r")
    assert resp.status_code == 200
    assert len(sink.events) == 1  # exactly once
    ev = sink.events[0]
    assert ev["http.request.method"] == "GET"
    assert ev["url.path"] == "/r"
    assert ev["http.response.status_code"] == 200
    assert ev["asgm.resource"] == "Ok"
    assert ev["asgm.outcome"] == "ok"
    assert ev["asgm.media_type"] == "application/json"
    assert "C4" in str(ev["asgm.decision_path"])
    assert isinstance(ev["duration_ms"], float)


def test_no_sink_does_not_break_the_request() -> None:
    assert _client(Ok()).get("/r").status_code == 200


def test_http_route_is_the_template_not_the_path() -> None:
    app = build_app([resource_route("/notes/{id}", Ok())], debug=True)
    sink = CaptureSink()
    app.state.event_sink = sink
    TestClient(app).get("/notes/42")
    ev = sink.events[0]
    assert ev["url.path"] == "/notes/42"  # concrete
    assert ev["http.route"] == "/notes/{id}"  # low-cardinality template


# --- resource enrichment -----------------------------------------------------


def test_resource_enriches_the_event() -> None:
    class Enriched(Resource):
        async def represent(self, ctx: Ctx) -> object:
            ctx.event["account.id"] = "acct_123"  # a domain field
            return {"ok": True}

    sink = CaptureSink()
    _client(Enriched(), sink=sink).get("/r")
    assert sink.events[0]["account.id"] == "acct_123"


# --- outcomes: halt / handled error / propagated ----------------------------


def test_halt_records_outcome_and_halted_at() -> None:
    class Missing(Resource):
        async def resource_exists(self, ctx: Ctx) -> bool:
            return False

        async def represent(self, ctx: Ctx) -> object:
            return {}

    sink = CaptureSink()
    resp = _client(Missing(), sink=sink).get("/r")
    assert resp.status_code == 404
    ev = sink.events[0]
    assert ev["asgm.outcome"] == "halt"
    assert ev["http.response.status_code"] == 404
    assert ev["asgm.halted_at"] == "L7"  # the terminal not-found node


def test_handled_500_records_error_fields() -> None:
    async def handler(ctx: Ctx, exc: Exception) -> None:
        return None  # graph-owned 500

    class Boom(Resource):
        async def represent(self, ctx: Ctx) -> object:
            raise RuntimeError("boom")

    sink = CaptureSink()
    resp = _client(Boom(), sink=sink, on_exception=handler).get("/r")
    assert resp.status_code == 500
    ev = sink.events[0]
    assert ev["asgm.outcome"] == "error"
    assert ev["http.response.status_code"] == 500
    assert ev["exception.type"] == "RuntimeError"
    assert ev["exception.message"] == "boom"
    assert ev["error.type"] == "RuntimeError"


def test_propagated_exception_still_emits_once() -> None:
    class Boom(Resource):
        async def represent(self, ctx: Ctx) -> object:
            raise RuntimeError("boom")

    sink = CaptureSink()
    # Default handler re-raises; client swallows so we can inspect the sink.
    _client(Boom(), sink=sink, rse=False).get("/r")
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev["asgm.outcome"] == "propagated"
    assert ev["exception.type"] == "RuntimeError"
    assert "http.response.status_code" not in ev  # the graph never owned a status


# --- a broken sink must not break the request -------------------------------


def test_broken_sink_is_swallowed() -> None:
    class BrokenSink:
        def emit(self, event: Mapping[str, object]) -> None:
            raise RuntimeError("sink down")

    resp = _client(Ok(), sink=BrokenSink()).get("/r")
    assert resp.status_code == 200  # observability failure is not a request failure


# --- emit happens after teardown (so a lifespan merge lands) -----------------


def test_lifespan_merge_lands_in_the_event() -> None:
    # A field written in the lifespan's teardown half (where a DB accumulator would
    # be merged) must appear in the emitted event -> the emit is after teardown.
    class Merged(Resource):
        async def lifespan(self, ctx: Ctx) -> AsyncGenerator[None]:
            yield
            ctx.event["db.query_count"] = 3  # merged at release

        async def represent(self, ctx: Ctx) -> object:
            return {"ok": True}

    sink = CaptureSink()
    _client(Merged(), sink=sink).get("/r")
    assert sink.events[0]["db.query_count"] == 3


# --- streaming: event emitted at stream close --------------------------------


def test_streaming_emits_at_close_with_merge() -> None:
    class Streamer(Resource):
        async def lifespan(self, ctx: Ctx) -> AsyncGenerator[None]:
            yield
            ctx.event["db.query_count"] = 7  # merged when the stream drains

        async def represent(self, ctx: Ctx) -> object:
            async def body() -> AsyncIterator[bytes]:
                yield b"chunk-1"
                yield b"chunk-2"

            return body()

    sink = CaptureSink()
    resp = _client(Streamer(), sink=sink).get("/r")
    assert resp.status_code == 200
    assert resp.content == b"chunk-1chunk-2"
    # The event fired at stream close, after teardown merged the field.
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev["asgm.outcome"] == "ok"
    assert ev["http.response.status_code"] == 200
    assert ev["db.query_count"] == 7


def test_event_type_is_a_plain_dict() -> None:
    ev: Event = {}
    ev["k"] = 1
    assert ev == {"k": 1}


# --- command lane: a thin event through the same sink -----------------------


def _cmd_client(command, *, sink=None, rse=True):
    app = build_app([command_route("/cmd", command)], event_sink=sink)
    return TestClient(app, raise_server_exceptions=rse)


class Echo(Command):
    async def handle(self, request) -> HttpResponse:
        return json_response({"ok": True})


def test_command_emits_thin_event() -> None:
    sink = CaptureSink()
    resp = _cmd_client(Echo(), sink=sink).post("/cmd")
    assert resp.status_code == 200
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev["asgm.lane"] == "command"
    assert ev["asgm.command"] == "Echo"
    assert ev["http.request.method"] == "POST"
    assert ev["url.path"] == "/cmd"
    assert ev["http.route"] == "/cmd"
    assert ev["http.response.status_code"] == 200
    assert ev["asgm.outcome"] == "ok"
    assert isinstance(ev["duration_ms"], float)
    assert "asgm.decision_path" not in ev  # no graph in this lane


def test_command_client_error_is_halt() -> None:
    class Rejects(Command):
        async def handle(self, request) -> HttpResponse:
            return json_response({"error": "nope"}, status=Status.BAD_REQUEST)

    sink = CaptureSink()
    resp = _cmd_client(Rejects(), sink=sink).post("/cmd")
    assert resp.status_code == 400
    ev = sink.events[0]
    assert ev["asgm.outcome"] == "halt"
    assert ev["http.response.status_code"] == 400


def test_command_exception_emits_propagated() -> None:
    class CmdBoom(Command):
        async def handle(self, request) -> HttpResponse:
            raise RuntimeError("kaboom")

    sink = CaptureSink()
    _cmd_client(CmdBoom(), sink=sink, rse=False).post("/cmd")
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev["asgm.outcome"] == "propagated"
    assert ev["exception.type"] == "RuntimeError"
    assert ev["exception.message"] == "kaboom"
    assert "http.response.status_code" not in ev
