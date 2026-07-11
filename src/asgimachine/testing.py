"""Test helpers (PLAN.md §11).

``make_client`` gives the tier-1 seam: ``make_client(build_app([...]))`` over the
substrate adapter, with fakes constructor-injected into resources. The
``assert_trace`` node-path helper is formalized in M1 alongside the debug header;
for now the trace is asserted directly against ``Ctx`` in core-level tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.testclient import TestClient

if TYPE_CHECKING:
    from starlette.applications import Starlette


def make_client(app: Starlette) -> TestClient:
    """A Starlette ``TestClient`` over an asgimachine app."""

    return TestClient(app)
