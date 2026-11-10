"""Shared bootstrap for scripts: make `backend.*` importable."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "backend")]
