"""asgimachine — a webmachine-style HTTP decision-graph framework.

Public surface for M0. The core (``core``/``resource``/``http``) never imports
Starlette; the substrate adapter is imported explicitly from
``asgimachine.substrate.starlette``.
"""

from __future__ import annotations

from .command import Command, json_response
from .core import run
from .http import HaltResponse, HttpRequest, HttpResponse, Status, serialize
from .policy import Decision, Effect, NamedRule, Policy, RuleEngine
from .resource import Acceptor, Ctx, Producer, Resource
from .schema import Operation, ResourceDescription, generate_openapi
from .trace import TRACE_HEADER, Trace, TraceEntry

__all__ = [
    "TRACE_HEADER",
    "Acceptor",
    "Command",
    "Ctx",
    "Decision",
    "Effect",
    "HaltResponse",
    "HttpRequest",
    "HttpResponse",
    "NamedRule",
    "Operation",
    "Policy",
    "Producer",
    "Resource",
    "ResourceDescription",
    "RuleEngine",
    "Status",
    "Trace",
    "TraceEntry",
    "generate_openapi",
    "json_response",
    "run",
    "serialize",
]
