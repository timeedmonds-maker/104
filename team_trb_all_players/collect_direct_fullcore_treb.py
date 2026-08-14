#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

BASE = "https://stats.nba.com/stats"
JINA = "https://r.jina.ai/http://stats.nba.com/stats"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def ids(v) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def pct(v) -> float:
    x = float(v)
    if x > 1.5:
        x /= 100.0
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"invalid percentage {v}")
    return x


def params(season: str, team_id: int) -> dict[str, object]:
    return {
        "DateFrom": "", "DateTo": "", "GameSegment": "", "LastNGames": 0,
        "LeagueID": "00", "Location": "", "MeasureType": "Advanced", "Month": 0,
        "OpponentTeamID": 0, "Outcome": "", "PORound": 0, "PaceAdjust": "N",
        "PerMode": "Totals", "Period": 0, "PlusMinus": "N", "Rank": "N",
        "Season": season, "SeasonSegment": "", "SeasonType": "Regular Season",
        "ShotClockRange": "", "TeamID": int(team_id), "VsConference": "", "VsDivision": "",
    }


def result_sets(payload: dict) -> list[dict]:
    value = payload.get("resultSets", payload.get("resultSet"))
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("No resultSets")
    return value


def rows(rs: dict) -> list[dict]:
    headers = rs.get("headers") or []
    return [dict(zip(headers, r)) for r in rs.get("rowSet", [])]


def choose_sets(payload: dict) -> tuple[dict, dict]:
    sets = result_sets(payload)
    by_name = {str(x.get("name", "")): x for x in sets}
    on = by_name.get("PlayersOnCourtTeamPlayerOnOffDetails")
    off = by_name.get("PlayersOffCourtTeamPlayerOnOffDetails")
    if on is None:
        hits = [x for x in sets if "playersoncourt" in str(x.get("name", "")).lower()]
        on = hits[0] if len(hits) == 1 else None
    if off is None:
        hits = [x for x in sets if "playersoffcourt" in str(x.get("name", "")).lower()]
        off = hits[0] if len(hits) == 1 else None
    if on is None or off is None:
        raise ValueError(f"Required ON/OFF result sets absent; available={[x.get('name') for x in sets]}")
    return on, off


def parse_payload_text(text: str) -> dict:
    t = text.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    candidates = re.findall(r"\{.*?\}", t, flags=re.S)
    for raw in reversed(candidates):
        try:
            d = json.loads(raw)
            result_sets(d)
            return d
        except Exception:
            continue
    m = re.search(r"(\{.*\"resultSets\".*\})", t, flags=re.S)
    if m:
        return json.loads(m.group(1))
    raise ValueError("No NBA JSON found in relay response")


