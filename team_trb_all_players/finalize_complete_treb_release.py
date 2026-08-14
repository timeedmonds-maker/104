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

KEYS = ["season", "team_id", "player_id"]
EXPECTED_CANONICAL = 14524
EXPECTED_PARTIAL = 4877
EXPECTED_FULL = 9647
EXPECTED_NATIVE_METRICS = 89
EXPECTED_NATIVE_ROWS = EXPECTED_CANONICAL * EXPECTED_NATIVE_METRICS
EXPECTED_CAREER_PLAYERS = 3122


def ids(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def canon(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["season"] = x["season"].astype(str)
    x["team_id"] = pd.to_numeric(x["team_id"], errors="raise").astype("int64")
    x["player_id"] = x["player_id"].map(ids).astype("string")
    return x


def read_targets(path: Path) -> pd.DataFrame:
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    t = canon(pd.DataFrame(rows))
    if len(t) != EXPECTED_CANONICAL or t[KEYS].duplicated().any():
        raise RuntimeError(f"canonical target gate failed rows={len(t)} dup={int(t[KEYS].duplicated().sum())}")
    return t


def weighted(g: pd.DataFrame, value: str, weight: str) -> float:
    v = pd.to_numeric(g[value], errors="coerce")
    w = pd.to_numeric(g[weight], errors="coerce")
    m = v.notna() & w.notna() & (w > 0)
    if not m.any():
        return math.nan
    return float(np.average(v[m], weights=w[m]))


def career_long(detail: pd.DataFrame, source: str) -> pd.DataFrame:
    rows = []
    for (player_id, metric), g in detail.groupby(["player_id", "metric"], sort=True, dropna=False):
        on = weighted(g, "on", "minutes_on")
        off = weighted(g, "off", "minutes_off")
        names = [str(v) for v in g.get("player", pd.Series(dtype=str)).dropna() if str(v).strip()]
        rows.append({
            "player_id": str(player_id), "player": names[0] if names else "", "metric": str(metric),
            "player_team_seasons": int(len(g)), "season_count": int(g.season.nunique()),
            "team_count": int(g.team_id.nunique()),
            "minutes_on": float(pd.to_numeric(g.minutes_on, errors="coerce").fillna(0).sum()),
            "minutes_off": float(pd.to_numeric(g.minutes_off, errors="coerce").fillna(0).sum()),
            "on": on, "off": off,
            "swing": on - off if np.isfinite(on) and np.isfinite(off) else math.nan,
            "value_source": source,
        })
    out = pd.DataFrame(rows)
    out["qualifies_10000_minutes"] = pd.to_numeric(out.minutes_on, errors="coerce") >= 10000
    return out.sort_values(["player_id", "metric"], kind="stable").reset_index(drop=True)


def build_wide(detail: pd.DataFrame, key_cols: list[str], meta_cols: list[str]) -> pd.DataFrame:
    p = detail.pivot(index=key_cols, columns="metric", values=["on", "off", "swing"])
    p.columns = [f"{metric}__{field}" for field, metric in p.columns]
    p = p.reset_index()
    present = [c for c in meta_cols if c in detail.columns and c not in key_cols]
    if present:
        meta = detail.groupby(key_cols, as_index=False, sort=True)[present].first()
        p = meta.merge(p, on=key_cols, how="left", validate="one_to_one")
    return p


def normalize_pct(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    x = x.where(x <= 1.5, x / 100.0)
    return x


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--native", type=Path, required=True)
    ap.add_argument("--exact", type=Path, required=True)
    ap.add_argument("--direct-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stage2-sha", required=True)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    targets = read_targets(args.targets)
    target_keys = targets[KEYS].drop_duplicates()
    full_targets = targets[targets.full_core_reuse.astype(bool)].copy()
    partial_targets = targets[~targets.full_core_reuse.astype(bool)].copy()
    if len(full_targets) != EXPECTED_FULL or len(partial_targets) != EXPECTED_PARTIAL:
        raise RuntimeError(f"V2 split gate full={len(full_targets)} partial={len(partial_targets)}")

    native = canon(pd.read_parquet(args.native))
    native["metric"] = native.metric.astype(str)
    if "off" not in native.columns:
        native["off"] = pd.to_numeric(native["off_corrected"], errors="coerce")
    if "swing" not in native.columns:
        native["swing"] = pd.to_numeric(native["on_minus_off_corrected"], errors="coerce")
    native["on"] = pd.to_numeric(native["on"], errors="coerce")
    native["off"] = pd.to_numeric(native["off"], errors="coerce")
    native["swing"] = pd.to_numeric(native["swing"], errors="coerce")
    if "player" not in native.columns:
        native["player"] = native.get("subject_player", "")
    names = sorted(native.metric.unique().tolist())
    km = native.groupby(KEYS).metric.nunique()
    if len(names) != 89 or len(native) != EXPECTED_NATIVE_ROWS or len(km) != EXPECTED_CANONICAL or km.min() != 89 or km.max() != 89:
        raise RuntimeError(f"native 89 gate rows={len(native)} metrics={len(names)} keys={len(km)} min={km.min()} max={km.max()}")
    if native.duplicated(KEYS + ["metric"]).any():
        raise RuntimeError("duplicate native metric keys")
    if "TotalReboundPct" in names:
        raise RuntimeError("architecture regression: TotalReboundPct must not be fabricated inside native Stage2 89")
    for m in ("OReb%", "DReb%"):
        if m not in names:
            raise RuntimeError(f"required native rebound metric absent: {m}")

    exact = canon(pd.read_parquet(args.exact))
    if len(exact) != EXPECTED_PARTIAL or exact[KEYS].duplicated().any():
        raise RuntimeError(f"exact partial gate rows={len(exact)} dup={int(exact[KEYS].duplicated().sum())}")
    chk = partial_targets[KEYS].merge(exact[KEYS], on=KEYS, how="outer", indicator=True)
    if len(chk) != EXPECTED_PARTIAL or not chk._merge.eq("both").all():
        raise RuntimeError("exact 4,877 keys do not equal V2 partial universe")

    direct_parts = []
    for p in sorted(args.direct_dir.glob("direct_treb_*.csv")):
        direct_parts.append(pd.read_csv(p, low_memory=False))
    if not direct_parts:
        raise RuntimeError("no direct full-core TREB runner outputs")
    direct = canon(pd.concat(direct_parts, ignore_index=True, sort=False))
    if len(direct) != EXPECTED_FULL or direct[KEYS].duplicated().any():
        raise RuntimeError(f"direct collector row gate rows={len(direct)} dup={int(direct[KEYS].duplicated().sum())}")
    failures = direct[direct.status.astype(str) != "PASS"].copy()
    if len(failures):
        failures.to_csv(out / "DIRECT_TREB_FAILURES.csv", index=False)
        raise RuntimeError(f"direct full-core source has {len(failures)} failed canonical rows; inventory written")
    chk = full_targets[KEYS].merge(direct[KEYS], on=KEYS, how="outer", indicator=True)
    if len(chk) != EXPECTED_FULL or not chk._merge.eq("both").all():
        raise RuntimeError("direct 9,647 keys do not equal V2 full-core universe")
    direct["direct_treb_on"] = normalize_pct(direct.direct_treb_on)
    direct["direct_treb_off"] = normalize_pct(direct.direct_treb_off)
    if direct[["direct_treb_on", "direct_treb_off"]].isna().any().any():
        raise RuntimeError("null direct TotalReboundPct values")
    if not ((direct.direct_treb_on.between(0.25, 0.75)) & (direct.direct_treb_off.between(0.25, 0.75))).all():
        raise RuntimeError("implausible direct TotalReboundPct values")

    key_meta = native.groupby(KEYS, as_index=False, sort=True).agg(
        player=("player", "first"), minutes_on=("minutes_on", "first"), minutes_off=("minutes_off", "first")
    )
    direct = direct.merge(key_meta, on=KEYS, how="left", validate="one_to_one", suffixes=("", "_native"))
    minute_delta = (pd.to_numeric(direct.direct_minutes_on, errors="coerce") - pd.to_numeric(direct.minutes_on, errors="coerce")).abs()
    max_minute_delta = float(minute_delta.dropna().max()) if minute_delta.notna().any() else math.nan
    rows_delta_gt_one = int((minute_delta > 1.0).sum())
    if rows_delta_gt_one:
        bad = direct.loc[minute_delta > 1.0, KEYS + ["direct_minutes_on", "minutes_on"]].copy()
        bad["abs_delta"] = minute_delta[minute_delta > 1.0]
        bad.to_csv(out / "DIRECT_TREB_MINUTE_MISMATCH.csv", index=False)
        raise RuntimeError(f"direct full-core minute identity failed for {rows_delta_gt_one} rows; max={max_minute_delta}")

    # Authoritative canonical 3-metric rebound overlay.
    pieces = []
    for metric, on_col, off_col in [
        ("TotalReboundPct", "treb_on", "treb_off"),
        ("OffReboundPct", "oreb_pct_on", "oreb_pct_off"),
        ("DefReboundPct", "dreb_pct_on", "dreb_pct_off"),
    ]:
        f = exact[KEYS + [c for c in ["player", "team_abbr", "minutes_on", "minutes_off"] if c in exact.columns]].copy()
        f["metric"] = metric
        f["on"] = pd.to_numeric(exact[on_col], errors="coerce").to_numpy()
        f["off"] = pd.to_numeric(exact[off_col], errors="coerce").to_numpy()
        f["swing"] = f["on"] - f["off"]
        f["value_source"] = "exact_locked_integer_rebound_counts"
        f["exact_count_overlay"] = True
        pieces.append(f)

    total_full = direct[KEYS + ["player_native", "minutes_on", "minutes_off", "direct_treb_on", "direct_treb_off", "source"]].copy()
    total_full = total_full.rename(columns={"player_native": "player", "direct_treb_on": "on", "direct_treb_off": "off"})
    total_full["metric"] = "TotalReboundPct"
    total_full["swing"] = total_full["on"] - total_full["off"]
    total_full["value_source"] = "direct_nba_stats_teamplayeronoffdetails_REB_PCT"
    total_full["exact_count_overlay"] = False
    pieces.append(total_full.drop(columns=["source"]))

    for native_metric, canonical_metric in [("OReb%", "OffReboundPct"), ("DReb%", "DefReboundPct")]:
        f = native[native.metric.eq(native_metric)].merge(full_targets[KEYS], on=KEYS, how="inner", validate="one_to_one")
        f = f[KEYS + [c for c in ["player", "team_abbr", "minutes_on", "minutes_off", "on", "off", "swing"] if c in f.columns]].copy()
        f["metric"] = canonical_metric
        f["on"] = normalize_pct(f["on"])
        f["off"] = normalize_pct(f["off"])
        f["swing"] = f["on"] - f["off"]
        f["value_source"] = f"stage2_native_{native_metric}_direct"
        f["exact_count_overlay"] = False
        pieces.append(f)

    overlay = pd.concat(pieces, ignore_index=True, sort=False)
    overlay = canon(overlay).sort_values(KEYS + ["metric"], kind="stable").reset_index(drop=True)
    if len(overlay) != EXPECTED_CANONICAL * 3 or overlay.duplicated(KEYS + ["metric"]).any():
        raise RuntimeError(f"canonical rebound overlay gate rows={len(overlay)} dup={int(overlay.duplicated(KEYS + ['metric']).sum())}")
    counts = overlay.groupby(KEYS).metric.nunique()
    if len(counts) != EXPECTED_CANONICAL or counts.min() != 3 or counts.max() != 3:
        raise RuntimeError("not every canonical key has all 3 rebound metrics")
    for metric in ["TotalReboundPct", "OffReboundPct", "DefReboundPct"]:
        g = overlay[overlay.metric.eq(metric)]
        if len(g) != EXPECTED_CANONICAL:
            raise RuntimeError(f"{metric} coverage={len(g)}")
        ex = int(g.exact_count_overlay.astype(bool).sum())
        if ex != EXPECTED_PARTIAL:
            raise RuntimeError(f"{metric} exact count={ex}")

    native_career = career_long(native, "stage2_native_tenure_corrected")
    if native_career.player_id.nunique() != EXPECTED_CAREER_PLAYERS or len(native_career) != EXPECTED_CAREER_PLAYERS * 89:
        raise RuntimeError(f"native career gate players={native_career.player_id.nunique()} rows={len(native_career)}")
    overlay_career = career_long(overlay, "canonical_rebound_overlay_mixed_exact_and_direct")
    if overlay_career.player_id.nunique() != EXPECTED_CAREER_PLAYERS or len(overlay_career) != EXPECTED_CAREER_PLAYERS * 3:
        raise RuntimeError(f"rebound career gate players={overlay_career.player_id.nunique()} rows={len(overlay_career)}")

    ad = overlay_career[(overlay_career.player_id.astype(str) == "203500") & overlay_career.metric.eq("TotalReboundPct")]
    if len(ad) != 1:
        raise RuntimeError(f"Steven Adams TotalReboundPct career rows={len(ad)}")
    ad_minutes = float(ad.minutes_on.iloc[0])
    if not (20460.0 <= ad_minutes <= 20485.0):
        raise RuntimeError(f"Steven Adams career minutes regression={ad_minutes}")
    q10 = overlay_career[(overlay_career.metric.eq("TotalReboundPct")) & (pd.to_numeric(overlay_career.minutes_on, errors="coerce") >= 10000)]
    qualifying = int(q10.player_id.astype(str).nunique())
    if not (540 <= qualifying <= 565):
        raise RuntimeError(f">=10000-minute TREB population regression={qualifying}")

    native.to_parquet(out / "all_metrics_player_team_season.parquet", index=False, compression="zstd")
    native.to_csv(out / "all_metrics_player_team_season.csv.gz", index=False, compression="gzip")
    native_career.to_parquet(out / "all_metrics_career.parquet", index=False, compression="zstd")
    native_career.to_csv(out / "all_metrics_career.csv.gz", index=False, compression="gzip")
    overlay.to_parquet(out / "treb_overlay_player_team_season_long.parquet", index=False, compression="zstd")
    overlay.to_csv(out / "treb_overlay_player_team_season_long.csv.gz", index=False, compression="gzip")
    overlay_career.to_parquet(out / "treb_overlay_career_long.parquet", index=False, compression="zstd")
    overlay_career.to_csv(out / "treb_overlay_career_long.csv.gz", index=False, compression="gzip")

    native_wide = build_wide(native, KEYS, ["player", "team_abbr", "minutes_on", "minutes_off"])
    overlay_wide = build_wide(overlay, KEYS, ["player", "team_abbr", "minutes_on", "minutes_off"])
    overlay_cols = KEYS + [c for c in overlay_wide.columns if c not in KEYS + ["player", "team_abbr", "minutes_on", "minutes_off"]]
    combined_wide = native_wide.merge(overlay_wide[overlay_cols], on=KEYS, how="left", validate="one_to_one")
    native_career_wide = build_wide(native_career.rename(columns={"player_team_seasons": "pts_count"}), ["player_id"], ["player", "minutes_on", "minutes_off"])
    overlay_career_wide = build_wide(overlay_career.rename(columns={"player_team_seasons": "pts_count"}), ["player_id"], ["player", "minutes_on", "minutes_off"])
    ocols = ["player_id"] + [c for c in overlay_career_wide.columns if c not in ["player_id", "player", "minutes_on", "minutes_off"]]
    combined_career_wide = native_career_wide.merge(overlay_career_wide[ocols], on="player_id", how="left", validate="one_to_one")
    combined_wide.to_parquet(out / "final_combined_player_team_season_wide.parquet", index=False, compression="zstd")
    combined_wide.to_csv(out / "final_combined_player_team_season_wide.csv.gz", index=False, compression="gzip")
    combined_career_wide.to_parquet(out / "final_combined_career_wide.parquet", index=False, compression="zstd")
    combined_career_wide.to_csv(out / "final_combined_career_wide.csv.gz", index=False, compression="gzip")

    dictionary = pd.DataFrame({"metric": names})
    dictionary["family"] = "native_stage2_89"
    dictionary.to_parquet(out / "metric_dictionary.parquet", index=False)
    dictionary.to_csv(out / "metric_dictionary.csv", index=False)
    overlay_dictionary = pd.DataFrame([
        {"metric": "TotalReboundPct", "partial_source": "exact locked integer counts", "full_core_source": "direct NBA Stats teamplayeronoffdetails Advanced REB_PCT ON/OFF", "backsolve": False},
        {"metric": "OffReboundPct", "partial_source": "exact locked integer counts", "full_core_source": "native Stage2 OReb% direct", "backsolve": False},
        {"metric": "DefReboundPct", "partial_source": "exact locked integer counts", "full_core_source": "native Stage2 DReb% direct", "backsolve": False},
    ])
    overlay_dictionary.to_parquet(out / "treb_overlay_dictionary.parquet", index=False)
    overlay_dictionary.to_csv(out / "treb_overlay_dictionary.csv", index=False)

    db_path = out / "TREB_all_metrics.duckdb"
    if db_path.exists(): db_path.unlink()
    db = duckdb.connect(str(db_path))
    for name, path in {
        "all_metrics_player_team_season": out / "all_metrics_player_team_season.parquet",
        "all_metrics_career": out / "all_metrics_career.parquet",
        "treb_overlay_player_team_season": out / "treb_overlay_player_team_season_long.parquet",
        "treb_overlay_career": out / "treb_overlay_career_long.parquet",
        "final_combined_player_team_season_wide": out / "final_combined_player_team_season_wide.parquet",
        "final_combined_career_wide": out / "final_combined_career_wide.parquet",
        "metric_dictionary": out / "metric_dictionary.parquet",
        "treb_overlay_dictionary": out / "treb_overlay_dictionary.parquet",
    }.items():
        db.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    db.execute("CREATE INDEX idx_native_pts ON all_metrics_player_team_season(player_id, season, team_id)")
    db.execute("CREATE INDEX idx_treb_pts ON treb_overlay_player_team_season(player_id, season, team_id)")
    db.close()

    qa = {
        "status": "PASS",
        "stage2_sha": args.stage2_sha,
        "canonical_player_team_season_rows": EXPECTED_CANONICAL,
        "seasons": int(overlay.season.nunique()),
        "native_stage2_metric_count": 89,
        "native_stage2_metric_rows": int(len(native)),
        "native_stage2_names_preserved": True,
        "total_rebound_pct_is_separate_from_native_89": True,
        "canonical_rebound_overlay_rows": int(len(overlay)),
        "canonical_rebound_metrics_per_key": 3,
        "exact_partial_keys": EXPECTED_PARTIAL,
        "full_core_complement_keys": EXPECTED_FULL,
        "treb_union_keys": int(overlay[KEYS].drop_duplicates().shape[0]),
        "total_rebound_pct_full_core_source": "direct NBA Stats teamplayeronoffdetails Advanced REB_PCT ON/OFF",
        "off_rebound_pct_full_core_source": "native Stage2 OReb%",
        "def_rebound_pct_full_core_source": "native Stage2 DReb%",
        "rounded_percentage_backsolve_used": False,
        "opponent_rebound_inference_used_for_full_core_total": False,
        "direct_full_core_max_on_minutes_abs_delta_vs_stage2": max_minute_delta,
        "direct_full_core_rows_gt_1_minute_delta": rows_delta_gt_one,
        "steven_adams_player_id": "203500",
        "steven_adams_minutes_on": ad_minutes,
        "steven_adams_regression": "PASS",
        "treb_10000_minute_qualifying_players": qualifying,
    }
    (out / "ALL_89_METRICS_QA.json").write_text(json.dumps(qa, indent=2) + "\n")
    (out / "MATERIAL_RELEASE_GATE.json").write_text(json.dumps({
        "status": "PASS", "canonical_treb_coverage": "14524/14524", "missing_canonical_keys": 0,
        "duplicate_canonical_metric_keys": 0, "seasons": 26, "exact_partial": 4877, "full_core": 9647,
        "native_89_preserved": True, "rounded_percentage_backsolve_used": False,
        "steven_adams_regression": "PASS", "treb_10000_population": qualifying,
    }, indent=2) + "\n")
    (out / "README_FINAL_DATABASE.txt").write_text(
        "TREB FINAL DATABASE 2000-01 TO 2025-26\n\n"
        "The native Stage2 layer remains exactly 89 metrics. TotalReboundPct is intentionally a separate canonical rebound overlay, not a fabricated 89th-name alias.\n"
        "Canonical rebound overlay coverage is 14,524/14,524 player-team-season keys. The 4,877 partial-tenure keys use exact locked integer rebound counts. The 9,647 full-core TotalReboundPct keys use direct NBA Stats teamplayeronoffdetails Advanced REB_PCT ON/OFF values. Full-core OReb%/DReb% use native Stage2 direct values.\n"
        "No rounded OREB%/DREB% values are inverted or backsolved to manufacture TotalReboundPct.\n",
        encoding="utf-8",
    )
    print(json.dumps(qa, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
