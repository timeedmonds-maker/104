#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

EXPECTED_METRICS = 89
EXPECTED_CANONICAL_ROWS = 14524
EXPECTED_STAGE2_WINDOWS = 15206
EXPECTED_STAGE2_METRIC_ROWS = 1353334
REB_OVERLAY = {
    "TotalReboundPct": ("treb_on", "treb_off"),
    "OffReboundPct": ("oreb_pct_on", "oreb_pct_off"),
    "DefReboundPct": ("dreb_pct_on", "dreb_pct_off"),
}
KEYS = ["season", "team_id", "player_id"]


def ids(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def weighted(g: pd.DataFrame, value: str, weight: str) -> float:
    v = pd.to_numeric(g[value], errors="coerce")
    w = pd.to_numeric(g[weight], errors="coerce")
    mask = v.notna() & w.notna() & (w > 0)
    if not mask.any():
        return math.nan
    return float(np.average(v[mask], weights=w[mask]))


def canonicalize(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["season"] = f["season"].astype(str)
    f["team_id"] = pd.to_numeric(f["team_id"], errors="raise").astype("int64")
    f["player_id"] = f["player_id"].map(ids).astype("string")
    return f


def career_from_detail(detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (player_id, metric), g in detail.groupby(["player_id", "metric"], sort=True, dropna=False):
        on = weighted(g, "on", "minutes_on")
        off = weighted(g, "off", "minutes_off")
        names = [str(x) for x in g.get("player", pd.Series(dtype=str)).dropna() if str(x).strip()]
        rows.append({
            "player_id": str(player_id),
            "player": names[0] if names else "",
            "metric": str(metric),
            "player_team_seasons": int(len(g)),
            "season_count": int(g.season.nunique()),
            "team_count": int(g.team_id.nunique()),
            "minutes_on": float(pd.to_numeric(g.minutes_on, errors="coerce").fillna(0).sum()),
            "minutes_off": float(pd.to_numeric(g.minutes_off, errors="coerce").fillna(0).sum()),
            "on": on,
            "off": off,
            "swing": on - off if np.isfinite(on) and np.isfinite(off) else math.nan,
            "aggregation_method": "minutes_weighted_across_player_team_seasons",
            "exact_count_overlay": False,
            "value_source": "stage2_tenure_corrected_89_metric",
        })
    return pd.DataFrame(rows)


def build_wide(detail: pd.DataFrame, exact_meta: pd.DataFrame) -> pd.DataFrame:
    p = detail.pivot(index=KEYS, columns="metric", values=["on", "off", "swing"])
    p.columns = [f"{metric}__{field}" for field, metric in p.columns]
    p = p.reset_index()
    meta_cols = [c for c in ["season", "team_id", "player_id", "player", "team_abbr", "minutes_on", "minutes_off", "games_processed"] if c in exact_meta.columns]
    meta = exact_meta[meta_cols].drop_duplicates(KEYS)
    return meta.merge(p, on=KEYS, how="one_to_one" if False else "left", validate="one_to_one")


def build_career_wide(career: pd.DataFrame, exact_career: pd.DataFrame) -> pd.DataFrame:
    p = career.pivot(index="player_id", columns="metric", values=["on", "off", "swing"])
    p.columns = [f"{metric}__{field}" for field, metric in p.columns]
    p = p.reset_index()
    meta_cols = [c for c in ["player_id", "player", "first_season", "last_season", "seasons", "teams", "team_list", "tenure_segments", "games_processed", "minutes_on", "minutes_off"] if c in exact_career.columns]
    meta = exact_career[meta_cols].copy()
    meta["player_id"] = meta.player_id.map(ids).astype("string")
    return meta.merge(p, on="player_id", how="left", validate="one_to_one")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage2-export-dir", type=Path, required=True)
    ap.add_argument("--exact-dir", type=Path, required=True)
    ap.add_argument("--stage2-sha", default="57ba237c36d75f7a3ef2cc998d91aa70a59b3c29")
    args = ap.parse_args()

    stage2 = args.stage2_export_dir
    out = args.exact_dir
    exact = canonicalize(pd.read_csv(out / "career_treb_detail.csv", low_memory=False))
    exact_career = pd.read_csv(out / "career_treb_summary.csv", low_memory=False)
    exact_career["player_id"] = exact_career.player_id.map(ids).astype("string")
    if len(exact) != EXPECTED_CANONICAL_ROWS or exact.duplicated(KEYS).any():
        raise RuntimeError(f"exact canonical key gate failed rows={len(exact)} duplicates={int(exact.duplicated(KEYS).sum())}")

    quality = json.loads((stage2 / "quality_report.json").read_text())
    if int(quality.get("metric_count", -1)) != EXPECTED_METRICS:
        raise RuntimeError(f"Stage2 metric count is not 89: {quality}")
    if int(quality.get("tenure_segment_metric_rows", -1)) != EXPECTED_STAGE2_METRIC_ROWS:
        raise RuntimeError(f"Stage2 metric-row count mismatch: {quality}")

    src = canonicalize(pd.read_parquet(stage2 / "player_team_season_corrected_on_off.parquet"))
    src["metric"] = src.metric.astype(str)
    metric_names = sorted(src.metric.unique().tolist())
    if len(metric_names) != EXPECTED_METRICS:
        raise RuntimeError(f"expected 89 Stage2 metrics, got {len(metric_names)}")
    if not set(REB_OVERLAY).issubset(metric_names):
        raise RuntimeError(f"exact rebound metric names missing: {sorted(set(REB_OVERLAY)-set(metric_names))}")

    canonical_keys = exact[KEYS].drop_duplicates()
    missing = canonical_keys.merge(src[KEYS].drop_duplicates(), on=KEYS, how="left", indicator=True)
    missing = missing[missing._merge == "left_only"]
    if len(missing):
        raise RuntimeError(f"Stage2 all-metric data missing {len(missing)} canonical player-team-season keys")

    detail = src.merge(canonical_keys, on=KEYS, how="inner", validate="many_to_one").copy()
    expected_metric_rows = EXPECTED_CANONICAL_ROWS * EXPECTED_METRICS
    if len(detail) != expected_metric_rows:
        counts = detail.groupby(KEYS).metric.nunique()
        raise RuntimeError(f"canonical all-metric row gate failed rows={len(detail)} expected={expected_metric_rows} min_metrics={int(counts.min()) if len(counts) else 0} max_metrics={int(counts.max()) if len(counts) else 0}")
    if detail.duplicated(KEYS + ["metric"]).any():
        raise RuntimeError("duplicate canonical player-team-season metric keys")

    exact_idx = exact.set_index(KEYS, drop=False)
    key_tuples = list(map(tuple, detail[KEYS].itertuples(index=False, name=None)))
    exact_name = exact_idx["player"].to_dict() if "player" in exact_idx.columns else {}
    exact_abbr = exact_idx["team_abbr"].to_dict() if "team_abbr" in exact_idx.columns else {}
    detail["player"] = [exact_name.get(k, "") for k in key_tuples]
    detail["team_abbr"] = [exact_abbr.get(k, "") for k in key_tuples]
    detail["off"] = pd.to_numeric(detail["off_corrected"], errors="coerce")
    detail["swing"] = pd.to_numeric(detail["on_minus_off_corrected"], errors="coerce")
    detail["exact_count_overlay"] = False
    detail["value_source"] = "stage2_tenure_corrected_89_metric"

    for metric, (on_col, off_col) in REB_OVERLAY.items():
        mask = detail.metric.eq(metric)
        rows = detail.loc[mask, KEYS]
        tuples = list(map(tuple, rows.itertuples(index=False, name=None)))
        on_lookup = exact_idx[on_col].to_dict()
        off_lookup = exact_idx[off_col].to_dict()
        min_lookup = exact_idx["minutes_on"].to_dict()
        moff_lookup = exact_idx["minutes_off"].to_dict()
        on_vals = [on_lookup[k] for k in tuples]
        off_vals = [off_lookup[k] for k in tuples]
        detail.loc[mask, "on"] = on_vals
        detail.loc[mask, "off"] = off_vals
        detail.loc[mask, "off_corrected"] = off_vals
        detail.loc[mask, "swing"] = [a-b if pd.notna(a) and pd.notna(b) else np.nan for a,b in zip(on_vals, off_vals)]
        detail.loc[mask, "on_minus_off_corrected"] = detail.loc[mask, "swing"].to_numpy()
        detail.loc[mask, "minutes_on"] = [min_lookup[k] for k in tuples]
        detail.loc[mask, "minutes_off"] = [moff_lookup[k] for k in tuples]
        detail.loc[mask, "aggregation_method"] = "exact_rebound_count_overlay"
        if "aggregation_confidence" in detail.columns:
            detail.loc[mask, "aggregation_confidence"] = "exact"
        detail.loc[mask, "exact_count_overlay"] = True
        detail.loc[mask, "value_source"] = "exact_rebound_counts"

    detail = detail.sort_values(KEYS + ["metric"], kind="stable").reset_index(drop=True)
    overlay_rows = int(detail.exact_count_overlay.sum())
    if overlay_rows != EXPECTED_CANONICAL_ROWS * len(REB_OVERLAY):
        raise RuntimeError(f"exact rebound overlay row mismatch: {overlay_rows}")

    career = career_from_detail(detail)
    exact_cidx = exact_career.set_index("player_id", drop=False)
    for metric, (on_col, off_col) in REB_OVERLAY.items():
        mask = career.metric.eq(metric)
        pids = career.loc[mask, "player_id"].astype(str).tolist()
        on_vals = [exact_cidx.at[p, on_col] for p in pids]
        off_vals = [exact_cidx.at[p, off_col] for p in pids]
        career.loc[mask, "on"] = on_vals
        career.loc[mask, "off"] = off_vals
        career.loc[mask, "swing"] = [a-b if pd.notna(a) and pd.notna(b) else np.nan for a,b in zip(on_vals, off_vals)]
        career.loc[mask, "minutes_on"] = [exact_cidx.at[p, "minutes_on"] for p in pids]
        career.loc[mask, "minutes_off"] = [exact_cidx.at[p, "minutes_off"] for p in pids]
        career.loc[mask, "aggregation_method"] = "exact_counts_summed_then_rate_recomputed"
        career.loc[mask, "exact_count_overlay"] = True
        career.loc[mask, "value_source"] = "exact_rebound_counts"

    career["qualifies_10000_minutes"] = pd.to_numeric(career.minutes_on, errors="coerce") >= 10000.0
    career["rank_high_to_low_10000"] = np.nan
    career["rank_low_to_high_10000"] = np.nan
    career["percentile_10000"] = np.nan
    for metric, idx in career[career.qualifies_10000_minutes & career.swing.notna()].groupby("metric").groups.items():
        values = career.loc[idx, "swing"]
        career.loc[idx, "rank_high_to_low_10000"] = values.rank(method="min", ascending=False)
        career.loc[idx, "rank_low_to_high_10000"] = values.rank(method="min", ascending=True)
        career.loc[idx, "percentile_10000"] = values.rank(method="average", pct=True) * 100.0
    career = career.sort_values(["player_id", "metric"], kind="stable").reset_index(drop=True)

    career_players = int(exact_career.player_id.nunique())
    expected_career_rows = career_players * EXPECTED_METRICS
    if career.player_id.nunique() != career_players or len(career) != expected_career_rows:
        raise RuntimeError(f"career all-metric gate failed players={career.player_id.nunique()}/{career_players} rows={len(career)}/{expected_career_rows}")

    # Preserve explicit ON / OFF / SWING naming while retaining the historical corrected-OFF columns.
    long_pq = out / "all_metrics_player_team_season.parquet"
    long_csv = out / "all_metrics_player_team_season.csv.gz"
    career_pq = out / "all_metrics_career.parquet"
    career_csv = out / "all_metrics_career.csv.gz"
    detail.to_parquet(long_pq, index=False, compression="zstd")
    detail.to_csv(long_csv, index=False, compression="gzip")
    career.to_parquet(career_pq, index=False, compression="zstd")
    career.to_csv(career_csv, index=False, compression="gzip")

    wide = build_wide(detail, exact)
    career_wide = build_career_wide(career, exact_career)
    wide.to_parquet(out / "all_metrics_player_team_season_wide.parquet", index=False, compression="zstd")
    wide.to_csv(out / "all_metrics_player_team_season_wide.csv.gz", index=False, compression="gzip")
    career_wide.to_parquet(out / "all_metrics_career_wide.parquet", index=False, compression="zstd")
    career_wide.to_csv(out / "all_metrics_career_wide.csv.gz", index=False, compression="gzip")

    dictionary = pd.read_parquet(stage2 / "metric_dictionary.parquet").copy()
    dictionary["metric"] = dictionary.metric.astype(str)
    dictionary["final_value_source"] = "stage2_tenure_corrected_89_metric"
    dictionary["final_on_off_swing_columns"] = "on | off | swing"
    dictionary["exact_count_overlay"] = dictionary.metric.isin(REB_OVERLAY)
    dictionary.loc[dictionary.exact_count_overlay, "final_value_source"] = "exact rebound counts; ON/OFF rates recomputed from counts"
    dictionary.to_csv(out / "all_metrics_metric_dictionary.csv", index=False)
    dictionary.to_parquet(out / "all_metrics_metric_dictionary.parquet", index=False)

    db = duckdb.connect(str(out / "TREB_all_metrics.duckdb"))
    db.execute(f"CREATE TABLE all_metrics_player_team_season AS SELECT * FROM read_parquet('{long_pq.as_posix()}')")
    db.execute(f"CREATE TABLE all_metrics_career AS SELECT * FROM read_parquet('{career_pq.as_posix()}')")
    db.execute(f"CREATE TABLE metric_dictionary AS SELECT * FROM read_parquet('{(out/'all_metrics_metric_dictionary.parquet').as_posix()}')")
    db.execute(f"CREATE TABLE exact_treb_player_team_season AS SELECT * FROM read_parquet('{(out/'career_treb_detail.parquet').as_posix()}')")
    db.execute(f"CREATE TABLE exact_treb_career AS SELECT * FROM read_parquet('{(out/'career_treb_summary.parquet').as_posix()}')")
    db.execute("CREATE INDEX idx_all_metric_pts ON all_metrics_player_team_season(player_id, season, team_id, metric)")
    db.execute("CREATE INDEX idx_all_metric_career ON all_metrics_career(player_id, metric)")
    db.close()

    qa = {
        "status": "PASS",
        "stage2_source_sha": args.stage2_sha,
        "stage2_windows": EXPECTED_STAGE2_WINDOWS,
        "stage2_source_metric_rows": EXPECTED_STAGE2_METRIC_ROWS,
        "metric_count": EXPECTED_METRICS,
        "metric_names": metric_names,
        "canonical_player_team_season_rows": EXPECTED_CANONICAL_ROWS,
        "player_team_season_metric_rows": int(len(detail)),
        "career_players": career_players,
        "career_metric_rows": int(len(career)),
        "exact_count_overlay_metrics": list(REB_OVERLAY),
        "exact_count_overlay_detail_rows": overlay_rows,
        "exact_count_overlay_career_rows": int(career.exact_count_overlay.sum()),
        "all_89_metrics_present_for_every_canonical_key": True,
        "career_89_metrics_present_for_every_player": True,
        "rebound_career_method": "sum exact OREB/DREB counts across career, then recompute rates",
        "non_rebound_career_method": "established Stage2 minutes-weighted aggregation across canonical player-team-seasons",
        "rounded_percentage_backsolve_used": False,
    }
    (out / "ALL_89_METRICS_QA.json").write_text(json.dumps(qa, indent=2) + "\n")
    (out / "README_ALL_89_METRICS.txt").write_text(
        "Complete 89-metric player impact database, regular seasons 2000-01 through 2025-26.\n"
        "all_metrics_player_team_season.*: long format; one row per canonical player-team-season per metric with explicit on, off and swing columns.\n"
        "all_metrics_player_team_season_wide.*: one row per canonical player-team-season, with <metric>__on, <metric>__off and <metric>__swing columns.\n"
        "all_metrics_career.* and all_metrics_career_wide.*: career aggregations for every player and all 89 metrics.\n"
        "TotalReboundPct, OffReboundPct and DefReboundPct are overlaid from exact stored rebound counts; no rounded percentage backsolve is used.\n"
        "All other metrics retain the completed Stage2 tenure-corrected ON/OFF values and the established minutes-weighted career aggregation.\n"
        "TREB_all_metrics.duckdb contains long all-metric tables plus the exact rebound detail and career tables.\n"
    )
    print(json.dumps(qa, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
