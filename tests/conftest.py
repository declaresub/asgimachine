"""Shared conformance fixtures.

A single configurable read resource lets one table drive every v0 node. Its
collaborators (existence, auth, availability, validators, offered types) are
constructor-injected fakes — no override registry, no monkeypatching (PLAN.md
§2.2, §11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC

import pytest

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route
from asgimachine.testing import make_client

FIXED_ETAG = 'W/"v0-fixture"'
FIXED_LAST_MODIFIED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@dataclass(slots=True)
class Toggles:
    available: bool = True
    methods: list[str] = field(default_factory=lambda: ["GET", "HEAD"])
    authorized: bool | str = True
    forbidden: bool = False
    exists: bool = True
    etag: str | None = FIXED_ETAG
    last_modified: datetime | None = FIXED_LAST_MODIFIED
    offered: list[str] = field(default_factory=lambda: ["application/json"])


class ConfigurableResource(Resource):
    def __init__(self, toggles: Toggles) -> None:
        self._t = toggles

    async def service_available(self, ctx: Ctx) -> bool:
        return self._t.available

    async def allowed_methods(self, ctx: Ctx) -> list[str]:
        return self._t.methods

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        return self._t.authorized

    async def forbidden(self, ctx: Ctx) -> bool:
        return self._t.forbidden

    async def resource_exists(self, ctx: Ctx) -> bool:
        return self._t.exists

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return self._t.etag

    async def last_modified(self, ctx: Ctx) -> datetime | None:
        return self._t.last_modified

    async def content_types_provided(self, ctx: Ctx):
        return [(mtype, self.to_json) for mtype in self._t.offered]

    async def to_json(self, ctx: Ctx) -> object:
        return {"ok": True}


@pytest.fixture
def client_for():
    """Return a factory: ``client_for(Toggles(...))`` -> TestClient at /r."""

    def _factory(toggles: Toggles):
        resource = ConfigurableResource(toggles)
        app = build_app([resource_route("/r", resource)])
        return make_client(app)

    return _factory
