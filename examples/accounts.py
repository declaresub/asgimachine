"""The v0 read-resource example (PLAN.md §5).

A pure-read ``accounts`` resource wired with no DI: its store and authenticator
are constructor arguments (§2.2). ``build_app`` is the composition root — it wires
the real collaborators; a test wires fakes. Run with:

    uvicorn examples.accounts:app --reload
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from asgimachine.resource import Ctx, Resource
from asgimachine.substrate.starlette import build_app, resource_route


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    name: str
    balance_cents: int


@dataclass(frozen=True, slots=True)
class User:
    id: str


# Collaborator types, wired at the composition root.
Authenticator = Callable[["Ctx"], Awaitable[User | None]]
AccountStore = Callable[[str], Awaitable[list[Account]]]


class AccountsResource(Resource):
    """GET/HEAD the authenticated user's accounts, with conditional GET."""

    ALLOWED_METHODS = frozenset({"GET", "HEAD"})

    def __init__(
        self, retrieve_accounts_for_user: AccountStore, authenticate: Authenticator
    ) -> None:
        self._retrieve = retrieve_accounts_for_user
        self._authenticate = authenticate

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        user = await self._authenticate(ctx)
        if user is None:
            return "Bearer"  # -> 401 WWW-Authenticate: Bearer
        ctx.user = user
        return True

    async def resource_exists(self, ctx: Ctx) -> bool:
        ctx.entity = await self._retrieve(ctx.user.id)
        return True

    async def generate_etag(self, ctx: Ctx) -> str | None:
        return f'W/"accounts-{ctx.user.id}-{len(ctx.entity)}"'

    async def represent(self, ctx: Ctx) -> object:
        return {"data": [asdict(account) for account in ctx.entity]}


# --- composition root: real collaborators ---------------------------------

_ACCOUNTS = {
    "u1": [Account(id="a1", name="Checking", balance_cents=125_00)],
}


async def _authenticate(ctx: Ctx) -> User | None:
    auth = ctx.request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
        if token in _ACCOUNTS:
            return User(id=token)
    return None


async def _retrieve_accounts_for_user(user_id: str) -> list[Account]:
    return list(_ACCOUNTS.get(user_id, []))


def make_app() -> object:
    resource = AccountsResource(_retrieve_accounts_for_user, _authenticate)
    return build_app([resource_route("/accounts", resource)])


app = make_app()
