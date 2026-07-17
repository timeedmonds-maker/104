import csv
import json
import re
import time
from collections import defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment

PLAYERS = [
    ("Andre Drummond", "drumman01", 2013, 2026),
    ("DeAndre Jordan", "jordade01", 2009, 2026),
    ("Nikola Vucevic", "vucevni01", 2012, 2026),
    ("Rudy Gobert", "goberru01", 2014, 2026),
    ("Kevin Love", "loveke01", 2009, 2026),
    ("Al Horford", "horfoal01", 2008, 2026),
    ("Jonas Valanciunas", "valanjo01", 2013, 2026),
    ("Nikola Jokic", "jokicni01", 2016, 2026),
    ("Giannis Antetokounmpo", "antetgi01", 2014, 2026),
    ("Anthony Davis", "davisan02", 2013, 2026),
    ("Karl-Anthony Towns", "townska01", 2016, 2026),
    ("Clint Capela", "capelca01", 2015, 2026),
    ("Domantas Sabonis", "sabondo01", 2017, 2026),
    ("Julius Randle", "randlju01", 2015, 2026),
    ("Brook Lopez", "lopezbr01", 2009, 2026),
    ("Draymond Green", "greendr01", 2013, 2026),
    ("Steven Adams", "adamsst01", 2014, 2026),
    ("Tobias Harris", "harrito02", 2012, 2026),
    ("Mason Plumlee", "plumlma01", 2014, 2026),
    ("Bam Adebayo", "adebaba01", 2018, 2026),
    ("Taj Gibson", "gibsota01", 2010, 2026),
    ("Jarrett Allen", "allenja01", 2018, 2026),
    ("Jusuf Nurkic", "nurkiju01", 2015, 2026),
    ("Joel Embiid", "embiijo01", 2017, 2026),
    ("Ivica Zubac", "zubaciv01", 2017, 2026),
    ("Bismack Biyombo", "biyombi01", 2012, 2026),
    ("Bobby Portis", "portibo01", 2016, 2026),
]

BASE = "https://www.basketball-reference.com"
OUT_DETAIL = Path("career_treb_detail.csv")
OUT_SUMMARY = Path("career_treb_summary.csv")
OUT_META = Path("career_treb_metadata.json")
REQUEST_DELAY = 3.2

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
})
last_request = 0.0
request_log = []


def polite_get(url, max_attempts=6):
    global last_request
    for attempt in range(1, max_attempts + 1):
        wait = REQUEST_DELAY - (time.time() - last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            r = session.get(url, timeout=45)
            last_request = time.time()
            request_log.append({"url": url, "status": r.status_code, "attempt": attempt, "length": len(r.content)})
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(180, 20 * attempt))
                continue
            time.sleep(10 * attempt)
        except requests.RequestException as exc:
            last_request = time.time()
            request_log.append({"url": url, "status": "exception", "attempt": attempt, "error": repr(exc)})
            time.sleep(min(180, 20 * attempt))
    return None


def uncomment_tables(soup):
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if "<table" in c:
            c.replace_with(BeautifulSoup(c, "lxml"))
    return soup


def discover_years(player_id, fallback_start, fallback_end):
    letter = player_id[0]
    profile_url = f"{BASE}/players/{letter}/{player_id}.html"
    r = polite_get(profile_url)
    years = []
    if r is not None and r.status_code == 200:
        soup = BeautifulSoup(r.text, "lxml")
        pattern = re.compile(rf"/players/{letter}/{re.escape(player_id)}/on-off/(\d{{4}})")
        for a in soup.find_all("a", href=True):
            m = pattern.search(a["href"])
            if m:
                years.append(int(m.group(1)))
    years = sorted(set(years))
    if not years:
        years = list(range(fallback_start, fallback_end + 1))
    return years, profile_url


def parse_regular_season_page(player, player_id, year, url, html):
    soup = uncomment_tables(BeautifulSoup(html, "lxml"))
    table = soup.find("table", id="on-off")
    if table is None:
        return [], "regular-season table not found"

    parsed_rows = []
    pending_on = None
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 7:
            continue
        split = cells[0]
        if split not in ("On Court", "Off Court"):
            continue
        try:
            team = cells[1]
            mp = int(cells[2].replace(",", ""))
            trb = float(cells[6].replace("+", ""))
            orb = float(cells[4].replace("+", ""))
            drb = float(cells[5].replace("+", ""))
        except (ValueError, IndexError):
            continue

        row = {"team": team, "mp": mp, "trb_pct": trb, "orb_pct": orb, "drb_pct": drb}
        if split == "On Court":
            pending_on = row
        elif split == "Off Court" and pending_on is not None:
            if pending_on["team"] != team:
                pending_on = None
                continue
            parsed_rows.append({
                "Player": player,
                "Player ID": player_id,
                "Season": f"{year-1}-{str(year)[-2:]}",
                "Season End Year": year,
                "Team": team,
                "On MP": pending_on["mp"],
                "On ORB%": pending_on["orb_pct"],
                "On DRB%": pending_on["drb_pct"],
                "On TRB%": pending_on["trb_pct"],
                "Off MP": row["mp"],
                "Off ORB%": row["orb_pct"],
                "Off DRB%": row["drb_pct"],
                "Off TRB%": row["trb_pct"],
                "TRB% Swing": round(pending_on["trb_pct"] - row["trb_pct"], 1),
                "Source": url,
            })
            pending_on = None
    return parsed_rows, None if parsed_rows else "no on/off row pairs found"


