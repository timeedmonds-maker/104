#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

EXPECTED_METRICS = 89
EXPECTED_CANONICAL_ROWS = 14524
EXPECTED_FULL_CORE_ROWS = 9647
EXPECTED_PARTIAL_ROWS = 4877
EXPECTED_EXACT_SEGMENTS = 5199
EXPECTED_STAGE2_WINDOWS = 15206
EXPECTED_STAGE2_METRIC_ROWS = 1353334
KEYS = ["season", "team_id", "player_id"]
COUNT_COLS = [
    "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on",
    "team_oreb_off", "team_dreb_off", "opponent_oreb_off", "opponent_dreb_off",
]
REB_OVERLAY = {
    "TotalReboundPct": ("treb_on", "treb_off"),
    "OffReboundPct": ("oreb_pct_on", "oreb_pct_off"),
    "DefReboundPct": ("dreb_pct_on", "dreb_pct_off"),
}


def ids(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def canonicalize(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["season"] = f["season"].astype(str)
    f["team_id"] = pd.to_numeric(f["team_id"], errors="raise").astype("int64")
    f["player_id"] = f["player_id"].map(ids).astype("string")
    return f


def first_nonblank(s: pd.Series) -> str:
    for v in s:
        if pd.notna(v) and str(v).strip():
            return str(v)
    return ""


def ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    den = a + b
    return a / den.where(den.ne(0))


def weighted(g: pd.DataFrame, value: str, weight: str) -> float:
    v = pd.to_numeric(g[value], errors="coerce")
    w = pd.to_numeric(g[weight], errors="coerce")
    mask = v.notna() & w.notna() & (w > 0)
    if not mask.any():
        return math.nan
    return float(np.average(v[mask], weights=w[mask]))


def aggregate_exact_partial_segments(exact_segments: pd.DataFrame) -> pd.DataFrame:
    e = canonicalize(exact_segments)
    if len(e) != EXPECTED_EXACT_SEGMENTS:
        raise RuntimeError(f"exact segment count mismatch: {len(e)} != {EXPECTED_EXACT_SEGMENTS}")
    for col in COUNT_COLS + ["seconds_on", "seconds_off", "games_processed"]:
        e[col] = pd.to_numeric(e[col], errors="raise")
        if e[col].isna().any() or (e[col] < 0).any():
            raise RuntimeError(f"invalid exact numeric field: {col}")
        if float((e[col] - e[col].round()).abs().max()) >= 1e-9:
            raise RuntimeError(f"non-integer exact numeric field: {col}")

    agg_map = {c: "sum" for c in COUNT_COLS + ["seconds_on", "seconds_off", "games_processed"]}
    if "player" in e.columns:
        agg_map["player"] = first_nonblank
    if "team_abbr" in e.columns:
        agg_map["team_abbr"] = first_nonblank
    p = e.groupby(KEYS, as_index=False, sort=True, dropna=False).agg(agg_map)
    if len(p) != EXPECTED_PARTIAL_ROWS or p.duplicated(KEYS).any():
        raise RuntimeError(
            f"partial exact aggregation gate failed rows={len(p)} duplicates={int(p.duplicated(KEYS).sum())}"
        )

    p["minutes_on"] = p["seconds_on"] / 60.0
    p["minutes_off"] = p["seconds_off"] / 60.0
    team_reb_on = p.team_oreb_on + p.team_dreb_on
    opp_reb_on = p.opponent_oreb_on + p.opponent_dreb_on
    team_reb_off = p.team_oreb_off + p.team_dreb_off
    opp_reb_off = p.opponent_oreb_off + p.opponent_dreb_off
    p["treb_on"] = ratio(team_reb_on, opp_reb_on)
    p["treb_off"] = ratio(team_reb_off, opp_reb_off)
    p["oreb_pct_on"] = ratio(p.team_oreb_on, p.opponent_dreb_on)
    p["oreb_pct_off"] = ratio(p.team_oreb_off, p.opponent_dreb_off)
    p["dreb_pct_on"] = ratio(p.team_dreb_on, p.opponent_oreb_on)
    p["dreb_pct_off"] = ratio(p.team_dreb_off, p.opponent_oreb_off)
    return p


def career_from_detail(detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (player_id, metric), g in detail.groupby(["player_id", "metric"], sort=True, dropna=False):
        on = weighted(g, "on", "minutes_on")
        off = weighted(g, "off", "minutes_off")
        exact_rows = int(g["exact_count_overlay"].sum())
        if exact_rows == len(g):
            source = "exact_partial_rebound_counts"
        elif exact_rows:
            source = "mixed_stage2_direct_and_exact_partial_overlay"
        else:
            source = "stage2_direct_metric_no_backsolve"
        rows.append({
            "player_id": str(player_id),
            "player": first_nonblank(g["player"]) if "player" in g.columns else "",
            "metric": str(metric),
            "player_team_seasons": int(len(g)),
            "season_count": int(g.season.nunique()),
            "team_count": int(g.team_id.nunique()),
            "minutes_on": float(pd.to_numeric(g.minutes_on, errors="coerce").fillna(0).sum()),
            "minutes_off": float(pd.to_numeric(g.minutes_off, errors="coerce").fillna(0).sum()),
            "on": on,
            "off": off,
            "swing": on - off if np.isfinite(on) and np.isfinite(off) else math.nan,
            "aggregation_method": "minutes_weighted_from_final_player_team_season_values",
            "exact_count_overlay_player_team_rows": exact_rows,
            "value_source": source,
        })
    career = pd.DataFrame(rows)
    career["qualifies_10000_minutes"] = pd.to_numeric(career.minutes_on, errors="coerce") >= 10000.0
    career["rank_high_to_low_10000"] = np.nan
    career["rank_low_to_high_10000"] = np.nan
    career["percentile_10000"] = np.nan
    for metric, idx in career[career.qualifies_10000_minutes & career.swing.notna()].groupby("metric").groups.items():
        values = career.loc[idx, "swing"]
        career.loc[idx, "rank_high_to_low_10000"] = values.rank(method="min", ascending=False)
        career.loc[idx, "rank_low_to_high_10000"] = values.rank(method="min", ascending=True)
        career.loc[idx, "percentile_10000"] = values.rank(method="average", pct=True) * 100.0
    return career.sort_values(["player_id", "metric"], kind="stable").reset_index(drop=True)


def build_wide(detail: pd.DataFrame) -> pd.DataFrame:
    p = detail.pivot(index=KEYS, columns="metric", values=["on", "off", "swing"])
    p.columns = [f"{metric}__{field}" for field, metric in p.columns]
    p = p.reset_index()
    meta_cols = [c for c in ["player", "team_abbr", "minutes_on", "minutes_off"] if c in detail.columns]
    meta = detail.groupby(KEYS, as_index=False, sort=True)[meta_cols].first() if meta_cols else detail[KEYS].drop_duplicates()
    return meta.merge(p, on=KEYS, how="left", validate="one_to_one")


def build_career_wide(career: pd.DataFrame) -> pd.DataFrame:
    p = career.pivot(index="player_id", columns="metric", values=["on", "off", "swing"])
    p.columns = [f"{metric}__{field}" for field, metric in p.columns]
    p = p.reset_index()
    meta = career.groupby("player_id", as_index=False, sort=True)[["player", "minutes_on", "minutes_off"]].first()
    return meta.merge(p, on="player_id", how="left", validate="one_to_one")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage2-export-dir", type=Path, required=True)
    ap.add_argument("--exact-dir", type=Path, required=True)
    ap.add_argument("--stage2-sha", required=True)
    args = ap.parse_args()

    stage2 = args.stage2_export_dir
    out = args.exact_dir
    quality = json.loads((stage2 / "quality_report.json").read_text())
    if int(quality.get("metric_count", -1)) != EXPECTED_METRICS:
        raise RuntimeError(f"Stage2 metric count is not 89: {quality}")
    if int(quality.get("tenure_segment_metric_rows", -1)) != EXPECTED_STAGE2_METRIC_ROWS:
        raise RuntimeError(f"Stage2 metric-row count mismatch: {quality}")

    src = canonicalize(pd.read_parquet(stage2 / "player_team_season_corrected_on_off.parquet"))
    src["metric"] = src.metric.astype(str)
    src["on"] = pd.to_numeric(src["on"], errors="coerce")
    src["off"] = pd.to_numeric(src["off_corrected"], errors="coerce")
    src["swing"] = pd.to_numeric(src["on_minus_off_corrected"], errors="coerce")
    if "player" not in src.columns:
        src["player"] = src.get("subject_player", "")

    # Stage2 intentionally contains a broader 14,600-key metric population. The
    # final release universe is the independently validated V2 roster-tenure set
    # of exactly 14,524 keys. Filter only by that immutable manifest; never infer
    # the release population from Stage2 rows themselves.
    target_path = Path(__file__).resolve().parent / "impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz"
    target_rows = []
    with gzip.open(target_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                target_rows.append(json.loads(line))
    targets = canonicalize(pd.DataFrame(target_rows))[KEYS].drop_duplicates()
    if len(targets) != EXPECTED_CANONICAL_ROWS:
        raise RuntimeError(f"authoritative V2 target count mismatch: {len(targets)}")
    source_keys = src[KEYS].drop_duplicates()
    missing_targets = targets.merge(source_keys, on=KEYS, how="left", indicator=True)
    missing_targets = missing_targets[missing_targets._merge == "left_only"]
    if len(missing_targets):
        raise RuntimeError(f"Stage2 is missing {len(missing_targets)} authoritative V2 canonical keys")
    src = src.merge(targets.assign(_v2_canonical=True), on=KEYS, how="inner", validate="many_to_one")
    src = src.drop(columns=["_v2_canonical"])

    metric_names = sorted(src.metric.unique().tolist())
    canonical_keys = src[KEYS].drop_duplicates()
    key_metric_counts = src.groupby(KEYS).metric.nunique()
    expected_metric_rows = EXPECTED_CANONICAL_ROWS * EXPECTED_METRICS
    if len(metric_names) != EXPECTED_METRICS:
        raise RuntimeError(f"expected 89 Stage2 metrics, got {len(metric_names)}")
    if len(canonical_keys) != EXPECTED_CANONICAL_ROWS:
        raise RuntimeError(f"V2-filtered canonical key count mismatch: {len(canonical_keys)}")
    if len(src) != expected_metric_rows or int(key_metric_counts.min()) != 89 or int(key_metric_counts.max()) != 89:
        raise RuntimeError(
            f"Stage2 canonical metric gate failed rows={len(src)}/{expected_metric_rows} "
            f"min_metrics={int(key_metric_counts.min())} max_metrics={int(key_metric_counts.max())}"
        )
    if src.duplicated(KEYS + ["metric"]).any():
        raise RuntimeError("duplicate Stage2 player-team-season metric keys")
    if not set(REB_OVERLAY).issubset(metric_names):
        raise RuntimeError(f"rebound metrics missing from Stage2: {sorted(set(REB_OVERLAY)-set(metric_names))}")

    exact_segments = pd.read_csv(out / "career_treb_detail.csv", low_memory=False)
    partial = aggregate_exact_partial_segments(exact_segments)
    partial_keys = partial[KEYS].drop_duplicates()
    missing_partial = partial_keys.merge(canonical_keys, on=KEYS, how="left", indicator=True)
    missing_partial = missing_partial[missing_partial._merge == "left_only"]
    if len(missing_partial):
        raise RuntimeError(f"exact partial layer contains {len(missing_partial)} keys outside canonical V2 universe")
    full_core_rows = len(canonical_keys) - len(partial_keys)
    if full_core_rows != EXPECTED_FULL_CORE_ROWS:
        raise RuntimeError(f"full-core complement mismatch: {full_core_rows} != {EXPECTED_FULL_CORE_ROWS}")

    detail = src.copy()
    detail["exact_count_overlay"] = False
    detail["value_source"] = "stage2_direct_metric_no_backsolve"
    pidx = partial.set_index(KEYS, drop=False)
    for metric, (on_col, off_col) in REB_OVERLAY.items():
        mask = detail.metric.eq(metric)
        key_tuples = list(map(tuple, detail.loc[mask, KEYS].itertuples(index=False, name=None)))
        is_partial = np.array([k in pidx.index for k in key_tuples], dtype=bool)
        mask_idx = detail.index[mask]
        overlay_idx = mask_idx[is_partial]
        overlay_keys = [k for k, yes in zip(key_tuples, is_partial) if yes]
        if len(overlay_idx) != EXPECTED_PARTIAL_ROWS:
            raise RuntimeError(f"{metric} exact partial overlay count={len(overlay_idx)} expected={EXPECTED_PARTIAL_ROWS}")
        on_vals = [pidx.at[k, on_col] for k in overlay_keys]
        off_vals = [pidx.at[k, off_col] for k in overlay_keys]
        detail.loc[overlay_idx, "on"] = on_vals
        detail.loc[overlay_idx, "off"] = off_vals
        detail.loc[overlay_idx, "off_corrected"] = off_vals
        swings = [a - b if pd.notna(a) and pd.notna(b) else np.nan for a, b in zip(on_vals, off_vals)]
        detail.loc[overlay_idx, "swing"] = swings
        detail.loc[overlay_idx, "on_minus_off_corrected"] = swings
        detail.loc[overlay_idx, "minutes_on"] = [pidx.at[k, "minutes_on"] for k in overlay_keys]
        detail.loc[overlay_idx, "minutes_off"] = [pidx.at[k, "minutes_off"] for k in overlay_keys]
        detail.loc[overlay_idx, "aggregation_method"] = "exact_locked_segments_summed_then_rate_recomputed"
        if "aggregation_confidence" in detail.columns:
            detail.loc[overlay_idx, "aggregation_confidence"] = "exact"
        detail.loc[overlay_idx, "exact_count_overlay"] = True
        detail.loc[overlay_idx, "value_source"] = "exact_locked_rebound_counts"

    detail = detail.sort_values(KEYS + ["metric"], kind="stable").reset_index(drop=True)
    overlay_metric_rows = int(detail.exact_count_overlay.sum())
    if overlay_metric_rows != EXPECTED_PARTIAL_ROWS * len(REB_OVERLAY):
        raise RuntimeError(f"exact rebound overlay metric-row mismatch: {overlay_metric_rows}")

    career = career_from_detail(detail)
    career_players = int(detail.player_id.nunique())
    expected_career_rows = career_players * EXPECTED_METRICS
    if len(career) != expected_career_rows or career.metric.nunique() != EXPECTED_METRICS:
        raise RuntimeError(
            f"career gate failed players={career_players} rows={len(career)}/{expected_career_rows} metrics={career.metric.nunique()}"
        )

    detail.to_parquet(out / "all_metrics_player_team_season.parquet", index=False, compression="zstd")
    detail.to_csv(out / "all_metrics_player_team_season.csv.gz", index=False, compression="gzip")
    career.to_parquet(out / "all_metrics_career.parquet", index=False, compression="zstd")
    career.to_csv(out / "all_metrics_career.csv.gz", index=False, compression="gzip")

    wide = build_wide(detail)
    career_wide = build_career_wide(career)
    wide.to_parquet(out / "all_metrics_player_team_season_wide.parquet", index=False, compression="zstd")
    wide.to_csv(out / "all_metrics_player_team_season_wide.csv.gz", index=False, compression="gzip")
    career_wide.to_parquet(out / "all_metrics_career_wide.parquet", index=False, compression="zstd")
    career_wide.to_csv(out / "all_metrics_career_wide.csv.gz", index=False, compression="gzip")

    dictionary = pd.read_parquet(stage2 / "metric_dictionary.parquet").copy()
    dictionary["metric"] = dictionary.metric.astype(str)
    dictionary["final_on_off_swing_columns"] = "on | off | swing"
    dictionary["exact_count_overlay"] = dictionary.metric.isin(REB_OVERLAY)
    dictionary["final_value_source"] = "Stage2 tenure-corrected direct metric; no percentage backsolve"
    dictionary.loc[dictionary.exact_count_overlay, "final_value_source"] = (
        "4,877 partial player-team-season rows use exact locked rebound counts; "
        "9,647 full-core rows retain Stage2 direct metric; no percentage backsolve"
    )
    dictionary.to_parquet(out / "metric_dictionary.parquet", index=False)
    dictionary.to_csv(out / "metric_dictionary.csv", index=False)

    db_path = out / "TREB_all_metrics.duckdb"
    if db_path.exists():
        db_path.unlink()
    db = duckdb.connect(str(db_path))
    for name, path in {
        "all_metrics_player_team_season": out / "all_metrics_player_team_season.parquet",
        "all_metrics_career": out / "all_metrics_career.parquet",
        "all_metrics_player_team_season_wide": out / "all_metrics_player_team_season_wide.parquet",
        "all_metrics_career_wide": out / "all_metrics_career_wide.parquet",
        "metric_dictionary": out / "metric_dictionary.parquet",
    }.items():
        db.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    db.execute("CREATE INDEX idx_all_metric_player ON all_metrics_player_team_season(player_id, season, team_id)")
    db.execute("CREATE INDEX idx_all_career_player ON all_metrics_career(player_id, metric)")
    db.close()

    qa = {
        "status": "PASS",
        "stage2_sha": args.stage2_sha,
        "stage2_windows": EXPECTED_STAGE2_WINDOWS,
        "stage2_source_metric_rows": EXPECTED_STAGE2_METRIC_ROWS,
        "metric_count": EXPECTED_METRICS,
        "canonical_population_source": "authoritative_v2_roster_tenure_manifest",
        "canonical_player_team_season_rows": int(len(canonical_keys)),
        "player_team_season_metric_rows": int(len(detail)),
        "career_players": career_players,
        "career_metric_rows": int(len(career)),
        "exact_count_segment_rows": int(len(exact_segments)),
        "exact_count_partial_player_team_rows": int(len(partial_keys)),
        "full_core_stage2_direct_rows": int(full_core_rows),
        "exact_count_overlay_metric_rows": overlay_metric_rows,
        "stage2_direct_rebound_metric_rows": EXPECTED_FULL_CORE_ROWS * len(REB_OVERLAY),
        "exact_count_overlay_metrics": sorted(REB_OVERLAY),
        "all_89_metrics_present_for_every_canonical_key": True,
        "career_89_metrics_present_for_every_player": True,
        "rounded_percentage_backsolve_used": False,
        "rebound_partial_policy": "sum validated exact locked segment counts, then recompute TREB/OREB/DREB rates",
        "rebound_full_core_policy": "retain authoritative Stage2 direct PBP Stats metric; never infer counts from rounded percentages",
        "career_policy": "minutes-weight final player-team-season ON/OFF values for every metric; exact partial rebound overlays flow into career values",
    }
    (out / "ALL_89_METRICS_QA.json").write_text(json.dumps(qa, indent=2) + "\n")
    (out / "README_ALL_89_METRICS.txt").write_text(
        "FINAL 89-METRIC DATABASE\n\n"
        "Primary files: all_metrics_player_team_season_* and all_metrics_career_*.\n"
        "Every authoritative V2 canonical player-team-season has all 89 metrics with ON, OFF and SWING.\n"
        "For TREB/OREB/DREB, the 4,877 partial player-team-season rows use exact locked rebound counts aggregated from 5,199 validated tenure segments.\n"
        "The 9,647 full-core rows retain the authoritative Stage2 direct PBP Stats rebound percentages. No rounded percentage is inverted or backsolved into counts anywhere.\n"
        "career_treb_detail.* and career_treb_summary.* are supporting exact partial-lock files, not the complete all-player career tables.\n",
        encoding="utf-8",
    )
    print(json.dumps(qa, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
