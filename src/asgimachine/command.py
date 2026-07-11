"""The command lane (PLAN.md §2.5, §12).

Genuinely *command-shaped* endpoints — credential exchange, webhook receivers —
run as plain handlers on the same substrate, bypassing the decision graph. The
tell that an endpoint belongs here: you'd be inventing an unaddressable noun and
faking ``resource_exists`` / ``content_types_provided`` to satisfy the model. Let
commands be commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .http import HttpResponse, Status, serialize

if TYPE_CHECKING:
    from .http import HttpRequest


class Command:
    """Base class for the plain-handler lane. Override :meth:`handle`."""

    async def handle(self, request: HttpRequest) -> HttpResponse:
        raise NotImplementedError


def json_response(
    value: object,
    *,
    status: Status | int = Status.OK,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    """Build a JSON :class:`HttpResponse` — the ergonomic return for a command."""

    return HttpResponse(
        status=int(status),
        headers={"Content-Type": "application/json", **(headers or {})},
        body=serialize(value),
    )
