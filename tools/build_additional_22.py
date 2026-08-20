from __future__ import annotations

import csv
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment

PLAYERS = [
    ("Tim Duncan", "duncati01", 1998, 2016),
    ("Dirk Nowitzki", "nowitdi01", 1999, 2019),
    ("Pau Gasol", "gasolpa01", 2002, 2019),
    ("Marc Gasol", "gasolma01", 2009, 2021),
    ("Dwight Howard", "howardw01", 2005, 2022),
    ("Tyson Chandler", "chandty01", 2002, 2020),
    ("Amar'e Stoudemire", "stoudam01", 2003, 2016),
    ("Zach Randolph", "randoza01", 2002, 2018),
    ("LaMarcus Aldridge", "aldrila01", 2007, 2022),
    ("Chris Bosh", "boshch01", 2004, 2016),
    ("David West", "westda01", 2004, 2018),
    ("Carlos Boozer", "boozeca01", 2003, 2015),
    ("Al Jefferson", "jeffeal01", 2005, 2018),
    ("Paul Millsap", "millspa01", 2007, 2022),
    ("Joakim Noah", "noahjo01", 2008, 2020),
    ("David Lee", "leeda02", 2006, 2017),
    ("Nene", "nenexx01", 2003, 2020),
    ("Elton Brand", "brandel01", 2000, 2016),
    ("Antawn Jamison", "jamisan01", 1999, 2014),
    ("Jermaine O'Neal", "onealje01", 1997, 2014),
    ("Ben Wallace", "wallabe01", 1997, 2012),
    ("Emeka Okafor", "okafoem01", 2005, 2018),
]

OUT = Path(os.environ.get("OUT_DIR", "expanded_output"))
CACHE = Path(os.environ.get("CACHE_DIR", "cache_bref"))
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; research data collection; public GitHub workflow)",
    "Accept-Language": "en-US,en;q=0.9",
})

DETAIL_FIELDS = [
    "Player", "Player ID", "Season", "Season End Year", "Team",
    "On MP", "On ORB%", "On DRB%", "On TRB%",
    "Off MP", "Off ORB%", "Off DRB%", "Off TRB%", "TRB% Swing", "Source",
]


def season_label(end_year: int) -> str:
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def safe_float(value: str) -> float:
    value = value.strip().replace("%", "").replace("+", "").replace("−", "-").replace("–", "-")
    if value in {"", "-", "—", "N/A"}:
        raise ValueError(value)
    return float(value)


def safe_int(value: str) -> int:
    return int(round(safe_float(value.replace(",", ""))))


def block_wait_seconds(text: str) -> int | None:
    m = re.search(r"blocked until ([A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} \d{4} \d{2}:\d{2}:\d{2}) GMT\+0000", text)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%a %b %d %Y %H:%M:%S").replace(tzinfo=timezone.utc)
    return max(1, int((dt - datetime.now(timezone.utc)).total_seconds()) + 4)


