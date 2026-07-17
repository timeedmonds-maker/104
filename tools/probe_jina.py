import requests
import time
from datetime import datetime, timezone
from pathlib import Path

# Wait until the temporary Jina/Basketball-Reference block window has expired.
target = datetime(2026, 7, 17, 19, 41, 0, tzinfo=timezone.utc)
wait = max(0, (target - datetime.now(timezone.utc)).total_seconds())
if wait:
    print(f'waiting {wait:.0f}s for block expiry', flush=True)
    time.sleep(wait)

urls = {
    'duncan_2016_direct': 'https://www.basketball-reference.com/players/d/duncati01/on-off/2016',
    'duncan_2016_jina': 'https://r.jina.ai/http://www.basketball-reference.com/players/d/duncati01/on-off/2016',
    'team_totals_raw': 'https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/Team%20Totals.csv',
}
out = Path('probe_output')
out.mkdir(exist_ok=True)
for name, url in urls.items():
    try:
        r = requests.get(url, timeout=90, headers={'User-Agent': 'Mozilla/5.0'})
        print(name, r.status_code, len(r.text), flush=True)
        (out / f'{name}.txt').write_text(r.text, encoding='utf-8')
    except Exception as exc:
        print(name, type(exc).__name__, exc, flush=True)
        (out / f'{name}.txt').write_text(repr(exc), encoding='utf-8')
