from __future__ import annotations

import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, Comment

PLAYERS = [
    ("Tim Duncan", "duncati01", "1997-98", 1998, 2016),
    ("Dirk Nowitzki", "nowitdi01", "1998-99", 1999, 2019),
    ("Pau Gasol", "gasolpa01", "2001-02", 2002, 2019),
    ("Marc Gasol", "gasolma01", "2008-09", 2009, 2021),
    ("Dwight Howard", "howardw01", "2004-05", 2005, 2022),
    ("Tyson Chandler", "chandty01", "2001-02", 2002, 2020),
    ("Amar'e Stoudemire", "stoudam01", "2002-03", 2003, 2016),
    ("Zach Randolph", "randoza01", "2001-02", 2002, 2018),
    ("LaMarcus Aldridge", "aldrila01", "2006-07", 2007, 2022),
    ("Chris Bosh", "boshch01", "2003-04", 2004, 2016),
    ("David West", "westda01", "2003-04", 2004, 2018),
    ("Carlos Boozer", "boozeca01", "2002-03", 2003, 2015),
    ("Al Jefferson", "jeffeal01", "2004-05", 2005, 2018),
    ("Paul Millsap", "millspa01", "2006-07", 2007, 2022),
    ("Joakim Noah", "noahjo01", "2007-08", 2008, 2020),
    ("David Lee", "leeda02", "2005-06", 2006, 2017),
    ("Nene", "nenexx01", "2002-03", 2003, 2020),
    ("Elton Brand", "brandel01", "1999-00", 2000, 2016),
    ("Antawn Jamison", "jamisan01", "1998-99", 1999, 2014),
    ("Jermaine O'Neal", "onealje01", "1996-97", 1997, 2014),
    ("Ben Wallace", "wallabe01", "1996-97", 1997, 2012),
    ("Emeka Okafor", "okafoem01", "2004-05", 2005, 2018),
]

