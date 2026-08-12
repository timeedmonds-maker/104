#!/usr/bin/env python3
"""Forensic context for the finite 2025-26 rebound cross-feed discrepancies.

Consumes the durable action-identity audit and inspects only its 3 shared-team
conflicts plus 12 CDN-only and 6 NBA-only rebound actions.  It records neighboring
raw actions and nearest prior miss semantics, and tests reciprocal pairing of
one-sided rows.  Diagnostic only; no source is promoted here.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
from collections import defaultdict
import pandas as pd

ISO=re.compile(r'PT(?:(\d+)M)?([0-9.]+)S',re.I)
PLAYER_MAX=1610612737

def clk(v):
    m=ISO.fullmatch(str(v).strip());return 60*int(m.group(1) or 0)+float(m.group(2)) if m else None

def num(v):
    x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0];return None if pd.isna(x) else int(x)
def team(r):
    t=num(r.get('teamId'))
    if t and t>0:return t
    p=num(r.get('personId'))
    if p and p>=PLAYER_MAX:return p
    return None

def norm(v):return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def is_miss(r):
    a=norm(r.get('actionType'));d=norm(r.get('description'));s=norm(r.get('subType'))
    return ('miss' in d) or a in {'missed shot','miss'} or (a in {'2pt','3pt','freethrow','free throw'} and ('miss' in s or 'miss' in d))
def rec(r):
    out={}
    for k in ['gameId','actionNumber','orderNumber','period','clock','actionType','subType','description','personId','playerName','teamId','teamTricode','possession']:
        if k in r.index:
            v=r[k];out[k]=None if pd.isna(v) else (v.item() if hasattr(v,'item') else v)
    out['effective_team_id']=team(r);return out

def context(frame,gid,action,n=4):
    g=frame[frame.gameId.eq(gid)].sort_values(['actionNumber','orderNumber'],kind='stable')
    hit=g.index[g.actionNumber.eq(action)].tolist()
    if not hit:return []
    pos=g.index.get_loc(hit[0]);
    if not isinstance(pos,int):pos=int(pos.start)
    return [rec(r) for _,r in g.iloc[max(0,pos-n):min(len(g),pos+n+1)].iterrows()]
def prior_miss(frame,gid,period,action):
    g=frame[frame.gameId.eq(gid)&frame.period.eq(period)&frame.actionNumber.lt(action)].sort_values(['actionNumber','orderNumber'],kind='stable')
    for _,r in g.iloc[::-1].iterrows():
        if is_miss(r):return rec(r)
        # Stop if a new made FG/turnover clearly starts/ends a different play before a miss is found.
        if norm(r.get('actionType')) in {'turnover'}:break
    return None

def semantic(row,miss):
    if row is None or miss is None:return None
    rt=row.get('effective_team_id');st=miss.get('effective_team_id');sub=norm(row.get('subType'))
    if rt is None or st is None:return None
    if sub in {'offensive','off','oreb'}:return bool(rt==st)
    if sub in {'defensive','def','dreb'}:return bool(rt!=st)
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cdn',type=Path,required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--identity',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    c=pd.read_csv(a.cdn,low_memory=False);n=pd.read_csv(a.nba,low_memory=False)
    for f in (c,n):
        f['gameId']=pd.to_numeric(f.gameId,errors='raise').astype('int64');f['period']=pd.to_numeric(f.period,errors='raise').astype('int64');f['actionNumber']=pd.to_numeric(f.actionNumber,errors='raise').astype('int64')
        if 'orderNumber' not in f.columns:f['orderNumber']=f.actionNumber
    ident=json.loads(a.identity.read_text());tc=ident['team_conflicts'];co=ident['cdn_only_rows'];no=ident['nba_only_rows']
    targets=[]
    for kind,rows in [('shared_team_conflict',tc),('cdn_only',co),('nba_only',no)]:
      for x in rows:
        base=x['cdn'] if kind=='shared_team_conflict' else x
        gid=int(base['gameId']);act=int(base['actionNumber']);per=int(base['period'])
        cr=c[c.gameId.eq(gid)&c.actionNumber.eq(act)&c.actionType.astype('string').str.lower().eq('rebound')]
        nr=n[n.gameId.eq(gid)&n.actionNumber.eq(act)&n.actionType.astype('string').str.lower().eq('rebound')]
        crec=rec(cr.iloc[0]) if len(cr)==1 else None;nrec=rec(nr.iloc[0]) if len(nr)==1 else None
        cm=prior_miss(c,gid,per,act);nm=prior_miss(n,gid,per,act)
        targets.append({'kind':kind,'game_id':gid,'action_number':act,'cdn_row':crec,'nba_row':nrec,'cdn_prior_miss':cm,'nba_prior_miss':nm,'cdn_internal_semantic_consistency':semantic(crec,cm),'nba_internal_semantic_consistency':semantic(nrec,nm),'cdn_context':context(c,gid,act),'nba_context':context(n,gid,act)})
    # Reciprocal unique pairing among one-sided rebounds by same game/period/team and near clock/action.
    pairs=[]
    cands_c=defaultdict(list);cands_n=defaultdict(list)
    for i,x in enumerate(targets):
      if x['kind']=='cdn_only' and x['cdn_row']:
        cr=x['cdn_row'];ct=cr['effective_team_id'];cc=clk(cr['clock'])
        for j,y in enumerate(targets):
          if y['kind']!='nba_only' or not y['nba_row']:continue
          nr=y['nba_row'];nc=clk(nr['clock'])
          if x['game_id']==y['game_id'] and int(cr['period'])==int(nr['period']) and ct==nr['effective_team_id'] and cc is not None and nc is not None and abs(cc-nc)<=15 and abs(x['action_number']-y['action_number'])<=12:
            cands_c[i].append(j);cands_n[j].append(i)
    for i,js in cands_c.items():
      if len(js)==1 and len(cands_n[js[0]])==1:
        j=js[0];x=targets[i];y=targets[j];pairs.append({'game_id':x['game_id'],'cdn_action':x['action_number'],'nba_action':y['action_number'],'same_team':x['cdn_row']['effective_team_id']==y['nba_row']['effective_team_id'],'clock_delta_seconds':clk(x['cdn_row']['clock'])-clk(y['nba_row']['clock']),'cdn_row':x['cdn_row'],'nba_row':y['nba_row']})
    out={'status':'COMPLETE','input_counts':ident['counts'],'target_rows':len(targets),'shared_team_conflicts':len(tc),'cdn_only':len(co),'nba_only':len(no),'reciprocal_unique_one_sided_pairs':len(pairs),'pairs':pairs,'targets':targets}
    assert len(targets)==21 and len(tc)==3 and len(co)==12 and len(no)==6
    a.output.write_text(json.dumps(out,indent=2,default=str)+'\n');print(json.dumps({k:v for k,v in out.items() if k not in {'pairs','targets'}},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
