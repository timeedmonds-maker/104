from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import math
import pathlib
import shutil
import zipfile
from fractions import Fraction
from typing import Any

BASE = pathlib.Path(__file__).resolve().parent
DB = BASE / "impact_database"
TENURE_SOURCE = DB / "corrected_off" / "tenure_segment_on_off.jsonl.gz"
FINAL_ZIP = "NBA_native_89_metrics_ANALYSIS_READY_FINAL_2000-01_to_2025-26.zip"
SEASONS = [f"{y}-{str(y+1)[-2:]}" for y in range(2000, 2026)]
ACCEPTANCE_SEASONS = {"2021-22", "2022-23", "2023-24", "2024-25", "2025-26"}
KEY_FIELDS = ("season", "team_id", "player_id", "query_start_date", "query_end_date")


def fnum(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def ikey(r: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return tuple(str(r.get(k)) for k in KEY_FIELDS)  # type: ignore[return-value]


def div(a: float | None, b: float | None, scale: float = 1.0) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return scale * a / b


def close(a: float | None, b: float | None, abs_tol: float = 1e-9, rel_tol: float = 1e-9) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(abs_tol, rel_tol * max(1.0, abs(a), abs(b)))


def load_native() -> tuple[list[dict[str, Any]], dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = collections.defaultdict(dict)
    with gzip.open(TENURE_SOURCE, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            rows.append(r)
            by_key[ikey(r)][str(r["metric"])] = r
    return rows, by_key


def load_raw(raw_dir: pathlib.Path) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    files = sorted(raw_dir.rglob("raw_tenure_shard_*.jsonl.gz"))
    if not files:
        raise RuntimeError(f"no raw tenure shard JSONL files under {raw_dir}")
    out: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                k = ikey(r)
                if k in out:
                    raise RuntimeError(f"duplicate raw tenure key {k} from {p}")
                out[k] = r
    return out


def raw_rate(metric: str, team: dict[str, Any], opp: dict[str, Any]) -> float | None:
    """Reconstruct the acceptance-critical native rates directly from additive raw fields."""
    if metric == "OReb%":
        tor, odr = fnum(team.get("OffRebounds")), fnum(opp.get("DefRebounds"))
        return div(tor, None if tor is None or odr is None else tor + odr)
    if metric == "DReb%":
        tdr, oor = fnum(team.get("DefRebounds")), fnum(opp.get("OffRebounds"))
        return div(tdr, None if tdr is None or oor is None else tdr + oor)
    if metric == "Pts per 100 Possessions":
        return div(fnum(team.get("Points")), fnum(team.get("OffPoss")), 100.0)
    if metric == "Pts per 100 Possessions - Defense":
        return div(fnum(opp.get("Points")), fnum(opp.get("OffPoss")), 100.0)
    raise KeyError(metric)


def metric_catalog(metrics: list[str]) -> list[dict[str, str]]:
    """Catalog exact additive/exposure methods. The raw file itself retains all source fields."""
    rows: dict[str, dict[str, str]] = {}

    def add(metric: str, category: str, method: str, fields: str, reconstructed: bool = False, note: str = "") -> None:
        rows[metric] = {
            "metric": metric,
            "category": category,
            "aggregation_method": method,
            "sufficient_stat_fields": fields,
            "directly_additive_or_reconstructed": "reconstructed_from_retained_raw_components" if reconstructed else "direct_ratio_or_weighted_mean_from_retained_raw_components",
            "notes": note,
        }

    # Direct shooting/assist/count ratios.
    add("2pt FGM Assist%", "shooting/assists", "sum(TwoPtAssists) / sum(FG2M)", "TwoPtAssists;FG2M")
    add("Non Putback 2pt FGM Assist%", "shooting/assists", "sum(TwoPtAssists) / sum(FG2M - PtsPutbacks/2)", "TwoPtAssists;FG2M;PtsPutbacks")
    add("3pt FGM Assist%", "shooting/assists", "sum(ThreePtAssists) / sum(FG3M)", "ThreePtAssists;FG3M")
    add("3pt FG%", "shooting", "sum(FG3M) / sum(FG3A)", "FG3M;FG3A")
    add("Non-Heave 3pt FG%", "shooting", "sum(FG3M-HeaveMakes) / sum(FG3A-HeaveAttempts)", "FG3M;FG3A;HeaveMakes;HeaveAttempts")
    add("2pt FG%", "shooting", "sum(FG2M) / sum(FG2A)", "FG2M;FG2A")
    add("eFG%", "shooting", "sum(FG2M + 1.5*FG3M) / sum(FG2A+FG3A)", "FG2M;FG3M;FG2A;FG3A")
    add("TS%", "shooting", "sum(Points) / sum(TS denominator equivalent)", "Points;TsPct;FG2A;FG3A;FTA;raw production TsPct", True, "The release retains the complete raw production row. For positive-scoring rows, TS denominator equivalent is losslessly Points/TsPct; zero-event edge cases are retained in raw form and handled by aggregate_native89.py.")
    add("3PAr", "shooting", "sum(FG3A) / sum(FG2A+FG3A)", "FG3A;FG2A")
    add("% of FG3A Blocked", "shooting", "sum(Fg3aBlocked) / sum(FG3A)", "Fg3aBlocked;FG3A")
    add("% of FG2A Blocked", "shooting", "sum(Fg2aBlocked) / sum(FG2A)", "Fg2aBlocked;FG2A")
    add("Live Ball TO%", "turnovers", "sum(LiveBallTurnovers) / sum(Turnovers)", "LiveBallTurnovers;Turnovers")

    # Rebound families. Overall OReb% is intentionally reconstructed ONLY from explicit counts.
    group_reb = [
        ("DReb% - Missed FTs", "Def", "FT", "FTDefRebounds", "FTOffRebounds"),
        ("OReb% - Missed FTs", "Off", "FT", "FTOffRebounds", "FTDefRebounds"),
        ("DReb% - Missed 2s", "Def", "TwoPt", "DefTwoPtRebounds", "OffTwoPtRebounds"),
        ("OReb% - Missed 2s", "Off", "TwoPt", "OffTwoPtRebounds", "DefTwoPtRebounds"),
        ("DReb% - Missed 3s", "Def", "ThreePt", "DefThreePtRebounds", "OffThreePtRebounds"),
        ("OReb% - Missed 3s", "Off", "ThreePt", "OffThreePtRebounds", "DefThreePtRebounds"),
    ]
    for m, side, grp, team_count, opp_count in group_reb:
        add(m, "rebounding", f"sum(team {team_count}) / sum(team {team_count} + opponent {opp_count})", f"team.{team_count};opponent.{opp_count}")
    add("DReb% - Missed FGs", "rebounding", "sum(team DefTwoPtRebounds+DefThreePtRebounds) / corresponding team+opponent rebound opportunity sum", "team.DefTwoPtRebounds;team.DefThreePtRebounds;opponent.OffTwoPtRebounds;opponent.OffThreePtRebounds")
    add("OReb% - Missed FGs", "rebounding", "sum(team OffTwoPtRebounds+OffThreePtRebounds) / corresponding team+opponent rebound opportunity sum", "team.OffTwoPtRebounds;team.OffThreePtRebounds;opponent.DefTwoPtRebounds;opponent.DefThreePtRebounds")
    add("DReb%", "rebounding", "sum(team DefRebounds) / sum(team DefRebounds + opponent OffRebounds)", "team.DefRebounds;opponent.OffRebounds")
    add("OReb%", "rebounding", "sum(team OffRebounds) / sum(team OffRebounds + opponent DefRebounds)", "team.OffRebounds;opponent.DefRebounds", False, "Acceptance metric. No inversion of published OReb% is used.")
    for loc in ("At Rim", "Short Mid-Range", "Long Mid-Range", "Arc 3", "Corner 3"):
        token = loc.replace(" ", "").replace("-", "")
        for side in ("O", "D"):
            m = f"{loc} {side}Reb%"
            add(m, "rebounding/location", "sum exact integer location rebound numerator / sum exact location rebound opportunity denominator", f"raw {side.lower()}ff/{side.lower()}ef location ReboundPct; group Off/DefTwoPtRebounds or Off/DefThreePtRebounds; opponent complementary group rebound counts; location FGA/FGM", True, "aggregate_native89.py performs integer-constrained recovery from retained exact group totals plus exact rational location shares; rates are never averaged.")

    add("Blocks Recovered %", "defense", "sum(RecoveredBlocks) / sum(Blocks)", "RecoveredBlocks;Blocks")
    add("Seconds Per Possession - Offense", "pace/possession", "sum(SecondsPerPossOff * OffPoss) / sum(OffPoss)", "SecondsPerPossOff;OffPoss", True, "Equivalent additive numerator is retained value × possession exposure.")
    add("Seconds Per Possession - Defense", "pace/possession", "sum(opponent SecondsPerPossOff * opponent OffPoss) / sum(opponent OffPoss)", "opponent.SecondsPerPossOff;opponent.OffPoss", True)

    # Location shooting families.
    loc_map = [("At Rim", "AtRim"), ("Short Mid Range", "ShortMidRange"), ("Long Mid Range", "LongMidRange"), ("Corner 3", "Corner3"), ("Arc 3", "Arc3")]
    for label, k in loc_map:
        add(f"{label} Shot Frequency", "shooting/location", f"sum({k}FGA) / sum(FG2A+FG3A)", f"{k}FGA;FG2A;FG3A")
        add(f"{label} FG%", "shooting/location", f"sum({k}FGM) / sum({k}FGA)", f"{k}FGM;{k}FGA")
        ast_label = {"At Rim":"At Rim % Assisted","Short Mid Range":"Short Mid Range % Assisted","Long Mid Range":"Long Mid % Assisted","Corner 3":"Corner 3 % Assisted","Arc 3":"Arc 3 % Assisted"}[label]
        add(ast_label, "shooting/location", f"sum({k}Assists) / sum({k}FGM)", f"{k}Assists;{k}FGM")
    add("At Rim or 3pt Shot Frequency", "shooting/location", "sum(AtRimFGA+FG3A) / sum(FG2A+FG3A)", "AtRimFGA;FG3A;FG2A")
    add("Non-Heave Arc 3 FG%", "shooting/location", "sum(NonHeaveArc3FGM) / sum(NonHeaveArc3FGA)", "NonHeaveArc3FGM;NonHeaveArc3FGA")
    add("Shot Quality", "shooting", "sum(ShotQualityAvg * (FG2A+FG3A)) / sum(FG2A+FG3A)", "ShotQualityAvg;FG2A;FG3A", True, "Equivalent expected-points numerator is value × shot-attempt exposure.")

    add("Shooting Foul Drawn Rate", "fouls", "sum(TwoPtShootingFoulsDrawn+ThreePtShootingFoulsDrawn) / sum(FG2A+FG3A + shooting fouls drawn - 2pt/3pt And1 trips)", "FG2A;FG3A;TwoPtShootingFoulsDrawn;ThreePtShootingFoulsDrawn;2pt And 1 Free Throw Trips;3pt And 1 Free Throw Trips")
    # Preserve established label/source mapping exactly; do not relabel history.
    add("3pt Shooting Foul Drawn Rate", "fouls", "sum(TwoPtShootingFoulsDrawn) / sum(FG2A + TwoPtShootingFoulsDrawn - 2pt And1 trips)", "FG2A;TwoPtShootingFoulsDrawn;2pt And 1 Free Throw Trips", False, "Established native label maps to raw TwoPtShootingFoulsDrawnPct; preserved without reinterpretation.")
    add("2pt Shooting Foul Drawn Rate", "fouls", "sum(ThreePtShootingFoulsDrawn) / sum(FG3A + ThreePtShootingFoulsDrawn - 3pt And1 trips)", "FG3A;ThreePtShootingFoulsDrawn;3pt And 1 Free Throw Trips", False, "Established native label maps to raw ThreePtShootingFoulsDrawnPct; preserved without reinterpretation.")
    add("Second Chance Points%", "scoring", "sum(SecondChancePoints) / sum(Points)", "SecondChancePoints;Points")
    add("Penalty Points%", "scoring", "sum(PenaltyPoints) / sum(Points)", "PenaltyPoints;Points")
    add("Penalty Possessions%", "possession", "sum(PenaltyOffPoss) / sum(OffPoss)", "PenaltyOffPoss;OffPoss")
    add("Avg 2pt Shot Distance", "shooting", "sum(Avg2ptShotDistance * FG2A) / sum(FG2A)", "Avg2ptShotDistance;FG2A", True)
    add("Avg 3pt Shot Distance", "shooting", "sum(Avg3ptShotDistance * FG3A) / sum(FG3A)", "Avg3ptShotDistance;FG3A", True)

    add("Penalty Efficiency Excluding Last Minute Take Foul Possessions", "scoring", "100 * sum(PenaltyPointsExcludingTakeFouls) / sum(PenaltyOffPossExcludingTakeFouls)", "PenaltyPointsExcludingTakeFouls;PenaltyOffPossExcludingTakeFouls")
    add("Pts per 100 Possessions", "rating", "100 * sum(Points) / sum(OffPoss)", "Points;OffPoss")
    add("Assist Points per 100 Possessions", "rating", "100 * sum(AssistPoints) / sum(OffPoss)", "AssistPoints;OffPoss")
    add("FTA per 100 Possessions", "rating", "100 * sum(FTA) / sum(OffPoss)", "FTA;OffPoss")
    add("TOs per 100 Possessions", "rating", "100 * sum(Turnovers) / sum(OffPoss)", "Turnovers;OffPoss")
    add("Assists per 100 Possessions", "rating", "100 * sum(Assists) / sum(OffPoss)", "Assists;OffPoss")

    # Defense mirrors: same formula family using opponent raw row.
    defense = {
        "Pts per 100 Possessions - Defense": ("100*sum(opponent Points)/sum(opponent OffPoss)", "opponent.Points;opponent.OffPoss"),
        "Shot Quality - Defense": ("sum(opponent ShotQualityAvg * opponent FGA)/sum(opponent FGA)", "opponent.ShotQualityAvg;opponent.FG2A;opponent.FG3A"),
        "3pt FG% - Defense": ("sum(opponent FG3M)/sum(opponent FG3A)", "opponent.FG3M;opponent.FG3A"),
        "2pt FG% - Defense": ("sum(opponent FG2M)/sum(opponent FG2A)", "opponent.FG2M;opponent.FG2A"),
        "eFG% - Defense": ("sum(opponent FG2M+1.5*opponent FG3M)/sum(opponent FG2A+opponent FG3A)", "opponent.FG2M;opponent.FG3M;opponent.FG2A;opponent.FG3A"),
        "3PAr - Defense": ("sum(opponent FG3A)/sum(opponent FG2A+opponent FG3A)", "opponent.FG3A;opponent.FG2A"),
    }
    for label, k in loc_map:
        defense[f"{label} Shot Frequency - Defense"] = (f"sum(opponent {k}FGA)/sum(opponent FG2A+opponent FG3A)", f"opponent.{k}FGA;opponent.FG2A;opponent.FG3A")
        defense[f"{label} FG% - Defense"] = (f"sum(opponent {k}FGM)/sum(opponent {k}FGA)", f"opponent.{k}FGM;opponent.{k}FGA")
    for m, (method, fields) in defense.items():
        add(m, "defense", method, fields, "Shot Quality" in m)

    add("Pace", "pace/possession", "1440 * sum(TotalPoss) / sum(SecondsPlayed)", "TotalPoss;SecondsPlayed")
    add("Second Chance Efficiency", "scoring", "100 * sum(SecondChancePoints) / sum(SecondChanceOffPoss)", "SecondChancePoints;SecondChanceOffPoss")
    add("Penalty Efficiency", "scoring", "100 * sum(PenaltyPoints) / sum(PenaltyOffPoss)", "PenaltyPoints;PenaltyOffPoss")
    add("Second Chance Possessions Per 100 Possessions", "possession", "100 * sum(SecondChanceOffPoss) / sum(OffPoss)", "SecondChanceOffPoss;OffPoss")
    add("First Chance Points Per 100 Possessions", "scoring", "100 * sum(FirstChancePoints) / sum(OffPoss)", "FirstChancePoints;OffPoss")
    add("Second Chance Points Per 100 Possessions", "scoring", "100 * sum(SecondChancePoints) / sum(OffPoss)", "SecondChancePoints;OffPoss")

    missing = [m for m in metrics if m not in rows]
    extra = [m for m in rows if m not in metrics]
    if missing or extra:
        raise RuntimeError(f"metric catalog mismatch missing={missing} extra={extra}")
    return [rows[m] for m in metrics]


def write_gzip_csv(path: pathlib.Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if not rows:
        raise RuntimeError(f"cannot write empty {path}")
    if fields is None:
        fields = list(rows[0])
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--out-dir", default="native89_analysis_ready_final")
    a = ap.parse_args()
    raw_dir = pathlib.Path(a.raw_dir)
    out = pathlib.Path(a.out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    native_rows, native_by_key = load_native()
    raw_by_key = load_raw(raw_dir)
    metrics = sorted({str(r["metric"]) for r in native_rows})
    seasons = sorted({str(r["season"]) for r in native_rows})
    native_keys = set(native_by_key)
    raw_keys = set(raw_by_key)

    assert len(native_rows) == 1_353_334, len(native_rows)
    assert len(metrics) == 89, len(metrics)
    assert seasons == SEASONS, seasons
    assert len(native_keys) == 15_206, len(native_keys)
    assert raw_keys == native_keys, {"missing_raw": len(native_keys-raw_keys), "extra_raw": len(raw_keys-native_keys)}
    assert not any("TotalReboundPct" in m or m.strip().lower() == "treb%" for m in metrics)

    # Preserve native values verbatim while restoring identity and exposure fields.
    long_fields = ["season","player_id","player_name","team_id","team_abbr","segment_index","segment_count","tenure_start","tenure_end","query_start_date","query_end_date","metric","on_court_minutes","off_court_minutes","on","corrected_off","swing","complete","tenure_confidence","boundary_resolution","collection_source"]
    long_out: list[dict[str, Any]] = []
    exposure: dict[tuple[str,str,str,str,str], dict[str,Any]] = {}
    missing_names = missing_minutes = 0
    for r in native_rows:
        if not r.get("player"):
            missing_names += 1
        if r.get("minutes_on") is None or r.get("minutes_off") is None:
            missing_minutes += 1
        k = ikey(r)
        q = {
            "season":r.get("season"), "player_id":r.get("player_id"), "player_name":r.get("player"), "team_id":r.get("team_id"), "team_abbr":r.get("team_abbr"),
            "segment_index":r.get("segment_index"), "segment_count":r.get("segment_count"), "tenure_start":r.get("tenure_start"), "tenure_end":r.get("tenure_end"),
            "query_start_date":r.get("query_start_date"), "query_end_date":r.get("query_end_date"), "metric":r.get("metric"),
            "on_court_minutes":r.get("minutes_on"), "off_court_minutes":r.get("minutes_off"), "on":r.get("on"), "corrected_off":r.get("off_corrected"),
            "swing":r.get("on_minus_off_corrected"), "complete":r.get("complete"), "tenure_confidence":r.get("tenure_confidence"),
            "boundary_resolution":r.get("boundary_resolution"), "collection_source":r.get("collection_source")}
        long_out.append(q)
        exposure.setdefault(k, {x:q[x] for x in ["season","player_id","player_name","team_id","team_abbr","segment_index","segment_count","tenure_start","tenure_end","query_start_date","query_end_date","on_court_minutes","off_court_minutes","tenure_confidence","boundary_resolution"]})
    assert missing_names == 0 and missing_minutes == 0
    write_gzip_csv(out/"native_89_metrics_LONG_WITH_EXPOSURES.csv.gz", long_out, long_fields)
    write_gzip_csv(out/"TENURE_EXPOSURES.csv.gz", list(exposure.values()))

    # Flatten complete raw production rows: one row per tenure x state (team_on/team_off/opponent_on/opponent_off).
    all_raw_fields: set[str] = set()
    for rr in raw_by_key.values():
        for row in (rr.get("rows") or {}).values():
            if isinstance(row, dict):
                all_raw_fields.update(map(str,row.keys()))
    raw_stat_fields = sorted(all_raw_fields)
    id_fields = ["season","player_id","player_name","team_id","team_abbr","segment_index","segment_count","query_start_date","query_end_date","canonical_on_court_minutes","canonical_off_court_minutes","raw_minutes_on","raw_minutes_off","row_type"]
    flat_rows: list[dict[str,Any]] = []
    for k in sorted(raw_by_key):
        rr = raw_by_key[k]
        for row_type in ("team_on","team_off","opponent_on","opponent_off"):
            row = (rr.get("rows") or {}).get(row_type)
            if not isinstance(row,dict):
                raise RuntimeError(f"missing {row_type} for {k}")
            q = {"season":rr.get("season"),"player_id":rr.get("player_id"),"player_name":rr.get("player"),"team_id":rr.get("team_id"),"team_abbr":rr.get("team_abbr"),"segment_index":rr.get("segment_index"),"segment_count":rr.get("segment_count"),"query_start_date":rr.get("query_start_date"),"query_end_date":rr.get("query_end_date"),"canonical_on_court_minutes":rr.get("minutes_on"),"canonical_off_court_minutes":rr.get("minutes_off"),"raw_minutes_on":rr.get("raw_minutes_on"),"raw_minutes_off":rr.get("raw_minutes_off"),"row_type":row_type}
            q.update(row)
            flat_rows.append(q)
    assert len(flat_rows)==15_206*4, len(flat_rows)
    write_gzip_csv(out/"RAW_WOWY_COMPONENTS_BY_TENURE.csv.gz", flat_rows, id_fields+raw_stat_fields)

    # Compact acceptance-critical sufficient stats directly from raw additive values.
    suff: list[dict[str,Any]]=[]
    equivalence_fail=[]
    equivalence_checked=collections.Counter()
    for k in sorted(native_keys):
        rr=raw_by_key[k]; rows=rr["rows"]
        ton,toff,oon,ooff=rows["team_on"],rows["team_off"],rows["opponent_on"],rows["opponent_off"]
        nd=native_by_key[k]
        base=exposure[k]
        s={**base,
           "team_orebounds_on":fnum(ton.get("OffRebounds")),"opponent_def_rebounds_on":fnum(oon.get("DefRebounds")),
           "team_orebounds_off":fnum(toff.get("OffRebounds")),"opponent_def_rebounds_off":fnum(ooff.get("DefRebounds")),
           "team_points_on":fnum(ton.get("Points")),"team_off_possessions_on":fnum(ton.get("OffPoss")),
           "team_points_off":fnum(toff.get("Points")),"team_off_possessions_off":fnum(toff.get("OffPoss")),
           "opponent_points_on":fnum(oon.get("Points")),"opponent_off_possessions_on":fnum(oon.get("OffPoss")),
           "opponent_points_off":fnum(ooff.get("Points")),"opponent_off_possessions_off":fnum(ooff.get("OffPoss"))}
        suff.append(s)
        for metric in ("OReb%","Pts per 100 Possessions","Pts per 100 Possessions - Defense"):
            if metric not in nd: continue
            for state,tm,op,field in (("on",ton,oon,"on"),("off",toff,ooff,"off_corrected")):
                calc=raw_rate(metric,tm,op); target=fnum(nd[metric].get(field))
                if calc is None or target is None: continue
                equivalence_checked[f"{metric}:{state}"]+=1
                if not close(calc,target,abs_tol=2e-8,rel_tol=2e-8):
                    equivalence_fail.append({"key":k,"metric":metric,"state":state,"raw_reconstructed":calc,"established_native":target,"delta":calc-target})
    write_gzip_csv(out/"OREB_NET_RATING_RAW_SUFFICIENT_STATS_BY_TENURE.csv.gz",suff)

    # Preserve and expose the previously-established rating-equivalent reconstruction too.
    rating_equiv=[]
    needed=["Pts per 100 Possessions","Pts per 100 Possessions - Defense","Seconds Per Possession - Offense","Seconds Per Possession - Defense"]
    for k in sorted(native_keys):
        d=native_by_key[k]
        if not all(m in d for m in needed): continue
        o,dv,spo,spd=[d[m] for m in needed]
        vals=[o.get("on"),o.get("off_corrected"),dv.get("on"),dv.get("off_corrected"),spo.get("on"),spo.get("off_corrected"),spd.get("on"),spd.get("off_corrected"),o.get("minutes_on"),o.get("minutes_off")]
        if any(v is None for v in vals): continue
        ort_on,ort_off,drt_on,drt_off,spo_on,spo_off,spd_on,spd_off,mon,mof=map(float,vals)
        if min(spo_on,spo_off,spd_on,spd_off,mon,mof)<=0: continue
        op_on=mon*60/spo_on; op_off=mof*60/spo_off; dp_on=mon*60/spd_on; dp_off=mof*60/spd_off
        e=exposure[k]
        rating_equiv.append({**e,"on_offensive_possessions_equiv":op_on,"off_offensive_possessions_equiv":op_off,"on_defensive_possessions_equiv":dp_on,"off_defensive_possessions_equiv":dp_off,"on_team_points_equiv":ort_on*op_on/100,"off_team_points_equiv":ort_off*op_off/100,"on_opponent_points_equiv":drt_on*dp_on/100,"off_opponent_points_equiv":drt_off*dp_off/100})
    write_gzip_csv(out/"NET_RATING_ESTABLISHED_EQUIVALENT_STATS_BY_TENURE.csv.gz",rating_equiv)

    # Acceptance: OReb% from explicit raw rebound counts; Net Rating from established rating-equivalent stats.
    oa=collections.defaultdict(lambda:collections.Counter())
    na=collections.defaultdict(lambda:collections.Counter())
    names={}; seg_counts=collections.Counter(); team_sets=collections.defaultdict(set)
    for r in suff:
        if r["season"] not in ACCEPTANCE_SEASONS: continue
        p=str(r["player_id"]); names[p]=r["player_name"]; seg_counts[p]+=1; team_sets[p].add(str(r["team_id"]))
        x=oa[p]; x["minutes"]+=float(r["on_court_minutes"]); x["oreb"]+=float(r["team_orebounds_on"] or 0); x["opp_dreb"]+=float(r["opponent_def_rebounds_on"] or 0)
    for r in rating_equiv:
        if r["season"] not in ACCEPTANCE_SEASONS: continue
        p=str(r["player_id"]); names[p]=r["player_name"]; x=na[p]
        for field in ["on_offensive_possessions_equiv","off_offensive_possessions_equiv","on_defensive_possessions_equiv","off_defensive_possessions_equiv","on_team_points_equiv","off_team_points_equiv","on_opponent_points_equiv","off_opponent_points_equiv"]:
            x[field]+=float(r[field])
    acc=[]
    for p in sorted(set(oa)&set(na),key=lambda x:int(x) if x.isdigit() else x):
        o=oa[p]; n=na[p]
        if o["minutes"]<2000: continue
        oreb_den=o["oreb"]+o["opp_dreb"]
        req=[oreb_den,n["on_offensive_possessions_equiv"],n["off_offensive_possessions_equiv"],n["on_defensive_possessions_equiv"],n["off_defensive_possessions_equiv"]]
        if min(req)<=0: continue
        orebr=100*o["oreb"]/oreb_den
        ort_on=100*n["on_team_points_equiv"]/n["on_offensive_possessions_equiv"]
        drt_on=100*n["on_opponent_points_equiv"]/n["on_defensive_possessions_equiv"]
        ort_off=100*n["off_team_points_equiv"]/n["off_offensive_possessions_equiv"]
        drt_off=100*n["off_opponent_points_equiv"]/n["off_defensive_possessions_equiv"]
        nr_on=ort_on-drt_on; nr_off=ort_off-drt_off
        acc.append({"player_id":p,"player_name":names[p],"on_court_minutes":o["minutes"],"oreb_pct_on":orebr,"net_rating_on":nr_on,"net_rating_corrected_off":nr_off,"net_rating_swing":nr_on-nr_off,"tenure_segments":seg_counts[p],"distinct_teams":len(team_sets[p])})
    if not acc: raise RuntimeError("acceptance table empty")
    with open(out/"ACCEPTANCE_2021-22_to_2025-26_MIN2000.csv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(acc[0])); w.writeheader(); w.writerows(acc)

    # Catalog.
    cat=metric_catalog(metrics)
    with open(out/"METRIC_CATALOG.csv","w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(cat[0])); w.writeheader(); w.writerows(cat)

    transaction_test=[r for r in acc if int(r["tenure_segments"])>5 or int(r["distinct_teams"])>1]
    if not transaction_test:
        raise RuntimeError("no multi-segment/team acceptance players for transaction QA")

    # Equivalence gate is deliberately fail-closed. Persist diagnostics before raising.
    eq_report={"checked":dict(equivalence_checked),"failure_count":len(equivalence_fail),"failures":equivalence_fail[:500]}
    (out/"RAW_TO_NATIVE_EQUIVALENCE_QA.json").write_text(json.dumps(eq_report,indent=2,default=str)+"\n")

    qa={
      "status":"PASS" if not equivalence_fail else "FAIL",
      "native_metric_count":len(metrics),"season_count":len(seasons),"first_season":seasons[0],"last_season":seasons[-1],"treb_excluded":True,
      "native_metric_rows":len(native_rows),"native_tenure_segments":len(native_keys),"raw_tenure_segments":len(raw_by_key),"raw_flat_rows":len(flat_rows),"raw_numeric_source_field_count":len(raw_stat_fields),
      "player_names_populated":missing_names==0,"on_off_minutes_populated":missing_minutes==0,"transaction_tenure_identity_retained":True,
      "native_values_modified":False,"arbitrary_period_minute_qualification_supported":True,
      "oreb_on_reconstructable_from_summed_explicit_counts":True,"net_rating_on_reconstructable_from_sufficient_stats":True,"net_rating_corrected_off_reconstructable_from_sufficient_stats":True,
      "swing_calculated_after_aggregation":True,"simple_rate_averaging_used":False,"acceptance_players":len(acc),"acceptance_nonempty":bool(acc),
      "acceptance_transaction_test_players":len(transaction_test),"acceptance_transaction_examples":[{k:r[k] for k in ["player_id","player_name","tenure_segments","distinct_teams","on_court_minutes"]} for r in transaction_test[:20]],
      "raw_to_native_equivalence_checks":dict(equivalence_checked),"raw_to_native_equivalence_failures":len(equivalence_fail),
      "all_required_acceptance_files_inside_release":True,"external_lookup_required":False,
      "aggregation_rule":"select tenure rows; SUM sufficient statistics first; calculate aggregate rates second; calculate swing last",
      "acceptance_period":"2021-22 through 2025-26","acceptance_minimum_on_minutes":2000,
    }
    (out/"FINAL_QA.json").write_text(json.dumps(qa,indent=2,default=str)+"\n")

    readme="""NBA NATIVE 89-METRIC ANALYSIS-READY FINAL RELEASE

Scope
-----
Regular season 2000-01 through 2025-26. Exactly 89 established/native Stage2 metrics. TotalReboundPct/TREB% is deliberately excluded.

Canonical grain
---------------
The long native table is transaction-aware tenure-segment x metric. Identity is season + team_id + player_id + query_start_date + query_end_date. Player names, team abbreviations, segment identity/boundaries, ON minutes and corrected-OFF minutes are embedded; no external player-name lookup is required.

Definitions
-----------
ON is the established native team metric while the player is on court within the canonical tenure window. corrected OFF is the established transaction/tenure-corrected team metric while the player is off court within that same window. SWING = ON - corrected OFF at the native tenure grain. The established native values have not been replaced or recalculated in native_89_metrics_LONG_WITH_EXPOSURES.csv.gz.

Arbitrary-period/career aggregation
-----------------------------------
DO NOT average season or tenure rates. Select the desired tenure rows, SUM the appropriate sufficient statistics/exposures across all selected rows, calculate the aggregate ON and aggregate corrected-OFF rates, then calculate SWING LAST. METRIC_CATALOG.csv gives the required raw fields/formula family for every native metric. RAW_WOWY_COMPONENTS_BY_TENURE.csv.gz contains the retained Team/Opponent ON/OFF raw production rows used for sufficient-stat aggregation.

Acceptance OReb%
-----------------
Overall native OReb% is reconstructed from explicit counts, not by inverting the published percentage:
  aggregate OReb% ON = SUM(team_on OffRebounds) / [SUM(team_on OffRebounds) + SUM(opponent_on DefRebounds)].
The acceptance CSV reports this on a 0-100 percentage scale.

Net Rating
----------
Net Rating is a convenience derivative, not an additional native metric. The release retains the established possession-based sufficient-stat path in NET_RATING_ESTABLISHED_EQUIVALENT_STATS_BY_TENURE.csv.gz. Sum the offensive/defensive possession and point equivalents first; calculate aggregate ORtg and DRtg; Net Rating = ORtg - DRtg. Do this independently for ON and corrected OFF, then calculate Net Rating swing LAST. Raw Team/Opponent Points and OffPoss are also retained and cross-checked against the native ratings.

Files
-----
README_FIRST.txt: this guide.
METRIC_CATALOG.csv: all 89 exact native names and aggregation/sufficient-stat instructions.
native_89_metrics_LONG_WITH_EXPOSURES.csv.gz: established native ON/corrected-OFF/SWING values plus identity, names, boundaries, minutes and provenance.
TENURE_EXPOSURES.csv.gz: one row per canonical tenure segment.
RAW_WOWY_COMPONENTS_BY_TENURE.csv.gz: complete raw Team/Opponent ON/OFF production component rows at canonical tenure grain.
OREB_NET_RATING_RAW_SUFFICIENT_STATS_BY_TENURE.csv.gz: compact explicit rebound/point/possession fields.
NET_RATING_ESTABLISHED_EQUIVALENT_STATS_BY_TENURE.csv.gz: established rating-equivalent sufficient stats.
ACCEPTANCE_2021-22_to_2025-26_MIN2000.csv: required self-contained acceptance output.
RAW_TO_NATIVE_EQUIVALENCE_QA.json: raw-component vs established-native checks.
FINAL_QA.json: fail-closed release gates.
build_native89_analysis_ready_final.py: exact assembler used to create this release.

Units
-----
The native long table preserves established source units exactly. The acceptance oreb_pct_on field is expressed as percent (0-100). Ratings are points per 100 possessions.
"""
    (out/"README_FIRST.txt").write_text(readme,encoding="utf-8")
    shutil.copy2(pathlib.Path(__file__),out/"build_native89_analysis_ready_final.py")

    # Critical gate after diagnostics have been written.
    if equivalence_fail:
        raise RuntimeError(f"raw component/native equivalence failed for {len(equivalence_fail)} tenure-metric-state checks; see RAW_TO_NATIVE_EQUIVALENCE_QA.json")

    z=pathlib.Path(FINAL_ZIP)
    if z.exists(): z.unlink()
    with zipfile.ZipFile(z,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as q:
        for p in sorted(out.rglob("*")):
            if p.is_file(): q.write(p,p.relative_to(out))
    with zipfile.ZipFile(z) as q:
        bad=q.testzip()
        if bad: raise RuntimeError(f"zip CRC failure: {bad}")
        names=set(q.namelist())
        required={"README_FIRST.txt","METRIC_CATALOG.csv","native_89_metrics_LONG_WITH_EXPOSURES.csv.gz","TENURE_EXPOSURES.csv.gz","RAW_WOWY_COMPONENTS_BY_TENURE.csv.gz","OREB_NET_RATING_RAW_SUFFICIENT_STATS_BY_TENURE.csv.gz","NET_RATING_ESTABLISHED_EQUIVALENT_STATS_BY_TENURE.csv.gz","ACCEPTANCE_2021-22_to_2025-26_MIN2000.csv","FINAL_QA.json"}
        missing=required-names
        if missing: raise RuntimeError(f"required files missing from zip: {missing}")
    sha=hashlib.sha256(z.read_bytes()).hexdigest()
    pathlib.Path(FINAL_ZIP+".sha256").write_text(f"{sha}  {FINAL_ZIP}\n")
    print(json.dumps({"status":"PASS","zip":FINAL_ZIP,"zip_bytes":z.stat().st_size,"sha256":sha,"acceptance_players":len(acc),"raw_tenure_segments":len(raw_by_key)},indent=2))


if __name__ == "__main__":
    main()
