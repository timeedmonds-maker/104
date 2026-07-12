from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

TEAM_ID = 1610612745
SEASONS = ["2024-25", "2025-26"]
URL = "https://stats.nba.com/stats/teamplayeronoffsummary"
OUT = Path("data")
OUT.mkdir(exist_ok=True)

PARAMS_BASE = {
    "DateFrom": "",
    "DateTo": "",
    "GameSegment": "",
    "LastNGames": 0,
    "LeagueID": "00",
    "Location": "",
    "MeasureType": "Advanced",
    "Month": 0,
    "OpponentTeamID": 0,
    "Outcome": "",
    "PaceAdjust": "N",
    "PerMode": "Totals",
    "Period": 0,
    "PlusMinus": "N",
    "Rank": "N",
    "SeasonSegment": "",
    "SeasonType": "Regular Season",
    "TeamID": TEAM_ID,
    "VsConference": "",
    "VsDivision": "",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "Connection": "keep-alive",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def fetch_requests(params: dict[str, Any]) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.get(URL, params=params, headers=HEADERS, timeout=60)
            print("requests", r.status_code, r.url)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            print("requests failure", repr(exc))
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"requests failed: {last}")


def fetch_curl_cffi(params: dict[str, Any]) -> dict[str, Any]:
    from curl_cffi import requests as curl_requests

    last: Exception | None = None
    for attempt in range(4):
        try:
            r = curl_requests.get(
                URL,
                params=params,
                headers=HEADERS,
                impersonate="chrome",
                timeout=60,
            )
            print("curl_cffi", r.status_code, r.url)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            print("curl_cffi failure", repr(exc))
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"curl_cffi failed: {last}")


def fetch_season(season: str) -> dict[str, Any]:
    params = dict(PARAMS_BASE)
    params["Season"] = season
    try:
        return fetch_requests(params)
    except Exception as first:
        print("Falling back to curl_cffi after", repr(first))
        return fetch_curl_cffi(params)


def result_frame(payload: dict[str, Any], name: str) -> pd.DataFrame:
    sets = payload.get("resultSets") or payload.get("resultSet") or []
    if isinstance(sets, dict):
        sets = [sets]
    for item in sets:
        if item.get("name") == name:
            return pd.DataFrame(item.get("rowSet", []), columns=item.get("headers", []))
    names = [x.get("name") for x in sets]
    raise KeyError(f"Missing result set {name}; got {names}")


season_rows: list[pd.DataFrame] = []
for season in SEASONS:
    payload = fetch_season(season)
    (OUT / f"rockets_onoff_{season}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    on = result_frame(payload, "PlayersOnCourtTeamPlayerOnOffSummary")
    off = result_frame(payload, "PlayersOffCourtTeamPlayerOnOffSummary")

    keep = ["VS_PLAYER_ID", "VS_PLAYER_NAME", "MIN", "OFF_RATING", "DEF_RATING", "NET_RATING"]
    on = on[keep].rename(columns={
        "MIN": "MIN_ON",
        "OFF_RATING": "OFF_RATING_ON",
        "DEF_RATING": "DEF_RATING_ON",
        "NET_RATING": "NET_RATING_ON",
    })
    off = off[keep].rename(columns={
        "MIN": "MIN_OFF",
        "OFF_RATING": "OFF_RATING_OFF",
        "DEF_RATING": "DEF_RATING_OFF",
        "NET_RATING": "NET_RATING_OFF",
    })
    merged = on.merge(off, on=["VS_PLAYER_ID", "VS_PLAYER_NAME"], how="outer", validate="one_to_one")
    merged.insert(0, "SEASON", season)
    merged["NET_RATING_SWING"] = merged["NET_RATING_ON"] - merged["NET_RATING_OFF"]
    season_rows.append(merged)

per_season = pd.concat(season_rows, ignore_index=True)
per_season.to_csv(OUT / "rockets_onoff_per_season.csv", index=False)

# Combine seasons using the only weighting field exposed by this summary table: minutes.
# On and off ratings are weighted separately by their corresponding minutes.
def weighted(group: pd.DataFrame, value: str, weight: str) -> float | None:
    valid = group[[value, weight]].dropna()
    if valid.empty or valid[weight].sum() == 0:
        return None
    return float((valid[value] * valid[weight]).sum() / valid[weight].sum())

combined_records: list[dict[str, Any]] = []
for (pid, name), group in per_season.groupby(["VS_PLAYER_ID", "VS_PLAYER_NAME"], dropna=False):
    on_net = weighted(group, "NET_RATING_ON", "MIN_ON")
    off_net = weighted(group, "NET_RATING_OFF", "MIN_OFF")
    on_off = weighted(group, "OFF_RATING_ON", "MIN_ON")
    on_def = weighted(group, "DEF_RATING_ON", "MIN_ON")
    off_off = weighted(group, "OFF_RATING_OFF", "MIN_OFF")
    off_def = weighted(group, "DEF_RATING_OFF", "MIN_OFF")
    combined_records.append({
        "PLAYER_ID": pid,
        "PLAYER": name,
        "SEASONS_PLAYED_FOR_HOU": ", ".join(group.loc[group["MIN_ON"].fillna(0) > 0, "SEASON"].astype(str)),
        "MIN_ON": float(group["MIN_ON"].fillna(0).sum()),
        "MIN_OFF": float(group["MIN_OFF"].fillna(0).sum()),
        "OFF_RATING_ON": on_off,
        "DEF_RATING_ON": on_def,
        "NET_RATING_ON": on_net,
        "OFF_RATING_OFF": off_off,
        "DEF_RATING_OFF": off_def,
        "NET_RATING_OFF": off_net,
        "NET_RATING_SWING": None if on_net is None or off_net is None else on_net - off_net,
    })

combined = pd.DataFrame(combined_records).sort_values(
    ["NET_RATING_SWING", "MIN_ON"], ascending=[False, False], na_position="last"
)
num_cols = combined.select_dtypes(include="number").columns
combined[num_cols] = combined[num_cols].round(2)
combined.to_csv(OUT / "rockets_onoff_combined_2024-25_2025-26.csv", index=False)
print(combined.to_string(index=False))
