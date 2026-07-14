"""Unit tests for the ordered Allow/Deny rule engine (PLAN.md §7)."""

from __future__ import annotations

from dataclasses import dataclass, field

from asgimachine.policy import Effect, NamedRule, RuleEngine
from asgimachine.resource import Ctx


@dataclass
class FakeRequest:
    method: str = "GET"
    path: str = "/"
    headers: dict[str, str] = field(default_factory=dict)
    path_params: dict[str, str] = field(default_factory=dict)
    route: str | None = None

    async def body(self) -> bytes:
        return b""


def _ctx() -> Ctx:
    return Ctx(request=FakeRequest())


async def _allow(ctx: Ctx) -> Effect | None:
    return Effect.ALLOW


async def _deny(ctx: Ctx) -> Effect | None:
    return Effect.DENY


async def _abstain(ctx: Ctx) -> Effect | None:
    return None


async def test_first_matching_rule_wins() -> None:
    engine = RuleEngine([NamedRule("a", _allow), NamedRule("b", _deny)])
    decision = await engine.evaluate(_ctx())
    assert decision.allowed is True
    assert decision.reason == "a"


async def test_earlier_deny_beats_later_allow() -> None:
    engine = RuleEngine([NamedRule("a", _deny), NamedRule("b", _allow)])
    decision = await engine.evaluate(_ctx())
    assert decision.allowed is False
    assert decision.reason == "a"


async def test_abstain_falls_through() -> None:
    engine = RuleEngine([NamedRule("skip", _abstain), NamedRule("yes", _allow)])
    decision = await engine.evaluate(_ctx())
    assert decision.reason == "yes"


async def test_default_deny_when_no_rule_fires() -> None:
    engine = RuleEngine([NamedRule("skip", _abstain)])
    decision = await engine.evaluate(_ctx())
    assert decision.allowed is False
    assert decision.reason == "default"


async def test_default_allow_configurable() -> None:
    engine = RuleEngine([NamedRule("skip", _abstain)], default=Effect.ALLOW)
    decision = await engine.evaluate(_ctx())
    assert decision.allowed is True


async def test_deciding_rule_recorded_in_trace() -> None:
    ctx = _ctx()
    engine = RuleEngine([NamedRule("owner", _allow)])
    await engine.evaluate(ctx)
    assert ctx.trace.nodes == ["policy:owner"]
