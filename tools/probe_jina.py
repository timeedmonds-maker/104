import requests
from pathlib import Path

urls = {
    'duncan_2016': 'https://r.jina.ai/http://www.basketball-reference.com/players/d/duncati01/on-off/2016',
    'sas_2016': 'https://r.jina.ai/http://www.basketball-reference.com/teams/SAS/2016.html',
    'duncan_main': 'https://r.jina.ai/http://www.basketball-reference.com/players/d/duncati01.html',
}
out = Path('probe_output')
out.mkdir(exist_ok=True)
for name, url in urls.items():
    r = requests.get(url, timeout=60, headers={'User-Agent': 'Mozilla/5.0'})
    print(name, r.status_code, len(r.text))
    (out / f'{name}.txt').write_text(r.text, encoding='utf-8')
