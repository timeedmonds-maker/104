from __future__ import annotations

import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, Comment

# name, public BRef player id, archived URL slug, career-page coverage end year, last NBA season end year
PLAYERS = [
    ("Al Jefferson", "jeffeal01", "jeffeal01", 2016, 2018),
    ("David Lee", "leeda02", "leeda02", 2016, 2017),
    ("David West", "westda01", "westda01", 2016, 2018),
    ("Dirk Nowitzki", "nowitdi01", "nowitdi01", 2017, 2019),
    ("Dwight Howard", "howardw01", "howardw01", 2016, 2022),
    ("Emeka Okafor", "okafoem01", "okafoem01", 2016, 2018),
    ("Joakim Noah", "noahjo01", "noahjo01", 2016, 2020),
    ("LaMarcus Aldridge", "aldrila01", "aldrila01", 2016, 2022),
    ("Marc Gasol", "gasolma01", "gasolma01", 2016, 2021),
    ("Pau Gasol", "gasolpa01", "gasolpa01", 2016, 2019),
    ("Paul Millsap", "millspa01", "millspa01", 2016, 2022),
    ("Tyson Chandler", "chandty01", "chandty01", 2016, 2020),
    ("Zach Randolph", "randoza01", "randoza01", 2016, 2018),
    # Nene's older archived BRef URLs used his former Hilario slug.
    ("Nene", "nenexx01", "hilarne01", 2002, 2020),
]

OUT = Path('missing_season_output')
OUT.mkdir(exist_ok=True)
RAW = OUT / 'unparsed'
RAW.mkdir(exist_ok=True)
HEADERS = {'User-Agent':'Mozilla/5.0 (compatible; basketball research; +https://github.com/timeedmonds-maker/104)'}


def get_json(url, attempts=6):
    last=None
    for i in range(attempts):
        try:
            r=requests.get(url,headers=HEADERS,timeout=45)
            if r.status_code==200: return r.json()
            last=RuntimeError(f'HTTP {r.status_code}: {r.text[:100]}')
        except Exception as e: last=e
        time.sleep(1.5*(i+1))
    raise last


def get_text(url, attempts=7):
    last=None
    for i in range(attempts):
        try:
            r=requests.get(url,headers=HEADERS,timeout=90)
            if r.status_code==200 and len(r.text)>4000: return r.text
            last=RuntimeError(f'HTTP {r.status_code}, len={len(r.text)}')
        except Exception as e: last=e
        time.sleep(2*(i+1))
    raise last


def availability(target):
    api='https://archive.org/wayback/available?url='+quote(target,safe='')+'&timestamp=20261231'
    data=get_json(api)
    return data.get('archived_snapshots',{}).get('closest',{})


def snapshot_for(slug, year):
    path=f'/players/{slug[0]}/{slug}/on-off/{year}'
    variants=[]
    for scheme in ('https','http'):
        for www in ('www.',''):
            for slash in ('','/'):
                variants.append(f'{scheme}://{www}basketball-reference.com{path}{slash}')
    best=None
    for target in variants:
        try:
            c=availability(target)
        except Exception:
            continue
        if c.get('available') and c.get('timestamp'):
            if best is None or c['timestamp']>best[0]:
                # Use the archived original URL from the response to preserve historical slug/scheme/port.
                archived_url=c.get('url','')
                m=re.search(r'/web/(\d+)/(https?://.*)$',archived_url)
                original=m.group(2) if m else target
                best=(c['timestamp'],f"https://web.archive.org/web/{c['timestamp']}id_/{original}")
    return best if best else (None,None)


def clean_num(v):
    return v.strip().replace('+','').replace('−','-').replace(',','')


