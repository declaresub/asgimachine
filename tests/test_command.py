"""Unit tests for the command lane helpers (PLAN.md §2.5)."""

from __future__ import annotations

import pytest

from asgimachine.command import Command, json_response
from asgimachine.http import Status


def test_json_response_defaults() -> None:
    resp = json_response({"ok": True})
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "application/json"
    assert resp.body == b'{"ok": true}'


def test_json_response_status_and_headers() -> None:
    resp = json_response(
        {"error": "nope"}, status=Status.BAD_REQUEST, headers={"X-Extra": "1"}
    )
    assert resp.status == 400
    assert resp.headers["X-Extra"] == "1"


async def test_base_command_requires_handle() -> None:
    class _Bare(Command):
        pass

    with pytest.raises(NotImplementedError):
        await _Bare().handle(object())  # type: ignore[arg-type]
