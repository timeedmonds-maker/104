import json, pathlib, re, requests
from bs4 import BeautifulSoup

urls = [
    'https://aws.basketball-reference.com/players/a/adamsst01/on-off/2025',
    'https://www.basketball-reference.com/players/a/adamsst01/on-off/2025',
]
headers = {'User-Agent':'Mozilla/5.0'}
out = pathlib.Path('team_trb_all_players/output')
out.mkdir(parents=True, exist_ok=True)
results=[]
for url in urls:
    try:
        r=requests.get(url, headers=headers, timeout=20)
        results.append({'url':url,'status':r.status_code,'bytes':len(r.content),'head':r.text[:200]})
        if r.ok and 'On Court' in r.text:
            (out/'adams_2025_on_off.html').write_text(r.text, encoding='utf-8')
            soup=BeautifulSoup(r.text,'lxml')
            rows=[]
            for tr in soup.select('table tr'):
                cells=[c.get_text(' ',strip=True) for c in tr.select('th,td')]
                if cells: rows.append(cells)
            (out/'parsed_rows.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
            print('SUCCESS',url,len(rows),flush=True)
            break
    except Exception as e:
        results.append({'url':url,'error':repr(e)})
(out/'bref_test.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print(json.dumps(results,indent=2),flush=True)
if not (out/'parsed_rows.json').exists(): raise SystemExit(2)
