from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
CORE = BASE / "impact_database" / "outputs"
ROSTER = BASE / "impact_database" / "roster_tenure"
SRC = BASE / "impact_database" / "corrected_off"
OUT = SRC / "final_export"
SEGMENTS = SRC / "tenure_segment_on_off.jsonl.gz"
COLLECTION = SRC / "corrected_off_collection_summary.json"
REVIEW = ROSTER / "tenure_review_queue_summary.json"
CORE_MANIFEST = CORE / "manifest.json"
MINUTES_THRESHOLD = 10_000.0


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"expected object: {path}")
    return data


def jsonl_frame(path: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return pd.DataFrame(rows)


def core_long() -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for season_dir in sorted(p for p in CORE.iterdir() if p.is_dir() and len(p.name) == 7 and p.name[4] == "-"):
        path = season_dir / "team_on_off_metrics.csv.gz"
        if not path.exists():
            raise RuntimeError(f"missing core long file: {path}")
        f = pd.read_csv(path, compression="gzip", low_memory=False)
        f["season"] = season_dir.name
        parts.append(f)
    result = pd.concat(parts, ignore_index=True, sort=False)
    result["player_id"] = result["subject_player_id"].astype("string").str.replace(r"\.0$", "", regex=True)
    result["team_id"] = pd.to_numeric(result["team_id"], errors="raise").astype("int64")
    result["metric"] = result["metric"].astype("string")
    result["core_on"] = pd.to_numeric(result["on"], errors="coerce")
    result["core_off_uncorrected"] = pd.to_numeric(result["off"], errors="coerce")
    result["core_swing_uncorrected"] = pd.to_numeric(result["on_off"], errors="coerce")
    return result[["season", "team_id", "player_id", "metric", "subject_player", "core_on", "core_off_uncorrected", "core_swing_uncorrected"]]


def weighted(group: pd.DataFrame, value: str, weight: str) -> float:
    v = pd.to_numeric(group[value], errors="coerce")
    w = pd.to_numeric(group[weight], errors="coerce")
    mask = v.notna() & w.notna() & (w > 0)
    if not mask.any():
        return math.nan
    return float(np.average(v[mask], weights=w[mask]))


def aggregate_player_team_metric(segments: pd.DataFrame) -> pd.DataFrame:
    keys = ["season", "team_id", "player_id", "player", "metric"]
    rows: list[dict[str, Any]] = []
    for key, g in segments.groupby(keys, dropna=False, sort=True):
        season, team_id, player_id, player, metric = key
        on = weighted(g, "on", "minutes_on")
        off = weighted(g, "off_corrected", "minutes_off")
        rows.append({
            "season": season,
            "team_id": int(team_id),
            "player_id": str(player_id),
            "player": str(player),
            "metric": str(metric),
            "segment_count": int(len(g)),
            "minutes_on": float(pd.to_numeric(g.minutes_on, errors="coerce").fillna(0).sum()),
            "minutes_off": float(pd.to_numeric(g.minutes_off, errors="coerce").fillna(0).sum()),
            "on": on,
            "off_corrected": off,
            "on_minus_off_corrected": on - off if np.isfinite(on) and np.isfinite(off) else math.nan,
            "aggregation_method": "exact_tenure_segment" if len(g) == 1 else "minutes_weighted_across_exact_tenure_segments",
            "aggregation_confidence": "high" if len(g) == 1 else "moderate",
            "tenure_start": str(g.query_start_date.min()),
            "tenure_end": str(g.query_end_date.max()),
        })
    return pd.DataFrame(rows)


def career_summary(team_metric: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    keys = ["player_id", "player", "metric"]
    for (player_id, player, metric), g in team_metric.groupby(keys, sort=True):
        on = weighted(g, "on", "minutes_on")
        off = weighted(g, "off_corrected", "minutes_off")
        rows.append({
            "player_id": str(player_id), "player": str(player), "metric": str(metric),
            "player_team_seasons": int(len(g)), "season_count": int(g.season.nunique()), "team_count": int(g.team_id.nunique()),
            "minutes_on": float(g.minutes_on.sum()), "minutes_off": float(g.minutes_off.sum()),
            "on": on, "off_corrected": off,
            "on_minus_off_corrected": on - off if np.isfinite(on) and np.isfinite(off) else math.nan,
            "aggregation_method": "minutes_weighted_across_player_team_seasons",
        })
    career = pd.DataFrame(rows)
    career["qualifies_10000_minutes"] = career.minutes_on >= MINUTES_THRESHOLD
    career["rank_high_to_low_10000"] = np.nan
    career["rank_low_to_high_10000"] = np.nan
    career["percentile_10000"] = np.nan
    for metric, idx in career[career.qualifies_10000_minutes & career.on_minus_off_corrected.notna()].groupby("metric").groups.items():
        values = career.loc[idx, "on_minus_off_corrected"]
        career.loc[idx, "rank_high_to_low_10000"] = values.rank(method="min", ascending=False)
        career.loc[idx, "rank_low_to_high_10000"] = values.rank(method="min", ascending=True)
        career.loc[idx, "percentile_10000"] = values.rank(method="average", pct=True) * 100.0
    return career


def metric_dictionary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for metric, g in frame.groupby("metric", sort=True):
        rows.append({
            "metric": metric,
            "rows": int(len(g)), "players": int(g.player_id.nunique()), "seasons": int(g.season.nunique()), "teams": int(g.team_id.nunique()),
            "on_non_null": int(g.on.notna().sum()), "off_corrected_non_null": int(g.off_corrected.notna().sum()),
            "source": "PBP Stats get-on-off/nba/team queried only over validated roster-tenure intervals",
            "off_definition": "team metric while player is OFF court, restricted to games/dates in which Stage 1 proves the player was rostered to that team",
            "swing_definition": "ON minus tenure-corrected OFF",
            "multi_segment_note": "multiple same-team roster stints are preserved exactly at segment level; player-team and career summaries use displayed-minutes weighting",
        })
    return pd.DataFrame(rows)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build() -> dict[str, Any]:
    review = load_json(REVIEW)
    collection = load_json(COLLECTION)
    core_manifest = load_json(CORE_MANIFEST)
    if review.get("stage1_exact_ready") is not True or int(review.get("review_queue_windows") or 0) != 0:
        raise RuntimeError("final package refused: Stage 1 exact-ready gate is not clean")
    if int(core_manifest.get("core_complete") or 0) != 780:
        raise RuntimeError("final package refused: original core is not 780/780")
    if int(collection.get("failed_windows") or 0) != 0 or int(collection.get("complete_windows") or 0) != int(collection.get("impact_windows_requested") or -1):
        raise RuntimeError(f"final package refused: corrected OFF collection incomplete: {collection}")

    segments = jsonl_frame(SEGMENTS)
    if segments.empty:
        raise RuntimeError("no tenure segment metric rows")
    for col in ("team_id", "minutes_on", "minutes_off", "on", "off_corrected", "on_minus_off_corrected"):
        segments[col] = pd.to_numeric(segments[col], errors="coerce")
    segments["player_id"] = segments.player_id.astype("string")
    duplicate_segments = int(segments.duplicated(["season", "team_id", "player_id", "query_start_date", "query_end_date", "metric"], keep=False).sum())
    if duplicate_segments:
        raise RuntimeError(f"duplicate corrected segment keys: {duplicate_segments}")

    team_metric = aggregate_player_team_metric(segments)
    duplicate_team = int(team_metric.duplicated(["season", "team_id", "player_id", "metric"], keep=False).sum())
    if duplicate_team:
        raise RuntimeError(f"duplicate corrected player-team metric keys: {duplicate_team}")

    original = core_long()
    merged = team_metric.merge(original, on=["season", "team_id", "player_id", "metric"], how="left", validate="one_to_one")
    merged["legacy_core_match"] = merged.core_on.notna()
    missing_core = int((~merged.legacy_core_match).sum())
    single = merged[(merged.segment_count == 1) & merged.on.notna() & merged.core_on.notna()].copy()
    single["on_validation_abs_delta"] = (single.on - single.core_on).abs()
    max_single_delta = float(single.on_validation_abs_delta.max()) if len(single) else math.nan
    mismatches = single[single.on_validation_abs_delta > 1e-6]
    if len(mismatches):
        raise RuntimeError(f"tenure query ON does not reproduce original core ON for {len(mismatches)} single-stint rows; max delta={max_single_delta}")

    merged["off_correction_delta"] = merged.off_corrected - merged.core_off_uncorrected
    merged["swing_correction_delta"] = merged.on_minus_off_corrected - merged.core_swing_uncorrected
    career = career_summary(merged)
    dictionary = metric_dictionary(merged)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    tables = {
        "tenure_segment_on_off": segments,
        "player_team_season_corrected_on_off": merged,
        "career_corrected_on_off": career,
        "metric_dictionary": dictionary,
    }
    for name, frame in tables.items():
        frame.to_parquet(OUT / f"{name}.parquet", index=False, compression="zstd")
        frame.to_csv(OUT / f"{name}.csv.gz", index=False, compression="gzip")

    db_path = OUT / "TREB_corrected_off.duckdb"
    db = duckdb.connect(str(db_path))
    for name in tables:
        db.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{(OUT / f'{name}.parquet').as_posix()}')")
    db.execute("CREATE INDEX idx_pts_player ON player_team_season_corrected_on_off(player_id, season, team_id)")
    db.execute("CREATE INDEX idx_career_metric ON career_corrected_on_off(metric, player_id)")
    db.execute("CREATE VIEW career_10000 AS SELECT * FROM career_corrected_on_off WHERE qualifies_10000_minutes")
    db.close()

    quality = {
        "stage1_exact_ready": True,
        "original_core_team_seasons": 780,
        "teammate_pair_layer_included": False,
        "tenure_segment_metric_rows": int(len(segments)),
        "player_team_season_metric_rows": int(len(merged)),
        "career_metric_rows": int(len(career)),
        "metric_count": int(merged.metric.nunique()),
        "unique_players": int(merged.player_id.nunique()),
        "duplicate_segment_keys": duplicate_segments,
        "duplicate_player_team_metric_keys": duplicate_team,
        "missing_original_core_matches": missing_core,
        "single_stint_on_validation_rows": int(len(single)),
        "single_stint_on_validation_max_abs_delta": max_single_delta,
        "single_stint_on_validation_failures": int(len(mismatches)),
        "qualification_minutes": MINUTES_THRESHOLD,
    }
    (OUT / "quality_report.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(
        "# TREB tenure-corrected OFF database\n\n"
        "Regular seasons 2000-01 through 2025-26. OFF values are re-queried only over Stage 1 validated roster-tenure intervals; ON-minus-OFF uses those corrected OFF values. The completed original 780 team-season core is not rerun and teammate-pair analysis is excluded.\n\n"
        "Exact tenure segments are retained in `tenure_segment_on_off`. For the uncommon case of multiple same-team roster stints inside one season, summary tables combine segment values using the endpoint's displayed ON/OFF minutes and flag that aggregation method explicitly.\n\n"
        "Career ranking columns are supplied for players with at least 10,000 ON-court minutes. Use the long tables for other thresholds.\n",
        encoding="utf-8",
    )
    provenance = {
        "stage1_review_summary": review,
        "corrected_off_collection_summary": {k: v for k, v in collection.items() if k != "failures"},
        "core_manifest": {"core_complete": core_manifest.get("core_complete"), "expected_team_seasons": core_manifest.get("expected_team_seasons")},
        "method": "PBP Stats tenure-scoped get-on-off/nba/team profiles with Stage 1 transaction/schedule roster windows",
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    files = sorted(p for p in OUT.iterdir() if p.is_file())
    manifest = {
        "package": "TREB tenure-corrected OFF historical player-impact database",
        "generated_utc": pd.Timestamp.utcnow().isoformat(),
        "quality": quality,
        "files": [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    zip_base = SRC / "TREB_corrected_off_final"
    archive = shutil.make_archive(str(zip_base), "zip", root_dir=OUT)
    archive_path = Path(archive)
    manifest["zip"] = {"name": archive_path.name, "bytes": archive_path.stat().st_size, "sha256": sha256(archive_path)}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def self_test() -> None:
    g = pd.DataFrame({"on": [10.0, 20.0], "minutes_on": [1.0, 3.0], "off_corrected": [8.0, 14.0], "minutes_off": [2.0, 2.0]})
    assert weighted(g, "on", "minutes_on") == 17.5
    assert weighted(g, "off_corrected", "minutes_off") == 11.0
    print("finalize_corrected_off_package self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(build(), indent=2))


if __name__ == "__main__":
    main()
