from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd

import finalize_corrected_off_package as legacy

BASE = Path(__file__).resolve().parent
SRC = BASE / "impact_database" / "corrected_off"
OUT = SRC / "final_export"
COLLECTION = SRC / "corrected_off_collection_summary.json"
MINUTES_THRESHOLD = 10_000.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def patch_legacy_completion_alias() -> None:
    data = json.loads(COLLECTION.read_text(encoding="utf-8"))
    total = int(data.get("impact_windows_total") or 0)
    complete = int(data.get("complete_windows") or 0)
    remaining = int(data.get("remaining_windows") or 0)
    failed = int(data.get("failed_windows") or 0)
    if not total or complete != total or remaining != 0 or failed != 0 or data.get("all_complete") is not True:
        raise RuntimeError(f"extended finalizer refused incomplete Stage2: complete={complete} total={total} remaining={remaining} failed={failed}")
    if int(data.get("impact_windows_requested") or 0) != total:
        data["impact_windows_requested"] = total
        COLLECTION.write_text(json.dumps(data, indent=2), encoding="utf-8")


def weighted(group: pd.DataFrame, value: str, weight: str) -> float:
    v = pd.to_numeric(group[value], errors="coerce")
    w = pd.to_numeric(group[weight], errors="coerce")
    mask = v.notna() & w.notna() & (w > 0)
    if not mask.any():
        return math.nan
    return float(np.average(v[mask], weights=w[mask]))


