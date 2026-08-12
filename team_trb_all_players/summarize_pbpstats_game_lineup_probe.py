#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent
SRC=BASE/'final_integrity_rebuild'/'PBPSTATS_GAME_LINEUP_PROBE.json'
OUT=BASE/'final_integrity_rebuild'/'PBPSTATS_GAME_LINEUP_SCHEMA_SUMMARY.json'

def summarize_response(req):
    d=req.get('raw_response',{});stats=d.get('stats',{})
    out={'status_code':req.get('status_code'),'home_team_id':d.get('home_team_id'),'away_team_id':d.get('away_team_id'),'date':d.get('date'),'season':d.get('season'),'sides':{}}
    for side,periods in stats.items():
        fg=periods.get('FullGame',[]) if isinstance(periods,dict) else []
        keys=sorted({k for r in fg for k in r})
        out['sides'][side]={'full_game_rows':len(fg),'entity_ids':[r.get('EntityId') for r in fg[:10]],'sample_names':[r.get('Name') for r in fg[:5]],'field_count':len(keys),'fields':keys,'has_raw_rebounds':all(x in keys for x in ['OffRebounds','DefRebounds','Rebounds']),'has_possessions':all(x in keys for x in ['OffPoss','DefPoss']),'sample_rows':fg[:2]}
    return out

def main():
    p=json.loads(SRC.read_text());req=p['requests']
    payload={'game_id':p['game_id'],'Lineup':summarize_response(req['Lineup']),'LineupOpponent':summarize_response(req['LineupOpponent']),'Player':summarize_response(req['Player'])}
    # Compare entity-id universes for the two lineup views.
    for side in ('Away','Home'):
        a=set(payload['Lineup']['sides'].get(side,{}).get('entity_ids',[]));b=set(payload['LineupOpponent']['sides'].get(side,{}).get('entity_ids',[]))
        payload.setdefault('entity_id_comparison',{})[side]={'lineup_sample_count':len(a),'lineup_opponent_sample_count':len(b),'same_sample_ids':a==b,'intersection':len(a&b)}
    OUT.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    compact={'game_id':p['game_id'],'Lineup_status':payload['Lineup']['status_code'],'LineupOpponent_status':payload['LineupOpponent']['status_code'],'Player_status':payload['Player']['status_code'],'Lineup_sides':{s:{k:v for k,v in d.items() if k in ['full_game_rows','field_count','has_raw_rebounds','has_possessions']} for s,d in payload['Lineup']['sides'].items()},'LineupOpponent_sides':{s:{k:v for k,v in d.items() if k in ['full_game_rows','field_count','has_raw_rebounds','has_possessions']} for s,d in payload['LineupOpponent']['sides'].items()},'entity_id_comparison':payload.get('entity_id_comparison')}
    print(json.dumps(compact,indent=2))
if __name__=='__main__':main()
