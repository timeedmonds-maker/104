"""Repository-local Python path bootstrap for GitHub Actions.

Makes modules under team_trb_all_players importable from inline Python runner
steps executed at the repository root.  No production logic is changed.
"""
from pathlib import Path
import sys

_root = Path(__file__).resolve().parent
_pkg = _root / "team_trb_all_players"
if _pkg.is_dir():
    _p = str(_pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)
