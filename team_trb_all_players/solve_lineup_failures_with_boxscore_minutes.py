#!/usr/bin/env python3
"""Resolve historical lineup ambiguities with independent official boxscore minutes.

This is a canary/repair-evidence generator, not a permissive production fallback.
For each previously excluded historical game it enumerates every substitution-
legal period opening five allowed by NBA event evidence, then selects complete
team-game solutions by exact agreement with CC0/NBA.com PlayerStatistics game
minutes. Q1 uses PlayerStatistics startingPosition when exactly five starters
are available.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any

import pandas as pd

import run_local_treb_production as histio
import production_treb_engine_v3 as eng

FAIL_GAMES={
    2002:[20201160],
    2004:[20400335],
    2006:[20600887],
    2007:[20700319],
    2008:[20800142],
    2011:[21100842],
    2015:[21500916],
    2018:[21800143],
    2020:[22000485],
}


def game_box(ps:pd.DataFrame,gid:int)->pd.DataFrame:
    g=ps[pd.to_numeric(ps.gameId,errors='coerce').eq(gid)].copy()
    g['personId']=pd.to_numeric(g.personId,errors='coerce').astype('Int64')
    g['playerteamId']=pd.to_numeric(g.playerteamId,errors='coerce').astype('Int64')
    g['seconds_official']=(pd.to_numeric(g.numMinutes,errors='coerce').fillna(0)*60).round().astype(int)
    return g[g.personId.notna() & g.playerteamId.notna()].copy()


def period_options(period:pd.DataFrame,team_id:int,player_team:dict[int,int],roster:set[int],official_starters:set[int]|None)->list[dict[str,Any]]:
    pnum=int(period.PERIOD.iloc[0])
    if pnum==1 and official_starters and len(official_starters)==5:
        combos=[set(official_starters)]
    else:
        cand=eng._candidate_starters(period,team_id,player_team)
        positive_roster={p for p in roster if p in player_team or p in cand}
        if len(cand)>=5:
            # A starter must be drawn from players whose period evidence does not prove
            # that they entered from the bench before first appearing.
            combos=[set(c) for c in itertools.combinations(sorted(cand),5)]
        else:
            pool=set(cand)|positive_roster
            combos=[set(c) for c in itertools.combinations(sorted(pool),5) if cand.issubset(c)]
    options=[]
    period_start=(pnum-1)*720 if pnum<=4 else 2880+(pnum-5)*300
    period_end=period_start+(720 if pnum<=4 else 300)
    for starters in combos:
        lineup=set(starters); sec={}; violations=[]; legal=True; last=period_start
        for _,row in period.iterrows():
            now=int(row.ELAPSED)
            if now>last:
                delta=now-last
                for pid in lineup: sec[pid]=sec.get(pid,0)+delta
                last=now
            if int(row.EVENTMSGTYPE)==8:
                try: st=eng._sub_team(row,player_team,{team_id:lineup})
                except Exception:
                    legal=False;break
                if st==team_id:
                    outgoing=int(row.PLAYER1_ID or 0); incoming=int(row.PLAYER2_ID or 0)
                    if outgoing not in lineup or incoming in lineup:
                        legal=False;break
                    lineup.remove(outgoing);lineup.add(incoming)
            for pid in eng._team_participants(row,team_id):
                if pid not in lineup:
                    violations.append({'event_num':int(row.EVENTNUM),'player_id':int(pid),'event_type':int(row.EVENTMSGTYPE),'elapsed':now})
        if not legal: continue
        if period_end>last:
            delta=period_end-last
            for pid in lineup: sec[pid]=sec.get(pid,0)+delta
        options.append({'starters':sorted(starters),'end_lineup':sorted(lineup),'seconds':sec,'participant_violations':violations,'violation_count':len(violations)})
    if not options:return []
    best=min(o['violation_count'] for o in options)
    # Keep zero-violation options where possible. If the feed itself is missing an
    # in-period transition, retain the minimum-violation frontier for diagnosis.
    return [o for o in options if o['violation_count']==best]


def solve_team(periods:list[tuple[int,list[dict[str,Any]]]],official:dict[int,int])->dict[str,Any]:
    counts=[len(opts) for _,opts in periods]
    product=1
    for c in counts:product*=max(c,1)
    if any(c==0 for c in counts):return {'status':'NO_PERIOD_SOLUTION','period_option_counts':counts}
    # Product is small for the known failures; hard guard against accidental blow-up.
    if product>500_000:return {'status':'SEARCH_SPACE_TOO_LARGE','period_option_counts':counts,'product':product}
    best_score=None;best=[]
    players=set(official)
    for _,opts in periods:
        for o in opts:
            players.update(o['seconds'])
    for choice in itertools.product(*[opts for _,opts in periods]):
        actual={p:0 for p in players}; violations=0
        for o in choice:
            violations+=int(o['violation_count'])
            for p,s in o['seconds'].items():actual[p]=actual.get(p,0)+int(s)
        diffs={p:int(actual.get(p,0)-official.get(p,0)) for p in players}
        absvals=[abs(v) for v in diffs.values()]
        score=(max(absvals) if absvals else 0,sum(absvals),violations)
        if best_score is None or score<best_score:
            best_score=score;best=[(choice,diffs,actual)]
        elif score==best_score:
            best.append((choice,diffs,actual))
    serial=[]
    for choice,diffs,actual in best[:50]:
        serial.append({
          'periods':[{'period':periods[i][0],'starters':o['starters'],'end_lineup':o['end_lineup'],'violation_count':o['violation_count'],'participant_violations':o['participant_violations']} for i,o in enumerate(choice)],
          'nonzero_second_diffs':{str(p):d for p,d in diffs.items() if d},
        })
    exact=best_score is not None and best_score[0]<=1 and best_score[1]<=10 and best_score[2]==0
    return {'status':'PASS_UNIQUE_EXACT' if exact and len(best)==1 else ('PASS_EXACT_AMBIGUOUS' if exact else 'REVIEW_REQUIRED'),
            'period_option_counts':counts,'search_product':product,'best_score':best_score,'best_solution_count':len(best),'solutions':serial}


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument('--raw',type=Path,required=True);ap.add_argument('--playerstatistics',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    ps=pd.read_csv(a.playerstatistics,usecols=['personId','gameId','playerteamId','numMinutes','startingPosition','firstName','lastName'],low_memory=False)
    results=[]
    for year,gids in FAIL_GAMES.items():
        nba_path=a.raw/f'nbastats_{year}.csv';v3_path=a.raw/f'nbastatsv3_{year}.csv'
        if not nba_path.exists():
            for gid in gids:results.append({'year':year,'game_id':gid,'status':'LEGACY_SOURCE_MISSING'})
            continue
        nba=histio.normalize_nba(pd.read_csv(nba_path,low_memory=False)); v3=eng.normalize_v3(pd.read_csv(v3_path,low_memory=False))
        for gid in gids:
            game=nba[nba.GAME_ID.eq(gid)].copy();vg=v3[v3.gameId.eq(gid)].copy();box=game_box(ps,gid)
            row={'year':year,'game_id':gid,'legacy_rows':len(game),'v3_rows':len(vg),'box_rows':len(box)}
            if game.empty or box.empty:
                row['status']='SOURCE_MISSING';results.append(row);continue
            prepared,_=eng.legacy.prepare_nba_game(game)
            prepared=prepared.copy();prepared['DESCRIPTION_NORM']=eng.core.nba_description(prepared);prepared['ELAPSED']=[eng.core.elapsed_seconds(int(p),c) for p,c in zip(prepared.PERIOD,prepared.PCTIMESTRING)]
            omap=eng._v3_action_map(vg);prepared['V3_ORDER']=[omap.get((int(p),int(ev)),10_000_000+int(ev)) for p,ev in zip(prepared.PERIOD,prepared.EVENTNUM)]
            prepared=prepared.sort_values(['PERIOD','ELAPSED','V3_ORDER','EVENTNUM'],kind='stable')
            pt=eng.core._player_team(prepared)
            for r in box.itertuples(index=False):pt[int(r.personId)]=int(r.playerteamId)
            teams=sorted(set(int(x) for x in box.playerteamId.dropna()))
            team_results={}
            for tid in teams:
                b=box[box.playerteamId.eq(tid)]
                official={int(r.personId):int(r.seconds_official) for r in b.itertuples(index=False)}
                roster=set(official)
                starters=set(int(r.personId) for r in b.itertuples(index=False) if str(r.startingPosition).strip() not in {'','nan','None'})
                popts=[]
                for pnum,period in prepared.groupby('PERIOD',sort=True):
                    period=period.sort_values(['ELAPSED','V3_ORDER','EVENTNUM'],kind='stable')
                    popts.append((int(pnum),period_options(period,tid,pt,roster,starters if int(pnum)==1 else None)))
                team_results[str(tid)]=solve_team(popts,official)
                team_results[str(tid)]['official_starters']=sorted(starters)
            row['teams']=team_results
            statuses=[v['status'] for v in team_results.values()]
            row['status']='PASS_ALL_TEAMS_UNIQUE_EXACT' if statuses and all(s=='PASS_UNIQUE_EXACT' for s in statuses) else ('PASS_ALL_TEAMS_EXACT' if statuses and all(s.startswith('PASS_') for s in statuses) else 'REVIEW_REQUIRED')
            results.append(row)
    payload={'games':results,'status_counts':{s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n');print(json.dumps({'status_counts':payload['status_counts'],'games':[(r['game_id'],r['status']) for r in results]},indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
