"""Streaming / SSE conformance (PLAN.md §8, M3).

A producer returning an async iterator streams through the graph; the graph fully
governs the envelope (auth/negotiation) before commit; a mid-stream failure
becomes an SSE ``error`` frame rather than a 500 (post-commit boundary).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from asgimachine.resource import Ctx, Resource
from asgimachine.streaming import guard_sse, sse_event
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import assert_trace, make_client


class StreamResource(Resource):
    def __init__(
        self, *, fail_after: int | None = None, authorized: bool = True
    ) -> None:
        self._fail_after = fail_after
        self._authorized = authorized

    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return ["GET"]

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        return True if self._authorized else "Bearer"

    async def content_types_provided(self, ctx: Ctx):
        return [("text/event-stream", self.to_events)]

    async def to_events(self, ctx: Ctx) -> AsyncIterator[bytes]:
        return guard_sse(self._events())

    async def _events(self) -> AsyncIterator[bytes]:
        for n in range(3):
            yield sse_event({"n": n}, event="tick")
            if self._fail_after is not None and n == self._fail_after:
                raise RuntimeError("boom mid-stream")


def _client(resource: StreamResource):
    return make_client(build_app([resource_route("/s", resource)], debug=True))


def test_stream_returns_200_event_stream() -> None:
    resp = _client(StreamResource()).get("/s")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: tick" in resp.text
    assert 'data: {"n": 0}' in resp.text


def test_stream_walks_the_graph() -> None:
    resp = _client(StreamResource()).get("/s")
    assert_trace(resp, ["B13", "B12", "B10", "B8", "B7", "C4", "G7", "O18"])


def test_midstream_error_becomes_sse_error_frame() -> None:
    # Fails after the second event; status is already 200 (committed), so the
    # failure surfaces as an SSE error frame, not a 500.
    resp = _client(StreamResource(fail_after=1)).get("/s")
    assert resp.status_code == 200
    assert "event: tick" in resp.text
    assert "event: error" in resp.text


def test_envelope_auth_precedes_stream() -> None:
    # The graph governs the envelope: an unauthorized request never streams.
    resp = _client(StreamResource(authorized=False)).get("/s")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert "event:" not in resp.text


def test_envelope_negotiation_precedes_stream() -> None:
    resp = _client(StreamResource()).get("/s", headers={"accept": "application/json"})
    assert resp.status_code == 406


# --- the examples/ POST SSE endpoint ---------------------------------------


def test_example_post_streams_events() -> None:
    from examples.events import EventsResource

    client = make_client(
        build_app([resource_route("/events", EventsResource())], debug=True)
    )
    resp = client.post(
        "/events",
        json={"count": 2},
        headers={"authorization": "Bearer u1"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"n": 0}' in resp.text
    assert "event: complete" in resp.text
    assert_trace(
        resp,
        [
            "B13",
            "B12",
            "B10",
            "B9",
            "B8",
            "B7",
            "B6",
            "B5",
            "B4",
            "C4",
            "G7",
            "N11",
            "O20",
        ],
    )


def test_example_post_requires_auth() -> None:
    from examples.events import EventsResource

    client = make_client(build_app([resource_route("/events", EventsResource())]))
    resp = client.post("/events", json={"count": 1})
    assert resp.status_code == 401


def test_example_post_malformed_body_is_400() -> None:
    from examples.events import EventsResource

    client = make_client(build_app([resource_route("/events", EventsResource())]))
    resp = client.post(
        "/events",
        content=b"not json",
        headers={"authorization": "Bearer u1", "content-type": "application/json"},
    )
    assert resp.status_code == 400
