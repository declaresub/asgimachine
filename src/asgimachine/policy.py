"""Authorization policy as a collaborator (PLAN.md §7).

Authorization is not baked into the core. Resources delegate ``forbidden`` (and
optionally ``is_authorized``) to a :class:`Policy` wired in at the composition
root. The shipped implementation is an **ordered Allow/Deny rule engine**: each
rule inspects the request + authenticated principal and either fires (allow/deny)
or abstains; first match wins. The deciding rule is recorded into the request's
decision trace, so "which rule denied me" and "which node returned 403" are one
story (§9).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .resource import Ctx


class Effect(Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of a policy evaluation; ``reason`` names the deciding rule."""

    allowed: bool
    reason: str


@runtime_checkable
class Policy(Protocol):
    async def evaluate(self, ctx: Ctx) -> Decision: ...


# A rule fires with an Effect, or returns None to abstain (let later rules decide).
Rule = Callable[["Ctx"], Awaitable["Effect | None"]]


@dataclass(frozen=True, slots=True)
class NamedRule:
    name: str
    check: Rule


class RuleEngine:
    """Ordered Allow/Deny rule engine; first matching rule wins (§7).

    ``default`` decides when no rule fires (deny-by-default is the safe choice).
    """

    __slots__ = ("_default", "_rules")

    def __init__(
        self, rules: Sequence[NamedRule], *, default: Effect = Effect.DENY
    ) -> None:
        self._rules = list(rules)
        self._default = default

    async def evaluate(self, ctx: Ctx) -> Decision:
        for rule in self._rules:
            effect = await rule.check(ctx)
            if effect is not None:
                ctx.trace.record(f"policy:{rule.name}", effect.value)
                return Decision(allowed=effect is Effect.ALLOW, reason=rule.name)
        ctx.trace.record("policy:default", self._default.value)
        return Decision(allowed=self._default is Effect.ALLOW, reason="default")
