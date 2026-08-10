from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

from finalize_corrected_off_package_extended import rebound_candidates, integer, num

BASE = Path(__file__).resolve().parent
SRC = BASE / "impact_database" / "corrected_off" / "tenure_segment_on_off.jsonl.gz"
OUT = BASE / "impact_database" / "corrected_off" / "steven_adams_career_treb_query.json"
PLAYER_ID = "203500"
PLAYER_NAME = "Steven Adams"

# Same aliases and count-reconstruction logic as the extended finalizer, but only for Adams.
ALIASES = {
    "oreb": {"OffRebounds", "OffensiveRebounds", "OREB"},
    "dreb": {"DefRebounds", "DefensiveRebounds", "DREB"},
    "oreb_pct": {"OffReboundPct", "OffensiveReboundPct", "OREBPct"},
    "dreb_pct": {"DefReboundPct", "DefensiveReboundPct", "DREBPct"},
}


def main():
    segments = defaultdict(dict)
    with gzip.open(SRC, "rt", encoding="utf-8") as h:
        for line in h:
            if not line.strip():
                continue
            r = json.loads(line)
            if str(r.get("player_id")) != PLAYER_ID and str(r.get("player") or "").casefold() != PLAYER_NAME.casefold():
                continue
            metric = str(r.get("metric") or "")
            kind = next((k for k, names in ALIASES.items() if metric in names), None)
            if kind is None:
                continue
            key = (str(r.get("season")), int(r.get("team_id")), str(r.get("query_start_date")), str(r.get("query_end_date")))
            segments[key][kind] = r

    totals = {
        "team_rebounds_on": 0, "opponent_rebounds_on_min": 0, "opponent_rebounds_on_max": 0,
        "team_rebounds_off": 0, "opponent_rebounds_off_min": 0, "opponent_rebounds_off_max": 0,
        "minutes_on": 0.0, "minutes_off": 0.0, "segments": 0,
    }
    detail = []
    for key, d in sorted(segments.items()):
        if set(d) != set(ALIASES):
            continue
        base = d["oreb"]
        mins_on = num(base.get("minutes_on")) or 0.0
        mins_off = num(base.get("minutes_off")) or 0.0
        seg = {"season": key[0], "team_id": key[1], "start": key[2], "end": key[3], "minutes_on": mins_on, "minutes_off": mins_off}
        valid = True
        for side, source in (("on", "on"), ("off", "off_corrected")):
            own_oreb = integer(d["oreb"].get(source))
            own_dreb = integer(d["dreb"].get(source))
            if own_oreb is None or own_dreb is None:
                valid = False; break
            minutes = mins_on if side == "on" else mins_off
            cap = max(25, math.ceil(minutes * 2.5 + 30))
            opp_dreb = rebound_candidates(own_oreb, d["oreb_pct"].get(source), cap)
            opp_oreb = rebound_candidates(own_dreb, d["dreb_pct"].get(source), cap)
            combos = sorted({a+b for a in opp_oreb for b in opp_dreb})
            if not combos:
                valid = False; break
            own = own_oreb + own_dreb
            seg[f"team_rebounds_{side}"] = own
            seg[f"opp_rebounds_{side}_min"] = min(combos)
            seg[f"opp_rebounds_{side}_max"] = max(combos)
        if not valid:
            continue
        totals["segments"] += 1
        totals["minutes_on"] += mins_on
        totals["minutes_off"] += mins_off
        for side in ("on", "off"):
            totals[f"team_rebounds_{side}"] += seg[f"team_rebounds_{side}"]
            totals[f"opponent_rebounds_{side}_min"] += seg[f"opp_rebounds_{side}_min"]
            totals[f"opponent_rebounds_{side}_max"] += seg[f"opp_rebounds_{side}_max"]
        detail.append(seg)

    out = {"player_id": PLAYER_ID, "player": PLAYER_NAME, **totals}
    for side in ("on", "off"):
        own = totals[f"team_rebounds_{side}"]
        omin = totals[f"opponent_rebounds_{side}_min"]
        omax = totals[f"opponent_rebounds_{side}_max"]
        pmax = 100.0 * own / (own + omin)
        pmin = 100.0 * own / (own + omax)
        out[f"treb_pct_{side}_min"] = pmin
        out[f"treb_pct_{side}_max"] = pmax
        out[f"treb_pct_{side}_mid"] = (pmin + pmax) / 2.0
        out[f"treb_pct_{side}_exact"] = omin == omax
    out["treb_swing_mid"] = out["treb_pct_on_mid"] - out["treb_pct_off_mid"]
    out["treb_swing_min"] = out["treb_pct_on_min"] - out["treb_pct_off_max"]
    out["treb_swing_max"] = out["treb_pct_on_max"] - out["treb_pct_off_min"]
    out["detail"] = detail
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in out.items() if k != "detail"}, indent=2))

if __name__ == "__main__":
    main()
