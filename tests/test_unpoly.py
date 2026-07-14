"""Unpoly example: the full 200 -> 304 -> 303 -> 200 hypermedia loop.

Unpoly drives conditional GET (poll -> 304 while unchanged) and POST-Redirect-Get
(write -> 303 -> re-fetch). The example serves both a full page and a bare fragment
from one URL, negotiated on ``X-Up-Version`` and separated in the ETag/Vary.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from examples.unpoly import NoteStore, make_app

UP = {"X-Up-Version": "3.0.0"}  # every Unpoly request carries this


def _client() -> TestClient:
    return TestClient(make_app(NoteStore(notes=["seed"]), debug=True))


def test_two_hundred_then_conditional_get_is_304() -> None:
    client = _client()
    first = client.get("/", headers=UP)
    assert first.status_code == 200
    assert first.headers["etag"] == '"notes-1-frag"'  # fragment variant
    assert first.headers["vary"] == "X-Up-Version"
    assert '<div id="notes"' in first.text and "<html" not in first.text

    again = client.get("/", headers={**UP, "if-none-match": first.headers["etag"]})
    assert again.status_code == 304  # nothing changed -> Unpoly keeps its fragment


def test_post_is_redirect_then_new_etag_serves_200() -> None:
    client = _client()
    stale = client.get("/", headers=UP).headers["etag"]

    created = client.post(
        "/",
        headers={**UP, "content-type": "application/x-www-form-urlencoded"},
        content="text=hello+unpoly",
        follow_redirects=False,
    )
    assert created.status_code == 303  # POST-Redirect-Get
    assert created.headers["location"] == "/"

    # The write bumped the validator, so the poll's stale If-None-Match now 200s.
    fresh = client.get("/", headers={**UP, "if-none-match": stale})
    assert fresh.status_code == 200
    assert fresh.headers["etag"] == '"notes-2-frag"'
    assert "hello unpoly" in fresh.text


def test_browser_navigation_gets_the_full_page() -> None:
    # No X-Up-Version -> the full document, a distinct ETag variant, same Vary axis.
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.headers["etag"] == '"notes-1-full"'
    assert resp.headers["vary"] == "X-Up-Version"
    assert "<html" in resp.text
