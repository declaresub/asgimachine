"""Drift-guard: the graph's trace nodes vs. the documentation.

The flowchart (``docs/concepts/flowchart.md``) and the coverage table
(``docs/concepts/webmachine-coverage.md``) are hand-authored, so they can silently
drift as the graph evolves. This pins them:

1. Every node ``core.py`` records to ``ctx.trace`` is enumerated in
   ``CANONICAL_NODES`` — so adding/removing/renaming a node fails until the set is
   updated (and, by the failure message, the docs are revisited).
2. Every canonical node appears in the coverage table — the authoritative node
   accounting. (The flowchart is a curated visual: it shows a few nodes by effect
   rather than by label, so it isn't checked node-for-node.)

If this fails after a graph change: update ``CANONICAL_NODES``, then update the
coverage table **and** the flowchart to match.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "src" / "asgimachine" / "core.py"
_COVERAGE = _ROOT / "docs" / "concepts" / "webmachine-coverage.md"

# Every node core.py records to ctx.trace: the request gates (B*), negotiation
# (C4/D5/F7), existence + conditionals (G/H/I/K/L), the missing-resource branch
# (K5/K7/L5/M5), the write path (M20/N11/O14/O18/O20/P3), and the additive nodes
# (B7a/B11/C4a/K5a/N11a/O20a/W1/P0). Keep in sync with the code; the test proves it.
CANONICAL_NODES = frozenset(
    {
        "B13",
        "B12",
        "B11",
        "B10",
        "B9",
        "B8",
        "B7",
        "B7a",
        "B6",
        "B5",
        "B4",
        "B3",
        "C4",
        "C4a",
        "D5",
        "F7",
        "G7",
        "G11",
        "H7",
        "H12",
        "I7",
        "K13",
        "L17",
        "K5",
        "K5a",
        "K7",
        "L5",
        "L7",
        "M5",
        "M20",
        "N11",
        "N11a",
        "O14",
        "O18",
        "O20",
        "O20a",
        "P0",
        "P3",
        "W1",
    }
)


def _code_nodes() -> set[str]:
    """The node labels core.py records — `ctx.trace.record("X", …)` and the label
    passed to `_halt(ctx, "X", …)`."""

    src = _CORE.read_text()
    labels = set(re.findall(r'trace\.record\(\s*"([^"]+)"', src))
    labels |= set(re.findall(r'_halt\(\s*ctx,\s*"([^"]+)"', src))
    return labels


def test_code_nodes_match_canonical() -> None:
    diff = _code_nodes() ^ set(CANONICAL_NODES)
    assert not diff, (
        f"core.py's trace nodes differ from CANONICAL_NODES on {sorted(diff)}. "
        "Update the set, then the coverage table and the flowchart."
    )


def test_every_node_is_in_the_coverage_table() -> None:
    doc = _COVERAGE.read_text()
    missing = [
        node
        for node in CANONICAL_NODES
        if re.search(rf"\b{re.escape(node)}\b", doc) is None
    ]
    assert not missing, (
        f"nodes recorded by the graph but absent from the coverage table: "
        f"{sorted(missing)}. Add them to docs/concepts/webmachine-coverage.md."
    )
