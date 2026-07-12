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

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from starlette.applications import Starlette

from asgimachine.command import Command, json_response
from asgimachine.http import HttpRequest, HttpResponse, Status
from asgimachine.policy import Effect, NamedRule, RuleEngine
from asgimachine.resource import Ctx, Resource
from asgimachine.schema import Operation, ResourceDescription, generate_openapi
from asgimachine.substrate.starlette import build_app, command_route, resource_route


class NoteInput(BaseModel):
    """The request body model — the core parses the body into this (400 on a bad
    body), so no malformed_request check is needed (parse, don't validate)."""

    text: str


# Response schema for a single note (a raw JSON Schema dict — Pydantic optional).
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


# scrypt work factors — demo-appropriate; a production app tunes these upward (or
# uses argon2/bcrypt). Passwords are hashed, never stored in the clear.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


@dataclass(frozen=True, slots=True)
class Credential:
    salt: bytes
    pwd_hash: bytes
    role: str


# Verifying against this for an unknown user keeps the work (and timing) the same
# whether or not the account exists — no username enumeration via a timing oracle.
_DUMMY = Credential(b"\x00" * 16, _hash_password("", b"\x00" * 16), "")


@dataclass(slots=True)
class Store:
    """In-memory state, wired into resources/commands at the composition root."""

    users: dict[str, Credential]  # username -> hashed credential
    tokens: dict[str, str] = field(default_factory=dict[str, str])  # token -> username
    notes: dict[str, Note] = field(default_factory=dict[str, "Note"])

    @classmethod
    def seeded(cls, plaintext: dict[str, tuple[str, str]]) -> Store:
        """Build a store from ``username -> (password, role)``, hashing each
        password with a per-user salt at construction."""

        users: dict[str, Credential] = {}
        for name, (password, role) in plaintext.items():
            salt = secrets.token_bytes(16)
            users[name] = Credential(salt, _hash_password(password, salt), role)
        return cls(users=users)

    def verify_credentials(self, username: str, password: str) -> bool:
        # Always hash (even for an unknown user, against _DUMMY) and compare in
        # constant time, so neither the result nor the timing leaks account existence.
        cred = self.users.get(username, _DUMMY)
        candidate = _hash_password(password, cred.salt)
        match = hmac.compare_digest(candidate, cred.pwd_hash)
        return match and username in self.users

    def authenticate(self, request: HttpRequest) -> User | None:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        username = self.tokens.get(auth.removeprefix("Bearer ").strip())
        cred = self.users.get(username) if username is not None else None
        if username is None or cred is None:
            return None
        return User(username, cred.role)

    def issue_token(self, username: str) -> str:
        token = secrets.token_urlsafe(16)
        self.tokens[token] = username
        return token

    def new_id(self) -> str:
        # Unguessable: an attacker can't enumerate other users' notes by id.
        return secrets.token_urlsafe(8)


# --- typed per-request contexts (§2.7) -------------------------------------


@dataclass(slots=True)
class NotesCtx(Ctx):
    user: User | None = None
    new_id: str = ""


@dataclass(slots=True)
class MemberCtx(Ctx):
    user: User | None = None
    note: Note | None = None


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
        if not self._store.verify_credentials(username, password):
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

    async def represent(self, ctx: Ctx) -> object:
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
                security=[],  # public
            ),
        )


class NotesCollection(Resource[NotesCtx]):
    """Collection: any authenticated user may list or create."""

    ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})
    context_class = NotesCtx

    def __init__(self, store: Store) -> None:
        self._store = store

    async def is_authorized(self, ctx: NotesCtx) -> bool | str:
        user = self._store.authenticate(ctx.request)
        if user is None:
            return "Bearer"
        ctx.user = user
        return True

    CONSUMES = ("application/json",)

    async def post_is_create(self, ctx: NotesCtx) -> bool:
        return True

    async def create_path(self, ctx: NotesCtx) -> str:
        ctx.new_id = self._store.new_id()
        return f"/notes/{ctx.new_id}"

    async def apply(self, ctx: NotesCtx, body: NoteInput) -> None:
        assert ctx.user is not None  # is_authorized ran first
        self._store.notes[ctx.new_id] = Note(ctx.new_id, ctx.user.username, body.text)
        return None

    async def represent(self, ctx: NotesCtx) -> object:
        assert ctx.user is not None
        mine = [n for n in self._store.notes.values() if n.owner == ctx.user.username]
        return {"notes": [{"id": n.id, "text": n.text} for n in mine]}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(
            get=Operation(
                summary="List your notes", responses={200: {"type": "object"}}
            ),
            # request body derives from apply(ctx, body: NoteInput).
            post=Operation(summary="Create a note", responses={201: None}),
        )


