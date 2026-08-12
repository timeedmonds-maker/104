#!/usr/bin/env python3
"""Regression compare rebound v5 against v4 on the forensic games."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import production_rebound_v4 as v4
import production_rebound_v5 as v5
import production_treb_engine_v3 as eng
import run_local_treb_production as io

def eq(a,b):
    if isinstance(a,(tuple,list)) or isinstance(b,(tuple,list)):return tuple(a)==tuple(b)
    if pd.isna(a) and pd.isna(b):return True
    return a==b

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--games',required=True);ap.add_argument('--chunk-id',required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--v3',type=Path,required=True);ap.add_argument('--pbp',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x];nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False));nv3=eng.normalize_v3(pd.read_csv(a.v3,low_memory=False));pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={int(g):f.copy() for g,f in nv3.groupby('gameId',sort=False)};pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    out={'chunk_id':a.chunk_id,'year':a.year,'games':0,'v4_unmatched':0,'v5_unmatched':0,'added_rows':0,'changed_existing_rows':0,'v5_direct_team_repairs':0,'game_records':[]}
    cols=['NBA_INDEX','NBA_EVENTNUM','NBA_ELAPSED','NBA_PLAYER1_ID','NBA_IS_REAL_REBOUND','LINEUP']
    for gid in ids:
        lu=eng.reconstruct_game_lineups(ng[gid],vg[gid]);j4,a4=v4.join_pbp_rebounds(lu,pg[gid]);j5,a5=v5.join_pbp_rebounds(lu,pg[gid])
        changed=[]
        for idx in j4.index:
            if idx not in j5.index:changed.append({'pbp_index':int(idx),'reason':'missing_in_v5'});continue
            for col in cols:
                if col in j4.columns and col in j5.columns and not eq(j4.loc[idx,col],j5.loc[idx,col]):changed.append({'pbp_index':int(idx),'column':col});break
        added=[int(x) for x in j5.index if x not in j4.index]
        if len(added)!=int(a5.get('direct_team_repairs',0)):raise AssertionError((gid,len(added),a5.get('direct_team_repairs')))
        out['games']+=1;out['v4_unmatched']+=int(a4['unmatched_rebound_bearing_rows']);out['v5_unmatched']+=int(a5['unmatched_rebound_bearing_rows']);out['added_rows']+=len(added);out['changed_existing_rows']+=len(changed);out['v5_direct_team_repairs']+=int(a5.get('direct_team_repairs',0))
        out['game_records'].append({'game_id':gid,'v4_unmatched':int(a4['unmatched_rebound_bearing_rows']),'v5_unmatched':int(a5['unmatched_rebound_bearing_rows']),'added_rows':len(added),'changed_existing_rows':changed,'remaining':a5.get('unmatched_rows',[])})
    a.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='game_records'},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
