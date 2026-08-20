from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path

import requests

OUT = Path('cohort_audit_output')
OUT.mkdir(exist_ok=True)
BASE = 'https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/'


def load(name):
    r = requests.get(BASE + name, timeout=180, headers={'User-Agent':'Mozilla/5.0'})
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def main():
    totals = load('Player%20Totals.csv')
    info = load('Player%20Season%20Info.csv')
    print('totals headers', totals[0].keys(), flush=True)
    print('info headers', info[0].keys(), flush=True)

    # NBA regular season only. For multi-team seasons, use the aggregate nTM row when present.
    by_ps = defaultdict(list)
    for r in totals:
        if r.get('lg') != 'NBA':
            continue
        by_ps[(r['player_id'], int(r['season']))].append(r)

    career = {}
    for (pid, season), rows in by_ps.items():
        totrows = [r for r in rows if re.fullmatch(r'\d+TM', r.get('team',''))]
        use = totrows[:1] if totrows else rows
        trb = sum(int(float(r.get('trb') or 0)) for r in use)
        rec = career.setdefault(pid, {'player': rows[0]['player'], 'player_id': pid, 'first_season': season, 'last_season': season, 'trb': 0})
        rec['first_season'] = min(rec['first_season'], season)
        rec['last_season'] = max(rec['last_season'], season)
        rec['trb'] += trb

    positions = defaultdict(list)
    for r in info:
        if r.get('lg') == 'NBA':
            positions[r['player_id']].append((int(r['season']), r.get('pos',''), r.get('team','')))

    rows = []
    for pid, rec in career.items():
        if rec['first_season'] < 1997 or rec['trb'] < 5000:
            continue
        posvals = positions.get(pid, [])
        unique_pos = sorted({p for _,p,_ in posvals if p})
        big_seasons = sum(1 for _,p,_ in posvals if re.search(r'(^|-)PF($|-)|(^|-)C($|-)', p))
        total_seasons = len({s for s,_,_ in posvals})
        rows.append({
            **rec,
            'rookie_season': f"{rec['first_season']-1}-{str(rec['first_season'])[-2:]}",
            'positions': ','.join(unique_pos),
            'big_position_seasons': big_seasons,
            'nba_seasons': total_seasons,
            'big_position_share': big_seasons/total_seasons if total_seasons else 0,
            'has_pf_or_c': bool(big_seasons),
        })
    rows.sort(key=lambda r: (-r['trb'], r['player']))
    fields = list(rows[0].keys())
    with (OUT/'eligible_5000_rebounds_since_1996_97.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    (OUT/'manifest.json').write_text(json.dumps({'eligible_with_any_pf_c': sum(r['has_pf_or_c'] for r in rows), 'all_5000_since_1997': len(rows)},indent=2))
    for r in rows:
        print(r['player'], r['trb'], r['rookie_season'], r['positions'], f"bigshare={r['big_position_share']:.2f}", flush=True)

if __name__=='__main__':
    main()