class NoteMember(Resource[MemberCtx]):
    """Member: every method (read and write) is governed by the policy — only the
    owner or an admin is allowed; everyone else gets 403."""

    ALLOWED_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})
    context_class = MemberCtx

    def __init__(self, store: Store, policy: RuleEngine[MemberCtx]) -> None:
        self._store = store
        self._policy = policy

    async def is_authorized(self, ctx: MemberCtx) -> bool | str:
        user = self._store.authenticate(ctx.request)
        if user is None:
            return "Bearer"
        ctx.user = user
        # Load the target now so the policy can authorize on ownership (B7 is
        # before G7, so authorization-relevant state must be loaded by here).
        ctx.note = self._store.notes.get(ctx.request.path_params["id"])
        return True

    async def forbidden(self, ctx: MemberCtx) -> bool:
        decision = await self._policy.evaluate(ctx)
        return not decision.allowed

    async def resource_exists(self, ctx: MemberCtx) -> bool:
        return ctx.note is not None

    async def generate_etag(self, ctx: MemberCtx) -> str | None:
        note = ctx.note
        return f'"{note.id}-{note.version}"' if note is not None else None

    CONSUMES = ("application/json",)

    async def apply(self, ctx: MemberCtx, body: NoteInput) -> None:
        assert ctx.user is not None
        if ctx.note is None:  # PUT-create (reached the missing branch)
            note_id = ctx.request.path_params["id"]
            self._store.notes[note_id] = Note(note_id, ctx.user.username, body.text)
        else:
            ctx.note.text = body.text
            ctx.note.version += 1
        return None

    async def delete_resource(self, ctx: MemberCtx) -> bool:
        assert ctx.note is not None  # resource_exists guaranteed it
        del self._store.notes[ctx.note.id]
        return True

    async def represent(self, ctx: MemberCtx) -> object:
        note = ctx.note
        assert note is not None
        return {"id": note.id, "owner": note.owner, "text": note.text}

    def describe(self) -> ResourceDescription:
        return ResourceDescription(
            # Declare only the success bodies; 401/403/404/406/304/412/415 are
            # auto-derived from the overridden callbacks.
            get=Operation(summary="Read a note", responses={200: _NOTE}),
            put=Operation(
                summary="Create or update a note",
                responses={201: None, 204: None},
            ),
            delete=Operation(summary="Delete a note", responses={204: None}),
        )


# --- auth policy: ordered Allow/Deny rules (§7) -----------------------------


async def _rule_admin(ctx: MemberCtx) -> Effect | None:
    return Effect.ALLOW if ctx.user is not None and ctx.user.role == "admin" else None


async def _rule_owner(ctx: MemberCtx) -> Effect | None:
    if (note := ctx.note) is not None and ctx.user is not None:
        return Effect.ALLOW if note.owner == ctx.user.username else None
    return None


def build_policy() -> RuleEngine[MemberCtx]:
    # Notes are private: only an admin or the owner may touch one, for *any*
    # method (read included). Everyone else falls through to the default deny.
    return RuleEngine(
        [
            NamedRule("admin", _rule_admin),
            NamedRule("owner", _rule_owner),
        ],
        default=Effect.DENY,
    )


# --- composition root ------------------------------------------------------


def seed_store() -> Store:
    return Store.seeded({"alice": ("pw-alice", "user"), "admin": ("pw-admin", "admin")})


class OpenApiCommand(Command):
    """Serves the app's own OpenAPI document, generated from the resources."""

    def __init__(self, routes: list[tuple[str, Resource[Any]]]) -> None:
        self._routes = routes

    async def handle(self, request: HttpRequest) -> HttpResponse:
        return json_response(
            generate_openapi(
                title="Notes API",
                version="1.0.0",
                routes=self._routes,
                security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}},
                security=["bearerAuth"],  # default: bearer token required
            )
        )


def make_app(store: Store | None = None, *, debug: bool = False) -> Starlette:
    store = store if store is not None else seed_store()
    policy = build_policy()
    resource_pairs: list[tuple[str, Resource[Any]]] = [
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


# debug=False: the decision-trace header (X-Asgimachine-Trace) would otherwise
# leak the node path and policy outcomes. Enable it only for local debugging.
app = make_app()
