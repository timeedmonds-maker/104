#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from pathlib import Path

import pandas as pd
from rapidfuzz.distance import Levenshtein

import run_local_treb_production as io
import production_treb_engine_v3 as engine
import canary_v3_lineup_repair as ambiguity
import local_treb_rebuild as rebound


def parse_list(text: str, label: str):
    m = re.search(rf"{re.escape(label)}=(\[[^\]]*\])", text)
    if not m:
        return None
    try:
        return [int(x) for x in ast.literal_eval(m.group(1))]
    except Exception:
        return None


def prepare(nba_game: pd.DataFrame, v3_game: pd.DataFrame):
    prepared, _ = engine.legacy.prepare_nba_game(nba_game)
    prepared = prepared.copy()
    prepared['DESCRIPTION_NORM'] = engine.core.nba_description(prepared)
    prepared['ELAPSED'] = [engine.core.elapsed_seconds(int(p), c) for p, c in zip(prepared.PERIOD, prepared.PCTIMESTRING)]
    order_map = engine._v3_action_map(v3_game)
    prepared['V3_ORDER'] = [order_map.get((int(p), int(ev)), 10_000_000 + int(ev)) for p, ev in zip(prepared.PERIOD, prepared.EVENTNUM)]
    return prepared


def suffix_legal(period: pd.DataFrame, team_id: int, player_team: dict[int, int], lineup: set[int], start_pos: int):
    lineup = set(lineup)
    for pos in range(start_pos, len(period)):
        row = period.iloc[pos]
        typ = int(row.EVENTMSGTYPE)
        if typ == 8:
            st = engine._sub_team(row, player_team, {team_id: lineup})
            if st == team_id:
                outp, inp = int(row.PLAYER1_ID or 0), int(row.PLAYER2_ID or 0)
                if outp not in lineup or inp in lineup:
                    return False, {'kind':'substitution','event_num':int(row.EVENTNUM),'out':outp,'in':inp,'lineup':sorted(lineup)}
                lineup.remove(outp); lineup.add(inp)
                continue
        for pid in engine._team_participants(row, team_id):
            if pid not in lineup:
                return False, {'kind':'participant','event_num':int(row.EVENTNUM),'player_id':pid,'event_type':typ,'lineup':sorted(lineup)}
    return True, None


def analyze_missing(error: str, nba_game: pd.DataFrame, v3_game: pd.DataFrame, pbp_game: pd.DataFrame):
    m = re.search(r"game=(\d+) period=(\d+) team=(\d+):", error)
    if not m:
        return {'class':'missing_transition','diagnostic':'parse_failed'}
    gid, period_no, team_id = map(int, m.groups())
    starters = parse_list(error, 'starters') or []
    vm = re.search(r"violations=(\[.*\])$", error)
    violations = []
    if vm:
        try: violations = ast.literal_eval(vm.group(1))
        except Exception: pass
    if not violations:
        return {'class':'missing_transition','game_id':gid,'diagnostic':'no_violation_payload'}
    first = violations[0]
    target_event = int(first['event_num']); incoming = int(first['player_id'])

    prepared = prepare(nba_game, v3_game)
    period = prepared[prepared.PERIOD.eq(period_no)].sort_values(['ELAPSED','V3_ORDER','EVENTNUM'], kind='stable').reset_index(drop=True)
    player_team = engine.core._player_team(prepared)
    lineup = set(starters)
    period_start = (period_no - 1) * 720 if period_no <= 4 else 2880 + (period_no - 5) * 300
    last_change_elapsed = period_start
    target_pos = None
    target_row = None
    for pos, row in period.iterrows():
        if int(row.EVENTNUM) == target_event:
            target_pos = int(pos); target_row = row; break
        if int(row.EVENTMSGTYPE) == 8:
            st = engine._sub_team(row, player_team, {team_id: lineup})
            if st == team_id:
                outp, inp = int(row.PLAYER1_ID or 0), int(row.PLAYER2_ID or 0)
                if outp in lineup and inp not in lineup:
                    lineup.remove(outp); lineup.add(inp); last_change_elapsed = int(row.ELAPSED)
    if target_pos is None:
        return {'class':'missing_transition','game_id':gid,'diagnostic':'target_event_missing'}

    participants = engine._team_participants(target_row, team_id)
    candidates = [p for p in sorted(lineup) if p not in participants]
    legal = []
    for outgoing in candidates:
        trial = set(lineup)
        if incoming in trial: continue
        trial.remove(outgoing); trial.add(incoming)
        ok, blocker = suffix_legal(period, team_id, player_team, trial, target_pos)
        if ok:
            legal.append(outgoing)

    target_elapsed = int(target_row.ELAPSED)
    pbpr = pbp_game[pbp_game.DESCRIPTION.fillna('').str.contains('rebound', case=False)].copy()
    if not pbpr.empty:
        pbpr['START_ELAPSED']=[rebound.elapsed_seconds(int(p),c) for p,c in zip(pbpr.PERIOD,pbpr.STARTTIME)]
        pbpr['END_ELAPSED']=[rebound.elapsed_seconds(int(p),c) for p,c in zip(pbpr.PERIOD,pbpr.ENDTIME)]
        window = pbpr[(pbpr.PERIOD.eq(period_no)) & (pbpr.END_ELAPSED.ge(last_change_elapsed)) & (pbpr.START_ELAPSED.le(target_elapsed))]
        rebound_rows_in_uncertainty = int(len(window))
    else:
        rebound_rows_in_uncertainty = 0

    role = []
    for n in (1,2,3):
        if int(target_row.get(f'PLAYER{n}_ID',0) or 0) == incoming:
            role.append(f'PLAYER{n}')
    return {
        'class':'missing_transition','game_id':gid,'period':period_no,'team_id':team_id,
        'event_num':target_event,'event_type':int(target_row.EVENTMSGTYPE),'participant_role':role,
        'description':str(target_row.DESCRIPTION_NORM),'incoming':incoming,'lineup_before':sorted(lineup),
        'candidate_outgoing':candidates,'legal_outgoing':legal,'legal_outgoing_count':len(legal),
        'last_real_lineup_change_elapsed':last_change_elapsed,'first_conflict_elapsed':target_elapsed,
        'rebound_rows_in_uncertainty_window':rebound_rows_in_uncertainty,
        'safe_single_swap_for_rebound_counts':bool(len(legal)==1 and rebound_rows_in_uncertainty==0),
    }


