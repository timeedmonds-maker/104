import requests
import time
from pathlib import Path

urls = {
    'duncan_career_jina': 'https://r.jina.ai/http://www.basketball-reference.com/players/d/duncati01/on-off/',
    'duncan_2016_jina': 'https://r.jina.ai/http://www.basketball-reference.com/players/d/duncati01/on-off/2016',
    'nowitzki_career_jina': 'https://r.jina.ai/http://www.basketball-reference.com/players/n/nowitdi01/on-off/',
}
out = Path('probe_output')
out.mkdir(exist_ok=True)
for name, url in urls.items():
    try:
        started = time.time()
        r = requests.get(url, timeout=120, headers={'User-Agent': 'Mozilla/5.0'})
        print(name, r.status_code, len(r.text), f'{time.time()-started:.2f}s', flush=True)
        (out / f'{name}.txt').write_text(r.text, encoding='utf-8')
    except Exception as exc:
        print(name, type(exc).__name__, exc, flush=True)
        (out / f'{name}.txt').write_text(repr(exc), encoding='utf-8')
