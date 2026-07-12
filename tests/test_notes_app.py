"""End-to-end behavior suite for the M4 dogfood app (PLAN.md §12).

Exercises the whole framework through examples/notes_app: the resource gradient
(public / simple-auth / policy-governed), the command lane, and the merged
decision + policy trace. This is the M4 go/no-go harness.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from asgimachine.testing import assert_trace
from examples.notes_app import Store, make_app


@pytest.fixture
def app_ctx():
    store = Store.seeded(
        {"alice": ("pw", "user"), "bob": ("pw", "user"), "admin": ("pw", "admin")}
    )
    client = TestClient(make_app(store, debug=True))

    def token(username: str) -> str:
        resp = client.post("/token", json={"username": username, "password": "pw"})
        return resp.json()["token"]

    return client, token


def _auth(tok: str) -> dict[str, str]:
    return {"authorization": f"Bearer {tok}"}


# --- command lane: /token ---------------------------------------------------


def test_token_issues_on_valid_credentials(app_ctx) -> None:
    client, _ = app_ctx
    resp = client.post("/token", json={"username": "alice", "password": "pw"})
    assert resp.status_code == 201
    assert "token" in resp.json()


def test_token_rejects_bad_credentials(app_ctx) -> None:
    client, _ = app_ctx
    resp = client.post("/token", json={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401


def test_token_rejects_bad_request(app_ctx) -> None:
    client, _ = app_ctx
    assert client.post("/token", json={"username": "alice"}).status_code == 400


def test_token_is_post_only(app_ctx) -> None:
    # Command lane: the router restricts methods (405), no graph involved.
    client, _ = app_ctx
    assert client.get("/token").status_code == 405


# --- public read-only resource ---------------------------------------------


def test_health_is_public(app_ctx) -> None:
    client, _ = app_ctx
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- collection: simple auth ------------------------------------------------


def test_collection_requires_auth(app_ctx) -> None:
    client, _ = app_ctx
    assert client.get("/notes").status_code == 401


def test_collection_create_and_list(app_ctx) -> None:
    client, token = app_ctx
    h = _auth(token("alice"))
    created = client.post("/notes", json={"text": "first"}, headers=h)
    assert created.status_code == 201
    loc = created.headers["location"]
    assert loc.startswith("/notes/")
    note_id = loc.removeprefix("/notes/")  # unguessable, so read it back from Location
    listing = client.get("/notes", headers=h)
    assert listing.json() == {"notes": [{"id": note_id, "text": "first"}]}


def test_collection_create_malformed_is_400(app_ctx) -> None:
    client, token = app_ctx
    resp = client.post("/notes", json={"nope": 1}, headers=_auth(token("alice")))
    assert resp.status_code == 400


# --- member: policy-governed authorization ---------------------------------


def _make_note(client: TestClient, tok: str) -> str:
    resp = client.post("/notes", json={"text": "hello"}, headers=_auth(tok))
    return resp.headers["location"]


def test_owner_can_read_and_update(app_ctx) -> None:
    client, token = app_ctx
    alice = token("alice")
    loc = _make_note(client, alice)
    assert client.get(loc, headers=_auth(alice)).status_code == 200
    assert (
        client.put(loc, json={"text": "edited"}, headers=_auth(alice)).status_code
        == 204
    )


def test_non_owner_cannot_read_403(app_ctx) -> None:
    # Notes are private: a non-owner (non-admin) reading another user's note is
    # denied by the policy default, recorded before B7 returns 403.
    client, token = app_ctx
    loc = _make_note(client, token("alice"))
    resp = client.get(loc, headers=_auth(token("bob")))
    assert resp.status_code == 403
    assert_trace(resp, ["B13", "B12", "B10", "B8", "policy:default", "B7"])


def test_non_owner_cannot_write_403(app_ctx) -> None:
    client, token = app_ctx
    loc = _make_note(client, token("alice"))
    resp = client.request("DELETE", loc, headers=_auth(token("bob")))
    assert resp.status_code == 403
    # No rule fires -> default deny, recorded before B7 returns 403.
    assert_trace(resp, ["B13", "B12", "B10", "B8", "policy:default", "B7"])


def test_owner_write_trace_shows_owner_rule(app_ctx) -> None:
    client, token = app_ctx
    alice = token("alice")
    loc = _make_note(client, alice)
    resp = client.put(loc, json={"text": "v2"}, headers=_auth(alice))
    assert resp.status_code == 204
    assert_trace(
        resp,
        [
            "B13",
            "B12",
            "B10",
            "B9",
            "B8",
            "policy:owner",
            "B7",
            "B6",
            "B5",
            "B4",
            "C4",
            "G7",
            "O14",
            "P0",
            "O20",
        ],
    )


def test_admin_can_delete_any_note(app_ctx) -> None:
    client, token = app_ctx
    loc = _make_note(client, token("alice"))
    admin = token("admin")
    assert client.request("DELETE", loc, headers=_auth(admin)).status_code == 204
    # Confirm it's gone via an *authorized* principal (admin): 404, not the 403 a
    # non-owner now gets (authorization runs before existence — webmachine order).
    assert client.get(loc, headers=_auth(admin)).status_code == 404


def test_member_conditional_get_304(app_ctx) -> None:
    client, token = app_ctx
    alice = token("alice")
    loc = _make_note(client, alice)
    first = client.get(loc, headers=_auth(alice))
    etag = first.headers["etag"]
    again = client.get(loc, headers={**_auth(alice), "if-none-match": etag})
    assert again.status_code == 304


def test_admin_can_put_create_but_user_cannot(app_ctx) -> None:
    client, token = app_ctx
    body = {"text": "seeded"}
    # A regular user cannot PUT-create at an arbitrary id (default deny -> 403).
    assert (
        client.put("/notes/x9", json=body, headers=_auth(token("bob"))).status_code
        == 403
    )
    # Admin can.
    assert (
        client.put("/notes/x9", json=body, headers=_auth(token("admin"))).status_code
        == 201
    )
