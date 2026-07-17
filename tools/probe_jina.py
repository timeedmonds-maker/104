import requests
import time
from pathlib import Path

out = Path('probe_output')
out.mkdir(exist_ok=True)
urls = {
    'nene_2019_jina': 'https://r.jina.ai/http://www.basketball-reference.com/players/h/hilarne01/on-off/2019',
    'nene_career_jina': 'https://r.jina.ai/http://www.basketball-reference.com/players/h/hilarne01/on-off/',
}
for name, url in urls.items():
    try:
        started = time.time()
        r = requests.get(url, timeout=180, headers={'User-Agent': 'Mozilla/5.0'})
        print(name, r.status_code, len(r.text), f'{time.time()-started:.2f}s', flush=True)
        print(r.text[:240], flush=True)
        (out / f'{name}.txt').write_text(r.text, encoding='utf-8')
    except Exception as exc:
        print(name, type(exc).__name__, exc, flush=True)
        (out / f'{name}_error.txt').write_text(repr(exc), encoding='utf-8')
