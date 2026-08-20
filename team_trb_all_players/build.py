import json
from pathlib import Path
import requests

OUT = Path('team_trb_all_players/output')
OUT.mkdir(parents=True, exist_ok=True)

URL = 'https://api.pbpstats.com/get-totals/nba'
PARAMS = {
    'Season': '2025-26',
    'SeasonType': 'Regular Season',
    'Type': 'Lineup',
}
HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://www.pbpstats.com',
    'Referer': 'https://www.pbpstats.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
}

r = requests.get(URL, params=PARAMS, headers=HEADERS, timeout=90)
summary = {
    'request_url': r.url,
    'status': r.status_code,
    'content_type': r.headers.get('content-type'),
    'bytes': len(r.content),
}
(OUT / 'pbpstats_http_summary.json').write_text(json.dumps(summary, indent=2))
r.raise_for_status()
payload = r.json()
rows = payload.get('multi_row_table_data') or payload.get('results') or payload.get('data') or []
if isinstance(rows, dict):
    rows = rows.get('multi_row_table_data') or rows.get('results') or rows.get('data') or []

report = {
    'top_level_keys': list(payload) if isinstance(payload, dict) else [],
    'row_count': len(rows) if isinstance(rows, list) else None,
    'row_keys': sorted(rows[0].keys()) if isinstance(rows, list) and rows else [],
    'sample_rows': rows[:3] if isinstance(rows, list) else rows,
}
(OUT / 'pbpstats_lineup_schema.json').write_text(json.dumps(report, indent=2, default=str))
print(json.dumps({'http': summary, 'schema': {k:v for k,v in report.items() if k != 'sample_rows'}}, indent=2))

if not isinstance(rows, list) or len(rows) < 500:
    raise SystemExit(f'Expected at least 500 lineup rows, got {len(rows) if isinstance(rows, list) else type(rows)}')
