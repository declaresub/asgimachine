"""M4 dogfood app — a small notes service exercising the whole framework (§12).

The resource gradient + the command lane + an auth policy, coexisting without
cosplay:

- ``GET /health``                  public read-only resource (no auth)
- ``POST /token``                  command lane: credential exchange -> token
- ``GET/POST /notes``              collection resource: simple auth (any user)
- ``GET/PUT/DELETE /notes/{id}``   member resource: authorization via the
                                   ``RuleEngine`` (admin or owner may write;
                                   any authenticated user may read)

    uvicorn examples.notes_app:app --reload
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field

from starlette.applications import Starlette

from asgimachine.command import Command, json_response
from asgimachine.http import HttpRequest, HttpResponse, Status
from asgimachine.policy import Effect, NamedRule, RuleEngine
from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, command_route, resource_route

# Reusable JSON Schemas for the note payloads (raw dicts — Pydantic optional).
_NOTE_INPUT = {
    "type": "object",
    "required": ["text"],
    "properties": {"text": {"type": "string"}},
}
_NOTE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "owner": {"type": "string"},
        "text": {"type": "string"},
    },
}


@dataclass(frozen=True, slots=True)
class User:
    username: str
    role: str


@dataclass(slots=True)
class Note:
    id: str
    owner: str
    text: str
    version: int = 1


@dataclass(slots=True)
class Store:
    """In-memory state, wired into resources/commands at the composition root."""

    users: dict[str, tuple[str, str]]  # username -> (password, role)
    tokens: dict[str, str] = field(default_factory=dict[str, str])  # token -> username
    notes: dict[str, Note] = field(default_factory=dict[str, "Note"])
    _seq: int = 0

    def authenticate(self, request: HttpRequest) -> User | None:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        username = self.tokens.get(auth.removeprefix("Bearer ").strip())
        if username is None:
            return None
        return User(username, self.users[username][1])

    def issue_token(self, username: str) -> str:
        token = secrets.token_urlsafe(16)
        self.tokens[token] = username
        return token

    def new_id(self) -> str:
        self._seq += 1
        return f"n{self._seq}"


# --- command lane: credential exchange -------------------------------------


class TokenCommand(Command):
    def __init__(self, store: Store) -> None:
        self._store = store

    async def handle(self, request: HttpRequest) -> HttpResponse:
        try:
            body = json.loads(await request.body())
            username, password = body["username"], body["password"]
        except ValueError, KeyError, TypeError, UnicodeDecodeError:
            return json_response(
                {"error": "invalid request"}, status=Status.BAD_REQUEST
            )
        record = self._store.users.get(username)
        if record is None or record[0] != password:
            return json_response(
                {"error": "invalid credentials"}, status=Status.UNAUTHORIZED
            )
        return json_response(
            {"token": self._store.issue_token(username)}, status=Status.CREATED
        )


# --- resources across the gradient -----------------------------------------


class HealthResource(Resource):
    """Public read-only resource — no auth."""

    ALLOWED_METHODS = frozenset({"GET", "HEAD"})

    async def to_json(self, ctx: Ctx) -> object:
        return {"status": "ok"}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(
            get=Operation(
                summary="Service health",
                responses={
                    200: {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                    }
                },
            ),
        )


class NotesCollection(Resource):
    """Collection: any authenticated user may list or create."""

    ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})

    def __init__(self, store: Store) -> None:
        self._store = store

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        user = self._store.authenticate(ctx.request)
        if user is None:
            return "Bearer"
        ctx.user = user
        return True

    async def malformed_request(self, ctx: Ctx) -> bool:
        try:
            body = json.loads(await ctx.request.body())
        except ValueError, UnicodeDecodeError:
            return True
        if not isinstance(body, dict) or "text" not in body:
            return True
        ctx.extra["body"] = body
        return False

    async def content_types_accepted(self, ctx: Ctx):
        return [("application/json", self._create)]

    async def post_is_create(self, ctx: Ctx) -> bool:
        return True

    async def create_path(self, ctx: Ctx) -> str:
        note_id = self._store.new_id()
        ctx.extra["new_id"] = note_id
        return f"/notes/{note_id}"

    async def _create(self, ctx: Ctx) -> None:
        note_id = ctx.extra["new_id"]
        self._store.notes[note_id] = Note(
            note_id, ctx.user.username, ctx.extra["body"]["text"]
        )
        return None

    async def to_json(self, ctx: Ctx) -> object:
        mine = [n for n in self._store.notes.values() if n.owner == ctx.user.username]
        return {"notes": [{"id": n.id, "text": n.text} for n in mine]}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(
            get=Operation(
                summary="List your notes", responses={200: {"type": "object"}}
            ),
            post=Operation(
                summary="Create a note", request=_NOTE_INPUT, responses={201: None}
            ),
        )


class NoteMember(Resource):
    """Member: read for any authenticated user; write governed by the policy."""

    ALLOWED_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})

    def __init__(self, store: Store, policy: RuleEngine) -> None:
        self._store = store
        self._policy = policy

    async def is_authorized(self, ctx: Ctx) -> bool | str:
        user = self._store.authenticate(ctx.request)
        if user is None:
            return "Bearer"
        ctx.user = user
        # Load the target now so the policy can authorize on ownership (B7 is
        # before G7, so authorization-relevant state must be loaded by here).
        ctx.entity = self._store.notes.get(ctx.request.path_params["id"])
        return True

    async def forbidden(self, ctx: Ctx) -> bool:
        decision = await self._policy.evaluate(ctx)
        return not decision.allowed

    async def resource_exists(self, ctx: Ctx) -> bool:
        return ctx.entity is not None

    async def generate_etag(self, ctx: Ctx) -> str | None:
        note: Note | None = ctx.entity
        return f'"{note.id}-{note.version}"' if note is not None else None

    async def malformed_request(self, ctx: Ctx) -> bool:
        try:
            body = json.loads(await ctx.request.body())
        except ValueError, UnicodeDecodeError:
            return True
        if not isinstance(body, dict) or "text" not in body:
            return True
        ctx.extra["body"] = body
        return False

    async def content_types_accepted(self, ctx: Ctx):
        return [("application/json", self._update)]

    async def _update(self, ctx: Ctx) -> None:
        note_id = ctx.request.path_params["id"]
        text = ctx.extra["body"]["text"]
        if ctx.entity is None:  # PUT-create (reached the missing branch)
            self._store.notes[note_id] = Note(note_id, ctx.user.username, text)
        else:
            ctx.entity.text = text
            ctx.entity.version += 1
        return None

    async def delete_resource(self, ctx: Ctx) -> bool:
        del self._store.notes[ctx.entity.id]
        return True

    async def to_json(self, ctx: Ctx) -> object:
        note: Note = ctx.entity
        return {"id": note.id, "owner": note.owner, "text": note.text}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(
            get=Operation(summary="Read a note", responses={200: _NOTE, 404: None}),
            put=Operation(
                summary="Create or update a note",
                request=_NOTE_INPUT,
                responses={201: None, 204: None, 403: None},
            ),
            delete=Operation(summary="Delete a note", responses={204: None, 403: None}),
        )


# --- auth policy: ordered Allow/Deny rules (§7) -----------------------------


async def _rule_admin(ctx: Ctx) -> Effect | None:
    return Effect.ALLOW if ctx.user.role == "admin" else None


async def _rule_read(ctx: Ctx) -> Effect | None:
    return Effect.ALLOW if ctx.request.method in {"GET", "HEAD"} else None


async def _rule_owner(ctx: Ctx) -> Effect | None:
    note: Note | None = ctx.entity
    if note is not None and note.owner == ctx.user.username:
        return Effect.ALLOW
    return None


def build_policy() -> RuleEngine:
    return RuleEngine(
        [
            NamedRule("admin", _rule_admin),
            NamedRule("read", _rule_read),
            NamedRule("owner", _rule_owner),
        ],
        default=Effect.DENY,
    )


# --- composition root ------------------------------------------------------


def seed_store() -> Store:
    return Store(users={"alice": ("pw-alice", "user"), "admin": ("pw-admin", "admin")})


class OpenApiCommand(Command):
    """Serves the app's own OpenAPI document, generated from the resources."""

    def __init__(self, routes: list[tuple[str, Resource]]) -> None:
        self._routes = routes

    async def handle(self, request: HttpRequest) -> HttpResponse:
        return json_response(
            generate_openapi(title="Notes API", version="1.0.0", routes=self._routes)
        )


def make_app(store: Store | None = None, *, debug: bool = False) -> Starlette:
    store = store if store is not None else seed_store()
    policy = build_policy()
    resource_pairs: list[tuple[str, Resource]] = [
        ("/health", HealthResource()),
        ("/notes", NotesCollection(store)),
        ("/notes/{id}", NoteMember(store, policy)),
    ]
    routes = [resource_route(path, resource) for path, resource in resource_pairs]
    routes.append(command_route("/token", TokenCommand(store)))
    routes.append(
        command_route("/openapi.json", OpenApiCommand(resource_pairs), methods=["GET"])
    )
    return build_app(routes, debug=debug)


app = make_app(debug=True)
