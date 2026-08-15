#!/usr/bin/env python3
"""Run residual_materiality_autopilot with JSON serialization support for NumPy scalars.

This wrapper changes serialization only. It does not alter TREB reconstruction,
scenario enumeration, materiality thresholds, minutes gates, or acceptance logic.
"""
from __future__ import annotations

import json
import runpy

import numpy as np

_original_default = json.JSONEncoder.default


def _numpy_safe_default(self, obj):
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
runpy.run_module("residual_materiality_autopilot", run_name="__main__")
