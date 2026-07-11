"""Input-bound + robustness guards on the header parsers (defensive hardening).

These pin the caps and qvalue clamping so a swap to a grammar parser later must
preserve the same defensive behavior.
"""

from __future__ import annotations

from asgimachine.conditional import if_none_match_matches
from asgimachine.negotiation import _clamp_qvalue, choose_media_type


def test_qvalue_clamps_inf_and_nan() -> None:
    assert _clamp_qvalue("inf") == 1.0
    assert _clamp_qvalue("nan") == 0.0
    assert _clamp_qvalue("2.5") == 1.0
    assert _clamp_qvalue("-1") == 0.0
    assert _clamp_qvalue("garbage") == 0.0
    assert _clamp_qvalue("0.7") == 0.7


def test_qinf_does_not_beat_a_normal_offer_unboundedly() -> None:
    # q=inf is clamped to 1.0, so it selects but stays within the offered set.
    chosen = choose_media_type("text/plain;q=inf", ["application/json", "text/plain"])
    assert chosen == "text/plain"


def test_accept_with_many_ranges_terminates() -> None:
    # A pathological Accept must not blow up; selection stays bounded and correct.
    hostile = ",".join(f"x/y{i}" for i in range(100_000)) + ",application/json"
    chosen = choose_media_type(hostile, ["application/json"])
    # The bound may drop the trailing json range, so the result is json or None —
    # never a hang and never a crash.
    assert chosen in {"application/json", None}


def test_if_none_match_with_many_tags_terminates() -> None:
    hostile = ",".join(f'"tag{i}"' for i in range(100_000))
    # Current tag is not in the (bounded) scanned prefix -> no match, no hang.
    assert if_none_match_matches(hostile, '"current"') is False


def test_if_none_match_early_tag_still_matches_under_cap() -> None:
    header = '"current",' + ",".join(f'"t{i}"' for i in range(100_000))
    assert if_none_match_matches(header, '"current"') is True
