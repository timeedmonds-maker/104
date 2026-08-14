#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
DIRECT_ATTEMPTS = 1
DIRECT_TIMEOUT_SECONDS = 4
RELAY_TIMEOUT_SECONDS = 8
TEAM_WORKERS = 4


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
    for attempt in range(DIRECT_ATTEMPTS):
        try:
            r = session.get(url, params=p, timeout=DIRECT_TIMEOUT_SECONDS)
            r.raise_for_status()
            d = r.json()
            choose_sets(d)
            cp.write_text(json.dumps(d))
            return d, "stats.nba.com/teamplayeronoffdetails"
        except Exception as exc:
            errors.append(f"direct[{attempt}]={exc!r}")
    try:
        relay = f"{JINA}/teamplayeronoffdetails?{urlencode(p)}"
        r = requests.get(relay, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=RELAY_TIMEOUT_SECONDS)
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


def write_checkpoint(
    output: Path,
    season: str,
    targets: list[dict],
    by_team: dict[int, list[dict]],
    output_rows: list[dict],
    team_report: list[dict],
    complete: bool,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "season", "team_id", "player_id", "player", "direct_treb_on", "direct_treb_off",
        "direct_minutes_on", "direct_minutes_off", "target_minutes_on", "status", "error", "source",
        "on_result_set", "off_result_set",
    ]
    df = pd.DataFrame(output_rows)
    if df.empty:
        df = pd.DataFrame(columns=columns)
    else:
        for c in columns:
            if c not in df.columns:
                df[c] = pd.NA
        df = df[columns]
        df = df.sort_values(["team_id", "player_id"], kind="stable").reset_index(drop=True)
    df.to_csv(output, index=False)
    reports = sorted(team_report, key=lambda x: int(x["team_id"]))
    report = {
        "season": season,
        "expected_full_core_rows": len(targets),
        "output_rows": len(df),
        "pass_rows": int(df.status.astype(str).eq("PASS").sum()) if "status" in df else 0,
        "failure_rows": int((~df.status.astype(str).eq("PASS")).sum()) if len(df) else 0,
        "team_count": len(by_team),
        "teams_completed": len(reports),
        "collection_complete": complete,
        "teams": reports,
        "transport_budget": {
            "direct_attempts": DIRECT_ATTEMPTS,
            "direct_timeout_seconds": DIRECT_TIMEOUT_SECONDS,
            "relay_timeout_seconds": RELAY_TIMEOUT_SECONDS,
            "team_workers": TEAM_WORKERS,
        },
        "source_semantics": "direct NBA Stats teamplayeronoffdetails Advanced REB_PCT from PlayersOnCourt and PlayersOffCourt result sets",
        "rounded_percentage_backsolve_used": False,
        "opponent_rebound_inference_used": False,
    }
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")


def collect_team(season: str, team_id: int, team_targets: list[dict], cache_dir: Path) -> tuple[int, list[dict], dict, str]:
    session = requests.Session()
    session.headers.update(HEADERS)
    team_rows: list[dict] = []
    team_status = "FETCH_FAIL"
    try:
        payload, source = fetch(session, season, team_id, cache_dir)
        on_rs, off_rs = choose_sets(payload)
        on_idx, off_idx = index_result(on_rs), index_result(off_rs)
        missing = 0
        for t in team_targets:
            pid = ids(t["player_id"])
            onrow, offrow = on_idx.get(pid), off_idx.get(pid)
            if onrow is None or offrow is None:
                missing += 1
                team_rows.append({
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
                team_rows.append({
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
                team_rows.append({
                    "season": season, "team_id": team_id, "player_id": pid,
                    "player": t.get("player", ""), "status": "INVALID_DIRECT_ROW",
                    "error": repr(exc), "source": source,
                })
        team_status = "PASS"
        team_report = {
            "season": season, "team_id": team_id, "status": "PASS",
            "target_rows": len(team_targets), "missing_rows": missing, "source": source,
        }
    except Exception as exc:
        error = repr(exc)
        team_report = {
            "season": season, "team_id": team_id, "status": "FETCH_FAIL",
            "target_rows": len(team_targets), "missing_rows": len(team_targets), "error": error,
        }
        for t in team_targets:
            team_rows.append({
                "season": season, "team_id": team_id, "player_id": ids(t["player_id"]),
                "player": t.get("player", ""), "status": "FETCH_FAIL", "error": error, "source": "",
            })
    finally:
        session.close()
    return team_id, team_rows, team_report, team_status


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

    output_rows: list[dict] = []
    team_report: list[dict] = []
    team_ids = sorted(by_team)
    write_checkpoint(args.output, season, targets, by_team, output_rows, team_report, complete=False)
    print(json.dumps({
        "event": "SEASON_START", "season": season, "team_count": len(team_ids),
        "team_workers": min(TEAM_WORKERS, len(team_ids)),
        "direct_timeout_seconds": DIRECT_TIMEOUT_SECONDS,
        "relay_timeout_seconds": RELAY_TIMEOUT_SECONDS,
    }), flush=True)

    with ThreadPoolExecutor(max_workers=min(TEAM_WORKERS, len(team_ids))) as pool:
        future_map = {}
        for team_id in team_ids:
            print(json.dumps({
                "event": "TEAM_SUBMIT", "season": season, "team_id": team_id,
                "target_rows": len(by_team[team_id]),
            }), flush=True)
            future = pool.submit(collect_team, season, team_id, by_team[team_id], args.cache_dir)
            future_map[future] = team_id

        completed = 0
        for future in as_completed(future_map):
            team_id = future_map[future]
            try:
                _, team_rows, report, team_status = future.result()
            except Exception as exc:
                error = repr(exc)
                team_rows = [{
                    "season": season, "team_id": team_id, "player_id": ids(t["player_id"]),
                    "player": t.get("player", ""), "status": "WORKER_FAIL", "error": error, "source": "",
                } for t in by_team[team_id]]
                report = {
                    "season": season, "team_id": team_id, "status": "WORKER_FAIL",
                    "target_rows": len(by_team[team_id]), "missing_rows": len(by_team[team_id]), "error": error,
                }
                team_status = "WORKER_FAIL"
            output_rows.extend(team_rows)
            team_report.append(report)
            completed += 1
            write_checkpoint(
                args.output, season, targets, by_team, output_rows, team_report,
                complete=(completed == len(team_ids)),
            )
            print(json.dumps({
                "event": "TEAM_CHECKPOINT", "season": season, "teams_completed": completed,
                "team_count": len(team_ids), "team_id": team_id, "team_status": team_status,
                "rows_checkpointed": len(output_rows),
            }), flush=True)

    df = pd.DataFrame(output_rows)
    print(json.dumps({
        "season": season,
        "expected_full_core_rows": len(targets),
        "pass_rows": int(df.status.astype(str).eq("PASS").sum()),
        "failure_rows": int((~df.status.astype(str).eq("PASS")).sum()),
        "team_count": len(by_team),
        "teams_completed": len(team_report),
        "team_workers": TEAM_WORKERS,
    }, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
