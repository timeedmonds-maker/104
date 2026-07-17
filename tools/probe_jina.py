import json
import requests
import time
from pathlib import Path
from urllib.parse import quote

out = Path('probe_output')
out.mkdir(exist_ok=True)
source = 'http://www.basketball-reference.com/players/d/duncati01/on-off/'
cdx = 'https://web.archive.org/cdx/search/cdx?url=' + quote(source, safe=':/') + '&output=json&filter=statuscode:200&filter=mimetype:text/html&fl=timestamp,original,statuscode,digest&collapse=digest&from=2012&to=2020'

for name, url in {'duncan_career_cdx': cdx}.items():
    try:
        started = time.time()
        r = requests.get(url, timeout=120, headers={'User-Agent': 'Mozilla/5.0'})
        print(name, r.status_code, len(r.text), f'{time.time()-started:.2f}s', flush=True)
        (out / f'{name}.txt').write_text(r.text, encoding='utf-8')
        if r.status_code == 200:
            data = r.json()
            if len(data) > 1:
                ts, orig = data[-1][0], data[-1][1]
                snap = f'https://web.archive.org/web/{ts}id_/{orig}'
                rr = requests.get(snap, timeout=120, headers={'User-Agent': 'Mozilla/5.0'})
                print('duncan_career_snapshot', rr.status_code, len(rr.text), snap, flush=True)
                (out / 'duncan_career_snapshot.txt').write_text(rr.text, encoding='utf-8')
    except Exception as exc:
        print(name, type(exc).__name__, exc, flush=True)
        (out / f'{name}.txt').write_text(repr(exc), encoding='utf-8')
