"""asgimachine — a webmachine-style HTTP decision-graph framework.

Public surface for M0. The core (``core``/``resource``/``http``) never imports
Starlette; the substrate adapter is imported explicitly from
``asgimachine.substrate.starlette``.
"""

from __future__ import annotations

from .auth import basic_credentials, bearer_token, parse_authorization
from .codec import DEFAULT_CODECS, Codec, JsonCodec
from .command import Command, json_response
from .core import run
from .event import Event, EventSink, LoggingEventSink
from .http import HaltResponse, HttpRequest, HttpResponse, Status, serialize
from .policy import Decision, Effect, NamedRule, Policy, RuleEngine
from .resource import Ctx, Producer, Resource, RetryHint
from .schema import Operation, ResourceDescription, generate_openapi
from .trace import TRACE_HEADER, Trace, TraceEntry

__all__ = [
    "DEFAULT_CODECS",
    "TRACE_HEADER",
    "Codec",
    "Command",
    "Ctx",
    "Decision",
    "Effect",
    "Event",
    "EventSink",
    "HaltResponse",
    "HttpRequest",
    "HttpResponse",
    "JsonCodec",
    "LoggingEventSink",
    "NamedRule",
    "Operation",
    "Policy",
    "Producer",
    "Resource",
    "ResourceDescription",
    "RetryHint",
    "RuleEngine",
    "Status",
    "Trace",
    "TraceEntry",
    "basic_credentials",
    "bearer_token",
    "generate_openapi",
    "json_response",
    "parse_authorization",
    "run",
    "serialize",
]