def fetch(session: requests.Session, season: str, team_id: int, cache_dir: Path) -> tuple[dict, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_dir / f"{season}_{team_id}.json"
    if cp.exists():
        return json.loads(cp.read_text()), "runner_cache"
    p = params(season, team_id)
    errors: list[str] = []
    url = f"{BASE}/teamplayeronoffdetails"
    for attempt in range(3):
        try:
            r = session.get(url, params=p, timeout=50)
            r.raise_for_status()
            d = r.json()
            choose_sets(d)
            cp.write_text(json.dumps(d))
            return d, "stats.nba.com/teamplayeronoffdetails"
        except Exception as exc:
            errors.append(f"direct[{attempt}]={exc!r}")
            time.sleep((1.2 * (2 ** attempt)) + random.random() * 0.4)
    try:
        relay = f"{JINA}/teamplayeronoffdetails?{urlencode(p)}"
        r = requests.get(relay, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=120)
        r.raise_for_status()
        d = parse_payload_text(r.text)
        choose_sets(d)
        cp.write_text(json.dumps(d))
        return d, "r.jina.ai/http://stats.nba.com/teamplayeronoffdetails"
    except Exception as exc:
        errors.append(f"relay={exc!r}")
    raise RuntimeError("; ".join(errors))


def index_result(rs: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows(rs):
        pid = r.get("VS_PLAYER_ID", r.get("PLAYER_ID", r.get("PlayerId")))
        if pid is None:
            continue
        out[ids(pid)] = r
    return out


def field(row: dict, *names: str):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def read_targets(path: Path, season: str) -> list[dict]:
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if str(r.get("season")) == season and bool(r.get("full_core_reuse")):
                out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--cache-dir", type=Path, required=True)
    args = ap.parse_args()
    season = f"{args.year}-{(args.year + 1) % 100:02d}"
    targets = read_targets(args.targets, season)
    if not targets:
        raise RuntimeError(f"No full-core targets for {season}")
    by_team: dict[int, list[dict]] = {}
    for r in targets:
        by_team.setdefault(int(r["team_id"]), []).append(r)

    session = requests.Session()
    session.headers.update(HEADERS)
    output_rows: list[dict] = []
    team_report: list[dict] = []
    for team_id in sorted(by_team):
        team_targets = by_team[team_id]
        try:
            payload, source = fetch(session, season, team_id, args.cache_dir)
            on_rs, off_rs = choose_sets(payload)
            on_idx, off_idx = index_result(on_rs), index_result(off_rs)
            missing = 0
            for t in team_targets:
                pid = ids(t["player_id"])
                onrow, offrow = on_idx.get(pid), off_idx.get(pid)
                if onrow is None or offrow is None:
                    missing += 1
                    output_rows.append({
                        "season": season, "team_id": team_id, "player_id": pid,
                        "player": t.get("player", ""), "status": "MISSING_PLAYER_ROW",
                        "error": f"on_present={onrow is not None};off_present={offrow is not None}",
                        "source": source,
                    })
                    continue
                try:
                    on_pct = pct(field(onrow, "REB_PCT", "TEAM_REB_PCT"))
                    off_pct = pct(field(offrow, "REB_PCT", "TEAM_REB_PCT"))
                    on_min = float(field(onrow, "MIN", "MINUTES") or 0.0)
                    off_min = float(field(offrow, "MIN", "MINUTES") or 0.0)
                    if not (0.25 <= on_pct <= 0.75 and 0.25 <= off_pct <= 0.75):
                        raise ValueError(f"implausible TREB pct on={on_pct} off={off_pct}")
                    output_rows.append({
                        "season": season, "team_id": team_id, "player_id": pid,
                        "player": t.get("player", field(onrow, "VS_PLAYER_NAME", "PLAYER_NAME") or ""),
                        "direct_treb_on": on_pct, "direct_treb_off": off_pct,
                        "direct_minutes_on": on_min, "direct_minutes_off": off_min,
                        "target_minutes_on": float(t.get("seconds_on", 0.0)) / 60.0,
                        "status": "PASS", "error": "", "source": source,
                        "on_result_set": str(on_rs.get("name", "")),
                        "off_result_set": str(off_rs.get("name", "")),
                    })
                except Exception as exc:
                    output_rows.append({
                        "season": season, "team_id": team_id, "player_id": pid,
                        "player": t.get("player", ""), "status": "INVALID_DIRECT_ROW",
                        "error": repr(exc), "source": source,
                    })
            team_report.append({"season": season, "team_id": team_id, "status": "PASS", "target_rows": len(team_targets), "missing_rows": missing, "source": source})
        except Exception as exc:
            team_report.append({"season": season, "team_id": team_id, "status": "FETCH_FAIL", "target_rows": len(team_targets), "missing_rows": len(team_targets), "error": repr(exc)})
            for t in team_targets:
                output_rows.append({
                    "season": season, "team_id": team_id, "player_id": ids(t["player_id"]),
                    "player": t.get("player", ""), "status": "FETCH_FAIL", "error": repr(exc), "source": "",
                })

    df = pd.DataFrame(output_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    report = {
        "season": season,
        "expected_full_core_rows": len(targets),
        "output_rows": len(df),
        "pass_rows": int(df.status.eq("PASS").sum()),
        "failure_rows": int((~df.status.eq("PASS")).sum()),
        "team_count": len(by_team),
        "teams": team_report,
        "source_semantics": "direct NBA Stats teamplayeronoffdetails Advanced REB_PCT from PlayersOnCourt and PlayersOffCourt result sets",
        "rounded_percentage_backsolve_used": False,
        "opponent_rebound_inference_used": False,
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ["season", "expected_full_core_rows", "pass_rows", "failure_rows", "team_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
