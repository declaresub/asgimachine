"""The Resource base class and per-request context (PLAN.md §5).

Every callback is ``async`` and ships a *correct HTTP default* (§2.3): a resource
that overrides only ``content_types_provided``/``to_json`` already gets
405/406/404/304/501/503 behavior for free.

Per-request state lives on :class:`Ctx`, never on the shared resource instance —
resources hold only their wired collaborators (§2.2).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .trace import Trace

if TYPE_CHECKING:
    from .http import HttpRequest

# A producer turns the resolved context into a representation value (§6). The
# core serializes its return: bytes/str pass through, Pydantic models via
# ``model_dump_json``, everything else via ``json.dumps``.
Producer = Callable[["Ctx"], Awaitable[Any]]


@dataclass(slots=True)
class Ctx:
    """webmachine's ReqData + Context: per-request scratch state.

    Carries the request, holds what callbacks compute, and accumulates the
    decision trace. Attributes like ``user``/``entity`` are set by resource
    callbacks; the core only writes ``chosen_media_type``.
    """

    request: HttpRequest
    trace: Trace = field(default_factory=Trace)
    chosen_media_type: str | None = None
    allowed_methods: list[str] = field(default_factory=list[str])
    user: Any = None
    entity: Any = None
    extra: dict[str, Any] = field(default_factory=dict[str, Any])


class Resource:
    """Base class for graph-lane endpoints. Override only what you care about."""

    KNOWN_METHODS = frozenset(
        {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
    )

    # --- B13 ---------------------------------------------------------------
    async def service_available(self, ctx: Ctx) -> bool:
        return True

    # --- B10 ---------------------------------------------------------------
    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return ["GET", "HEAD"]

    # --- B8 ----------------------------------------------------------------
    async def is_authorized(self, ctx: Ctx) -> bool | str:
        # True = authorized; False = 401 (no challenge); str = 401 with that
        # WWW-Authenticate challenge value.
        return True

    # --- B7 ----------------------------------------------------------------
    async def forbidden(self, ctx: Ctx) -> bool:
        return False

    # --- G7 ----------------------------------------------------------------
    async def resource_exists(self, ctx: Ctx) -> bool:
        return True

    # --- conditional GET (G8-L17 subset) -----------------------------------
    async def generate_etag(self, ctx: Ctx) -> str | None:
        return None

    async def last_modified(self, ctx: Ctx) -> datetime | None:
        return None

    # --- C3/C4: content negotiation ---------------------------------------
    async def content_types_provided(self, ctx: Ctx) -> Sequence[tuple[str, Producer]]:
        return [("application/json", self.to_json)]

    async def to_json(self, ctx: Ctx) -> Any:
        # The default JSON producer. Read resources override this (or
        # content_types_provided) to build their representation.
        raise NotImplementedError(
            f"{type(self).__name__} provides application/json but does not "
            "implement to_json() (or override content_types_provided).",
        )
