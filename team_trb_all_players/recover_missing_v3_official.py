#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re,time
from pathlib import Path
import pandas as pd, requests

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'

def norm_clock(x):
    s=str(x or '').strip()
    m=re.match(r'^PT(?:(\d+)M)?([0-9.]+)S$',s)
    if m: return f"{int(m.group(1) or 0):02d}:{int(float(m.group(2))):02d}"
    m=re.match(r'^(\d+):(\d+)',s)
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}" if m else s

def fetch_live(gid):
    game=f'{int(gid):010d}'
    urls=[
      f'https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{game}.json',
      f'https://stats.nba.com/stats/playbyplayv3?GameID={game}&StartPeriod=0&EndPeriod=14'
    ]
    hdr={'User-Agent':UA,'Referer':'https://www.nba.com/','Origin':'https://www.nba.com'}
    errs=[]
    for u in urls:
      try:
        r=requests.get(u,headers=hdr,timeout=25); r.raise_for_status(); j=r.json()
        acts=(j.get('game') or {}).get('actions') or j.get('actions') or ((j.get('resultSets') or [{}])[0].get('rowSet') if isinstance(j,dict) else None)
        if isinstance(acts,list) and acts and isinstance(acts[0],dict): return acts,u
        errs.append(f'{u}:no_dict_actions')
      except Exception as e: errs.append(f'{u}:{type(e).__name__}:{e}')
    raise RuntimeError('; '.join(errs))

def main():
  ap=argparse.ArgumentParser(); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--games',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--qa',type=Path,required=True); a=ap.parse_args()
  nba=pd.read_csv(a.nba,low_memory=False); v3=pd.read_csv(a.v3,low_memory=False)
  gids=[int(x) for x in json.load(open(a.games))]
  rows=[]; qa={}
  for gid in gids:
    g=nba[pd.to_numeric(nba.GAME_ID,errors='coerce').eq(gid)].copy()
    if g.empty: qa[str(gid)]={'status':'NO_NBA'}; continue
    amb=[]
    for (p,c),x in g.groupby(['PERIOD','PCTIMESTRING'],sort=False):
      if len(x)>1 and pd.to_numeric(x.EVENTMSGTYPE,errors='coerce').eq(8).any(): amb.extend(int(z) for z in pd.to_numeric(x.EVENTNUM,errors='coerce').dropna())
    try: acts,url=fetch_live(gid)
    except Exception as e: qa[str(gid)]={'status':'FETCH_FAIL','error':str(e)}; continue
    amap={}
    for idx,x in enumerate(acts):
      an=x.get('actionNumber',x.get('actionId'))
      try: an=int(an)
      except: continue
      per=int(x.get('period') or 0); clk=norm_clock(x.get('clock'))
      amap[(per,an)]={'order':idx,'clock':clk,'raw':x}
    missing=[]; clock_bad=[]
    for ev in amb:
      rr=g[pd.to_numeric(g.EVENTNUM,errors='coerce').eq(ev)].iloc[0]; k=(int(rr.PERIOD),ev)
      if k not in amap: missing.append(ev); continue
      if norm_clock(rr.PCTIMESTRING)!=amap[k]['clock']: clock_bad.append(ev)
    if missing or clock_bad:
      qa[str(gid)]={'status':'IDENTITY_FAIL','missing_eventnums':missing,'clock_mismatch':clock_bad,'source':url}; continue
    for (per,ev),x in amap.items():
      raw=x['raw']; rows.append({'gameId':gid,'period':per,'actionNumber':ev,'actionId':x['order'],'personId':raw.get('personId',0),'teamId':raw.get('teamId',0)})
    qa[str(gid)]={'status':'PASS','source':url,'ambiguous_legacy_events_validated':len(amb),'official_actions':len(acts)}
    print(json.dumps({'game_id':gid,**qa[str(gid)]}),flush=True); time.sleep(.2)
  add=pd.DataFrame(rows)
  if not add.empty:
    v3=pd.concat([v3[~pd.to_numeric(v3.get('gameId'),errors='coerce').isin(gids)],add],ignore_index=True,sort=False)
  a.out.parent.mkdir(parents=True,exist_ok=True); v3.to_csv(a.out,index=False); a.qa.write_text(json.dumps(qa,indent=2)+'\n')
  print('OFFICIAL_V3_PASS_GAMES',sum(x.get('status')=='PASS' for x in qa.values()),'OF',len(gids),flush=True)
if __name__=='__main__': main()
