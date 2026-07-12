"""The connection example: a pooled connection is released after every request.

End-to-end through the Starlette substrate — the lifespan's `async with
pool.acquire()` must return the connection whether the request succeeds or halts.
"""

from __future__ import annotations

from asgimachine.testing import make_client
from examples.connection import Pool, WidgetsResource, make_app
from asgimachine.substrate.starlette import build_app, resource_route


def test_request_serves_from_a_connection_then_releases_it() -> None:
    pool = Pool()
    client = make_client(make_app(pool))

    r = client.get("/widgets")
    assert r.status_code == 200
    assert r.json()["served_by_conn"] == 1
    assert [w["name"] for w in r.json()["widgets"]] == ["sprocket", "flange"]
    # The connection went back to the pool.
    assert pool.live == 0
    assert pool.issued == 1


def test_each_request_gets_a_fresh_connection() -> None:
    pool = Pool()
    client = make_client(make_app(pool))

    first = client.get("/widgets").json()["served_by_conn"]
    second = client.get("/widgets").json()["served_by_conn"]
    assert (first, second) == (1, 2)  # distinct connections, in order
    assert pool.live == 0  # neither leaked


def test_connection_released_on_a_halt() -> None:
    # A method the resource doesn't allow halts at B10 (405) before represent,
    # but the lifespan still opened and must still release.
    pool = Pool()
    client = make_client(build_app([resource_route("/widgets", WidgetsResource(pool))]))

    assert client.post("/widgets", json={}).status_code == 405
    assert pool.live == 0
    assert pool.issued == 1  # the lifespan did open a connection
