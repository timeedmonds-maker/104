#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

COUNT_COLS = [
    "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on",
    "team_oreb_off", "team_dreb_off", "opponent_oreb_off", "opponent_dreb_off",
]

EXPECTED_EXCLUDED_GAMES = {
    20201160,
    20400335, 20400736,
    20500090, 20500102,
    20600887,
    20700319,
    20800032, 20800142,
    20900113,
    21000431, 21000997,
    21100842,
    21200919, 21201167,
    21301048,
    21400968,
    21500711, 21500903, 21500916,
    21600358, 21600655, 21600668,
    21701085,
    21800143,
    21901316, 21901317, 21901318,
    22000485, 22000853,
    22100688,
    22200140, 22200182, 22200207, 22200234, 22200778, 22201040,
    22300452, 22300599,
    22400433,
}


def pct(n: float, d: float):
    return n / d if d else None


def add_rates(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["team_rebounds_on"] = frame["team_oreb_on"] + frame["team_dreb_on"]
    frame["opponent_rebounds_on"] = frame["opponent_oreb_on"] + frame["opponent_dreb_on"]
    frame["team_rebounds_off"] = frame["team_oreb_off"] + frame["team_dreb_off"]
    frame["opponent_rebounds_off"] = frame["opponent_oreb_off"] + frame["opponent_dreb_off"]

    frame["treb_on"] = [pct(a, a+b) for a,b in zip(frame.team_rebounds_on, frame.opponent_rebounds_on)]
    frame["treb_off"] = [pct(a, a+b) for a,b in zip(frame.team_rebounds_off, frame.opponent_rebounds_off)]
    frame["treb_swing_pp"] = [((a-b)*100.0 if a is not None and b is not None else None) for a,b in zip(frame.treb_on, frame.treb_off)]

    frame["oreb_pct_on"] = [pct(a, a+b) for a,b in zip(frame.team_oreb_on, frame.opponent_dreb_on)]
    frame["oreb_pct_off"] = [pct(a, a+b) for a,b in zip(frame.team_oreb_off, frame.opponent_dreb_off)]
    frame["oreb_swing_pp"] = [((a-b)*100.0 if a is not None and b is not None else None) for a,b in zip(frame.oreb_pct_on, frame.oreb_pct_off)]
    frame["dreb_pct_on"] = [pct(a, a+b) for a,b in zip(frame.team_dreb_on, frame.opponent_oreb_on)]
    frame["dreb_pct_off"] = [pct(a, a+b) for a,b in zip(frame.team_dreb_off, frame.opponent_oreb_off)]
    frame["dreb_swing_pp"] = [((a-b)*100.0 if a is not None and b is not None else None) for a,b in zip(frame.dreb_pct_on, frame.dreb_pct_off)]
    return frame


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locks-dir", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    expected_seasons = [f"{y}-{(y+1)%100:02d}" for y in range(2000, 2026)]
    lock_files = sorted(args.locks_dir.glob("*.json"))
    locks = []
    for p in lock_files:
        d = json.loads(p.read_text(encoding="utf-8"))
        locks.append((p, d))

    found_seasons = [d.get("season") for _, d in locks]
    if sorted(found_seasons) != sorted(expected_seasons):
        raise SystemExit(f"season coverage mismatch: found={found_seasons}")

    rows = []
    exclusions = []
    lock_summary = []
    all_unmatched = []
    all_exceptions = []
    for path, d in locks:
        season = d["season"]
        if d.get("status") != "COMPLETE":
            raise SystemExit(f"non-COMPLETE lock: {season} {d.get('status')}")
        all_exceptions.extend({"season": season, **x} for x in d.get("exceptions", []))
        all_unmatched.extend({"season": season, **x} for x in d.get("unmatched_rebound_rows", []))
        for x in d.get("accepted_excluded_games", []):
            gid = int(x.get("game_id"))
            exclusions.append({"season": season, **x, "game_id": gid})
        lock_summary.append({
            "season": season,
            "status": d.get("status"),
            "engine": d.get("engine", "historical_recovered_completion"),
            "target_count": int(d.get("target_count", len(d.get("targets", [])))),
            "games_required": int(d.get("games_required", 0)),
            "accepted_excluded_games": len(d.get("accepted_excluded_games", [])),
            "exceptions": len(d.get("exceptions", [])),
            "unmatched_rebound_rows": len(d.get("unmatched_rebound_rows", [])),
            "code_commit": d.get("code_commit"),
        })
        for t in d.get("targets", []):
            r = dict(t)
            r["lock_engine"] = d.get("engine", "historical_recovered_completion")
            r["lock_code_commit"] = d.get("code_commit")
            rows.append(r)

    if all_exceptions:
        raise SystemExit(f"unexpected reconstruction exceptions remain: {len(all_exceptions)}")
    if all_unmatched:
        raise SystemExit(f"unexpected unmatched rebound rows remain: {len(all_unmatched)}")

    observed_excluded = {int(x["game_id"]) for x in exclusions}
    if observed_excluded != EXPECTED_EXCLUDED_GAMES:
        missing = sorted(EXPECTED_EXCLUDED_GAMES - observed_excluded)
        extra = sorted(observed_excluded - EXPECTED_EXCLUDED_GAMES)
        raise SystemExit(f"accepted exclusion ledger mismatch missing={missing} extra={extra}")

    detail = pd.DataFrame(rows)
    if detail.empty:
        raise SystemExit("no target rows")
    for c in COUNT_COLS + ["seconds_on", "seconds_off", "games_processed"]:
        detail[c] = pd.to_numeric(detail[c], errors="raise")

    detail["minutes_on"] = detail["seconds_on"] / 60.0
    detail["minutes_off"] = detail["seconds_off"] / 60.0
    detail = add_rates(detail)

    key_cols = [c for c in ["season", "player_id", "team_id", "segment_index", "query_start_date", "query_end_date"] if c in detail.columns]
    duplicate_keys = int(detail.duplicated(key_cols).sum())
    if duplicate_keys:
        raise SystemExit(f"duplicate tenure keys: {duplicate_keys}")

    def first_nonblank(s: pd.Series):
        for v in s:
            if pd.notna(v) and str(v).strip():
                return str(v)
        return ""

    grouped = detail.groupby("player_id", sort=True, dropna=False)
    career = grouped[COUNT_COLS + ["seconds_on", "seconds_off", "games_processed"]].sum().reset_index()
    names = grouped["player"].agg(first_nonblank).rename("player").reset_index()
    meta = grouped.agg(
        first_season=("season", "min"),
        last_season=("season", "max"),
        seasons=("season", "nunique"),
        teams=("team_abbr", "nunique"),
        tenure_segments=("season", "size"),
    ).reset_index()
    team_lists = grouped["team_abbr"].agg(lambda s: ",".join(sorted({str(x) for x in s if pd.notna(x)}))).rename("team_list").reset_index()
    career = career.merge(names, on="player_id", how="left", validate="one_to_one")
    career = career.merge(meta, on="player_id", how="left", validate="one_to_one")
    career = career.merge(team_lists, on="player_id", how="left", validate="one_to_one")
    career["minutes_on"] = career["seconds_on"] / 60.0
    career["minutes_off"] = career["seconds_off"] / 60.0
    career = add_rates(career)

    career_cols_front = ["player_id", "player", "first_season", "last_season", "seasons", "teams", "team_list", "tenure_segments", "games_processed", "minutes_on", "minutes_off"]
    career = career[career_cols_front + [c for c in career.columns if c not in career_cols_front and c not in {"seconds_on", "seconds_off"}] + ["seconds_on", "seconds_off"]]

    detail = detail.sort_values(["season", "player_id", "team_id", "query_start_date", "query_end_date"], kind="stable")
    career = career.sort_values(["minutes_on", "player_id"], ascending=[False, True], kind="stable")
    exclusions_df = pd.DataFrame(exclusions).sort_values(["season", "game_id"], kind="stable")
    locks_df = pd.DataFrame(lock_summary).sort_values("season", kind="stable")

    detail_path = args.output_dir / "career_treb_detail.csv"
    career_path = args.output_dir / "career_treb_summary.csv"
    exclusions_path = args.output_dir / "accepted_exclusions.csv"
    locks_path = args.output_dir / "season_lock_manifest.csv"
    db_path = args.output_dir / "treb_2000_01_to_2025_26.sqlite"

    detail.to_csv(detail_path, index=False)
    career.to_csv(career_path, index=False)
    exclusions_df.to_csv(exclusions_path, index=False)
    locks_df.to_csv(locks_path, index=False)

    with sqlite3.connect(db_path) as conn:
        detail.to_sql("tenure_detail", conn, index=False, if_exists="replace")
        career.to_sql("career_summary", conn, index=False, if_exists="replace")
        exclusions_df.to_sql("accepted_exclusions", conn, index=False, if_exists="replace")
        locks_df.to_sql("season_locks", conn, index=False, if_exists="replace")
        conn.execute("CREATE INDEX idx_tenure_player ON tenure_detail(player_id)")
        conn.execute("CREATE INDEX idx_tenure_season ON tenure_detail(season)")
        conn.execute("CREATE INDEX idx_career_minutes ON career_summary(minutes_on)")

    qa = {
        "status": "PASS",
        "seasons_expected": 26,
        "seasons_complete": len(locks),
        "first_season": min(found_seasons),
        "last_season": max(found_seasons),
        "tenure_detail_rows": int(len(detail)),
        "career_players": int(len(career)),
        "duplicate_tenure_keys": duplicate_keys,
        "unexpected_exceptions": 0,
        "unmatched_rebound_rows": 0,
        "accepted_excluded_games": len(exclusions_df),
        "accepted_exclusion_ledger_exact_match": True,
        "modern_2025_engine": next(d.get("engine") for _,d in locks if d.get("season") == "2025-26"),
        "modern_2025_policy": next(d.get("modern_bridge_audit", {}).get("policy") for _,d in locks if d.get("season") == "2025-26"),
        "outputs": [detail_path.name, career_path.name, exclusions_path.name, locks_path.name, db_path.name],
    }
    (args.output_dir / "qa_report.json").write_text(json.dumps(qa, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "README.txt").write_text(
        "TREB final database: regular season 2000-01 through 2025-26.\n"
        "career_treb_detail.csv = player/team/tenure-segment on/off rebound counts and rates.\n"
        "career_treb_summary.csv = career aggregation by player.\n"
        "treb_2000_01_to_2025_26.sqlite = same data in SQLite tables.\n"
        "accepted_exclusions.csv = exact documented source/lineup exception ledger.\n"
        "season_lock_manifest.csv = provenance for all 26 durable season locks.\n"
        "qa_report.json = final integrity gate results.\n",
        encoding="utf-8",
    )
    print(json.dumps(qa, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
