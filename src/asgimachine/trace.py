"""The decision trace — webmachine's signature debugging feature (PLAN.md §9).

v0 records the ordered path of visited nodes and each node's outcome. The debug
response header and ``assert_trace`` helper land in M1; the recording substrate
is here from day one so every node can append as it decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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

    def __str__(self) -> str:
        return " -> ".join(str(e) for e in self.entries)
