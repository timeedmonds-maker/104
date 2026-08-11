from __future__ import annotations

import gzip
import json
import re
import unicodedata
from datetime import timedelta
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
IMPACT = BASE / "impact_database"
ROSTER = IMPACT / "roster_tenure"
OUT = IMPACT / "roster_tenure_v2"
TX = ROSTER / "normalized_transactions.jsonl.gz"
GAMES_DIR = ROSTER / "regular_season_games_raw"
CORE_OUT = IMPACT / "outputs"
GATE = ROSTER / "ROSTER_REPAIR_READY_V2"

ORPHAN_KEYS = {
    ("2000-01", "145", 1610612747),
    ("2003-04", "1917", 1610612746),
}

SAFE_ALIASES = {
    "2436": {"flipmurray", "ronaldmurray"},
    "2747": {"jrsmith"},
    "120": {"stevensmith", "stevesmith"},
    "2369": {"normanrichardson", "normrichardson"},
    "202353": {"tiborpleiss"},
    "375": {"isaiahrider", "jrrider"},
    "221": {"clarweatherspoon", "clarenceweatherspoon"},
    "201173": {"marcuswilliams", "marcusewilliams"},
    "200754": {"mouhamedsene", "saersene"},
}

MANUAL_INTERVALS = {
    # NBA/Pelicans page dates the Haston waiver to Oct 29, 2003, not 2002.
    ("2002-03", "2213", 1610612740): "FULL",
    # Basketball-Reference 2002-03 Utah page has Oct 2 signing and no in-season departure.
    ("2002-03", "349", 1610612762): "FULL",
    # Continued contracts after incomplete 10-day transaction records.
    ("2004-05", "1983", 1610612752): [("2005-02-28", "SEASON_END", "continued_after_10day_verified")],
    ("2011-12", "202388", 1610612739): [("2012-03-16", "SEASON_END", "continued_after_10day_verified")],
    # Lance Thomas: trade to NYK, waived Jan 7; then two 10-days and rest-of-season deal.
    ("2014-15", "202498", 1610612752): [
        ("2015-01-06", "2015-01-07", "trade_then_waived"),
        ("2015-01-10", "SEASON_END", "10day_then_rest_of_season_verified"),
    ],
    # First of two Toronto 10-days; no season-close tenure.
    ("2010-11", "201234", 1610612761): [("2011-01-26", "2011-02-15", "two_10day_derived")],
}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def read_jsonl_gz(path: Path) -> list[dict]:
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def load_core() -> pd.DataFrame:
    parts = [pd.read_csv(p, compression="gzip") for p in sorted(CORE_OUT.glob("*/team_rebound_derived.csv.gz"))]
    core = pd.concat(parts, ignore_index=True)
    core["player_id"] = core["player_id"].astype(str)
    core["team_id"] = core["team_id"].astype(int)
    return core[core["seconds"].fillna(0) > 0].copy()


