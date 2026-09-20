"""Make the repo root importable for the test suite (the answers package)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
