#!/usr/bin/env python3
"""Finite reconciliation of the 21 2025-26 rebound cross-feed discrepancies.

This does not promote a source.  It tests whether one-sided/conflicting rebound rows
are feed renumbering/correction artifacts by matching semantic rebound identity across
the full opposing game, and independently checks CDN OREB/DREB against the preceding
miss team using chronological CDN orderNumber.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import pandas as pd

TEAM_MIN=1610612737
COUNTER_RE=re.compile(r'\(\s*Off\s*:\s*(\d+)\s+Def\s*:\s*(\d+)\s*\)',re.I)
ISO=re.compile(r'PT(?:(\d+)M)?([0-9.]+)S',re.I)

def nint(v):
    x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0]
    return None if pd.isna(x) else int(x)
def clock(v):
    m=ISO.fullmatch(str(v).strip());return None if not m else 60*int(m.group(1) or 0)+float(m.group(2))
def pid(r):
    x=nint(r.get('personId'));return x if x is not None and 0<x<TEAM_MIN else None
def tid(r):
    x=nint(r.get('teamId'))
    if x is not None and x>=TEAM_MIN:return x
    x=nint(r.get('personId'));return x if x is not None and x>=TEAM_MIN else None
def counters(r):
    o=nint(r.get('reboundOffensiveTotal'));d=nint(r.get('reboundDefensiveTotal'))
    if o is None or d is None:
        m=COUNTER_RE.search(str(r.get('description','')))
        if m:o=o if o is not None else int(m.group(1));d=d if d is not None else int(m.group(2))
    return o,d
def rec(r):
    ks=['gameId','actionNumber','orderNumber','period','clock','actionType','subType','description','personId','playerName','teamId','teamTricode','reboundOffensiveTotal','reboundDefensiveTotal']
    out={k:(None if pd.isna(r[k]) else (r[k].item() if hasattr(r[k],'item') else r[k])) for k in ks if k in r.index}
    out['effective_team_id']=tid(r);out['player_id']=pid(r);out['counters']=list(counters(r));return out
def is_miss(r):
    a=str(r.get('actionType','')).strip().lower();d=str(r.get('description','')).lower();s=str(r.get('subType','')).lower()
    return 'miss' in d or a in {'missed shot','miss'} or (a in {'2pt','3pt','freethrow','free throw'} and ('miss' in s or 'miss' in d))
def chronological(frame,gid,period):
    g=frame[frame.gameId.eq(gid)&frame.period.eq(period)].copy()
    if 'orderNumber' in g and pd.to_numeric(g.orderNumber,errors='coerce').notna().all():return g.sort_values(['orderNumber','actionNumber'],kind='stable')
    return g.sort_values(['actionNumber'],kind='stable')
def prior_miss(frame,row):
    gid=int(row.gameId);per=int(row.period);g=chronological(frame,gid,per);idx=row.name
    loc=g.index.get_loc(idx)
    if not isinstance(loc,int):loc=int(loc.start)
    for _,r in g.iloc[:loc].iloc[::-1].iterrows():
        if is_miss(r):return r
        a=str(r.get('actionType','')).strip().lower()
        if a in {'turnover','made shot'}:break
    return None
def cdn_kind_check(cdn,row):
    sub=str(row.get('subType','')).lower();rt=tid(row);m=prior_miss(cdn,row);mt=tid(m) if m is not None else None
    expected=None
    if sub in {'offensive','off','oreb'}:expected=True
    elif sub in {'defensive','def','dreb'}:expected=False
    ok=None if expected is None or rt is None or mt is None else ((rt==mt)==expected)
    return {'subtype':sub,'rebound_team':rt,'prior_miss_team':mt,'consistent':ok,'prior_miss':None if m is None else rec(m)}
def candidates(other,row):
    gid=int(row.gameId);per=int(row.period);p=pid(row);t=tid(row);o,d=counters(row);clk=clock(row.get('clock'))
    g=other[other.gameId.eq(gid)&other.period.eq(per)&other.action_norm.eq('rebound')].copy();out=[]
    for _,r in g.iterrows():
        rp=pid(r);rt=tid(r);ro,rd=counters(r);rc=clock(r.get('clock'))
        if p is not None:
            if rp!=p:continue
            if o is not None and d is not None and ro is not None and rd is not None and (o,d)!=(ro,rd):continue
        else:
            if rp is not None or rt!=t:continue
            if clk is not None and rc is not None and abs(clk-rc)>15:continue
        q=rec(r);q['clock_delta_seconds']=None if clk is None or rc is None else clk-rc;q['action_delta']=int(row.actionNumber)-int(r.actionNumber);out.append(q)
    out.sort(key=lambda x:(abs(x['clock_delta_seconds']) if x['clock_delta_seconds'] is not None else 9999,abs(x['action_delta'])))
    return out
def getrow(frame,gid,act):
    h=frame[frame.gameId.eq(gid)&frame.actionNumber.eq(act)&frame.action_norm.eq('rebound')]
    return None if len(h)!=1 else h.iloc[0]
def analyze(source,other,row,source_name,cdn):
    if row is None:return None
    cs=candidates(other,row)
    out={'row':rec(row),'cross_feed_candidates':cs,'candidate_count':len(cs),'unique_semantic_crossmatch':len(cs)==1}
    if source_name=='cdn':out['cdn_kind_check']=cdn_kind_check(cdn,row)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cdn',type=Path,required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--identity',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    c=pd.read_csv(a.cdn,low_memory=False);n=pd.read_csv(a.nba,low_memory=False)
    for f in (c,n):
        f['gameId']=pd.to_numeric(f.gameId,errors='raise').astype('int64');f['period']=pd.to_numeric(f.period,errors='raise').astype('int64');f['actionNumber']=pd.to_numeric(f.actionNumber,errors='raise').astype('int64');f['action_norm']=f.actionType.astype('string').fillna('').str.strip().str.lower()
    ident=json.loads(a.identity.read_text());results=[]
    for x in ident['team_conflicts']:
        gid=int(x['gameId']);act=int(x['actionNumber']);cr=getrow(c,gid,act);nr=getrow(n,gid,act)
        results.append({'kind':'shared_team_conflict','game_id':gid,'action_number':act,'cdn':analyze(c,n,cr,'cdn',c),'nba':analyze(n,c,nr,'nba',c)})
    for x in ident['cdn_only_rows']:
        gid=int(x['gameId']);act=int(x['actionNumber']);cr=getrow(c,gid,act)
        results.append({'kind':'cdn_only','game_id':gid,'action_number':act,'cdn':analyze(c,n,cr,'cdn',c),'nba':None})
    for x in ident['nba_only_rows']:
        gid=int(x['gameId']);act=int(x['actionNumber']);nr=getrow(n,gid,act)
        results.append({'kind':'nba_only','game_id':gid,'action_number':act,'cdn':None,'nba':analyze(n,c,nr,'nba',c)})
    assert len(results)==21
    cdn_rows=[r['cdn'] for r in results if r['cdn']]
    nba_only=[r['nba'] for r in results if r['kind']=='nba_only']
    out={'status':'COMPLETE','input_counts':ident['counts'],'target_rows':21,
         'cdn_target_rows':len(cdn_rows),'cdn_kind_checkable':sum(x['cdn_kind_check']['consistent'] is not None for x in cdn_rows),
         'cdn_kind_consistent':sum(x['cdn_kind_check']['consistent'] is True for x in cdn_rows),
         'cdn_kind_inconsistent':sum(x['cdn_kind_check']['consistent'] is False for x in cdn_rows),
         'cdn_unique_crossmatches':sum(x['unique_semantic_crossmatch'] for x in cdn_rows),
         'nba_only_rows':len(nba_only),'nba_only_unique_crossmatches_into_cdn':sum(x['unique_semantic_crossmatch'] for x in nba_only),
         'results':results}
    a.output.write_text(json.dumps(out,indent=2,default=str)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='results'},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