def player_season_generic(team_metric: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (season, player_id, player, metric), g in team_metric.groupby(["season", "player_id", "player", "metric"], dropna=False, sort=True):
        on = weighted(g, "on", "minutes_on")
        off = weighted(g, "off_corrected", "minutes_off")
        rows.append({
            "season": season,
            "player_id": str(player_id),
            "player": str(player),
            "metric": str(metric),
            "team_count": int(g.team_id.nunique()),
            "minutes_on": float(pd.to_numeric(g.minutes_on, errors="coerce").fillna(0).sum()),
            "minutes_off": float(pd.to_numeric(g.minutes_off, errors="coerce").fillna(0).sum()),
            "on": on,
            "off_corrected": off,
            "on_minus_off_corrected": on - off if np.isfinite(on) and np.isfinite(off) else math.nan,
            "aggregation_method": "minutes_weighted_across_player_team_seasons",
        })
    return pd.DataFrame(rows)


def num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def integer(value: Any) -> int | None:
    out = num(value)
    if out is None or abs(out - round(out)) > 1e-6:
        return None
    return int(round(out))


def pct_fraction(value: Any) -> float | None:
    out = num(value)
    if out is None:
        return None
    if 1 < out <= 100:
        out /= 100.0
    return out if 0 <= out <= 1 else None


def rebound_candidates(own: int, displayed_pct: Any, max_opponent: int) -> list[int]:
    pct = pct_fraction(displayed_pct)
    if pct is None or own < 0:
        return []
    if own == 0:
        return list(range(0, max_opponent + 1)) if pct <= 0.0005 else []
    p = Decimal(str(pct))
    half = Decimal("0.0005")
    low = max(Decimal("0"), p - half)
    high = min(Decimal("1"), p + half)
    own_d = Decimal(own)
    lower = max(0, math.ceil(float(own_d / high - own_d) - 1e-12)) if high > 0 else 0
    upper = max_opponent if low <= 0 else min(max_opponent, math.floor(float(own_d / low - own_d) + 1e-12))
    if upper < lower:
        return []
    return [opp for opp in range(lower, upper + 1) if abs(own / (own + opp) - pct) <= 0.0005000001]


def alias(available: set[str], choices: Iterable[str]) -> str:
    for choice in choices:
        if choice in available:
            return choice
    raise RuntimeError(f"required rebound metric missing; tried {list(choices)}")


def derive_segment_treb(segments: pd.DataFrame) -> pd.DataFrame:
    available = set(segments.metric.astype(str).unique())
    names = {
        "oreb": alias(available, ["OffRebounds", "OffensiveRebounds", "OREB"]),
        "dreb": alias(available, ["DefRebounds", "DefensiveRebounds", "DREB"]),
        "oreb_pct": alias(available, ["OffReboundPct", "OffensiveReboundPct", "OREBPct"]),
        "dreb_pct": alias(available, ["DefReboundPct", "DefensiveReboundPct", "DREBPct"]),
    }
    keys = ["season", "team_id", "player_id", "player", "query_start_date", "query_end_date"]
    subset = segments[segments.metric.isin(names.values())].copy()
    wide = subset.pivot_table(index=keys, columns="metric", values=["on", "off_corrected", "minutes_on", "minutes_off"], aggfunc="first")
    wide.columns = [f"{value}_{metric}" for value, metric in wide.columns]
    wide = wide.reset_index()
    rows: list[dict[str, Any]] = []
    for record in wide.to_dict("records"):
        out = {key: record[key] for key in keys}
        out["team_id"] = int(out["team_id"])
        out["player_id"] = str(out["player_id"])
        out["minutes_on"] = num(record.get(f"minutes_on_{names['oreb']}")) or 0.0
        out["minutes_off"] = num(record.get(f"minutes_off_{names['oreb']}")) or 0.0
        valid = True
        for side, source in (("on", "on"), ("off", "off_corrected")):
            own_oreb = integer(record.get(f"{source}_{names['oreb']}"))
            own_dreb = integer(record.get(f"{source}_{names['dreb']}"))
            if own_oreb is None or own_dreb is None:
                valid = False
                break
            minutes = out["minutes_on"] if side == "on" else out["minutes_off"]
            cap = max(25, math.ceil(float(minutes) * 2.5 + 30))
            opp_dreb = rebound_candidates(own_oreb, record.get(f"{source}_{names['oreb_pct']}"), cap)
            opp_oreb = rebound_candidates(own_dreb, record.get(f"{source}_{names['dreb_pct']}"), cap)
            totals = sorted({a + b for a in opp_oreb for b in opp_dreb})
            if not totals:
                valid = False
                break
            own = own_oreb + own_dreb
            out[f"team_rebounds_{side}"] = own
            out[f"opponent_rebounds_{side}_min"] = min(totals)
            out[f"opponent_rebounds_{side}_max"] = max(totals)
        if valid:
            rows.append(out)
    if not rows:
        raise RuntimeError("Total Rebound % derivation produced no valid tenure segments")
    return pd.DataFrame(rows)


def aggregate_treb(frame: pd.DataFrame, keys: list[str], method: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, g in frame.groupby(keys, dropna=False, sort=True):
        values = key if isinstance(key, tuple) else (key,)
        row = {name: value for name, value in zip(keys, values)}
        for side in ("on", "off"):
            own = int(pd.to_numeric(g[f"team_rebounds_{side}"], errors="coerce").fillna(0).sum())
            omin = int(pd.to_numeric(g[f"opponent_rebounds_{side}_min"], errors="coerce").fillna(0).sum())
            omax = int(pd.to_numeric(g[f"opponent_rebounds_{side}_max"], errors="coerce").fillna(0).sum())
            row[f"team_rebounds_{side}"] = own
            row[f"opponent_rebounds_{side}_min"] = omin
            row[f"opponent_rebounds_{side}_max"] = omax
            row[f"treb_pct_{side}_max"] = 100.0 * own / (own + omin) if own + omin else math.nan
            row[f"treb_pct_{side}_min"] = 100.0 * own / (own + omax) if own + omax else math.nan
            row[f"treb_pct_{side}_mid"] = (row[f"treb_pct_{side}_min"] + row[f"treb_pct_{side}_max"]) / 2.0
            row[f"treb_pct_{side}_exact"] = omin == omax
        row["minutes_on"] = float(pd.to_numeric(g.minutes_on, errors="coerce").fillna(0).sum())
        row["minutes_off"] = float(pd.to_numeric(g.minutes_off, errors="coerce").fillna(0).sum())
        row["treb_swing_mid"] = row["treb_pct_on_mid"] - row["treb_pct_off_mid"]
        row["treb_swing_min"] = row["treb_pct_on_min"] - row["treb_pct_off_max"]
        row["treb_swing_max"] = row["treb_pct_on_max"] - row["treb_pct_off_min"]
        row["treb_pct_exact"] = bool(row["treb_pct_on_exact"] and row["treb_pct_off_exact"])
        row["source_segments"] = int(len(g))
        row["aggregation_method"] = method
        if "team_id" in row:
            row["team_id"] = int(row["team_id"])
        if "player_id" in row:
            row["player_id"] = str(row["player_id"])
        rows.append(row)
    return pd.DataFrame(rows)


def rank_career_treb(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["qualifies_10000_minutes"] = out.minutes_on >= MINUTES_THRESHOLD
    idx = out.index[out.qualifies_10000_minutes & out.treb_pct_on_mid.notna()]
    out.loc[idx, "rank_on_10000"] = out.loc[idx, "treb_pct_on_mid"].rank(method="min", ascending=False)
    idx = out.index[out.qualifies_10000_minutes & out.treb_swing_mid.notna()]
    out.loc[idx, "rank_swing_high_to_low_10000"] = out.loc[idx, "treb_swing_mid"].rank(method="min", ascending=False)
    out.loc[idx, "rank_swing_low_to_high_10000"] = out.loc[idx, "treb_swing_mid"].rank(method="min", ascending=True)
    out.loc[idx, "swing_percentile_10000"] = out.loc[idx, "treb_swing_mid"].rank(method="average", pct=True) * 100.0
    return out


def write_table(name: str, frame: pd.DataFrame) -> None:
    frame.to_parquet(OUT / f"{name}.parquet", index=False, compression="zstd")
    frame.to_csv(OUT / f"{name}.csv.gz", index=False, compression="gzip")


def rebuild_package() -> dict[str, Any]:
    patch_legacy_completion_alias()
    legacy.build()

    team_metric = pd.read_parquet(OUT / "player_team_season_corrected_on_off.parquet")
    segments = pd.read_parquet(OUT / "tenure_segment_on_off.parquet")
    player_season = player_season_generic(team_metric)
    treb_segments = derive_segment_treb(segments)
    treb_team = aggregate_treb(treb_segments, ["season", "team_id", "player_id", "player"], "count-summed across exact tenure segments")
    treb_player_season = aggregate_treb(treb_segments, ["season", "player_id", "player"], "count-summed across teams and exact tenure segments")
    treb_career = rank_career_treb(aggregate_treb(treb_segments, ["player_id", "player"], "count-summed across all exact tenure segments"))

    write_table("player_season_corrected_on_off", player_season)
    write_table("tenure_segment_total_rebound_pct", treb_segments)
    write_table("player_team_season_total_rebound_pct", treb_team)
    write_table("player_season_total_rebound_pct", treb_player_season)
    write_table("career_total_rebound_pct", treb_career)

    dictionary_path = OUT / "metric_dictionary.parquet"
    dictionary = pd.read_parquet(dictionary_path)
    if "TotalReboundPct_Derived" not in set(dictionary.metric.astype(str)):
        dictionary = pd.concat([dictionary, pd.DataFrame([{
            "metric": "TotalReboundPct_Derived",
            "rows": len(treb_team),
            "players": int(treb_career.player_id.nunique()),
            "seasons": int(treb_team.season.nunique()),
            "teams": int(treb_team.team_id.nunique()),
            "on_non_null": int(treb_team.treb_pct_on_mid.notna().sum()),
            "off_corrected_non_null": int(treb_team.treb_pct_off_mid.notna().sum()),
            "source": "Derived from PBP Stats OffRebounds, DefRebounds, OffReboundPct and DefReboundPct over exact roster-tenure intervals",
            "off_definition": "team total rebound share while player is OFF court, restricted to exact roster tenure",
            "swing_definition": "derived ON TREB% minus tenure-corrected OFF TREB%",
            "multi_segment_note": "rebound counts and opponent-rebound rounding bounds are summed before percentages are calculated",
        }])], ignore_index=True)
        write_table("metric_dictionary", dictionary)

    db_path = OUT / "TREB_corrected_off.duckdb"
    db = duckdb.connect(str(db_path))
    for name in ["player_season_corrected_on_off", "tenure_segment_total_rebound_pct", "player_team_season_total_rebound_pct", "player_season_total_rebound_pct", "career_total_rebound_pct", "metric_dictionary"]:
        db.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_parquet('{(OUT / f'{name}.parquet').as_posix()}')")
    db.execute("CREATE OR REPLACE VIEW career_treb_10000 AS SELECT * FROM career_total_rebound_pct WHERE qualifies_10000_minutes")
    db.close()

    quality_path = OUT / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    collection = json.loads(COLLECTION.read_text(encoding="utf-8"))
    quality.update({
        "stage2_exact_ready": True,
        "impact_windows": int(collection.get("impact_windows_total") or 0),
        "player_season_metric_rows": int(len(player_season)),
        "metric_count_endpoint": int(team_metric.metric.nunique()),
        "metric_count_including_derived_treb": int(team_metric.metric.nunique()) + 1,
        "unique_player_seasons": int(player_season[["season", "player_id"]].drop_duplicates().shape[0]),
        "treb_career_players": int(treb_career.player_id.nunique()),
        "treb_10000_players": int(treb_career.qualifies_10000_minutes.sum()),
        "treb_exact_career_players": int(treb_career.treb_pct_exact.sum()),
    })
    quality_path.write_text(json.dumps(quality, indent=2), encoding="utf-8")

    provenance_path = OUT / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["total_rebound_pct_method"] = "sum team rebound counts and inferred opponent rebound-count bounds from displayed OREB%/DREB%, then calculate total rebound share"
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    readme = OUT / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nThe extended package also includes player-season summaries across traded teams and a dedicated Total Rebound % layer at tenure-segment, player-team-season, player-season and career levels.\n", encoding="utf-8")

    manifest = {
        "package": "TREB tenure-corrected historical player-impact database",
        "generated_utc": pd.Timestamp.utcnow().isoformat(),
        "quality": quality,
        "files": [],
    }
    for path in sorted(p for p in OUT.iterdir() if p.is_file() and p.name != "manifest.json"):
        manifest["files"].append({"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)})
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = Path(shutil.make_archive(str(SRC / "TREB_corrected_off_final"), "zip", root_dir=OUT))
    manifest["zip"] = {"name": archive.name, "bytes": archive.stat().st_size, "sha256": sha256(archive)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def self_test() -> None:
    assert rebound_candidates(10, 0.5, 30) == [10]
    assert rebound_candidates(0, 0, 3) == [0, 1, 2, 3]
    print("finalize_corrected_off_package_extended self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    print(json.dumps({"self_test": True}, indent=2)) if args.self_test and not self_test() else None
    if not args.self_test:
        print(json.dumps(rebuild_package(), indent=2))


if __name__ == "__main__":
    main()
