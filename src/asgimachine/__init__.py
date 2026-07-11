"""asgimachine — a webmachine-style HTTP decision-graph framework.

Public surface for M0. The core (``core``/``resource``/``http``) never imports
Starlette; the substrate adapter is imported explicitly from
``asgimachine.substrate.starlette``.
"""

from __future__ import annotations

from .core import run
from .http import HaltResponse, HttpRequest, HttpResponse, Status
from .resource import Ctx, Producer, Resource
from .trace import TRACE_HEADER, Trace, TraceEntry

__all__ = [
    "TRACE_HEADER",
    "Ctx",
    "HaltResponse",
    "HttpRequest",
    "HttpResponse",
    "Producer",
    "Resource",
    "Status",
    "Trace",
    "TraceEntry",
    "run",
]
