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

EXPECTED_NATIVE_METRICS = 89
EXPECTED_CANONICAL_ROWS = 14524
EXPECTED_STAGE2_WINDOWS = 15206
EXPECTED_STAGE2_SOURCE_ROWS = 1353334
EXPECTED_NATIVE_ROWS = EXPECTED_CANONICAL_ROWS * EXPECTED_NATIVE_METRICS
EXPECTED_CAREER_PLAYERS = 3122
EXPECTED_CAREER_NATIVE_ROWS = EXPECTED_CAREER_PLAYERS * EXPECTED_NATIVE_METRICS
EXPECTED_EXACT_SEGMENTS = 5199
EXPECTED_EXACT_PLAYER_TEAM_ROWS = 4877
KEYS = ["season", "team_id", "player_id"]
COUNT_COLS = [
    "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on",
    "team_oreb_off", "team_dreb_off", "opponent_oreb_off", "opponent_dreb_off",
]
OVERLAY = {
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


def ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    den = a + b
    return a / den.where(den.ne(0))


def first_nonblank(s: pd.Series) -> str:
    for v in s:
        if pd.notna(v) and str(v).strip():
            return str(v)
    return ""


def weighted(g: pd.DataFrame, value: str, weight: str) -> float:
    v = pd.to_numeric(g[value], errors="coerce")
    w = pd.to_numeric(g[weight], errors="coerce")
    m = v.notna() & w.notna() & (w > 0)
    if not m.any():
        return math.nan
    return float(np.average(v[m], weights=w[m]))


def load_targets() -> pd.DataFrame:
    p = Path(__file__).resolve().parent / "impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz"
    rows = []
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    t = canonicalize(pd.DataFrame(rows))[KEYS].drop_duplicates()
    if len(t) != EXPECTED_CANONICAL_ROWS:
        raise RuntimeError(f"authoritative canonical target count {len(t)} != {EXPECTED_CANONICAL_ROWS}")
    return t


def aggregate_exact_segments(exact_segments: pd.DataFrame) -> pd.DataFrame:
    e = canonicalize(exact_segments)
    if len(e) != EXPECTED_EXACT_SEGMENTS:
        raise RuntimeError(f"exact segment count {len(e)} != {EXPECTED_EXACT_SEGMENTS}")
    for col in COUNT_COLS + ["seconds_on", "seconds_off", "games_processed"]:
        e[col] = pd.to_numeric(e[col], errors="raise")
        if e[col].isna().any() or (e[col] < 0).any():
            raise RuntimeError(f"invalid exact numeric column {col}")
        if float((e[col] - e[col].round()).abs().max()) >= 1e-9:
            raise RuntimeError(f"non-integer exact count field {col}")

    agg = {c: "sum" for c in COUNT_COLS + ["seconds_on", "seconds_off", "games_processed"]}
    if "player" in e:
        agg["player"] = first_nonblank
    if "team_abbr" in e:
        agg["team_abbr"] = first_nonblank
    p = e.groupby(KEYS, as_index=False, sort=True, dropna=False).agg(agg)
    if len(p) != EXPECTED_EXACT_PLAYER_TEAM_ROWS or p.duplicated(KEYS).any():
        raise RuntimeError(
            f"exact player-team aggregation rows={len(p)} duplicates={int(p.duplicated(KEYS).sum())}"
        )

    p["minutes_on"] = p["seconds_on"] / 60.0
    p["minutes_off"] = p["seconds_off"] / 60.0
    p["treb_on"] = ratio(p.team_oreb_on + p.team_dreb_on, p.opponent_oreb_on + p.opponent_dreb_on)
    p["treb_off"] = ratio(p.team_oreb_off + p.team_dreb_off, p.opponent_oreb_off + p.opponent_dreb_off)
    p["oreb_pct_on"] = ratio(p.team_oreb_on, p.opponent_dreb_on)
    p["oreb_pct_off"] = ratio(p.team_oreb_off, p.opponent_dreb_off)
    p["dreb_pct_on"] = ratio(p.team_dreb_on, p.opponent_oreb_on)
    p["dreb_pct_off"] = ratio(p.team_dreb_off, p.opponent_oreb_off)
    p["treb_swing"] = p.treb_on - p.treb_off
    p["oreb_pct_swing"] = p.oreb_pct_on - p.oreb_pct_off
    p["dreb_pct_swing"] = p.dreb_pct_on - p.dreb_pct_off
    return p


def career_native(detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (player_id, metric), g in detail.groupby(["player_id", "metric"], sort=True, dropna=False):
        on = weighted(g, "on", "minutes_on")
        off = weighted(g, "off", "minutes_off")
        rows.append({
            "player_id": str(player_id),
            "player": first_nonblank(g["player"]) if "player" in g else "",
            "metric": str(metric),
            "player_team_seasons": int(len(g)),
            "season_count": int(g.season.nunique()),
            "team_count": int(g.team_id.nunique()),
            "minutes_on": float(pd.to_numeric(g.minutes_on, errors="coerce").fillna(0).sum()),
            "minutes_off": float(pd.to_numeric(g.minutes_off, errors="coerce").fillna(0).sum()),
            "on": on,
            "off": off,
            "swing": on - off if np.isfinite(on) and np.isfinite(off) else math.nan,
            "value_source": "stage2_native_tenure_corrected",
        })
    c = pd.DataFrame(rows)
    c["qualifies_10000_minutes"] = pd.to_numeric(c.minutes_on, errors="coerce") >= 10000.0
    c["rank_high_to_low_10000"] = np.nan
    c["rank_low_to_high_10000"] = np.nan
    c["percentile_10000"] = np.nan
    for metric, idx in c[c.qualifies_10000_minutes & c.swing.notna()].groupby("metric").groups.items():
        v = c.loc[idx, "swing"]
        c.loc[idx, "rank_high_to_low_10000"] = v.rank(method="min", ascending=False)
        c.loc[idx, "rank_low_to_high_10000"] = v.rank(method="min", ascending=True)
        c.loc[idx, "percentile_10000"] = v.rank(method="average", pct=True) * 100.0
    return c.sort_values(["player_id", "metric"], kind="stable").reset_index(drop=True)


def overlay_long(overlay: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for metric, (on_col, off_col) in OVERLAY.items():
        cols = KEYS + [c for c in ["player", "team_abbr", "minutes_on", "minutes_off"] if c in overlay.columns]
        f = overlay[cols].copy()
        f["metric"] = metric
        f["on"] = pd.to_numeric(overlay[on_col], errors="coerce").to_numpy()
        f["off"] = pd.to_numeric(overlay[off_col], errors="coerce").to_numpy()
        f["swing"] = f["on"] - f["off"]
        f["value_source"] = "exact_locked_integer_rebound_counts"
        pieces.append(f)
    return pd.concat(pieces, ignore_index=True).sort_values(KEYS + ["metric"], kind="stable").reset_index(drop=True)


def wide_native(detail: pd.DataFrame) -> pd.DataFrame:
    p = detail.pivot(index=KEYS, columns="metric", values=["on", "off", "swing"])
    p.columns = [f"{metric}__{field}" for field, metric in p.columns]
    p = p.reset_index()
    meta_cols = [c for c in ["player", "team_abbr", "minutes_on", "minutes_off"] if c in detail]
    meta = detail.groupby(KEYS, as_index=False, sort=True)[meta_cols].first()
    return meta.merge(p, on=KEYS, how="left", validate="one_to_one")


def wide_career(career: pd.DataFrame) -> pd.DataFrame:
    p = career.pivot(index="player_id", columns="metric", values=["on", "off", "swing"])
    p.columns = [f"{metric}__{field}" for field, metric in p.columns]
    p = p.reset_index()
    meta = career.groupby("player_id", as_index=False, sort=True)[["player", "minutes_on", "minutes_off"]].first()
    return meta.merge(p, on="player_id", how="left", validate="one_to_one")


def exact_career_long(exact_career: pd.DataFrame) -> pd.DataFrame:
    e = exact_career.copy()
    e["player_id"] = e.player_id.map(ids).astype("string")
    pieces = []
    for metric, (on_col, off_col) in OVERLAY.items():
        cols = [c for c in ["player_id", "player", "first_season", "last_season", "seasons", "teams", "team_list",
                            "tenure_segments", "games_processed", "minutes_on", "minutes_off"] if c in e]
        f = e[cols].copy()
        f["metric"] = metric
        f["on"] = pd.to_numeric(e[on_col], errors="coerce")
        f["off"] = pd.to_numeric(e[off_col], errors="coerce")
        f["swing"] = f["on"] - f["off"]
        f["value_source"] = "exact_locked_integer_rebound_counts"
        pieces.append(f)
    return pd.concat(pieces, ignore_index=True).sort_values(["player_id", "metric"], kind="stable").reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage2-export-dir", type=Path, required=True)
    ap.add_argument("--exact-dir", type=Path, required=True)
    ap.add_argument("--stage2-sha", required=True)
    args = ap.parse_args()
    out = args.exact_dir
    stage2 = args.stage2_export_dir

    quality = json.loads((stage2 / "quality_report.json").read_text())
    if int(quality.get("metric_count", -1)) != EXPECTED_NATIVE_METRICS:
        raise RuntimeError(f"Stage2 native metric gate failed: {quality}")
    if int(quality.get("tenure_segment_metric_rows", -1)) != EXPECTED_STAGE2_SOURCE_ROWS:
        raise RuntimeError(f"Stage2 source-row gate failed: {quality}")

    src = canonicalize(pd.read_parquet(stage2 / "player_team_season_corrected_on_off.parquet"))
    src["metric"] = src.metric.astype(str)
    src["on"] = pd.to_numeric(src["on"], errors="coerce")
    src["off"] = pd.to_numeric(src["off_corrected"], errors="coerce")
    src["swing"] = pd.to_numeric(src["on_minus_off_corrected"], errors="coerce")
    if "player" not in src:
        src["player"] = src.get("subject_player", "")
    targets = load_targets()
    missing = targets.merge(src[KEYS].drop_duplicates(), on=KEYS, how="left", indicator=True)
    missing = missing[missing._merge == "left_only"]
    if len(missing):
        raise RuntimeError(f"Stage2 release export missing {len(missing)} authoritative canonical keys")
    native = src.merge(targets.assign(_canonical=True), on=KEYS, how="inner", validate="many_to_one").drop(columns="_canonical")
    native_metric_names = sorted(native.metric.unique().tolist())
    counts = native.groupby(KEYS).metric.nunique()
    if len(native_metric_names) != EXPECTED_NATIVE_METRICS:
        raise RuntimeError(f"native metric names={len(native_metric_names)} expected={EXPECTED_NATIVE_METRICS}")
    if len(native) != EXPECTED_NATIVE_ROWS or int(counts.min()) != 89 or int(counts.max()) != 89:
        raise RuntimeError(
            f"native row gate rows={len(native)}/{EXPECTED_NATIVE_ROWS} min={int(counts.min())} max={int(counts.max())}"
        )
    if native.duplicated(KEYS + ["metric"]).any():
        raise RuntimeError("duplicate native canonical metric rows")
    native["value_source"] = "stage2_native_tenure_corrected"
    native = native.sort_values(KEYS + ["metric"], kind="stable").reset_index(drop=True)

    exact_segments = pd.read_csv(out / "career_treb_detail.csv", low_memory=False)
    exact = aggregate_exact_segments(exact_segments)
    outside = exact[KEYS].merge(targets, on=KEYS, how="left", indicator=True)
    if int((outside._merge == "left_only").sum()):
        raise RuntimeError("exact rebound overlay contains keys outside canonical universe")
    exact_long = overlay_long(exact)
    if len(exact_long) != EXPECTED_EXACT_PLAYER_TEAM_ROWS * len(OVERLAY):
        raise RuntimeError("exact overlay long row gate failed")

    career = career_native(native)
    if career.player_id.nunique() != EXPECTED_CAREER_PLAYERS or len(career) != EXPECTED_CAREER_NATIVE_ROWS:
        raise RuntimeError(
            f"career native gate players={career.player_id.nunique()}/{EXPECTED_CAREER_PLAYERS} "
            f"rows={len(career)}/{EXPECTED_CAREER_NATIVE_ROWS}"
        )

    native_wide = wide_native(native)
    career_wide = wide_career(career)
    exact_career = pd.read_csv(out / "career_treb_summary.csv", low_memory=False)
    exact_career_long_df = exact_career_long(exact_career)

    exact_wide = exact.copy()
    rename = {
        "treb_on": "TotalReboundPct__on", "treb_off": "TotalReboundPct__off", "treb_swing": "TotalReboundPct__swing",
        "oreb_pct_on": "OffReboundPct__on", "oreb_pct_off": "OffReboundPct__off", "oreb_pct_swing": "OffReboundPct__swing",
        "dreb_pct_on": "DefReboundPct__on", "dreb_pct_off": "DefReboundPct__off", "dreb_pct_swing": "DefReboundPct__swing",
    }
    exact_wide = exact_wide.rename(columns=rename)
    exact_career_wide = exact_career.copy()
    exact_career_wide["player_id"] = exact_career_wide.player_id.map(ids).astype("string")
    exact_career_wide = exact_career_wide.rename(columns=rename)

    combined_wide = native_wide.merge(
        exact_wide[KEYS + list(rename.values())], on=KEYS, how="left", validate="one_to_one"
    )
    combined_career_wide = career_wide.merge(
        exact_career_wide[["player_id"] + list(rename.values())], on="player_id", how="left", validate="one_to_one"
    )

    outputs = {
        "all_metrics_player_team_season": native,
        "all_metrics_career": career,
        "all_metrics_player_team_season_wide": native_wide,
        "all_metrics_career_wide": career_wide,
        "exact_rebound_overlay_player_team_season": exact,
        "exact_rebound_overlay_player_team_season_long": exact_long,
        "exact_rebound_overlay_career": exact_career_wide,
        "exact_rebound_overlay_career_long": exact_career_long_df,
        "final_combined_player_team_season_wide": combined_wide,
        "final_combined_career_wide": combined_career_wide,
    }
    for name, frame in outputs.items():
        frame.to_parquet(out / f"{name}.parquet", index=False, compression="zstd")
        frame.to_csv(out / f"{name}.csv.gz", index=False, compression="gzip")

    dictionary = pd.read_parquet(stage2 / "metric_dictionary.parquet").copy()
    dictionary["metric"] = dictionary.metric.astype(str)
    if len(dictionary) != EXPECTED_NATIVE_METRICS or dictionary.metric.nunique() != EXPECTED_NATIVE_METRICS:
        raise RuntimeError("native metric dictionary gate failed")
    dictionary["metric_family"] = "stage2_native_89"
    dictionary["final_on_off_swing_columns"] = "on | off | swing"
    dictionary["final_value_source"] = "Stage2 tenure-corrected direct metric"
    dictionary.to_parquet(out / "metric_dictionary.parquet", index=False)
    dictionary.to_csv(out / "metric_dictionary.csv", index=False)

    overlay_dictionary = pd.DataFrame([
        {"metric": "TotalReboundPct", "metric_family": "exact_rebound_overlay", "on_source": "treb_on",
         "off_source": "treb_off", "definition": "team total rebound share while player is ON/OFF"},
        {"metric": "OffReboundPct", "metric_family": "exact_rebound_overlay", "on_source": "oreb_pct_on",
         "off_source": "oreb_pct_off", "definition": "team offensive rebound percentage while player is ON/OFF"},
        {"metric": "DefReboundPct", "metric_family": "exact_rebound_overlay", "on_source": "dreb_pct_on",
         "off_source": "dreb_pct_off", "definition": "team defensive rebound percentage while player is ON/OFF"},
    ])
    overlay_dictionary["value_source"] = "stored integer rebound counts; rates recomputed directly; no percentage backsolve"
    overlay_dictionary.to_parquet(out / "exact_rebound_overlay_dictionary.parquet", index=False)
    overlay_dictionary.to_csv(out / "exact_rebound_overlay_dictionary.csv", index=False)

    db_path = out / "TREB_all_metrics.duckdb"
    if db_path.exists():
        db_path.unlink()
    db = duckdb.connect(str(db_path))
    for name in outputs:
        db.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{(out / f'{name}.parquet').as_posix()}')")
    db.execute(f"CREATE TABLE metric_dictionary AS SELECT * FROM read_parquet('{(out/'metric_dictionary.parquet').as_posix()}')")
    db.execute(f"CREATE TABLE exact_rebound_overlay_dictionary AS SELECT * FROM read_parquet('{(out/'exact_rebound_overlay_dictionary.parquet').as_posix()}')")
    db.execute("CREATE INDEX idx_native_pts ON all_metrics_player_team_season(player_id, season, team_id, metric)")
    db.execute("CREATE INDEX idx_native_career ON all_metrics_career(player_id, metric)")
    db.execute("CREATE INDEX idx_exact_pts ON exact_rebound_overlay_player_team_season(player_id, season, team_id)")
    db.close()

    overlap = sorted(set(native_metric_names).intersection(OVERLAY))
    qa = {
        "status": "PASS",
        "release_contract": "preserve 89 native Stage2 metrics; exact TREB/OREB/DREB is a separate overlay family",
        "stage2_source_sha": args.stage2_sha,
        "stage2_windows": EXPECTED_STAGE2_WINDOWS,
        "stage2_source_metric_rows": EXPECTED_STAGE2_SOURCE_ROWS,
        "native_metric_count": EXPECTED_NATIVE_METRICS,
        "native_metric_names": native_metric_names,
        "canonical_player_team_season_rows": EXPECTED_CANONICAL_ROWS,
        "native_player_team_metric_rows": len(native),
        "career_players": int(career.player_id.nunique()),
        "career_native_metric_rows": len(career),
        "exact_count_segment_rows": len(exact_segments),
        "exact_count_player_team_rows": len(exact),
        "exact_overlay_metric_count": len(OVERLAY),
        "exact_overlay_metrics": sorted(OVERLAY),
        "exact_overlay_player_team_metric_rows": len(exact_long),
        "exact_overlay_career_players": int(exact_career_long_df.player_id.nunique()),
        "exact_overlay_career_metric_rows": len(exact_career_long_df),
        "native_stage2_name_overlap_with_exact_overlay": overlap,
        "native_gate_requires_exact_overlay_names": False,
        "all_89_native_metrics_present_for_every_canonical_key": True,
        "career_89_native_metrics_present_for_every_player": True,
        "rounded_percentage_backsolve_used": False,
        "exact_rates_recomputed_from_stored_integer_counts": True,
    }
    (out / "ALL_89_METRICS_QA.json").write_text(json.dumps(qa, indent=2) + "\n")
    (out / "README_ALL_89_METRICS.txt").write_text(
        "FINAL TREB DATABASE RELEASE CONTRACT\n\n"
        "The 89-metric Stage2 dataset is preserved exactly as the native metric family.\n"
        "TotalReboundPct, OffReboundPct and DefReboundPct are a separate exact-count overlay family and are not required to exist as Stage2-native metric names.\n"
        "Exact overlay values are recomputed from stored integer team/opponent rebound counts; rounded percentage backsolving is forbidden and not used.\n"
        "Use final_combined_*_wide for a convenience join of the native 89-metric family and exact rebound overlay where exact-count coverage exists.\n",
        encoding="utf-8",
    )
    print(json.dumps(qa, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