OUT = Path("wayback_career_output")
OUT.mkdir(exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; basketball research; +https://github.com/timeedmonds-maker/104)"})


def get_json(url: str, attempts: int = 5):
    last = None
    for i in range(attempts):
        try:
            r = SESSION.get(url, timeout=90)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            last = e
        time.sleep(1.2 * (i + 1))
    raise last or RuntimeError("request failed")


def get_text(url: str, attempts: int = 5):
    last = None
    for i in range(attempts):
        try:
            r = SESSION.get(url, timeout=120)
            if r.status_code == 200 and len(r.text) > 5000:
                return r.text
            last = RuntimeError(f"HTTP {r.status_code}, len={len(r.text)}: {r.text[:200]}")
        except Exception as e:
            last = e
        time.sleep(1.2 * (i + 1))
    raise last or RuntimeError("request failed")


def query_cdx(base: str):
    q = (
        "https://web.archive.org/cdx/search/cdx?url=" + quote(base, safe=":/")
        + "&output=json&filter=statuscode:200&filter=mimetype:text/html"
        + "&fl=timestamp,original,statuscode,digest&collapse=digest&from=2012&to=2026"
    )
    data = get_json(q)
    return data[1:] if len(data) > 1 else []


def cdx_rows(player_id: str):
    path = f"/players/{player_id[0]}/{player_id}/on-off/"
    bases = [
        "http://www.basketball-reference.com" + path,
        "https://www.basketball-reference.com" + path,
        "http://basketball-reference.com" + path,
        "https://basketball-reference.com" + path,
        "http://www.basketball-reference.com" + path.rstrip("/"),
        "https://www.basketball-reference.com" + path.rstrip("/"),
    ]
    found = {}
    for base in bases:
        try:
            for row in query_cdx(base):
                found[(row[0], row[1])] = row
        except Exception as exc:
            print("CDX variant failed", player_id, base, exc, flush=True)
    return sorted(found.values(), key=lambda x: x[0])


def parse_table(text: str):
    soup = BeautifulSoup(text, "lxml")
    # Some BRef eras wrapped tables in HTML comments.
    for comment in list(soup.find_all(string=lambda s: isinstance(s, Comment))):
        if "On Court" in comment and "Off Court" in comment and "ORB%" in comment:
            comment.replace_with(BeautifulSoup(str(comment), "lxml"))

    candidates = []
    preferred = soup.find("table", id="on-off")
    if preferred is not None:
        candidates.append(preferred)
    candidates.extend(t for t in soup.find_all("table") if t is not preferred)

    parsed_rows = None
    for table in candidates:
        rows = {}
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            label = cells[0].get_text(" ", strip=True).replace("−", "-")
            if label not in {"On Court", "Off Court", "On - Off"}:
                continue
            vals = {}
            with_stats = any(c.get("data-stat") for c in cells)
            if with_stats:
                for cell in cells:
                    stat = cell.get("data-stat")
                    if stat:
                        vals[stat] = cell.get_text(" ", strip=True).replace("+", "").replace("−", "-")
            else:
                raw = [c.get_text(" ", strip=True).replace("+", "").replace("−", "-") for c in cells]
                # Historical BRef table column order: split, MP, eFG%, ORB%, DRB%, TRB%, ...
                if len(raw) >= 6:
                    vals = {"split_id": raw[0], "mp": raw[1], "orb_pct": raw[3], "drb_pct": raw[4], "trb_pct": raw[5]}
            if vals:
                rows[label] = vals
        if "On Court" in rows and "Off Court" in rows:
            parsed_rows = rows
            break
    if not parsed_rows:
        return None

    on, off = parsed_rows["On Court"], parsed_rows["Off Court"]
    note = ""
    for node in soup.find_all(string=re.compile(r"Play-by-play data available")):
        note = node.strip()
        break
    def f(d, k): return float(d[k])
    def i(d, k): return int(d[k].replace(",", ""))
    coverage_end = None
    m = re.search(r"through\s+(\d{4})-(\d{2})", note)
    if m:
        coverage_end = int(m.group(1)) + 1
    return {
        "On MP": i(on, "mp"), "Off MP": i(off, "mp"),
        "On OREB%": f(on, "orb_pct"), "Off OREB%": f(off, "orb_pct"),
        "OREB% Swing": f(on, "orb_pct") - f(off, "orb_pct"),
        "On DREB%": f(on, "drb_pct"), "Off DREB%": f(off, "drb_pct"),
        "DREB% Swing": f(on, "drb_pct") - f(off, "drb_pct"),
        "On TRB%": f(on, "trb_pct"), "Off TRB%": f(off, "trb_pct"),
        "TRB% Swing": f(on, "trb_pct") - f(off, "trb_pct"),
        "Coverage Note": note, "Coverage End Year": coverage_end,
    }


def collect(player):
    name, pid, rookie, first_end, last_end = player
    rows = cdx_rows(pid)
    errors = []
    best = None
    for timestamp, original, _status, _digest in reversed(rows):
        snap = f"https://web.archive.org/web/{timestamp}id_/{original}"
        try:
            text = get_text(snap, attempts=3)
            parsed = parse_table(text)
            if parsed:
                parsed.update({
                    "Player": name, "Player ID": pid, "Rookie Season": rookie,
                    "First Season End": first_end, "Last Season End": last_end,
                    "Snapshot Timestamp": timestamp, "Source": snap,
                    "Published Precision": "One decimal",
                    "Data Method": "Archived Basketball-Reference career on/off table",
                })
                best = parsed
                # Stop when archived coverage reaches the player's final NBA season.
                if parsed.get("Coverage End Year") and parsed["Coverage End Year"] >= last_end:
                    return parsed
                # Otherwise latest parseable snapshot is still the best fallback.
                return parsed
            errors.append(f"{timestamp}: table not parsed")
        except Exception as exc:
            errors.append(f"{timestamp}: {type(exc).__name__}: {exc}")
    if best:
        return best
    raise RuntimeError(f"{name}: no usable snapshot. Last errors: {errors[-3:]}; CDX rows={len(rows)}")


def main():
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(collect, p): p for p in PLAYERS}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                row = fut.result()
                results.append(row)
                print("OK", p[0], row["Snapshot Timestamp"], row["On MP"], row.get("Coverage End Year"), flush=True)
            except Exception as exc:
                failures.append({"player": p[0], "player_id": p[1], "error": str(exc)})
                print("FAIL", p[0], exc, flush=True)
    results.sort(key=lambda x: x["Player"])
    fields = [
        "Player", "Player ID", "Rookie Season", "First Season End", "Last Season End",
        "On MP", "Off MP", "On OREB%", "Off OREB%", "OREB% Swing",
        "On DREB%", "Off DREB%", "DREB% Swing", "On TRB%", "Off TRB%", "TRB% Swing",
        "Coverage End Year", "Coverage Note", "Published Precision", "Data Method", "Snapshot Timestamp", "Source",
    ]
    with (OUT / "additional_22_career_onoff.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(results)
    (OUT / "failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
    (OUT / "manifest.json").write_text(json.dumps({"expected": len(PLAYERS), "completed": len(results), "failed": len(failures)}, indent=2), encoding="utf-8")
    if failures:
        raise SystemExit(f"Failed {len(failures)} players")


if __name__ == "__main__":
    main()
