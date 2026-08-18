#!/usr/bin/env python3
"""Solve the 23 +1 rebound semantic control mismatches as exact event constraints.

For each mismatch game, candidate removals are generic/team rebound events already counted in
that mismatched component. A legal solution must remove exactly one candidate from every +1
control equation. This is diagnostic only; even a unique event is not a promotion rule until
its source semantics generalise and pass the broad zero-mismatch control gate.
"""
import argparse, gzip, itertools, json, pathlib
import pandas as pd

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mismatches',required=True);ap.add_argument('--events',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    mm=pd.read_csv(a.mismatches,low_memory=False);ev=pd.read_csv(a.events,low_memory=False);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    if len(mm)!=23:raise RuntimeError(f'EXPECTED_23_MISMATCHES got={len(mm)}')
    rows=[];forced=[]
    for (season,game),mdf in mm.groupby(['season','game_id'],sort=True):
        constraints=[];allevents=set()
        for _,r in mdf.iterrows():
            mask=(ev.season.astype(str)==str(season))&(ev.game_id.astype(int)==int(game))&(ev.kind.astype(str)==str(r['kind']))&(ev.team_id.astype(int)==int(r.team_id))&(ev.component.astype(str)==str(r.component))
            if pd.isna(r.player_id):mask &= ev.player_id.isna()
            else:mask &= pd.to_numeric(ev.player_id,errors='coerce').eq(float(r.player_id))
            c=set(pd.to_numeric(ev.loc[mask & ev.generic_team_rebound.eq(True),'nba_eventnum'],errors='coerce').dropna().astype(int));constraints.append(c);allevents|=c
        allevents=sorted(allevents);sol=[]
        for bits in itertools.product((0,1),repeat=len(allevents)):
            removed={e for e,b in zip(allevents,bits) if b}
            if all(len(c & removed)==1 for c in constraints):sol.append(tuple(sorted(removed)))
        unique=(len(sol)==1); forced_events=list(sol[0]) if unique else []
        rows.append({'season':season,'game_id':int(game),'mismatch_constraints':len(constraints),'candidate_generic_events':'|'.join(map(str,allevents)),'legal_event_exclusion_solutions':len(sol),'unique_solution':unique,'forced_eventnums':'|'.join(map(str,forced_events))})
        if unique:
            for en in forced_events:
                rr=ev[(ev.season.astype(str)==str(season))&(ev.game_id.astype(int)==int(game))&(pd.to_numeric(ev.nba_eventnum,errors='coerce').eq(en))].iloc[0]
                forced.append({k:rr.get(k) for k in ['season','game_id','nba_eventnum','period','nba_elapsed','pbp_description','pbp_prev_description','nba_description','nba_eventmsgtype','nba_actiontype','nba_player1_id','nba_player1_team_id','prev_nba_description','prev_nba_eventmsgtype','prev_nba_actiontype','next_nba_description','next_nba_eventmsgtype','next_nba_actiontype','next2_nba_description','next2_nba_eventmsgtype','next2_nba_actiontype','pbp_start_time','pbp_end_time','pbp_offensive_rebounds','pbp_start_equals_end','next_same_clock','next_elapsed_delta']})
    rdf=pd.DataFrame(rows);rdf.to_csv(out/'TREB_SEMANTIC_EVENT_CONSTRAINT_SOLUTIONS.csv',index=False)
    pd.DataFrame(forced).to_csv(out/'TREB_SEMANTIC_FORCED_EVENTS.csv',index=False)
    qa={'status':'PASS_DIAGNOSTIC','mismatch_rows':len(mm),'mismatch_games':int(mm[['season','game_id']].drop_duplicates().shape[0]),'games_with_unique_event_solution':int(rdf.unique_solution.sum()),'games_still_semantically_ambiguous':int((~rdf.unique_solution).sum()),'forced_events':len(forced),'promotion_performed':False,'integrity':{'exact_integer_control_constraints_only':True,'generic_rebound_candidates_only':True,'statistical_rule_fit':False,'promotion_requires_subsequent_source_semantic_rule_and_zero_mismatch_validation':True}}
    (out/'TREB_SEMANTIC_EVENT_CONSTRAINT_QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2))
if __name__=='__main__':main()
