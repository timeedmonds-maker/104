#!/usr/bin/env python3
from __future__ import annotations
import gzip,json
from pathlib import Path
import pandas as pd

BASE=Path(__file__).resolve().parent
EVID=BASE/'final_integrity_rebuild'/'TENURE_REPAIR_EVIDENCE.json'
GAMES=BASE/'impact_database'/'roster_tenure'/'regular_season_games_raw'
OUT=BASE/'final_integrity_rebuild'/'TENURE_REPAIR_PROPOSALS.json'
SUMMARY=BASE/'final_integrity_rebuild'/'TENURE_REPAIR_PROPOSALS_SUMMARY.json'

def schedule_map():
    out={}
    for p in sorted(GAMES.glob('*.json.gz')):
        season=p.name.replace('.json.gz','')
        with gzip.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        for g in d['results']:
            row={'game_id':int(g['GameId']),'game_date':str(pd.Timestamp(g['Date']).date())}
            for tid in (int(g['HomeTeamId']),int(g['AwayTeamId'])):out.setdefault((season,tid),[]).append(row)
    for k in out:out[k].sort(key=lambda x:(x['game_date'],x['game_id']))
    return out
def runs(indices,games):
    if not indices:return []
    vals=sorted(set(indices));groups=[];cur=[vals[0]]
    for x in vals[1:]:
        if x==cur[-1]+1:cur.append(x)
        else:groups.append(cur);cur=[x]
    groups.append(cur)
    return [{'start_date':games[g[0]]['game_date'],'end_date':games[g[-1]]['game_date'],'team_games':len(g),'start_game_id':games[g[0]]['game_id'],'end_game_id':games[g[-1]]['game_id']} for g in groups]
def main():
    ev=json.loads(EVID.read_text());sched=schedule_map();out=[]
    for r in ev['rows']:
        games=sched[(r['season'],int(r['team_id']))];idx={g['game_id']:i for i,g in enumerate(games)}
        roster_ids=[int(x['game_id']) for x in r['cc0_roster_rows'] if int(x['game_id']) in idx];pos_ids=[int(x) for x in r['cc0_positive_game_ids'] if int(x) in idx]
        roster_runs=runs([idx[x] for x in roster_ids],games);pos_runs=runs([idx[x] for x in pos_ids],games)
        appearance_complete=(len(pos_ids)==int(r['core_games']));all_positive_covered=set(pos_ids).issubset(set(roster_ids))
        status='EVIDENCE_COMPLETE_CANDIDATE' if appearance_complete and all_positive_covered else 'SOURCE_REVIEW_REQUIRED'
        out.append({'season':r['season'],'player_id':r['player_id'],'player':r['player'],'team_id':int(r['team_id']),'core_games':int(r['core_games']),'core_seconds':float(r['core_seconds']),'old_v2_window_games':int(r['v2_window_games']),'old_v2_seconds_diff':float(r['v2_seconds_diff']),'appearance_count_exact':appearance_complete,'cc0_roster_rows':len(roster_ids),'cc0_positive_games':len(pos_ids),'roster_presence_runs':roster_runs,'positive_appearance_runs':pos_runs,'old_v2_segments':r['v2_target'].get('segments',[]),'transaction_rows':r['transaction_rows'],'proposal_status':status,'acceptance_note':'Candidate only. Promotion requires a corrected interval/game-id set whose rebuilt PBP ON seconds exactly equals locked core seconds and contains all locked GamesPlayed.'})
    payload={'target_pts':len(out),'evidence_complete_candidates':sum(x['proposal_status']=='EVIDENCE_COMPLETE_CANDIDATE' for x in out),'source_review_required':sum(x['proposal_status']!='EVIDENCE_COMPLETE_CANDIDATE' for x in out),'rows':out};OUT.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    compact={'target_pts':len(out),'evidence_complete_candidates':payload['evidence_complete_candidates'],'source_review_required':payload['source_review_required'],'complete_single_roster_run':sum(x['proposal_status']=='EVIDENCE_COMPLETE_CANDIDATE' and len(x['roster_presence_runs'])==1 for x in out),'complete_multi_roster_run':sum(x['proposal_status']=='EVIDENCE_COMPLETE_CANDIDATE' and len(x['roster_presence_runs'])>1 for x in out),'rows':[{'season':x['season'],'player_id':x['player_id'],'player':x['player'],'team_id':x['team_id'],'core_games':x['core_games'],'cc0_roster_rows':x['cc0_roster_rows'],'cc0_positive_games':x['cc0_positive_games'],'roster_runs':x['roster_presence_runs'],'status':x['proposal_status']} for x in out]};SUMMARY.write_text(json.dumps(compact,indent=2)+'\n')
    print(json.dumps({k:v for k,v in compact.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
