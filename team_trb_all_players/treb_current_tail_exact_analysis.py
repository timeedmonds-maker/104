#!/usr/bin/env python3
"""Exact, fail-closed analysis of the CURRENT 19 validation + 21 tenure TREB tail.

The authoritative residual diagnostics are read at runtime; no tail IDs are hard-coded.
This lane uses the retained exact roster/game ledger and canonical player-team-season targets
only to prove tenure identity and official player-game minute compatibility. It does not
promote TREB values. Any later promotion must use these proofs against the then-current
exact rebound primitive universe and reclosure gate.
"""
from __future__ import annotations
import argparse, csv, gzip, json, math
from collections import defaultdict
from pathlib import Path

MINUTES_GATE_SECONDS = 60.0


def sid(v):
    s = str(v).strip()
    if s.endswith('.0') and s[:-2].isdigit(): s = s[:-2]
    return s

def tid(v): return str(int(float(v)))
def gid(v):
    s=sid(v)
    try:return str(int(float(s))).zfill(10)
    except:return s.zfill(10)
def finite(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except:return None

def key(r): return (str(r['season']),tid(r['team_id']),sid(r['player_id']))


def load_targets(path: Path, wanted: set[tuple[str,str,str]]):
    out={}
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            if not line.strip(): continue
            r=json.loads(line)
            try:k=key(r)
            except:continue
            if k in wanted: out[k]=r
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--current-dir',required=True)
    ap.add_argument('--repo-root',default='.')
    ap.add_argument('--out-dir',required=True)
    a=ap.parse_args()
    cur=Path(a.current_dir); repo=Path(a.repo_root); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)

    diag_path=cur/'TREB_SHARED_GAME_RECLOSURE_DIAGNOSTICS.csv'
    if not diag_path.exists(): raise FileNotFoundError(diag_path)
    diag=list(csv.DictReader(diag_path.open(newline='',encoding='utf-8')))
    validation={key(r) for r in diag if r.get('status')=='BLOCKED_VALIDATION'}
    tenure={key(r) for r in diag if r.get('status')=='NO_EXACT_TENURE_IDENTITY'}
    if len(validation)!=19 or len(tenure)!=21:
        raise RuntimeError(f'AUTHORITATIVE_TAIL_DRIFT validation={len(validation)} tenure={len(tenure)}')
    wanted=validation|tenure

    target_path=repo/'team_trb_all_players/impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz'
    ledger_path=repo/'team_trb_all_players/impact_database/roster_tenure_v3/player_game_roster_ledger.csv.gz'
    if not target_path.exists(): raise FileNotFoundError(target_path)
    if not ledger_path.exists(): raise FileNotFoundError(ledger_path)
    targets=load_targets(target_path,wanted)

    # Ledger is retained exact game/roster participation evidence. Preserve team schedule order
    # using a date-like field when available, then game id as deterministic fallback.
    team_games=defaultdict(dict)   # (season,team)->game->{date/order evidence}
    player_games=defaultdict(dict) # key->game->seconds_game
    ledger_fields=[]
    with gzip.open(ledger_path,'rt',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f); ledger_fields=list(rd.fieldnames or [])
        date_field=next((z for z in ('game_date','GAME_DATE','date','GAME_DATE_EST') if z in ledger_fields),None)
        for r in rd:
            try:s=str(r['season']); t=tid(r['team_id']); g=gid(r['game_id']); p=sid(r['player_id'])
            except:continue
            dt=str(r.get(date_field,'')).strip() if date_field else ''
            team_games[(s,t)][g]=dt
            k=(s,t,p)
            if k in wanted:
                player_games[k][g]=finite(r.get('seconds_game'))

    diag_by={key(r):r for r in diag if key(r) in wanted}
    rows=[]; recovered=[]
    for k in sorted(wanted):
        s,t,p=k; tr=targets.get(k); dr=diag_by[k]
        rec={'season':s,'team_id':t,'player_id':p,
             'prior_status':dr.get('status',''),'prior_tenure_identity_source':dr.get('tenure_identity_source',''),
             'prior_minutes_delta_seconds':finite(dr.get('minutes_delta_seconds')),
             'target_record_present':bool(tr),'ledger_player_games':len(player_games.get(k,{}))}
        if tr is None:
            rec.update(status='UNRESOLVED',reason='NO_CANONICAL_TARGET_RECORD'); rows.append(rec); continue
        expected=int(float(tr.get('team_games_in_tenure') or 0))
        target_sec=finite(tr.get('seconds_on'))
        if target_sec is None:
            m=finite(tr.get('minutes_on')); target_sec=None if m is None else m*60.0
        rec.update(expected_team_games=expected,target_seconds=target_sec)
        games_map=team_games.get((s,t),{})
        # If date strings exist for every game, use date then game id; otherwise game id.
        use_dates=bool(games_map) and all(games_map[g] for g in games_map)
        sched=sorted(games_map,key=(lambda g:(games_map[g],g)) if use_dates else (lambda g:g))
        rec.update(schedule_games=len(sched),schedule_order_source='ledger_game_date' if use_dates else 'game_id_order')
        known=set(player_games.get(k,{}))
        if expected<=0 or target_sec is None or len(sched)<expected:
            rec.update(status='UNRESOLVED',reason='INSUFFICIENT_TARGET_OR_TEAM_SCHEDULE'); rows.append(rec); continue

        candidates=[]
        for i in range(len(sched)-expected+1):
            window=tuple(sched[i:i+expected]); ws=set(window)
            if not known.issubset(ws): continue
            # Exact official seconds across the proposed tenure. Missing ledger rows are not
            # silently treated as DNP: every game needs a player ledger row with finite seconds.
            vals=[]; complete=True
            for g in window:
                if g not in player_games.get(k,{}) or player_games[k][g] is None:
                    complete=False; break
                vals.append(float(player_games[k][g]))
            if not complete: continue
            sec=sum(vals); delta=sec-target_sec
            if abs(delta)<=MINUTES_GATE_SECONDS+1e-9:
                candidates.append((window,sec,delta))
        rec['minute_compatible_contiguous_candidates']=len(candidates)
        if len(candidates)!=1:
            rec.update(status='UNRESOLVED',reason='NO_UNIQUE_EXACT_LEDGER_TENURE'); rows.append(rec); continue
        games,sec,delta=candidates[0]
        rec.update(status='PROVEN_UNIQUE_EXACT_LEDGER_TENURE',reason='unique contiguous team-schedule window containing all exact roster-ledger evidence and satisfying <=60-second official-minutes gate',
                   proven_seconds=sec,proven_minutes_delta_seconds=delta,proven_game_ids='|'.join(games))
        rows.append(rec); recovered.append(dict(rec))

    fields=sorted({x for r in rows for x in r})
    with (out/'TREB_CURRENT_40_EXACT_TAIL_AUDIT.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    rf=sorted({x for r in recovered for x in r}) if recovered else fields
    with (out/'TREB_CURRENT_40_PROVEN_TENURES.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=rf);w.writeheader();w.writerows(recovered)
    by_status=defaultdict(int)
    by_prior=defaultdict(int)
    for r in rows:
        by_status[r['status']]+=1;by_prior[r['prior_status']]+=1
    qa={'status':'PASS_DIAGNOSTIC','authoritative_validation_keys':len(validation),'authoritative_tenure_keys':len(tenure),
        'target_records_found':len(targets),'proven_unique_exact_ledger_tenures':len(recovered),
        'proven_validation_keys':sum(r['prior_status']=='BLOCKED_VALIDATION' for r in recovered),
        'proven_prior_no_tenure_keys':sum(r['prior_status']=='NO_EXACT_TENURE_IDENTITY' for r in recovered),
        'result_status_counts':dict(by_status),'prior_status_counts':dict(by_prior),'minutes_gate_seconds':MINUTES_GATE_SECONDS,
        'promotion_performed':False,
        'next':'Use only proven tenure/minutes rows as repair proofs; re-run current exact rebound reclosure before any promotion.',
        'integrity':{'current_authoritative_ids_dynamic':True,'ambiguous_tenures_promoted':False,'missing_ledger_games_treated_as_zero':False,
                     'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'partial_tenure_whole_team_subtraction_used':False}}
    (out/'TREB_CURRENT_40_EXACT_TAIL_QA.json').write_text(json.dumps(qa,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(qa,indent=2))

if __name__=='__main__': main()
