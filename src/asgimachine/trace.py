"""The decision trace — webmachine's signature debugging feature (PLAN.md §9).

Records the ordered path of visited nodes and each node's outcome. In debug mode
the core emits the node path as the ``X-Asgimachine-Trace`` response header, and
``testing.assert_trace`` asserts it — pinning the graph wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Response header carrying the comma-separated node path (debug mode only).
TRACE_HEADER = "X-Asgimachine-Trace"


@dataclass(slots=True)
class TraceEntry:
    node: str
    outcome: object

    def __str__(self) -> str:
        return f"{self.node}={self.outcome!r}"


@dataclass(slots=True)
class Trace:
    entries: list[TraceEntry] = field(default_factory=list[TraceEntry])

    def record(self, node: str, outcome: object) -> None:
        self.entries.append(TraceEntry(node, outcome))

    @property
    def nodes(self) -> list[str]:
        return [e.node for e in self.entries]

    @property
    def header_value(self) -> str:
        """The ordered node path as a single header-safe token list."""

        return ",".join(self.nodes)

    def __str__(self) -> str:
        return " -> ".join(str(e) for e in self.entries)
