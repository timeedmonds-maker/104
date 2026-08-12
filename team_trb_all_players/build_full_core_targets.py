#!/usr/bin/env python3
"""Build the exact full-core TREB production target manifest from authoritative V2 PTS targets.

This does not change the V2 roster model. It selects the 9,647 PTS rows already
classified as full_core_reuse and expands each to exactly one whole-team-season
production target so the solved PBP/lineup engine can recover exact rebound
counts instead of inferring opponent counts from rounded legacy percentages.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
IMPACT = BASE / "impact_database"
V2 = IMPACT / "roster_tenure_v2" / "player_team_season_targets.jsonl.gz"
SCHEDULE = IMPACT / "roster_tenure" / "regular_season_games.jsonl.gz"
CORE_OUT = IMPACT / "outputs"
OUT = IMPACT / "roster_tenure_v2" / "full_core_segments.jsonl.gz"
SUMMARY = IMPACT / "roster_tenure_v2" / "full_core_segments_summary.json"
EXPECTED = 9647


def read_jsonl_gz(path: Path) -> list[dict]:
    out=[]
    with gzip.open(path,"rt",encoding="utf-8") as f:
        for line in f:
            if line.strip(): out.append(json.loads(line))
    return out


def write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row,separators=(",",":"),sort_keys=True)+"\n")


def load_abbr_map() -> dict[tuple[str,int],str]:
    m={}
    for p in sorted(CORE_OUT.glob("*/player_team_totals.csv.gz")):
        d=pd.read_csv(p,compression="gzip",usecols=["season","team_id","TeamAbbreviation"]).drop_duplicates()
        for r in d.itertuples(index=False):
            k=(str(r.season),int(r.team_id)); v=str(r.TeamAbbreviation)
            if k in m and m[k] != v:
                raise RuntimeError(f"team abbreviation conflict {k}: {m[k]} vs {v}")
            m[k]=v
    return m


def build() -> None:
    pts=read_jsonl_gz(V2)
    full=[r for r in pts if bool(r.get("full_core_reuse"))]
    assert len(pts)==14524,len(pts)
    assert len(full)==EXPECTED,len(full)
    schedule=read_jsonl_gz(SCHEDULE)
    by_team: dict[tuple[str,int],list[dict]]={}
    for g in schedule:
        season=str(g["season"])
        for tid in (int(g["home_team_id"]),int(g["away_team_id"])):
            by_team.setdefault((season,tid),[]).append(g)
    for games in by_team.values():
        games.sort(key=lambda x:(x["game_date"],int(x["game_id"])))
    abbr=load_abbr_map()
    targets=[]
    season_counts=Counter()
    for row in full:
        season=str(row["season"]); tid=int(row["team_id"])
        games=by_team.get((season,tid),[])
        if not games:
            raise RuntimeError(f"no schedule for {(season,tid)}")
        total=int(row["total_team_games"]); tenure=int(row["team_games_in_tenure"])
        if len(games)!=total or tenure!=total:
            raise RuntimeError(f"full-core schedule mismatch {(season,row['player_id'],tid)} schedule={len(games)} tenure={tenure} total={total}")
        expected_seconds=int(round(float(row["seconds_on"])))
        target={
            "season":season,
            "team_id":tid,
            "team_abbr":abbr[(season,tid)],
            "player_id":str(row["player_id"]),
            "player":row["player"],
            "query_start_date":str(games[0]["game_date"]),
            "query_end_date":str(games[-1]["game_date"]),
            "team_games_in_window":len(games),
            "expected_seconds_on":expected_seconds,
            "expected_minutes_on":expected_seconds/60.0,
            "segment_index":1,
            "segment_count":1,
            "needs_on":False,
            "source":"full_core_exact_rebuild_v1",
            "boundary_reason":"full_core_all_team_games",
        }
        targets.append(target); season_counts[season]+=1
    keys={(r["season"],int(r["team_id"]),str(r["player_id"])) for r in targets}
    assert len(targets)==EXPECTED and len(keys)==EXPECTED,(len(targets),len(keys))
    write_jsonl_gz(OUT,targets)
    payload={
        "status":"READY",
        "target_rows":len(targets),
        "unique_pts_keys":len(keys),
        "source_v2_pts_rows":len(pts),
        "source_v2_full_core_rows":len(full),
        "season_counts":dict(sorted(season_counts.items())),
        "policy":"One exact whole-team-season PBP reconstruction target per V2 full_core_reuse PTS row; no rounded legacy TREB inference.",
    }
    SUMMARY.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(payload,indent=2))


if __name__ == "__main__":
    build()