def dist(a,b):
    n=max(len(a),len(b)); return Levenshtein.distance(a,b)/n if n else 0.0


def unmatched_count(lineups, pbp_game, alpha, threshold):
    ordered=pbp_game.copy()
    rb=ordered[ordered.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    rb['DESCRIPTION_NORM']=rb.DESCRIPTION.map(rebound.normalize_description)
    rb['START_ELAPSED']=[rebound.elapsed_seconds(int(p),c) for p,c in zip(rb.PERIOD,rb.STARTTIME)]
    rb['END_ELAPSED']=[rebound.elapsed_seconds(int(p),c) for p,c in zip(rb.PERIOD,rb.ENDTIME)]
    nba=lineups.events
    unmatched=0; ambiguous=0
    for _,row in rb.iterrows():
        cand=nba[(nba.PERIOD.eq(row.PERIOD)) & (nba.ELAPSED.gt(row.START_ELAPSED-alpha)) & (nba.ELAPSED.lt(row.END_ELAPSED+alpha))]
        scores=[(dist(row.DESCRIPTION_NORM,d),int(ev)) for ev,d in zip(cand.EVENTNUM,cand.DESCRIPTION_NORM)]
        ok=[x for x in scores if x[0] < threshold]
        if not ok: unmatched += 1
        elif len(ok)>1: ambiguous += 1
    return unmatched, ambiguous


def analyze_unmatched(error, nba_game, v3_game, pbp_game):
    gid=int(re.search(r'game=(\d+)',error).group(1))
    lu=engine.reconstruct_game_lineups(nba_game,v3_game)
    trials=[]
    for alpha,threshold in [(5,.2),(8,.2),(12,.2),(5,.25),(8,.25),(12,.25),(12,.30)]:
        u,a=unmatched_count(lu,pbp_game,alpha,threshold)
        trials.append({'alpha':alpha,'threshold':threshold,'unmatched':u,'ambiguous':a})
    clean=[x for x in trials if x['unmatched']==0]
    return {'class':'unmatched_rebound','game_id':gid,'trials':trials,'resolvable_by_bounded_match_expansion':bool(clean),'best_clean_trial':clean[0] if clean else None}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--failed',type=Path,required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    season=f"{a.year}-{(a.year+1)%100:02d}"
    rows=[]
    with a.failed.open(encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r['season']==season: rows.append(r)
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    out=[]
    for i,r in enumerate(rows,1):
        gid=int(r['game_id']); err=r['error']; rec={'season':season,'game_id':gid,'original_error':err}
        try:
            if 'missing in-period lineup transition' in err:
                rec.update(analyze_missing(err,ng[gid],vg[gid],pg[gid]))
            elif 'unmatched PBP rebound rows' in err:
                rec.update(analyze_unmatched(err,ng[gid],vg[gid],pg[gid]))
            elif 'non-unique v3/team-local starter solution' in err:
                search=ambiguity.search_full_game_repairs(ng[gid],vg[gid],gid)
                rec.update({'class':'nonunique_starter','full_game_solution_count':search.get('full_game_solution_count'),'search_complete':search.get('search_complete'),'terminal_failure_count':search.get('terminal_failure_count')})
            else:
                rec['class']='other'
        except Exception as exc:
            rec['diagnostic_error']=f'{type(exc).__name__}: {exc}'
        out.append(rec)
        if i%10==0 or i==len(rows): print(f'DIAG year={a.year} {i}/{len(rows)}',flush=True)
    summary={'year':a.year,'season':season,'failed_games':len(rows),'records':out}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'year':a.year,'failed_games':len(rows),'classes':pd.Series([x.get('class') for x in out]).value_counts().to_dict()},indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
