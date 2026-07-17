import requests
import time
from pathlib import Path

out = Path('probe_output')
out.mkdir(exist_ok=True)
base = 'http://www.basketball-reference.com:80/players/h/hilarne01/on-off'
urls = {
    'nene_wayback_normal': f'https://web.archive.org/web/20160622131444/{base}',
    'nene_wayback_id': f'https://web.archive.org/web/20160622131444id_/{base}',
    'nene_wayback_if': f'https://web.archive.org/web/20160622131444if_/{base}',
    'nene_wayback_im': f'https://web.archive.org/web/20160622131444im_/{base}',
}
for name, url in urls.items():
    try:
        started = time.time()
        r = requests.get(url, timeout=180, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True)
        print(name, r.status_code, len(r.text), f'{time.time()-started:.2f}s', r.url, 'On Court' in r.text, flush=True)
        print(r.text[:160], flush=True)
        (out / f'{name}.txt').write_text(r.text, encoding='utf-8')
    except Exception as exc:
        print(name, type(exc).__name__, exc, flush=True)
        (out / f'{name}_error.txt').write_text(repr(exc), encoding='utf-8')
