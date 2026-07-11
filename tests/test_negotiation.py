"""Multi-type negotiation + Vary (PLAN.md M1).

Content negotiation over multiple offered types, and the Vary header that keeps
negotiated responses cacheable by intermediaries.
"""

from __future__ import annotations

import pytest

from tests.conftest import FIXED_ETAG, Toggles

JSON = "application/json"
CSV = "text/csv"


@pytest.mark.parametrize(
    ("accept", "expected_ct"),
    [
        ("text/csv", CSV),
        ("application/json", JSON),
        ("application/json;q=0.5, text/csv;q=0.9", CSV),
        ("application/json;q=0.9, text/csv;q=0.5", JSON),
        ("application/json, text/csv;q=0", JSON),  # csv rejected -> json
        ("*/*", JSON),  # tie -> first offered wins
    ],
)
def test_multi_type_negotiation(client_for, accept, expected_ct) -> None:
    resp = client_for(Toggles(offered=[JSON, CSV])).get(
        "/r", headers={"accept": accept}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == expected_ct


def test_406_when_no_offer_acceptable(client_for) -> None:
    resp = client_for(Toggles(offered=[JSON, CSV])).get(
        "/r", headers={"accept": "image/png"}
    )
    assert resp.status_code == 406


def test_406_when_only_offer_is_q0_rejected(client_for) -> None:
    # Rejecting the sole matching offer with q=0, and listing nothing else
    # acceptable, is a 406 — not a fallback to an unlisted type.
    resp = client_for(Toggles(offered=[CSV])).get(
        "/r", headers={"accept": "text/csv;q=0"}
    )
    assert resp.status_code == 406


def test_vary_accept_added_when_multiple_types_offered(client_for) -> None:
    resp = client_for(Toggles(offered=[JSON, CSV])).get("/r")
    assert resp.status_code == 200
    assert "Accept" in {v.strip() for v in resp.headers["vary"].split(",")}


def test_no_vary_when_single_type_and_no_variances(client_for) -> None:
    resp = client_for(Toggles(offered=[JSON])).get("/r")
    assert resp.status_code == 200
    assert "vary" not in resp.headers


def test_resource_variances_merge_with_accept(client_for) -> None:
    resp = client_for(Toggles(offered=[JSON, CSV], variances=["Authorization"])).get(
        "/r"
    )
    vary = {v.strip() for v in resp.headers["vary"].split(",")}
    assert vary == {"Accept", "Authorization"}


def test_vary_present_on_304(client_for) -> None:
    # A 304 must echo Vary so caches revalidate against the right key.
    resp = client_for(Toggles(offered=[JSON, CSV])).get(
        "/r",
        headers={"if-none-match": FIXED_ETAG, "accept": JSON},
    )
    assert resp.status_code == 304
    assert "Accept" in {v.strip() for v in resp.headers["vary"].split(",")}
