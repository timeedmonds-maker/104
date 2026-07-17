import json
import requests
from pathlib import Path
from urllib.parse import quote

out = Path('probe_output')
out.mkdir(exist_ok=True)
source = 'http://www.basketball-reference.com/players/h/hilarne01/on-off/'
cdx = 'https://web.archive.org/cdx/search/cdx?url=' + quote(source, safe=':/') + '&output=json&filter=statuscode:200&filter=mimetype:text/html&fl=timestamp,original,statuscode,digest&collapse=digest&from=2010&to=2020'

r = requests.get(cdx, timeout=120, headers={'User-Agent': 'Mozilla/5.0'})
print('nene_cdx', r.status_code, len(r.text), flush=True)
(out / 'nene_cdx.txt').write_text(r.text, encoding='utf-8')
if r.status_code == 200:
    data = r.json()
    print('rows', max(0, len(data)-1), flush=True)
    if len(data) > 1:
        ts, orig = data[-1][0], data[-1][1]
        snap = f'https://web.archive.org/web/{ts}id_/{orig}'
        rr = requests.get(snap, timeout=120, headers={'User-Agent': 'Mozilla/5.0'})
        print('nene_snapshot', rr.status_code, len(rr.text), ts, flush=True)
        (out / 'nene_snapshot.txt').write_text(rr.text, encoding='utf-8')
        (out / 'nene_meta.json').write_text(json.dumps({'timestamp': ts, 'original': orig, 'snapshot': snap}, indent=2), encoding='utf-8')
