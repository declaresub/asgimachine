"""Conditional-request evaluation helpers (PLAN.md §4 G8-L17 subset).

v0 covers the read path: ``If-None-Match`` vs a generated ETag and
``If-Modified-Since`` vs Last-Modified, both driving 304. ``If-Match`` /
``If-Unmodified-Since`` → 412 arrive with the write path in M2.
"""

from __future__ import annotations

from datetime import datetime, UTC
from email.utils import format_datetime, parsedate_to_datetime


def _split_etag(raw: str) -> tuple[bool, str]:
    """Return ``(is_weak, opaque_tag)`` for a single entity-tag token."""

    token = raw.strip()
    weak = token.startswith(("W/", "w/"))
    if weak:
        token = token[2:]
    return weak, token.strip('"')


def if_none_match_matches(header: str, etag: str | None) -> bool:
    """True when ``If-None-Match`` matches the current ETag (→ 304 for GET/HEAD).

    ``*`` matches any existing representation. Comparison is weak per RFC 9110
    §8.8.3.2 (the weak/strong flag is ignored for If-None-Match).
    """

    if etag is None:
        return False
    header = header.strip()
    if header == "*":
        return True
    _, current = _split_etag(etag)
    for candidate in header.split(","):
        _, tag = _split_etag(candidate)
        if tag == current:
            return True
    return False


def not_modified_since(header: str, last_modified: datetime | None) -> bool:
    """True when the resource was NOT modified after ``If-Modified-Since`` (→ 304)."""

    if last_modified is None:
        return False
    try:
        since = parsedate_to_datetime(header)
    except TypeError, ValueError:
        return False
    lm = last_modified if last_modified.tzinfo else last_modified.replace(tzinfo=UTC)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    # HTTP-date has second resolution; truncate to avoid sub-second false negatives.
    return lm.replace(microsecond=0) <= since


def http_date(value: datetime) -> str:
    """Format a datetime as an RFC 9110 HTTP-date (for Last-Modified headers)."""

    dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    return format_datetime(dt.astimezone(UTC), usegmt=True)
