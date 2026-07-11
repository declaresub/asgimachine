"""Immutable feed / caching conformance (PLAN.md §4 v3, M5 acceptance).

Archived pages are immutable + CDN-cacheable; the head page revalidates via
conditional GET. Appending to the outbox changes the head validator but never an
archived page's.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from examples.feed import IMMUTABLE, FeedResource, Outbox
from asgimachine.substrate.starlette import build_app, resource_route


def _client(outbox: Outbox) -> TestClient:
    app = build_app([resource_route("/feed/{page}", FeedResource(outbox))], debug=True)
    return TestClient(app)


def _seeded() -> Outbox:
    outbox = Outbox()
    for i in range(7):  # pages 0,1 archived; page 2 head (1 event)
        outbox.append(f"e{i}")
    return outbox


def test_archived_page_is_immutable() -> None:
    resp = _client(_seeded()).get("/feed/0")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == IMMUTABLE
    assert resp.headers["etag"] == '"feed-0"'
    assert resp.json() == {"page": 0, "archived": True, "events": ["e0", "e1", "e2"]}


def test_head_page_revalidates() -> None:
    resp = _client(_seeded()).get("/feed/2")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["etag"] == '"feed-2-1"'
    assert resp.json()["archived"] is False


def test_head_page_conditional_get_304() -> None:
    client = _client(_seeded())
    etag = client.get("/feed/2").headers["etag"]
    again = client.get("/feed/2", headers={"if-none-match": etag})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"


def test_archived_page_conditional_get_304() -> None:
    resp = _client(_seeded()).get("/feed/0", headers={"if-none-match": '"feed-0"'})
    assert resp.status_code == 304
    assert resp.headers["cache-control"] == IMMUTABLE


def test_out_of_range_page_is_404() -> None:
    assert _client(_seeded()).get("/feed/9").status_code == 404


def test_append_changes_head_validator_not_archived() -> None:
    outbox = _seeded()
    client = _client(outbox)
    archived_before = client.get("/feed/0").headers["etag"]
    head_before = client.get("/feed/2").headers["etag"]

    outbox.append("e7")  # extends the head page

    assert client.get("/feed/0").headers["etag"] == archived_before  # immutable
    assert client.get("/feed/2").headers["etag"] != head_before  # head moved
