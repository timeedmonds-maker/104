#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd

GAME_IDS=[21901316,21901317,21901318]

def clean(v):
    if pd.isna(v): return None
    if hasattr(v,'item'):
        try:return v.item()
        except:pass
    return v

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--v3',type=Path,required=True);ap.add_argument('--playerstatistics',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    v=pd.read_csv(a.v3,low_memory=False); ps=pd.read_csv(a.playerstatistics,usecols=['personId','gameId','playerteamId','numMinutes','startingPosition','firstName','lastName'],low_memory=False)
    for c in ('gameId','period','actionId','actionNumber','personId','teamId'): 
        if c in v.columns:v[c]=pd.to_numeric(v[c],errors='coerce')
    ps['gameIdN']=pd.to_numeric(ps.gameId,errors='coerce')
    payload={'columns':list(v.columns),'games':[]}
    for gid in GAME_IDS:
        g=v[v.gameId.eq(gid)].sort_values([c for c in ['period','actionId','actionNumber'] if c in v.columns],kind='stable')
        b=ps[ps.gameIdN.eq(gid)].copy()
        counts={}
        if 'actionType' in g.columns:counts=g.actionType.fillna('NA').astype(str).value_counts().to_dict()
        sub=g[g.get('actionType',pd.Series(index=g.index,dtype=object)).astype(str).str.contains('sub',case=False,na=False)] if 'actionType' in g.columns else pd.DataFrame()
        if sub.empty and 'description' in g.columns:sub=g[g.description.astype(str).str.contains('SUB:',case=False,na=False)]
        keep=[c for c in ['gameId','period','actionId','actionNumber','clock','actionType','subType','personId','teamId','teamTricode','description','playerName','playerNameI','possession'] if c in g.columns]
        sub_rows=[{c:clean(r[c]) for c in keep} for _,r in sub.iterrows()]
        first_rows=[]
        for pnum,pg in g.groupby('period',sort=True):
            for _,r in pg.head(12).iterrows():first_rows.append({c:clean(r[c]) for c in keep})
        box=[]
        for _,r in b.iterrows():
            box.append({'personId':clean(r.personId),'teamId':clean(r.playerteamId),'minutes':clean(r.numMinutes),'startingPosition':clean(r.startingPosition),'name':f"{clean(r.firstName)} {clean(r.lastName)}"})
        payload['games'].append({'game_id':gid,'v3_rows':len(g),'action_type_counts':counts,'substitution_rows':sub_rows,'period_opening_sample':first_rows,'boxscore':box})
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({'columns':payload['columns'],'games':[(x['game_id'],x['v3_rows'],len(x['substitution_rows'])) for x in payload['games']]},indent=2))
if __name__=='__main__':main()
