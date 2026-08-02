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
    'Referer': 'https://www.pbpstats.com/totals/nba/lineup?Season=2025-26&SeasonType=Regular%2BSeason',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
}

response = requests.get(URL, params=PARAMS, headers=HEADERS, timeout=90)
http_summary = {
    'request_url': response.url,
    'status': response.status_code,
    'content_type': response.headers.get('content-type'),
    'bytes': len(response.content),
}
(OUT / 'pbpstats_http_summary.json').write_text(json.dumps(http_summary, indent=2))
response.raise_for_status()
payload = response.json()

rows = None
rows_path = None
if isinstance(payload, dict):
    candidates = [
        ('multi_row_table_data', payload.get('multi_row_table_data')),
        ('results', payload.get('results')),
        ('data', payload.get('data')),
    ]
    for name, value in candidates:
        if isinstance(value, list):
            rows, rows_path = value, name
            break
        if isinstance(value, dict):
            for child_name in ('multi_row_table_data', 'results', 'data'):
                child = value.get(child_name)
                if isinstance(child, list):
                    rows, rows_path = child, f'{name}.{child_name}'
                    break
        if rows is not None:
            break

report = {
    'http': http_summary,
    'payload_type': type(payload).__name__,
    'top_level_keys': sorted(payload.keys()) if isinstance(payload, dict) else [],
    'rows_path': rows_path,
    'row_count': len(rows) if isinstance(rows, list) else None,
    'row_keys': sorted(rows[0].keys()) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else [],
    'sample_rows': rows[:5] if isinstance(rows, list) else None,
    'payload_preview': payload if not isinstance(rows, list) else None,
}
(OUT / 'pbpstats_lineup_schema.json').write_text(json.dumps(report, indent=2, default=str))
print(json.dumps({k: v for k, v in report.items() if k not in ('sample_rows', 'payload_preview')}, indent=2))

if not isinstance(rows, list) or not rows:
    raise SystemExit('PBP Stats returned no lineup rows')
if len(rows) <= 500:
    print(f'WARNING: API returned {len(rows)} rows; may be capped', flush=True)
