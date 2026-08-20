#!/usr/bin/env python3
import argparse, gzip, io, json, zipfile
from pathlib import Path
import pandas as pd

EXACT_VERDICT = 'EXACT_TRANSACTION_STATE_SCHEDULE_TENURE_IDENTITY'
TENURE_STATUS = 'NO_EXACT_TENURE_IDENTITY'

def norm_key(df):
    out=df.copy()
    out['season']=out['season'].astype(str)
    out['player_id']=pd.to_numeric(out['player_id'], errors='raise').astype('int64')
    out['team_id']=pd.to_numeric(out['team_id'], errors='raise').astype('int64')
    return out

def parse_games(v):
    if pd.isna(v) or str(v).strip()=='' : return []
    return [str(x).strip() for x in str(v).split('|') if str(x).strip()]

def read_gzip_member(zf, name):
    with zf.open(name) as raw:
        with gzip.GzipFile(fileobj=raw) as gz:
            return pd.read_csv(gz)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--authoritative-zip', required=True)
    ap.add_argument('--resolution-csv', required=True)
    ap.add_argument('--out-dir', required=True)
    args=ap.parse_args()
    out=Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.authoritative_zip) as z:
        qa=json.load(z.open('TREB_SHARED_GAME_RECLOSURE_QA.json'))
        diag=norm_key(pd.read_csv(z.open('TREB_SHARED_GAME_RECLOSURE_DIAGNOSTICS.csv')))
        blockers=norm_key(pd.read_csv(z.open('AUTONOMOUS_BLOCKER_MANIFEST.csv')))
        pg=read_gzip_member(z, 'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz')
        tg=read_gzip_member(z, 'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz')

    if int(qa.get('ending_production_resolved_full_core', -1)) != 9084 or int(qa.get('ending_residual', -1)) != 563:
        raise SystemExit('authoritative checkpoint drift: expected 9084/563')
    if qa.get('integrity',{}).get('empirical_model_used') or qa.get('integrity',{}).get('rounded_percentage_backsolve_used') or qa.get('integrity',{}).get('opponent_rebound_inference_used') or qa.get('integrity',{}).get('partial_tenure_whole_team_subtraction_used'):
        raise SystemExit('authoritative integrity gate failed')

    ten=diag[diag['status'].eq(TENURE_STATUS)].copy()
    if len(ten)!=21:
        raise SystemExit(f'expected 21 tenure blockers, found {len(ten)}')
    res=norm_key(pd.read_csv(args.resolution_csv))
    exact=res[res['verdict'].eq(EXACT_VERDICT)].copy()
    # Fail closed if the retained exact resolution is not a subset of current tenure blockers.
    keys=['season','player_id','team_id']
    exact=exact.merge(ten[keys+['missing_player_count','missing_team_count','bad_count']], on=keys, how='inner', validate='one_to_one')
    if exact.empty:
        raise SystemExit('no current exact tenure resolutions matched authoritative blockers')

    pg=pg.copy(); tg=tg.copy()
    for d in (pg,tg):
        d['season']=d['season'].astype(str)
        d['team_id']=pd.to_numeric(d['team_id'], errors='raise').astype('int64')
        d['game_id']=d['game_id'].astype(str).str.replace(r'\.0$','',regex=True)
    pg['player_id']=pd.to_numeric(pg['player_id'], errors='raise').astype('int64')

    rows=[]
    for _,r in exact.iterrows():
        games=parse_games(r['accepted_game_ids'])
        uniq=set(games)
        expected=int(r['expected'])
        sched=int(r['schedule_games'])
        psub=pg[(pg.season==r.season)&(pg.player_id==r.player_id)&(pg.team_id==r.team_id)]
        tsub=tg[(tg.season==r.season)&(tg.team_id==r.team_id)]
        pg_out=sorted(set(psub.game_id)-uniq)
        tg_out=sorted(set(tsub.game_id)-uniq)
        schedule_ok=(len(games)==expected==sched and len(uniq)==expected)
        current_missing_zero=(int(r['missing_player_count'])==0 and int(r['missing_team_count'])==0 and int(r['bad_count'])==0)
        conflict_free=(not pg_out and not tg_out)
        verdict='READY_FOR_AUTHORITATIVE_RECLOSURE' if schedule_ok and current_missing_zero and conflict_free else 'FAIL_CLOSED'
        rows.append({
            'season':r.season,'player_id':int(r.player_id),'team_id':int(r.team_id),
            'expected_games':expected,'accepted_games':len(games),'unique_games':len(uniq),
            'schedule_ok':schedule_ok,'current_missing_primitives_zero':current_missing_zero,
            'shared_player_rows':len(psub),'shared_team_rows':len(tsub),
            'shared_player_games_outside_exact_tenure':'|'.join(pg_out),
            'shared_team_games_outside_exact_tenure':'|'.join(tg_out),
            'conflict_free_against_injected_shared_ledgers':conflict_free,
            'verdict':verdict,
            'tenure_source':'pinned_static_schedule+high_confidence_transaction_state'
        })
    detail=pd.DataFrame(rows).sort_values(keys)
    detail.to_csv(out/'TREB_CURRENT_12_TENURE_RECLOSURE_PREFLIGHT.csv', index=False)
    ready=int(detail['verdict'].eq('READY_FOR_AUTHORITATIVE_RECLOSURE').sum())
    summary={
        'authoritative_production_resolved':int(qa['ending_production_resolved_full_core']),
        'authoritative_residual':int(qa['ending_residual']),
        'current_tenure_blockers':len(ten),
        'exact_transaction_schedule_resolutions_matched':len(detail),
        'ready_for_authoritative_reclosure':ready,
        'failed_closed':int(len(detail)-ready),
        'zero_numeric_conflicts_in_authoritative':bool(qa.get('zero_numeric_conflicts', True)),
        'minutes_gate_seconds':float(qa['integrity'].get('minutes_gate_seconds',60.0)),
        'note':'Readiness only. No production mutation. Existing authoritative reclosure/minutes/materiality gates remain mandatory.'
    }
    (out/'TREB_CURRENT_12_TENURE_RECLOSURE_PREFLIGHT_SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    if ready==0:
        raise SystemExit('zero exact tenure rows passed bounded preflight')

if __name__=='__main__': main()