def request_text(url: str, attempts: int = 8, min_delay: float = 0.8) -> tuple[str, str]:
    key = re.sub(r"[^A-Za-z0-9._-]+", "_", url)[-180:]
    cache_path = CACHE / f"{key}.txt"
    meta_path = CACHE / f"{key}.json"
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"method": "cache"}
        return cache_path.read_text(encoding="utf-8", errors="replace"), meta.get("method", "cache")

    targets = [(url, "direct")]
    if "basketball-reference.com" in url:
        targets.append(("https://r.jina.ai/http://" + url.split("://", 1)[1], "jina"))

    last_error = ""
    for attempt in range(1, attempts + 1):
        for target, method in targets:
            try:
                r = SESSION.get(target, timeout=90)
                text = r.text
                if r.status_code == 200 and len(text) > 700 and not ("AbuseAlleviationError" in text or "ROBOTS_DENIED" in text):
                    cache_path.write_text(text, encoding="utf-8")
                    meta_path.write_text(json.dumps({"url": url, "target": target, "method": method, "status": r.status_code}, indent=2))
                    time.sleep(min_delay)
                    return text, method
                wait = block_wait_seconds(text)
                if wait is not None and wait <= 1800:
                    print(f"Jina block window detected; sleeping {wait}s", flush=True)
                    time.sleep(wait)
                    continue
                last_error = f"{method} HTTP {r.status_code}: {text[:240]}"
            except Exception as exc:
                last_error = f"{method}: {type(exc).__name__}: {exc}"
        sleep_s = min(60, 2 ** attempt)
        print(f"Fetch retry {attempt}/{attempts} for {url}: {last_error}; sleeping {sleep_s}s", flush=True)
        time.sleep(sleep_s)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def parse_markdown_rows(text: str) -> list[dict]:
    result = []
    pending = {}
    for line in text.splitlines():
        if "|" not in line or ("On Court" not in line and "Off Court" not in line):
            continue
        cells = [html.unescape(x.strip()) for x in line.strip().strip("|").split("|")]
        try:
            idx = next(i for i, c in enumerate(cells) if c in {"On Court", "Off Court"})
        except StopIteration:
            continue
        cells = cells[idx:]
        if len(cells) < 7:
            continue
        split = cells[0]
        team = re.sub(r"[^A-Z0-9]", "", cells[1])
        if not re.fullmatch(r"[A-Z]{2,3}", team):
            continue
        try:
            row = {"Team": team, "MP": safe_int(cells[2]), "ORB%": safe_float(cells[4]), "DRB%": safe_float(cells[5]), "TRB%": safe_float(cells[6])}
        except Exception:
            continue
        if split == "On Court":
            pending[team] = row
        elif team in pending:
            on = pending.pop(team)
            result.append({
                "Team": team,
                "On MP": on["MP"], "On ORB%": on["ORB%"], "On DRB%": on["DRB%"], "On TRB%": on["TRB%"],
                "Off MP": row["MP"], "Off ORB%": row["ORB%"], "Off DRB%": row["DRB%"], "Off TRB%": row["TRB%"],
            })
    return result


def parse_html_rows(text: str) -> list[dict]:
    soup = BeautifulSoup(text, "lxml")
    for comment in list(soup.find_all(string=lambda s: isinstance(s, Comment))):
        if "On Court" in comment and "Off Court" in comment:
            comment.replace_with(BeautifulSoup(str(comment), "lxml"))
    result = []
    pending = {}
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not cells or cells[0] not in {"On Court", "Off Court"} or len(cells) < 7:
            continue
        split, team = cells[0], re.sub(r"[^A-Z0-9]", "", cells[1])
        try:
            row = {"Team": team, "MP": safe_int(cells[2]), "ORB%": safe_float(cells[4]), "DRB%": safe_float(cells[5]), "TRB%": safe_float(cells[6])}
        except Exception:
            continue
        if split == "On Court":
            pending[team] = row
        elif team in pending:
            on = pending.pop(team)
            result.append({
                "Team": team,
                "On MP": on["MP"], "On ORB%": on["ORB%"], "On DRB%": on["DRB%"], "On TRB%": on["TRB%"],
                "Off MP": row["MP"], "Off ORB%": row["ORB%"], "Off DRB%": row["DRB%"], "Off TRB%": row["TRB%"],
            })
    return result


def parse_onoff(text: str) -> list[dict]:
    rows = parse_markdown_rows(text) or parse_html_rows(text)
    dedup = {}
    for r in rows:
        key = (r["Team"], r["On MP"], r["Off MP"], r["On ORB%"], r["Off ORB%"], r["On TRB%"], r["Off TRB%"])
        dedup[key] = r
    return list(dedup.values())


def fetch_csv(url: str) -> list[dict]:
    r = SESSION.get(url, timeout=90)
    r.raise_for_status()
    return list(csv.DictReader(r.text.splitlines()))


