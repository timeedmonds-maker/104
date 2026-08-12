#!/usr/bin/env python3
"""Validate bracketed PBP team prefixes against matched NBA rebound team identity.

This is diagnostic only.  It validates the semantic step used by the direct-team
repair candidate class: e.g. '[HOU]' must resolve through source-native v3
teamTricode -> teamId to the same team as the already-matched NBA rebound event.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v4 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

BRACKET_RE=re.compile(r"^\s*\[([A-Za-z]{2,4})\]\s*")

def tricode_map(v3_game:pd.DataFrame)->dict[str,int]:
    d={}
    for _,r in v3_game.dropna(subset=['teamTricode','teamId']).iterrows():
        tri=str(r.teamTricode).strip().upper(); tid=int(r.teamId)
        if tri and tid>0: d.setdefault(tri,set()).add(tid)
    return {k:next(iter(v)) for k,v in d.items() if len(v)==1}

def event_team(events:pd.DataFrame, idx:int, pteam:dict[int,int]):
    r=events.loc[idx]; pid=int(r.PLAYER1_ID)
    if 0<pid<core.PLAYER_MAX: return pteam.get(pid)
    if 'PLAYER1_TEAM_ID' in r.index and pd.notna(r.PLAYER1_TEAM_ID) and int(r.PLAYER1_TEAM_ID)>0:
        return int(r.PLAYER1_TEAM_ID)
    if pid>=core.PLAYER_MAX: return pid
    return None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--games',required=True); ap.add_argument('--chunk-id',required=True)
    ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    counts={'bracket_rebound_rows':0,'matched_bracket_controls':0,'resolvable_prefix_controls':0,'team_correct':0,'team_wrong':0,'actual_team_unresolved':0,'prefix_unresolved':0,'team_placeholder_controls':0,'player_controls':0}
    wrong=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); events=lu.events; joined,_=rebound.join_pbp_rebounds(lu,pg[gid]); tri=tricode_map(vg[gid]); pteam=core._player_team(ng[gid])
        rows=pg[gid][pg[gid].DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
        for idx,row in rows.iterrows():
            m=BRACKET_RE.match(str(row.DESCRIPTION))
            if not m: continue
            counts['bracket_rebound_rows']+=1
            if idx not in joined.index or pd.isna(joined.loc[idx,'NBA_INDEX']): continue
            counts['matched_bracket_controls']+=1
            code=m.group(1).upper(); expected=tri.get(code)
            if expected is None:
                counts['prefix_unresolved']+=1; continue
            counts['resolvable_prefix_controls']+=1
            ni=int(joined.loc[idx,'NBA_INDEX']); actual=event_team(events,ni,pteam); pid=int(events.loc[ni,'PLAYER1_ID'])
            if 0<pid<core.PLAYER_MAX: counts['player_controls']+=1
            else: counts['team_placeholder_controls']+=1
            if actual is None:
                counts['actual_team_unresolved']+=1; continue
            if int(actual)==int(expected): counts['team_correct']+=1
            else:
                counts['team_wrong']+=1
                wrong.append({'game_id':gid,'pbp_index':int(idx),'description':str(row.DESCRIPTION),'code':code,'expected_team_id':int(expected),'actual_team_id':int(actual),'nba_eventnum':int(events.loc[ni,'EVENTNUM']),'nba_player1_id':pid,'nba_description':str(events.loc[ni,'DESCRIPTION_NORM'])})
    out={'status':'DIAGNOSTIC_ONLY','chunk_id':a.chunk_id,'year':a.year,'counts':counts,'wrong_records':wrong}
    a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps(out,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
