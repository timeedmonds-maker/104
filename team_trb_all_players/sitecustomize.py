"""Runner-local JSON compatibility for NumPy scalars.

Python imports sitecustomize automatically when this directory is on PYTHONPATH.
The TREB Actions workflows already set PYTHONPATH to team_trb_all_players.
This changes JSON serialization only; no reconstruction/materiality logic changes.
"""
from __future__ import annotations

import json

try:
    import numpy as np
except Exception:  # NumPy may not be installed for every repository command.
    np = None

_original_default = json.JSONEncoder.default


def _numpy_safe_default(self, obj):
    if np is not None:
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    return _original_default(self, obj)


json.JSONEncoder.default = _numpy_safe_default
