#!/usr/bin/env python3
"""Adjudicate the finite 2025-26 rebound discrepancies against current official NBA liveData PBP."""
from __future__ import annotations
import argparse,json,re,urllib.request
from pathlib import Path
from collections import Counter
import pandas as pd
TEAM_MIN=1610612737
COUNTER_RE=re.compile(r'\(\s*Off\s*:\s*(\d+)\s+Def\s*:\s*(\d+)\s*\)',re.I)
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36','Referer':'https://www.nba.com/','Origin':'https://www.nba.com','Accept':'application/json, text/plain, */*','Accept-Language':'en-US,en;q=0.9'}

def ni(v):
    x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0];return None if pd.isna(x) else int(x)
def pid(r):
    x=ni(r.get('personId'));return x if x is not None and 0<x<TEAM_MIN else None
def tid(r):
    x=ni(r.get('teamId'))
    if x is not None and x>=TEAM_MIN:return x
    x=ni(r.get('personId'));return x if x is not None and x>=TEAM_MIN else None
def ctr(r):
    o=ni(r.get('reboundOffensiveTotal'));d=ni(r.get('reboundDefensiveTotal'))
    if o is None or d is None:
        m=COUNTER_RE.search(str(r.get('description','')))
        if m:o=o if o is not None else int(m.group(1));d=d if d is not None else int(m.group(2))
    return o,d
def kind(r):
    s=str(r.get('subType','')).strip().lower()
    if s in {'offensive','off','oreb'}:return 'oreb'
    if s in {'defensive','def','dreb'}:return 'dreb'
    return None
def sig(r):
    p=pid(r);gid=ni(r.get('gameId'));per=ni(r.get('period'))
    if p is not None:
        o,d=ctr(r);return ('player',gid,per,p,o,d)
    return ('team',gid,per,tid(r),str(r.get('clock')),kind(r))
def rec(r):
    ks=['gameId','actionNumber','orderNumber','period','clock','actionType','subType','description','personId','playerName','teamId','teamTricode','reboundOffensiveTotal','reboundDefensiveTotal']
    return {k:(None if pd.isna(r.get(k)) else r.get(k)) for k in ks if k in r.index}
def load_live(gid):
    full=f'{gid:010d}';url=f'https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{full}.json';req=urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=30) as h:d=json.loads(h.read().decode())
    rows=d['game']['actions'];f=pd.DataFrame(rows);f['gameId']=gid
    f['action_norm']=f.actionType.astype('string').fillna('').str.strip().str.lower()
    return f[f.action_norm.eq('rebound')].copy(),url
def frame_rows(path):
    f=pd.read_csv(path,low_memory=False);f['gameId']=pd.to_numeric(f.gameId,errors='raise').astype('int64');f['actionNumber']=pd.to_numeric(f.actionNumber,errors='raise').astype('int64');f['action_norm']=f.actionType.astype('string').fillna('').str.strip().str.lower();return f[f.action_norm.eq('rebound')].copy()
def get(frame,gid,act):
    h=frame[frame.gameId.eq(gid)&frame.actionNumber.eq(act)];return None if len(h)!=1 else h.iloc[0]
def source_target_rows(ident,c,n):
    out=[]
    for x in ident['team_conflicts']:
        gid=int(x['gameId']);act=int(x['actionNumber']);out.append(('conflict_cdn',gid,act,get(c,gid,act)));out.append(('conflict_nba',gid,act,get(n,gid,act)))
    for x in ident['cdn_only_rows']:
        gid=int(x['gameId']);act=int(x['actionNumber']);out.append(('cdn_only',gid,act,get(c,gid,act)))
    for x in ident['nba_only_rows']:
        gid=int(x['gameId']);act=int(x['actionNumber']);out.append(('nba_only',gid,act,get(n,gid,act)))
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cdn',type=Path,required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--identity',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    c=frame_rows(a.cdn);n=frame_rows(a.nba);ident=json.loads(a.identity.read_text());targets=source_target_rows(ident,c,n);gids=sorted({g for _,g,_,_ in targets})
    lives={};urls={}
    for g in gids:lives[g],urls[g]=load_live(g)
    live_counts={g:Counter(sig(r) for _,r in f.iterrows()) for g,f in lives.items()}
    source_counts={'cdn':Counter(sig(r) for _,r in c[c.gameId.isin(gids)].iterrows()),'nba':Counter(sig(r) for _,r in n[n.gameId.isin(gids)].iterrows())}
    rows=[]
    for typ,g,act,r in targets:
        assert r is not None,(typ,g,act);s=sig(r);lc=live_counts[g][s]
        src='cdn' if 'cdn' in typ else 'nba';sc=source_counts[src][s]
        rows.append({'target_type':typ,'game_id':g,'action_number':act,'source_row':rec(r),'semantic_signature':list(s),'official_live_multiplicity':lc,'source_game_multiplicity':sc,'represented_in_official_live':lc>0,'multiplicity_matches_official':lc==sc})
    conf=[]
    for x in ident['team_conflicts']:
        g=int(x['gameId']);a0=int(x['actionNumber']);z=[r for r in rows if r['game_id']==g and r['action_number']==a0 and r['target_type'].startswith('conflict_')]
        conf.append({'game_id':g,'action_number':a0,'cdn_represented':next(r for r in z if r['target_type']=='conflict_cdn')['represented_in_official_live'],'nba_represented':next(r for r in z if r['target_type']=='conflict_nba')['represented_in_official_live']})
    out={'status':'COMPLETE','official_source':'cdn.nba.com liveData playbyplay','target_games':len(gids),'target_rows_checked':len(rows),'cdn_target_rows_represented':sum(r['represented_in_official_live'] for r in rows if 'cdn' in r['target_type']),'cdn_target_rows_total':sum(1 for r in rows if 'cdn' in r['target_type']),'nba_only_rows_represented':sum(r['represented_in_official_live'] for r in rows if r['target_type']=='nba_only'),'nba_only_rows_total':sum(1 for r in rows if r['target_type']=='nba_only'),'conflicts':conf,'source_urls':urls,'rows':rows}
    a.output.write_text(json.dumps(out,indent=2,default=str)+'\n');print(json.dumps({k:v for k,v in out.items() if k not in {'rows','source_urls'}},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