def build_team_totals(detail_rows: list[dict]) -> list[dict]:
    teams = fetch_csv("https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/Team%20Totals.csv")
    opps = fetch_csv("https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/Opponent%20Totals.csv")
    team_map = {(int(r["season"]), r["abbreviation"]): r for r in teams if r.get("lg") == "NBA" and r.get("abbreviation") not in {"NA", ""}}
    opp_map = {(int(r["season"]), r["abbreviation"]): r for r in opps if r.get("lg") == "NBA" and r.get("abbreviation") not in {"NA", ""}}
    alias = {"CHA": "CHO", "NOH": "NOP", "NOK": "NOP"}
    output, missing = [], []
    for year, team in sorted({(int(r["Season End Year"]), r["Team"]) for r in detail_rows}):
        t = o = None
        used = team
        for candidate in [team, alias.get(team, team)]:
            if (year, candidate) in team_map and (year, candidate) in opp_map:
                t, o, used = team_map[(year, candidate)], opp_map[(year, candidate)], candidate
                break
        if t is None:
            missing.append((year, team))
            continue
        team_orb, team_drb = int(float(t["orb"])), int(float(t["drb"]))
        opp_orb, opp_drb = int(float(o["orb"])), int(float(o["drb"]))
        output.append({
            "Season End Year": year, "Team": team, "Dataset Team": used,
            "Team ORB": team_orb, "Team DRB": team_drb, "Opponent ORB": opp_orb, "Opponent DRB": opp_drb,
            "OREB Opportunities": team_orb + opp_drb, "DREB Opportunities": team_drb + opp_orb,
            "Total Rebound Opportunities": team_orb + opp_drb + team_drb + opp_orb,
            "Source": "https://github.com/sumitrodatta/bball-reference-datasets",
        })
    if missing:
        (OUT / "missing_team_totals.json").write_text(json.dumps(missing, indent=2))
        raise RuntimeError(f"Missing team totals for {missing}")
    return output


def main() -> None:
    detail_rows, failures = [], []
    for pidx, (name, pid, first_end, last_end) in enumerate(PLAYERS, start=1):
        print(f"[{pidx}/{len(PLAYERS)}] {name}", flush=True)
        player_count = 0
        for year in range(first_end, last_end + 1):
            url = f"https://www.basketball-reference.com/players/{pid[0]}/{pid}/on-off/{year}"
            try:
                text, method = request_text(url)
                rows = parse_onoff(text)
                if not rows:
                    low = text.lower()
                    if any(token in low for token in ["page not found", "404", "no data", "did not play"]):
                        continue
                    debug = OUT / "unparsed"; debug.mkdir(exist_ok=True)
                    (debug / f"{pid}_{year}_{method}.txt").write_text(text, encoding="utf-8")
                    failures.append({"player": name, "player_id": pid, "year": year, "reason": "unparsed", "method": method})
                    continue
                for r in rows:
                    detail_rows.append({
                        "Player": name, "Player ID": pid, "Season": season_label(year), "Season End Year": year, "Team": r["Team"],
                        "On MP": r["On MP"], "On ORB%": r["On ORB%"], "On DRB%": r["On DRB%"], "On TRB%": r["On TRB%"],
                        "Off MP": r["Off MP"], "Off ORB%": r["Off ORB%"], "Off DRB%": r["Off DRB%"], "Off TRB%": r["Off TRB%"],
                        "TRB% Swing": r["On TRB%"] - r["Off TRB%"], "Source": url,
                    })
                    player_count += 1
            except Exception as exc:
                failures.append({"player": name, "player_id": pid, "year": year, "reason": f"{type(exc).__name__}: {exc}"})
                print(f"  {year}: {exc}", flush=True)
        print(f"  collected {player_count} team-season stints", flush=True)

    detail_rows.sort(key=lambda r: (r["Player"], int(r["Season End Year"]), r["Team"]))
    with (OUT / "additional_22_detail.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=DETAIL_FIELDS); w.writeheader(); w.writerows(detail_rows)
    (OUT / "scrape_failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
    counts = {name: sum(1 for r in detail_rows if r["Player"] == name) for name, *_ in PLAYERS}
    (OUT / "player_row_counts.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
    missing_players = [p for p, n in counts.items() if n == 0]
    if missing_players:
        raise RuntimeError(f"No parsed data for players: {missing_players}")

    totals = build_team_totals(detail_rows)
    total_fields = ["Season End Year", "Team", "Dataset Team", "Team ORB", "Team DRB", "Opponent ORB", "Opponent DRB", "OREB Opportunities", "DREB Opportunities", "Total Rebound Opportunities", "Source"]
    with (OUT / "additional_22_team_totals.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=total_fields); w.writeheader(); w.writerows(totals)

    manifest = {"players": len(PLAYERS), "team_season_stints": len(detail_rows), "team_seasons": len(totals), "failures": len(failures), "generated_at_utc": datetime.now(timezone.utc).isoformat()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