def load_games():
    team_games = {}
    season_bounds = {}
    team_abbr = {}
    for p in sorted(GAMES_DIR.glob("*.json.gz")):
        season = p.name.replace(".json.gz", "")
        with gzip.open(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
        dates = []
        for g in data["results"]:
            dt = pd.Timestamp(g["Date"]).date()
            dates.append(dt)
            gid = str(g["GameId"])
            for tid_key, abbr_key in (("HomeTeamId", "HomeTeamAbbreviation"), ("AwayTeamId", "AwayTeamAbbreviation")):
                tid = int(g[tid_key])
                team_games.setdefault((season, tid), []).append((dt, gid))
                team_abbr[(season, tid)] = g[abbr_key]
        season_bounds[season] = (min(dates), max(dates))
    for k in team_games:
        team_games[k].sort()
    return team_games, team_abbr, season_bounds


def load_transactions(core: pd.DataFrame) -> pd.DataFrame:
    tx = pd.DataFrame(read_jsonl_gz(TX))
    tx["player_id"] = tx["player_id"].astype(str)
    core_names = core.groupby("player_id")["player"].agg(lambda s: list(pd.unique(s.dropna().astype(str)))).to_dict()

    def valid(pid, txname):
        n = norm_name(txname)
        names = {norm_name(x) for x in core_names.get(pid, [])}
        return n in names or n in SAFE_ALIASES.get(pid, set())

    tx["valid_name"] = [valid(p, n) for p, n in zip(tx.player_id, tx.player_name)]
    return tx


def merge_intervals(ints):
    merged = []
    for a, b, reason in sorted(ints):
        if not merged or a > merged[-1][1] + timedelta(days=1):
            merged.append([a, b, reason])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    return [tuple(x) for x in merged]


def build() -> None:
    core = load_core()
    assert len(core) == 14526, len(core)
    team_games, team_abbr, season_bounds = load_games()
    tx = load_transactions(core)
    seasons = sorted(core.season.unique())
    prev_season = {s: seasons[i - 1] if i else None for i, s in enumerate(seasons)}
    core_teams = {(s, p): set(g.team_id.astype(int)) for (s, p), g in core.groupby(["season", "player_id"])}

    def events_for(season, pid, team_id):
        s0, e0 = season_bounds[season]
        g = tx[(tx.season == season) & (tx.player_id == pid) & tx.valid_name]
        out = []
        for _, r in g.iterrows():
            try:
                dt = pd.Timestamp(r.exact_date).date()
            except Exception:
                continue
            if not (s0 <= dt <= e0):
                continue
            txt = str(r.raw_text).lower()
            et = r.event_type
            src = None if pd.isna(r.source_team_id) else int(r.source_team_id)
            dst = None if pd.isna(r.destination_team_id) else int(r.destination_team_id)
            if et == "trade":
                if src == team_id:
                    out.append((dt, "out_trade", txt))
                if dst == team_id:
                    out.append((dt, "in_trade", txt))
            elif et in ("acquire", "claim"):
                if dst != team_id:
                    continue
                if "extension" in txt or (("re-signed" in txt or "resigned" in txt) and "not re-signed" not in txt):
                    continue
                prev = prev_season.get(season)
                if prev and team_id in core_teams.get((prev, pid), set()) and "multi-year contract" in txt:
                    continue
                out.append((dt, "in_acquire", txt))
            elif et == "depart" and src == team_id:
                out.append((dt, "out_depart", txt))
        dedup = []
        seen = set()
        for x in sorted(out, key=lambda z: (z[0], z[1], z[2])):
            if x not in seen:
                seen.add(x)
                dedup.append(x)
        return dedup

    def intervals_for(row):
        season, pid, team_id = row.season, str(row.player_id), int(row.team_id)
        s0, e0 = season_bounds[season]
        key = (season, pid, team_id)
        if key in MANUAL_INTERVALS:
            spec = MANUAL_INTERVALS[key]
            if spec == "FULL":
                return [(s0, e0, "manual_verified_full")]
            ans = []
            for a, b, reason in spec:
                aa = s0 if a == "SEASON_START" else pd.Timestamp(a).date()
                bb = e0 if b == "SEASON_END" else pd.Timestamp(b).date()
                ans.append((aa, bb, reason))
            return ans
        ev = events_for(season, pid, team_id)
        if not ev:
            return [(s0, e0, "full_no_inseason_event")]
        active = ev[0][1].startswith("out")
        cur = s0 if active else None
        ints = []
        for dt, typ, _ in ev:
            if typ == "in_trade":
                st = dt + timedelta(days=1)
                if not active:
                    active, cur = True, st
            elif typ == "in_acquire":
                if not active:
                    active, cur = True, dt
            elif active and cur is not None:
                if cur <= dt:
                    ints.append((cur, dt, typ))
                active, cur = False, None
        if active and cur is not None and cur <= e0:
            ints.append((cur, e0, "season_close"))
        return merge_intervals(ints)

    rows = []
    for r in core.itertuples(index=False):
        key = (r.season, str(r.player_id), int(r.team_id))
        if key in ORPHAN_KEYS:
            continue
        rows.append({
            "season": r.season,
            "player_id": str(r.player_id),
            "player": r.player,
            "team_id": int(r.team_id),
            "seconds_on": float(r.seconds),
            "minutes_on": float(r.seconds) / 60.0,
            "intervals": intervals_for(r),
        })
    assert len(rows) == 14524, len(rows)

    by_ps = {}
    for i, row in enumerate(rows):
        by_ps.setdefault((row["season"], row["player_id"]), []).append(i)

    # Same-calendar-day handoffs for ordinary sign/expiry moves are non-overlapping.
    for _, idxs in by_ps.items():
        start_map = {}
        for j in idxs:
            for seg in rows[j]["intervals"]:
                start_map.setdefault(seg[0], set()).add(j)
        for i in idxs:
            new_ints = []
            for a, b, reason in rows[i]["intervals"]:
                others = start_map.get(b, set()) - {i}
                if others and reason != "out_trade":
                    b = b - timedelta(days=1)
                if a <= b:
                    new_ints.append((a, b, reason))
            rows[i]["intervals"] = new_ints

    def game_ids(row, one_interval=None):
        ints = [one_interval] if one_interval else row["intervals"]
        return [gid for dt, gid in team_games[(row["season"], row["team_id"])] if any(a <= dt <= b for a, b, *_ in ints)]

    impossible, empty = [], []
    for row in rows:
        ids = game_ids(row)
        row["team_games_in_tenure"] = len(ids)
        row["total_team_games"] = len(team_games[(row["season"], row["team_id"])])
        row["full_core_reuse"] = len(ids) == row["total_team_games"]
        if not ids:
            empty.append((row["season"], row["player_id"], row["team_id"]))
        if row["minutes_on"] > len(ids) * 65.0 + 1.0:
            impossible.append((row["season"], row["player_id"], row["team_id"], row["minutes_on"], len(ids)))

    overlaps = []
    for (season, pid), idxs in by_ps.items():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                arow, brow = rows[idxs[x]], rows[idxs[y]]
                if arow["team_id"] == brow["team_id"]:
                    continue
                for a, b, *_ in arow["intervals"]:
                    for c, d, *_ in brow["intervals"]:
                        if a <= d and c <= b:
                            overlaps.append((season, pid, arow["team_id"], brow["team_id"], str(max(a, c)), str(min(b, d))))
    if impossible or empty or overlaps:
        raise RuntimeError(json.dumps({"impossible": impossible[:20], "empty": empty[:20], "overlaps": overlaps[:20]}, indent=2))

    full_rows = sum(1 for r in rows if r["full_core_reuse"])
    partial_rows = len(rows) - full_rows
    targets = []
    for row in rows:
        if row["full_core_reuse"]:
            continue
        game_segments = []
        for seg in row["intervals"]:
            ids = game_ids(row, seg)
            if ids:
                game_segments.append((seg, ids))
        count = len(game_segments)
        for idx, (seg, ids) in enumerate(game_segments, 1):
            a, b, reason = seg
            targets.append({
                "season": row["season"],
                "team_id": row["team_id"],
                "team_abbr": team_abbr.get((row["season"], row["team_id"])),
                "player_id": row["player_id"],
                "player": row["player"],
                "query_start_date": str(a),
                "query_end_date": str(b),
                "team_games_in_window": len(ids),
                "minutes_on": row["minutes_on"] if count == 1 else None,
                "segment_index": idx,
                "segment_count": count,
                "needs_on": bool(count > 1),
                "source": "roster_tenure_v2",
                "boundary_reason": reason,
            })

    assert full_rows == 9647, full_rows
    assert partial_rows == 4877, partial_rows
    assert len(targets) == 5199, len(targets)

    roster_rows = []
    for r in rows:
        roster_rows.append({
            **{k: v for k, v in r.items() if k != "intervals"},
            "intervals": [{"start": str(a), "end": str(b), "reason": reason} for a, b, reason in r["intervals"]],
        })

    OUT.mkdir(parents=True, exist_ok=True)
    write_jsonl_gz(OUT / "player_team_season_targets.jsonl.gz", roster_rows)
    write_jsonl_gz(OUT / "wowy_partial_segments.jsonl.gz", targets)
    summary = {
        "version": "roster_repair_v2",
        "positive_core_rows_original": 14526,
        "excluded_stale_core_artifacts": 2,
        "validated_player_team_season_rows": len(rows),
        "full_core_reuse_rows": full_rows,
        "partial_player_team_season_rows": partial_rows,
        "wowy_game_bearing_segments": len(targets),
        "cross_team_overlap_pairs": 0,
        "impossible_minute_rows": 0,
        "empty_played_tenures": 0,
        "transaction_identity_filter": "core player-id name + explicit safe aliases",
        "trade_day_policy": "outgoing team retains transaction date; incoming trade starts next calendar day",
        "ordinary_signing_policy": "same-day start; same-day prior-team expiry trimmed to previous date",
        "notes": [
            "Offseason transactions do not directly cut regular-season windows.",
            "Contract extensions and re-sign continuations do not create new roster-entry boundaries.",
            "Two stale core affiliation artifacts are excluded and retained in audit documentation.",
        ],
    }
    (OUT / "roster_repair_v2_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    GATE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    build()
