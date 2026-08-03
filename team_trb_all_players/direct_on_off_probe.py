from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.pbpstats.com/get-on-off/nba/player"
SEASON = os.getenv("PBPSTATS_PROBE_SEASON", "2025-26")
TEAM_ID = os.getenv("PBPSTATS_PROBE_TEAM_ID", "1610612745")
PLAYER_ID = os.getenv("PBPSTATS_PROBE_PLAYER_ID", "203500")
OUT = Path(os.getenv("PBPSTATS_ON_OFF_PROBE_OUT", "team_trb_all_players/direct_on_off_probe_output"))
OUT.mkdir(parents=True, exist_ok=True)

params = {
    "Season": SEASON,
    "SeasonType": "Regular Season",
    "TeamId": TEAM_ID,
    "PlayerId": PLAYER_ID,
}
headers = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.pbpstats.com",
    "Referer": "https://www.pbpstats.com/",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}

started = datetime.now(timezone.utc).isoformat()
response = requests.get(API, params=params, headers=headers, timeout=(10, 120))
result = {
    "started_utc": started,
    "completed_utc": datetime.now(timezone.utc).isoformat(),
    "request_url": response.url,
    "status_code": response.status_code,
    "season": SEASON,
    "team_id": TEAM_ID,
    "player_id": PLAYER_ID,
}
try:
    result["payload"] = response.json()
except Exception:
    result["response_text"] = response.text

output_path = OUT / "probe_result.json"
output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
print(f"status={response.status_code} evidence={output_path}")
response.raise_for_status()