def load_existing_detail():
    if not OUT_DETAIL.exists():
        return []
    with OUT_DETAIL.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_detail(rows):
    fields = [
        "Player", "Player ID", "Season", "Season End Year", "Team",
        "On MP", "On ORB%", "On DRB%", "On TRB%",
        "Off MP", "Off ORB%", "Off DRB%", "Off TRB%", "TRB% Swing", "Source"
    ]
    with OUT_DETAIL.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary(detail_rows):
    grouped = defaultdict(list)
    for row in detail_rows:
        grouped[row["Player"]].append(row)

    summary = []
    player_order = {name: idx for idx, (name, *_rest) in enumerate(PLAYERS)}
    for player, player_id, _start, _end in PLAYERS:
        rows = grouped.get(player, [])
        if not rows:
            summary.append({
                "Player": player, "Player ID": player_id, "First Season": "", "Last Season": "",
                "Seasons": 0, "Team Stints": 0, "On MP": 0, "Off MP": 0,
                "Career On TRB%": "", "Career Off TRB%": "", "Career TRB% Swing": "",
                "Coverage": "No data"
            })
            continue
        on_mp = sum(int(float(r["On MP"])) for r in rows)
        off_mp = sum(int(float(r["Off MP"])) for r in rows)
        on_num = sum(int(float(r["On MP"])) * float(r["On TRB%"]) for r in rows)
        off_num = sum(int(float(r["Off MP"])) * float(r["Off TRB%"]) for r in rows)
        on_pct = on_num / on_mp if on_mp else None
        off_pct = off_num / off_mp if off_mp else None
        swing = on_pct - off_pct if on_pct is not None and off_pct is not None else None
        years = sorted({int(float(r["Season End Year"])) for r in rows})
        summary.append({
            "Player": player,
            "Player ID": player_id,
            "First Season": f"{years[0]-1}-{str(years[0])[-2:]}",
            "Last Season": f"{years[-1]-1}-{str(years[-1])[-2:]}",
            "Seasons": len(years),
            "Team Stints": len(rows),
            "On MP": on_mp,
            "Off MP": off_mp,
            "Career On TRB%": round(on_pct, 1),
            "Career Off TRB%": round(off_pct, 1),
            "Career TRB% Swing": round(swing, 1),
            "Coverage": "Basketball-Reference regular-season on/off pages; minutes-weighted across team-season stints",
        })

    summary.sort(key=lambda r: (-999 if r["Career TRB% Swing"] == "" else -float(r["Career TRB% Swing"]), player_order[r["Player"]]))
    fields = [
        "Rank", "Player", "Player ID", "First Season", "Last Season", "Seasons", "Team Stints",
        "On MP", "Off MP", "Career On TRB%", "Career Off TRB%", "Career TRB% Swing", "Coverage"
    ]
    with OUT_SUMMARY.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        rank = 0
        for r in summary:
            if r["Career TRB% Swing"] != "":
                rank += 1
                r = {"Rank": rank, **r}
            else:
                r = {"Rank": "", **r}
            writer.writerow(r)
    return summary


def main():
    existing = load_existing_detail()
    detail_rows = []
    for r in existing:
        # Normalize types only when rewriting; preserve any completed rows.
        detail_rows.append(r)
    completed_pages = {(r["Player ID"], int(float(r["Season End Year"]))) for r in detail_rows}
    errors = []
    discovery = {}

    for idx, (player, player_id, start, end) in enumerate(PLAYERS, 1):
        years, profile_url = discover_years(player_id, start, end)
        years = [y for y in years if start <= y <= end]
        discovery[player] = {"player_id": player_id, "years": years, "profile_url": profile_url}
        print(f"[{idx}/{len(PLAYERS)}] {player}: {len(years)} seasons ({years[0] if years else 'none'}-{years[-1] if years else 'none'})", flush=True)
        for year in years:
            if (player_id, year) in completed_pages:
                continue
            letter = player_id[0]
            url = f"{BASE}/players/{letter}/{player_id}/on-off/{year}"
            r = polite_get(url)
            if r is None:
                errors.append({"player": player, "year": year, "url": url, "error": "request failed after retries"})
                continue
            if r.status_code == 404:
                errors.append({"player": player, "year": year, "url": url, "error": "404"})
                continue
            rows, error = parse_regular_season_page(player, player_id, year, url, r.text)
            if error:
                errors.append({"player": player, "year": year, "url": url, "error": error})
            else:
                detail_rows.extend(rows)
                completed_pages.add((player_id, year))
            # Save a local checkpoint after every page. The workflow commits at the end.
            detail_rows.sort(key=lambda x: (x["Player"], int(float(x["Season End Year"])), x["Team"]))
            write_detail(detail_rows)

    # Keep the user-specified player ordering in detail output, then chronological seasons.
    order = {name: i for i, (name, *_rest) in enumerate(PLAYERS)}
    detail_rows.sort(key=lambda x: (order.get(x["Player"], 999), int(float(x["Season End Year"])), x["Team"]))
    write_detail(detail_rows)
    summary = write_summary(detail_rows)
    metadata = {
        "source": "Basketball-Reference player regular-season on/off pages",
        "coverage": "1996-97 through 2025-26 where player seasons are available",
        "aggregation": "On and off TRB% are weighted separately by Basketball-Reference on/off minutes for every team-season stint; swing = weighted on minus weighted off.",
        "rounding": "Final percentages and swing rounded to one decimal because source percentages are displayed to one decimal.",
        "players_requested": len(PLAYERS),
        "players_with_data": sum(1 for r in summary if r["Career TRB% Swing"] != ""),
        "detail_rows": len(detail_rows),
        "discovery": discovery,
        "errors": errors,
        "requests": request_log,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    OUT_META.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: metadata[k] for k in ["players_requested", "players_with_data", "detail_rows", "generated_utc"]}, indent=2))
    print(f"Errors: {len(errors)}")


if __name__ == "__main__":
    main()
