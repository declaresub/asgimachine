"""Wide-event logging — one structured event per request.

The canonical-log-line / "observability 2.0" approach: instead of scattering
narrow log lines through the walk, the core accumulates a single wide event on
``ctx.event`` and emits it once at the request boundary through a pluggable
:class:`EventSink`. The decision trace is its spine; resources and instrumented
code (a database wrapper, say) enrich it with domain fields.

Field names follow OpenTelemetry semantic conventions where they exist
(``http.request.method``, ``http.response.status_code``, ``url.path``,
``error.type``, ``exception.type``/``exception.message``); graph-specific fields
live under the ``asgm.`` namespace (``asgm.resource``, ``asgm.decision_path``,
``asgm.outcome``, the negotiated ``asgm.media_type``/``language``/``encoding``).
Those namespaces are reserved for the core; put domain fields in your own.

The framework owns the event and the emission seam (Layer 1); the *sink* — stdlib
logging, structlog, OpenTelemetry, an error reporter — is rented (Layer 2). No
sink is configured by default, so nothing is emitted until you wire one at the
composition root (``build_app(event_sink=...)``); :class:`LoggingEventSink` is the
batteries-included reference.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from collections.abc import Mapping

# The per-request wide event: a flat, mutable mapping. ``object`` values keep it
# sink-agnostic (a dict is exactly what structlog / OTel / a JSON logger want).
Event = dict[str, object]


@runtime_checkable
class EventSink(Protocol):
    """Where a completed wide event goes. One synchronous method — sync so it is
    safe to call from the request's exit path even under cancellation (a
    disconnect); hand off to an async queue inside ``emit`` if you must."""

    def emit(self, event: Mapping[str, object]) -> None: ...


class LoggingEventSink:
    """Reference :class:`EventSink` over the stdlib ``logging`` module.

    The full event rides on the log record as ``record.event`` (via ``extra``), so
    a structured/JSON handler can render every field; the formatted message is a
    terse ``METHOD path -> status`` summary for plain handlers. Swap in a structlog
    or OpenTelemetry sink at the composition root when you want those backends.
    """

    __slots__ = ("_level", "_logger")

    def __init__(
        self, logger_name: str = "asgimachine.event", level: int = logging.INFO
    ) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = level

    def emit(self, event: Mapping[str, object]) -> None:
        self._logger.log(
            self._level,
            "%s %s -> %s",
            event.get("http.request.method", "-"),
            event.get("url.path", "-"),
            event.get("http.response.status_code", "-"),
            extra={"event": dict(event)},
        )
