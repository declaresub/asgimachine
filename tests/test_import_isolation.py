"""The core seam must stay Starlette-free (PLAN.md §2.6, §3).

Importing the core modules must not pull Starlette into ``sys.modules``. This
pins the "own Layer 1" boundary from day one.
"""

from __future__ import annotations

import subprocess
import sys

CORE_MODULES = ["asgimachine.core", "asgimachine.resource", "asgimachine.http"]


def test_core_does_not_import_starlette() -> None:
    # Run in a fresh interpreter so nothing else has imported starlette first.
    code = (
        "import sys;"
        f"import {', '.join(CORE_MODULES)};"
        "assert 'starlette' not in sys.modules, "
        "sorted(m for m in sys.modules if m.startswith('starlette'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
