from __future__ import annotations

import csv
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Comment

PLAYERS = [
    ("Tim Duncan", "duncati01", "1997-98", "2015-16"),
    ("Dirk Nowitzki", "nowitdi01", "1998-99", "2018-19"),
    ("Pau Gasol", "gasolpa01", "2001-02", "2018-19"),
    ("Marc Gasol", "gasolma01", "2008-09", "2020-21"),
    ("Dwight Howard", "howardw01", "2004-05", "2021-22"),
    ("Tyson Chandler", "chandty01", "2001-02", "2019-20"),
    ("Amar'e Stoudemire", "stoudam01", "2002-03", "2015-16"),
    ("Zach Randolph", "randoza01", "2001-02", "2017-18"),
    ("LaMarcus Aldridge", "aldrila01", "2006-07", "2021-22"),
    ("Chris Bosh", "boshch01", "2003-04", "2015-16"),
    ("David West", "westda01", "2003-04", "2017-18"),
    ("Carlos Boozer", "boozeca01", "2002-03", "2014-15"),
    ("Al Jefferson", "jeffeal01", "2004-05", "2017-18"),
    ("Paul Millsap", "millspa01", "2006-07", "2021-22"),
    ("Joakim Noah", "noahjo01", "2007-08", "2019-20"),
    ("David Lee", "leeda02", "2005-06", "2016-17"),
    ("Nene", "nenexx01", "2002-03", "2019-20"),
    ("Elton Brand", "brandel01", "1999-00", "2015-16"),
    ("Antawn Jamison", "jamisan01", "1998-99", "2013-14"),
    ("Jermaine O'Neal", "onealje01", "1996-97", "2013-14"),
    ("Ben Wallace", "wallabe01", "1996-97", "2011-12"),
    ("Emeka Okafor", "okafoem01", "2004-05", "2017-18"),
]

OUT = Path("career_22_output")
OUT.mkdir(exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})


def block_wait_seconds(text: str) -> int | None:
    match = re.search(r"blocked until ([A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} \d{4} \d{2}:\d{2}:\d{2}) GMT\+0000", text)
    if not match:
        return None
    target = datetime.strptime(match.group(1), "%a %b %d %Y %H:%M:%S").replace(tzinfo=timezone.utc)
    return max(2, int((target - datetime.now(timezone.utc)).total_seconds()) + 5)


def fetch_page(url: str) -> str:
    jina = "https://r.jina.ai/http://" + url.split("://", 1)[1]
    last = ""
    for attempt in range(1, 6):
        try:
            response = SESSION.get(jina, timeout=120)
            text = response.text
            if response.status_code == 200 and len(text) > 1000 and "AbuseAlleviationError" not in text and "ROBOTS_DENIED" not in text:
                time.sleep(1.5)
                return text
            wait = block_wait_seconds(text)
            if wait and wait <= 1800:
                print(f"source throttle: waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            last = f"HTTP {response.status_code}: {text[:200]}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(min(45, 2 ** attempt))
    raise RuntimeError(last)


def clean_cell(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"[*_`]", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", value)
    return value.strip()


def parse_markdown(text: str) -> dict:
    # Keep only the career regular-season section when headings are present.
    regular = re.search(r"(?:##|###)\s*Career Regular Season(?: Table)?\s*(.*?)(?=(?:##|###)\s*Career Playoffs|\Z)", text, flags=re.S | re.I)
    section = regular.group(1) if regular else text
    found = {}
    for line in section.splitlines():
        if "|" not in line or not any(label in line for label in ("On Court", "Off Court", "On − Off", "On - Off")):
            continue
        cells = [clean_cell(cell) for cell in line.strip().strip("|").split("|")]
        label_index = next((i for i, cell in enumerate(cells) if cell in {"On Court", "Off Court", "On − Off", "On - Off"}), None)
        if label_index is None:
            continue
        cells = cells[label_index:]
        if len(cells) < 6:
            continue
        label = cells[0]
        if label in {"On Court", "Off Court"}:
            try:
                found[label] = {
                    "MP": int(float(cells[1].replace(",", ""))),
                    "ORB%": float(cells[3].replace("%", "")),
                    "DRB%": float(cells[4].replace("%", "")),
                    "TRB%": float(cells[5].replace("%", "")),
                }
            except (ValueError, IndexError):
                continue
    if "On Court" not in found or "Off Court" not in found:
        raise ValueError("career regular-season rows not found in markdown")
    return {"On": found["On Court"], "Off": found["Off Court"]}


def parse_html(text: str) -> dict:
    soup = BeautifulSoup(text, "lxml")
    for comment in list(soup.find_all(string=lambda node: isinstance(node, Comment))):
        if 'id="on-off"' in str(comment):
            comment.replace_with(BeautifulSoup(str(comment), "lxml"))
    table = soup.find("table", id="on-off")
    if table is None:
        raise ValueError("career regular-season table not found in HTML")
    found = {}
    for row in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if len(cells) < 6 or cells[0] not in {"On Court", "Off Court"}:
            continue
        found[cells[0]] = {"MP": int(cells[1].replace(",", "")), "ORB%": float(cells[3]), "DRB%": float(cells[4]), "TRB%": float(cells[5])}
    if "On Court" not in found or "Off Court" not in found:
        raise ValueError("career rows not found in HTML")
    return {"On": found["On Court"], "Off": found["Off Court"]}


def parse_career(text: str) -> dict:
    try:
        return parse_markdown(text)
    except Exception:
        return parse_html(text)


def main() -> None:
    rows = []
    failures = []
    for index, (player, player_id, first_season, last_season) in enumerate(PLAYERS, start=1):
        url = f"https://www.basketball-reference.com/players/{player_id[0]}/{player_id}/on-off/"
        print(f"[{index}/22] {player}", flush=True)
        try:
            text = fetch_page(url)
            parsed = parse_career(text)
            on, off = parsed["On"], parsed["Off"]
            rows.append({
                "Player": player,
                "Player ID": player_id,
                "First Season": first_season,
                "Last Season": last_season,
                "On MP": on["MP"],
                "Off MP": off["MP"],
                "Career On OREB%": on["ORB%"],
                "Career Off OREB%": off["ORB%"],
                "OREB% Swing": on["ORB%"] - off["ORB%"],
                "Career On DREB%": on["DRB%"],
                "Career Off DREB%": off["DRB%"],
                "DREB% Swing": on["DRB%"] - off["DRB%"],
                "Career On TRB%": on["TRB%"],
                "Career Off TRB%": off["TRB%"],
                "TRB% Swing": on["TRB%"] - off["TRB%"],
                "Source": url,
                "Method": "Basketball-Reference direct career regular-season On/Off table",
            })
        except Exception as exc:
            failures.append({"Player": player, "Player ID": player_id, "Error": f"{type(exc).__name__}: {exc}"})
            print(f"FAILED {player}: {exc}", flush=True)
    fields = [
        "Player", "Player ID", "First Season", "Last Season", "On MP", "Off MP",
        "Career On OREB%", "Career Off OREB%", "OREB% Swing",
        "Career On DREB%", "Career Off DREB%", "DREB% Swing",
        "Career On TRB%", "Career Off TRB%", "TRB% Swing", "Source", "Method",
    ]
    with (OUT / "career_22_direct.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
    manifest = {"expected_players": 22, "completed_players": len(rows), "failures": len(failures), "generated_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    if len(rows) != 22:
        raise RuntimeError(f"Expected 22 completed players, received {len(rows)}")


if __name__ == "__main__":
    main()
