#!/usr/bin/env python
"""Launch the Step-10 scene server without needing PYTHONPATH.

Adds `src/` to sys.path itself so a plain `python scripts/serve.py` works (the
`PYTHONPATH=src python -m server` form breaks if the env var gets dropped — e.g.
a multi-line paste where the assignment lands on its own command).

Examples:
  ~/projects/vid2sim/venv/bin/python scripts/serve.py
  ~/projects/vid2sim/venv/bin/python scripts/serve.py --scene out/scene_office_3 --port 8000
then open http://127.0.0.1:8000/
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server import main  # noqa: E402

if __name__ == "__main__":
    main()
