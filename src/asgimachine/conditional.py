"""Conditional-request evaluation helpers (PLAN.md §4 G8-L17 subset).

v0 covers the read path: ``If-None-Match`` vs a generated ETag and
``If-Modified-Since`` vs Last-Modified, both driving 304. ``If-Match`` /
``If-Unmodified-Since`` → 412 arrive with the write path in M2.
"""

from __future__ import annotations

from datetime import datetime, UTC
from email.utils import format_datetime, parsedate_to_datetime

# Defensive input bounds on the client-supplied If-None-Match list (no RFC limit
# on its length). Beyond the cap we stop scanning: a match among the first N
# tags still returns 304; the tail can only have produced more matches.
_MAX_INM_LEN = 8192
_MAX_ETAGS = 64


def _split_etag(raw: str) -> tuple[bool, str]:
    """Return ``(is_weak, opaque_tag)`` for a single entity-tag token."""

    token = raw.strip()
    weak = token.startswith(("W/", "w/"))
    if weak:
        token = token[2:]
    return weak, token.strip('"')


def if_none_match_matches(header: str, etag: str | None) -> bool:
    """True when ``If-None-Match`` matches the current ETag (→ 304 for GET/HEAD).

    ``*`` matches any existing representation — so it is checked *before* the ETag,
    since existence, not the presence of an ETag string, is what ``*`` tests (the
    caller only reaches here on the resource-exists branch). Otherwise comparison
    is weak per RFC 9110 §13.1.2 (the weak/strong flag is ignored for If-None-Match).
    """

    header = header.strip()
    if header == "*":
        return True
    if etag is None:
        return False
    _, current = _split_etag(etag)
    for candidate in header[:_MAX_INM_LEN].split(",")[:_MAX_ETAGS]:
        _, tag = _split_etag(candidate)
        if tag == current:
            return True
    return False


def if_match_matches(header: str, etag: str | None) -> bool:
    """True when ``If-Match`` is satisfied (→ proceed); False means 412.

    ``*`` is satisfied by any existing representation (the caller only evaluates
    this on the resource-exists branch). Otherwise the current ETag must appear in
    the list under the **strong** comparison function (RFC 9110 §13.1.1): a weak
    validator on *either* side never satisfies ``If-Match``.
    """

    header = header.strip()
    if header == "*":
        return True
    if etag is None:
        return False
    resource_weak, current = _split_etag(etag)
    if resource_weak:
        return False  # a weak resource validator can never satisfy a strong match
    for candidate in header[:_MAX_INM_LEN].split(",")[:_MAX_ETAGS]:
        candidate_weak, tag = _split_etag(candidate)
        if not candidate_weak and tag == current:
            return True
    return False


def _parse_since(
    header: str, last_modified: datetime | None
) -> tuple[datetime, datetime] | None:
    """``(last_modified, since)``, both tz-aware and truncated to seconds, or
    ``None`` when the precondition is *unverifiable* — no Last-Modified, or an
    unparseable HTTP-date (RFC 9110 §13.1.3/§13.1.4 both say to ignore such)."""

    if last_modified is None:
        return None
    try:
        since = parsedate_to_datetime(header)
    except TypeError, ValueError:
        return None
    lm = last_modified if last_modified.tzinfo else last_modified.replace(tzinfo=UTC)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    # HTTP-date has second resolution; truncate to avoid sub-second false results.
    return lm.replace(microsecond=0), since


def not_modified_since(header: str, last_modified: datetime | None) -> bool:
    """True when the resource was NOT modified after ``If-Modified-Since`` (→ 304).

    An unverifiable precondition yields False — serve the full response, never 304.
    """

    parsed = _parse_since(header, last_modified)
    return parsed is not None and parsed[0] <= parsed[1]


def modified_since(header: str, last_modified: datetime | None) -> bool:
    """True only when the resource is *known* to have been modified after the
    ``If-Unmodified-Since`` date (→ 412). An unverifiable precondition yields False
    — proceed, since RFC 9110 §13.1.4 says to ignore an unusable If-Unmodified-Since
    rather than fail it.
    """

    parsed = _parse_since(header, last_modified)
    return parsed is not None and parsed[0] > parsed[1]


def http_date(value: datetime) -> str:
    """Format a datetime as an RFC 9110 HTTP-date (for Last-Modified headers)."""

    dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    return format_datetime(dt.astimezone(UTC), usegmt=True)
