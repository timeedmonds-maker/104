import json
import requests
from pathlib import Path
from urllib.parse import quote

out = Path('probe_output')
out.mkdir(exist_ok=True)
players = {'pau':'gasolpa01','nene':'nenexx01','millsap':'millspa01','dwight':'howardw01'}
for name,pid in players.items():
    target=f'https://www.basketball-reference.com/players/{pid[0]}/{pid}/on-off/'
    api='https://archive.org/wayback/available?url='+quote(target,safe='')+'&timestamp=20261231'
    try:
        r=requests.get(api,timeout=60,headers={'User-Agent':'Mozilla/5.0'})
        print(name,'api',r.status_code,len(r.text),r.text[:300],flush=True)
        (out/f'{name}_available.json').write_text(r.text,encoding='utf-8')
        if r.status_code==200:
            data=r.json(); closest=data.get('archived_snapshots',{}).get('closest',{})
            url=closest.get('url')
            if url:
                # id_ avoids replay toolbar rewriting.
                url=url.replace('/web/','/web/').replace('/https://','id_/https://') if 'id_/' not in url else url
                # More reliable construction from timestamp and original target.
                ts=closest.get('timestamp')
                snap=f'https://web.archive.org/web/{ts}id_/{target}'
                rr=requests.get(snap,timeout=120,headers={'User-Agent':'Mozilla/5.0'})
                print(name,'snap',rr.status_code,len(rr.text),snap,flush=True)
                (out/f'{name}_snapshot.txt').write_text(rr.text,encoding='utf-8')
    except Exception as exc:
        print(name,type(exc).__name__,exc,flush=True)
        (out/f'{name}_error.txt').write_text(repr(exc),encoding='utf-8')