def parse_page(text):
    soup=BeautifulSoup(text,'lxml')
    for comment in list(soup.find_all(string=lambda s:isinstance(s,Comment))):
        if 'On Court' in comment and 'Off Court' in comment and 'ORB%' in comment:
            comment.replace_with(BeautifulSoup(str(comment),'lxml'))
    table=soup.find('table',id='on-off')
    if table is None:
        table=next((t for t in soup.find_all('table') if t.get('id')!='on-off-post' and 'On Court' in t.get_text(' ',strip=True) and 'Off Court' in t.get_text(' ',strip=True)),None)
    if table is None: return []
    pending=[]; pairs=[]
    for tr in table.find_all('tr'):
        cells=tr.find_all(['th','td'])
        if not cells: continue
        texts=[c.get_text(' ',strip=True) for c in cells]
        label=texts[0].replace('−','-')
        if label not in {'On Court','Off Court'}: continue
        ds={c.get('data-stat'):clean_num(c.get_text(' ',strip=True)) for c in cells if c.get('data-stat')}
        team=''
        for key in ('team_id','team_name','team'):
            if ds.get(key): team=ds[key]; break
        if ds.get('mp') and ds.get('orb_pct') and ds.get('drb_pct') and ds.get('trb_pct'):
            row={'label':label,'team':team,'mp':int(float(ds['mp'])),'orb':float(ds['orb_pct']),'drb':float(ds['drb_pct']),'trb':float(ds['trb_pct'])}
        else:
            offset=1
            if len(texts)>2 and re.fullmatch(r'[A-Z]{2,3}|\d+TM',texts[1]):
                team=texts[1]; offset=2
            try:
                row={'label':label,'team':team,'mp':int(float(clean_num(texts[offset]))),'orb':float(clean_num(texts[offset+2])),'drb':float(clean_num(texts[offset+3])),'trb':float(clean_num(texts[offset+4]))}
            except Exception:
                continue
        if label=='On Court': pending.append(row)
        else:
            idx=next((i for i,x in enumerate(pending) if x['team']==row['team']),0 if pending else None)
            if idx is not None:
                on=pending.pop(idx)
                pairs.append({'Team':on['team'] or row['team'],'On MP':on['mp'],'On OREB%':on['orb'],'On DREB%':on['drb'],'On TRB%':on['trb'],'Off MP':row['mp'],'Off OREB%':row['orb'],'Off DREB%':row['drb'],'Off TRB%':row['trb']})
    unique={}
    for p in pairs:
        key=tuple(p[k] for k in ['Team','On MP','On OREB%','On DREB%','On TRB%','Off MP','Off OREB%','Off DREB%','Off TRB%'])
        unique[key]=p
    return list(unique.values())


def collect(task):
    name,pid,slug,year=task
    ts,snap=snapshot_for(slug,year)
    target=f'https://www.basketball-reference.com/players/{slug[0]}/{slug}/on-off/{year}'
    if not snap:
        return [], {'Player':name,'Player ID':pid,'Season End Year':year,'Status':'No archive','Source':target}
    text=get_text(snap)
    rows=parse_page(text)
    if not rows:
        (RAW/f'{pid}_{year}.html').write_text(text,encoding='utf-8')
        return [], {'Player':name,'Player ID':pid,'Season End Year':year,'Status':'Unparsed','Source':snap}
    out=[]
    for r in rows:
        r.update({'Player':name,'Player ID':pid,'Season':f'{year-1}-{str(year)[-2:]}','Season End Year':year,'Snapshot Timestamp':ts,'Source':snap})
        out.append(r)
    return out,None


def main():
    tasks=[]
    for name,pid,slug,covered,last in PLAYERS:
        for year in range(covered+1,last+1): tasks.append((name,pid,slug,year))
    rows=[]; issues=[]
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs={ex.submit(collect,t):t for t in tasks}
        for f in as_completed(fs):
            t=fs[f]
            try:
                got,issue=f.result(); rows.extend(got)
                if issue: issues.append(issue); print('ISSUE',issue,flush=True)
                else: print('OK',t[0],t[3],len(got),flush=True)
            except Exception as e:
                issue={'Player':t[0],'Player ID':t[1],'Season End Year':t[3],'Status':f'{type(e).__name__}: {e}','Source':''}
                issues.append(issue); print('FAIL',issue,flush=True)
    rows.sort(key=lambda r:(r['Player'],r['Season End Year'],r['Team']))
    fields=['Player','Player ID','Season','Season End Year','Team','On MP','On OREB%','On DREB%','On TRB%','Off MP','Off OREB%','Off DREB%','Off TRB%','Snapshot Timestamp','Source']
    with (OUT/'missing_season_onoff.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    with (OUT/'issues.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['Player','Player ID','Season End Year','Status','Source']);w.writeheader();w.writerows(issues)
    (OUT/'manifest.json').write_text(json.dumps({'tasks':len(tasks),'parsed_rows':len(rows),'issues':len(issues)},indent=2))

if __name__=='__main__': main()
