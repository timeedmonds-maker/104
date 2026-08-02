import json
from pathlib import Path
import requests

OUT = Path('team_trb_all_players/output')
OUT.mkdir(parents=True, exist_ok=True)

URL = 'https://api.pbpstats.com/get-totals/nba'
BASE = {
    'Season': '2025-26',
    'SeasonType': 'Regular Season',
}
HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://www.pbpstats.com',
    'Referer': 'https://www.pbpstats.com/totals/nba/lineup?Season=2025-26&SeasonType=Regular%2BSeason',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
}


def run(label, **params):
    query = {**BASE, **params}
    r = requests.get(URL, params=query, headers=HEADERS, timeout=90)
    item = {
        'label': label,
        'request_url': r.url,
        'status': r.status_code,
        'content_type': r.headers.get('content-type'),
        'bytes': len(r.content),
    }
    try:
        payload = r.json()
    except Exception:
        item['body_preview'] = r.text[:1000]
        return item
    item['top_level_keys'] = sorted(payload) if isinstance(payload, dict) else []
    rows = payload.get('multi_row_table_data', []) if isinstance(payload, dict) else []
    item['row_count'] = len(rows) if isinstance(rows, list) else None
    item['row_keys'] = sorted(rows[0]) if isinstance(rows, list) and rows else []
    item['sample_rows'] = rows[:3] if isinstance(rows, list) else None
    if r.status_code >= 400:
        item['error_payload'] = payload
    return item

results = [
    run('league_lineup', Type='Lineup'),
    run('hou_lineup', Type='Lineup', TeamId='1610612745'),
    run('hou_lineup_opponent', Type='LineupOpponent', TeamId='1610612745'),
    run('hou_opponent', Type='Opponent', TeamId='1610612745'),
]

# Compare lineup IDs if the lineup-opponent entity type is available.
by_label = {x['label']: x for x in results}
lineup_rows = by_label['hou_lineup'].get('sample_rows') or []
opp_rows = by_label['hou_lineup_opponent'].get('sample_rows') or []
comparison = {
    'lineup_sample_entity_ids': [r.get('EntityId') for r in lineup_rows],
    'lineup_opponent_sample_entity_ids': [r.get('EntityId') for r in opp_rows],
}

report = {'results': results, 'comparison': comparison}
(OUT / 'pbpstats_team_filter_report.json').write_text(json.dumps(report, indent=2, default=str))
print(json.dumps({
    x['label']: {
        'status': x.get('status'),
        'bytes': x.get('bytes'),
        'row_count': x.get('row_count'),
        'row_keys': x.get('row_keys'),
        'error_payload': x.get('error_payload'),
    }
    for x in results
}, indent=2, default=str))

hou = by_label['hou_lineup']
if hou.get('status') != 200 or not hou.get('row_count'):
    raise SystemExit('Team-filtered lineup query failed')
if hou.get('row_count', 0) >= 500:
    raise SystemExit('A single team is still capped at 500 rows')
