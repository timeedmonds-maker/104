from __future__ import annotations

import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from build_career_22 import PLAYERS, parse_html

OUT = Path("career_22_widgets_output")
OUT.mkdir(exist_ok=True)


def fetch_one(item):
    player, player_id, first_season, last_season = item
    path = f"/players/{player_id[0]}/{player_id}/on-off/"
    widget = (
        "https://widgets.sports-reference.com/w.fcgi?css=1&site=bbr&url="
        + quote(path, safe="")
        + "&div=div_on-off"
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.basketball-reference.com/"})
    last = ""
    for attempt in range(1, 5):
        try:
            response = session.get(widget, timeout=120)
            if response.status_code == 200 and len(response.text) > 1000:
                parsed = parse_html(response.text)
                on, off = parsed["On"], parsed["Off"]
                source = f"https://www.basketball-reference.com{path}"
                return {
                    "Player": player, "Player ID": player_id,
                    "First Season": first_season, "Last Season": last_season,
                    "On MP": on["MP"], "Off MP": off["MP"],
                    "Career On OREB%": on["ORB%"], "Career Off OREB%": off["ORB%"], "OREB% Swing": on["ORB%"] - off["ORB%"],
                    "Career On DREB%": on["DRB%"], "Career Off DREB%": off["DRB%"], "DREB% Swing": on["DRB%"] - off["DRB%"],
                    "Career On TRB%": on["TRB%"], "Career Off TRB%": off["TRB%"], "TRB% Swing": on["TRB%"] - off["TRB%"],
                    "Source": source, "Widget URL": widget,
                    "Method": "Basketball-Reference direct career regular-season On/Off table via Sports Reference widget",
                }
            last = f"HTTP {response.status_code}, {len(response.text)} bytes: {response.text[:120]}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(min(20, 2 ** attempt))
    raise RuntimeError(f"{player}: {last}")


def main():
    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_one, item): item for item in PLAYERS}
        for future in as_completed(futures):
            item = futures[future]
            try:
                row = future.result()
                rows.append(row)
                print(f"completed {row['Player']}", flush=True)
            except Exception as exc:
                failures.append({"Player": item[0], "Player ID": item[1], "Error": str(exc)})
                print(f"FAILED {item[0]}: {exc}", flush=True)
    rows.sort(key=lambda row: row["Player"])
    fields = [
        "Player", "Player ID", "First Season", "Last Season", "On MP", "Off MP",
        "Career On OREB%", "Career Off OREB%", "OREB% Swing",
        "Career On DREB%", "Career Off DREB%", "DREB% Swing",
        "Career On TRB%", "Career Off TRB%", "TRB% Swing",
        "Source", "Widget URL", "Method",
    ]
    with (OUT / "career_22_direct.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    (OUT / "failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
    manifest = {"expected_players": 22, "completed_players": len(rows), "failures": len(failures), "generated_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    if len(rows) != 22:
        raise RuntimeError(f"Expected 22 completed players, received {len(rows)}")


if __name__ == "__main__":
    main()
