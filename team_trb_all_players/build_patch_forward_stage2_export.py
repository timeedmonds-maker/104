#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KEYS = ["season", "team_id", "player_id"]
EXPECTED_MISSING = 23
EXPECTED_METRICS = 89
EXPECTED_CANONICAL_KEYS = 14524

BASE = Path(__file__).resolve().parent
DIAG = BASE / "final_integrity_rebuild/diagnostics/stage2_missing_23/STAGE2_MISSING_23_DIAGNOSTIC.json"
REC16 = BASE / "final_integrity_rebuild/diagnostics/stage2_missing_23/recovered_short_tenure_16/recovered_short_tenure_16.jsonl.gz"
REC11 = BASE / "final_integrity_rebuild/diagnostics/stage2_missing_23/final_11_targeted_recovery/FINAL_11_RECOVERY_QA.json"


def pid(v: Any) -> str:
    s = str(v or "").strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def finite(v: Any) -> float | None:
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def key_of(r: dict[str, Any]) -> tuple[str, int, str]:
    return str(r["season"]), int(r["team_id"]), pid(r["player_id"])


def load_gzip_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def first_nonblank(values: pd.Series) -> str:
    for v in values:
        if pd.notna(v) and str(v).strip():
            return str(v)
    return ""


def canonical(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["season"] = f["season"].astype(str)
    f["team_id"] = pd.to_numeric(f["team_id"], errors="raise").astype("int64")
    f["player_id"] = f["player_id"].map(pid).astype(str)
    return f


def core_rows(stage2_root: Path, season: str, team_id: int, player_id: str) -> pd.DataFrame:
    path = stage2_root / "impact_database/outputs" / season / "team_on_off_metrics.csv.gz"
    if not path.exists():
        raise RuntimeError(f"missing established core file: {path}")
    f = pd.read_csv(path, compression="gzip", low_memory=False)
    if "subject_player_id" not in f.columns or "metric" not in f.columns:
        raise RuntimeError(f"unexpected core schema: {path}")
    ids = f["subject_player_id"].map(pid)
    teams = pd.to_numeric(f["team_id"], errors="coerce")
    q = f[(ids == str(player_id)) & (teams == int(team_id))].copy()
    if len(q) != EXPECTED_METRICS or q.metric.astype(str).nunique() != EXPECTED_METRICS:
        raise RuntimeError(f"core coverage is not 89 for {season}|{team_id}|{player_id}: rows={len(q)} metrics={q.metric.astype(str).nunique()}")
    return q


def metric_payload_map(rec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics = rec.get("metrics") or []
    out = {str(m.get("metric") or ""): m for m in metrics if str(m.get("metric") or "")}
    return out if len(out) == EXPECTED_METRICS else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage2-export-dir", type=Path, required=True)
    ap.add_argument("--stage2-root", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--optional-final6-json", type=Path)
    args = ap.parse_args()

    if not DIAG.exists() or not REC11.exists():
        raise RuntimeError("required persisted missing-key diagnostics are absent")

    diag = json.loads(DIAG.read_text())
    missing_meta = diag.get("missing_keys") or []
    if len(missing_meta) != EXPECTED_MISSING:
        raise RuntimeError(f"diagnostic drift: expected 23 missing keys, got {len(missing_meta)}")
    target_meta = {key_of(r): r for r in missing_meta}

    stage2_file = args.stage2_export_dir / "player_team_season_corrected_on_off.parquet"
    quality_file = args.stage2_export_dir / "quality_report.json"
    src = canonical(pd.read_parquet(stage2_file))
    src["metric"] = src.metric.astype(str)
    for c, default in [
        ("patch_forward_source", ""),
        ("accepted_materiality_exception", False),
        ("patch_forward_note", ""),
    ]:
        if c not in src.columns:
            src[c] = default

    source_keys = set(map(tuple, src[KEYS].drop_duplicates().itertuples(index=False, name=None)))
    missing_now = [k for k in target_meta if k not in source_keys]
    if len(missing_now) != EXPECTED_MISSING:
        raise RuntimeError(f"base Stage2 missing-key count drift: {len(missing_now)}")

    metric_universe = set(src.metric.astype(str).unique())
    if len(metric_universe) != EXPECTED_METRICS:
        raise RuntimeError(f"Stage2 metric universe drift: {len(metric_universe)}")

    team_abbr: dict[tuple[str, int], str] = {}
    if "team_abbr" in src.columns:
        for k, g in src.groupby(["season", "team_id"], sort=False):
            team_abbr[(str(k[0]), int(k[1]))] = first_nonblank(g["team_abbr"])

    candidates: dict[tuple[str, int, str], tuple[int, str, dict[str, Any]]] = {}

    def register(rec: dict[str, Any] | None, rank: int, source: str) -> None:
        if not rec:
            return
        try:
            k = key_of(rec)
        except Exception:
            return
        if k not in target_meta or not metric_payload_map(rec):
            return
        prev = candidates.get(k)
        if prev is None or rank > prev[0]:
            candidates[k] = (rank, source, rec)

    for rec in load_gzip_jsonl(REC16):
        register(rec, 10, "targeted_recovery_round1")

    q11 = json.loads(REC11.read_text())
    for rec in q11.get("minute_repairs") or []:
        register(rec, 20, "targeted_recovery_final11_minute")
    for item in q11.get("team_repairs") or []:
        register(item.get("result"), 20, "targeted_recovery_final11_team")

    if args.optional_final6_json and args.optional_final6_json.exists():
        q6 = json.loads(args.optional_final6_json.read_text())
        for rec in q6.get("minute_repairs") or []:
            register(rec, 30, "targeted_recovery_final6_minute")
        for item in q6.get("team_repairs") or []:
            register(item.get("result"), 30, "targeted_recovery_final6_team")

    supplemental: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []

    for k in sorted(missing_now):
        season, team_id, player_id = k
        meta = target_meta[k]
        core = core_rows(args.stage2_root, season, team_id, player_id)
        core_by_metric = {str(r["metric"]): r for _, r in core.iterrows()}
        player = str(meta.get("player") or first_nonblank(core.get("subject_player", pd.Series(dtype=str))) or "")
        intervals = meta.get("intervals") or []
        tenure_start = str(intervals[0].get("start")) if intervals else ""
        tenure_end = str(intervals[-1].get("end")) if intervals else ""
        target_minutes_on = float(meta.get("minutes_on") or 0.0)
        tenure_games = int(meta.get("team_games_in_tenure") or 0)
        full_core = bool(meta.get("full_core_reuse"))

        candidate = candidates.get(k)
        bounded = False
        note = ""
        minute_method = ""

        if full_core:
            source_kind = "established_full_core_direct_supplement"
            payload = None
            minutes_on_values = pd.to_numeric(core["minutes_on"], errors="coerce").dropna().unique().tolist()
            minutes_off_values = pd.to_numeric(core["minutes_off"], errors="coerce").dropna().unique().tolist()
            if len(minutes_on_values) != 1 or len(minutes_off_values) != 1:
                raise RuntimeError(f"non-unique full-core minutes for {k}")
            minutes_on = float(minutes_on_values[0])
            minutes_off = float(minutes_off_values[0])
            minute_method = "established_full_core_display_minutes"
        elif candidate is not None:
            _, candidate_source, rec = candidate
            payload = metric_payload_map(rec)
            source_kind = f"exact_tenure_metric_supplement:{candidate_source}"
            minutes_on = finite(rec.get("minutes_on"))
            minutes_off = finite(rec.get("minutes_off"))
            if minutes_on is not None and minutes_off is not None:
                minute_method = "recovered_tenure_endpoint_minutes"
            else:
                minutes_on = target_minutes_on
                minutes_off = max(0.0, tenure_games * 240.0 - minutes_on)
                minute_method = "accepted_bounded_regulation_team_minutes_proxy"
                bounded = True
                note = "89 exact tenure-scoped ON/OFF/SWING metrics recovered; only OFF weighting minutes use regulation-team-minutes proxy."
        else:
            payload = None
            source_kind = "accepted_bounded_direct_core_one_game_fallback"
            minutes_on = target_minutes_on
            minutes_off = max(0.0, tenure_games * 240.0 - minutes_on)
            minute_method = "accepted_bounded_regulation_team_minutes_proxy"
            bounded = True
            note = "One-game tenure-scoped endpoint remained unavailable; established direct core values used for non-rebound metrics. Exact rebound metrics are overwritten downstream from locked exact counts."

        if minutes_on is None or minutes_off is None or minutes_on < 0 or minutes_off < 0:
            raise RuntimeError(f"invalid supplemental minutes for {k}: on={minutes_on} off={minutes_off}")

        for metric in sorted(metric_universe):
            cr = core_by_metric.get(metric)
            if cr is None:
                raise RuntimeError(f"core metric missing for {k} {metric}")
            if payload is not None:
                m = payload.get(metric)
                if m is None:
                    raise RuntimeError(f"recovered metric missing for {k} {metric}")
                on = finite(m.get("on"))
                off = finite(m.get("off_corrected"))
                swing = finite(m.get("on_minus_off_corrected"))
            else:
                on = finite(cr.get("on"))
                off = finite(cr.get("off"))
                swing = finite(cr.get("on_off"))

            row = {c: np.nan for c in src.columns}
            row.update({
                "season": season,
                "team_id": int(team_id),
                "player_id": str(player_id),
                "player": player,
                "metric": metric,
                "segment_count": 1,
                "minutes_on": float(minutes_on),
                "minutes_off": float(minutes_off),
                "on": on,
                "off_corrected": off,
                "on_minus_off_corrected": swing if swing is not None else (on - off if on is not None and off is not None else np.nan),
                "aggregation_method": source_kind,
                "aggregation_confidence": "accepted_bounded" if bounded else "high",
                "tenure_start": tenure_start,
                "tenure_end": tenure_end,
                "legacy_core_match": True,
                "core_on": finite(cr.get("on")),
                "core_off_uncorrected": finite(cr.get("off")),
                "core_swing_uncorrected": finite(cr.get("on_off")),
                "patch_forward_source": source_kind,
                "accepted_materiality_exception": bool(bounded),
                "patch_forward_note": note,
            })
            if "team_abbr" in row:
                row["team_abbr"] = team_abbr.get((season, int(team_id)), "")
            off_v = finite(row.get("off_corrected"))
            core_off_v = finite(row.get("core_off_uncorrected"))
            swing_v = finite(row.get("on_minus_off_corrected"))
            core_swing_v = finite(row.get("core_swing_uncorrected"))
            if off_v is not None and core_off_v is not None:
                row["off_correction_delta"] = off_v - core_off_v
            if swing_v is not None and core_swing_v is not None:
                row["swing_correction_delta"] = swing_v - core_swing_v
            supplemental.append(row)

        ledger_rows.append({
            "season": season,
            "team_id": int(team_id),
            "player_id": str(player_id),
            "player": player,
            "team_games_in_tenure": tenure_games,
            "minutes_on": float(minutes_on),
            "minutes_off": float(minutes_off),
            "full_core_reuse": full_core,
            "patch_forward_source": source_kind,
            "minute_method": minute_method,
            "accepted_materiality_exception": bool(bounded),
            "note": note,
        })

    supp = pd.DataFrame(supplemental)
    if len(supp) != EXPECTED_MISSING * EXPECTED_METRICS:
        raise RuntimeError(f"supplemental row count {len(supp)} != {EXPECTED_MISSING * EXPECTED_METRICS}")
    if supp.duplicated(KEYS + ["metric"]).any():
        raise RuntimeError("duplicate supplemental key-metric rows")

    patched = pd.concat([src, supp[src.columns]], ignore_index=True, sort=False)
    if patched.duplicated(KEYS + ["metric"]).any():
        raise RuntimeError("patch-forward created duplicate key-metric rows")

    manifest_path = BASE / "impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz"
    all_targets = canonical(pd.DataFrame(load_gzip_jsonl(manifest_path)))[KEYS].drop_duplicates()
    if len(all_targets) != EXPECTED_CANONICAL_KEYS:
        raise RuntimeError(f"authoritative target count drift: {len(all_targets)}")
    filtered = patched.merge(all_targets.assign(_target=True), on=KEYS, how="inner", validate="many_to_one")
    counts = filtered.groupby(KEYS).metric.nunique()
    if len(counts) != EXPECTED_CANONICAL_KEYS or int(counts.min()) != EXPECTED_METRICS or int(counts.max()) != EXPECTED_METRICS:
        raise RuntimeError(f"patched canonical coverage failed keys={len(counts)} min={int(counts.min())} max={int(counts.max())}")

    patched.to_parquet(stage2_file, index=False, compression="zstd")
    patched.to_csv(args.stage2_export_dir / "player_team_season_corrected_on_off.csv.gz", index=False, compression="gzip")

    sources = Counter(r["patch_forward_source"] for r in ledger_rows)
    bounded_rows = [r for r in ledger_rows if r["accepted_materiality_exception"]]
    fallback_rows = [r for r in bounded_rows if r["patch_forward_source"] == "accepted_bounded_direct_core_one_game_fallback"]
    proxy_rows = [r for r in bounded_rows if r["minute_method"] == "accepted_bounded_regulation_team_minutes_proxy"]
    if len(bounded_rows) > 6 or len(fallback_rows) > 4 or len(proxy_rows) > 6:
        raise RuntimeError(f"materiality envelope exceeded bounded={len(bounded_rows)} fallback={len(fallback_rows)} proxy={len(proxy_rows)}")

    ledger = {
        "status": "PASS",
        "policy": "completion_first_materiality_bounded_patch_forward",
        "stage2_historical_rebuild": False,
        "structural_integrity_gates_relaxed": False,
        "authoritative_canonical_keys": EXPECTED_CANONICAL_KEYS,
        "base_stage2_missing_authoritative_keys": EXPECTED_MISSING,
        "supplemental_keys": len(ledger_rows),
        "supplemental_metric_rows": len(supp),
        "bounded_exception_keys": len(bounded_rows),
        "bounded_exception_minutes_on": float(sum(r["minutes_on"] for r in bounded_rows)),
        "direct_core_one_game_fallback_keys": len(fallback_rows),
        "off_minute_proxy_keys": len(proxy_rows),
        "exact_or_established_supplement_keys": len(ledger_rows) - len(bounded_rows),
        "source_breakdown": dict(sorted(sources.items())),
        "records": ledger_rows,
        "notes": [
            "No historical Stage2 collection is rerun by this patch layer.",
            "Any remaining one-game direct-core fallback is explicitly bounded and flagged per row.",
            "For validated partial-tenure rows, TotalReboundPct, OffReboundPct and DefReboundPct are overwritten downstream from locked exact rebound counts.",
            "No rounded percentage backsolve is introduced.",
        ],
    }
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")

    if quality_file.exists():
        q = json.loads(quality_file.read_text())
        q["patch_forward_supplemental_keys"] = len(ledger_rows)
        q["patch_forward_supplemental_metric_rows"] = len(supp)
        q["patch_forward_bounded_exception_keys"] = len(bounded_rows)
        q["patch_forward_policy"] = ledger["policy"]
        quality_file.write_text(json.dumps(q, indent=2) + "\n")

    print(json.dumps({k: ledger[k] for k in [
        "status", "supplemental_keys", "supplemental_metric_rows", "bounded_exception_keys",
        "bounded_exception_minutes_on", "direct_core_one_game_fallback_keys", "off_minute_proxy_keys",
        "exact_or_established_supplement_keys", "source_breakdown"
    ]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
