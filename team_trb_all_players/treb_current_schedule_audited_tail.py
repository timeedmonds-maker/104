#!/usr/bin/env python3
"""Dynamic current-checkpoint schedule-audited tenure proof for TREB tail.

Reuses the already-successful retained schedule-audited tenure methodology, but derives the
current 21 NO_EXACT_TENURE_IDENTITY and 19 BLOCKED_VALIDATION keys at runtime. Diagnostic/proof
only: no TREB values are promoted here.
"""
from __future__ import annotations
import argparse,csv,gzip,json,pathlib,collections,datetime

def pid(x):
    s=str(x).strip(); return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s
def tid(x): return str(int(float(x)))
def gid(x):
    s=pid(x)
    try:return str(int(float(s))).zfill(10)
    except:return s.zfill(10)
def key(r): return (str(r['season']),tid(r['team_id']),pid(r['player_id']))
def day(x):
    s=str(x or '').strip()
    if not s:return None
    return datetime.date.fromisoformat(s[:10])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--repo-root',default='.');ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    current=pathlib.Path(a.current_dir);root=pathlib.Path(a.repo_root);OUT=pathlib.Path(a.out_dir);OUT.mkdir(parents=True,exist_ok=True)
    BASE=root/'team_trb_all_players/impact_database'
    dp=current/'TREB_SHARED_GAME_RECLOSURE_DIAGNOSTICS.csv'
    diag=list(csv.DictReader(dp.open(newline='',encoding='utf-8')))
    noid={key(r):r for r in diag if r.get('status')=='NO_EXACT_TENURE_IDENTITY'}
    val={key(r):r for r in diag if r.get('status')=='BLOCKED_VALIDATION'}
    if len(noid)!=21 or len(val)!=19: raise RuntimeError(f'AUTHORITATIVE_TAIL_DRIFT noid={len(noid)} val={len(val)}')
    wanted=set(noid)|set(val)

    targets={}
    with gzip.open(BASE/'roster_tenure_v2/player_team_season_targets.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:
            if not line.strip():continue
            r=json.loads(line)
            try:k=key(r)
            except:continue
            if k in wanted:targets[k]=r
    if len(targets)!=40: raise RuntimeError(f'TARGET_RECORD_DRIFT {len(targets)}')

    windows=collections.defaultdict(list); wp=BASE/'roster_tenure/player_team_season_windows_schedule_audited.jsonl.gz'
    with gzip.open(wp,'rt',encoding='utf-8') as f:
        for line in f:
            if not line.strip():continue
            r=json.loads(line)
            try:k=key(r)
            except:continue
            if k in wanted:windows[k].append(r)

    sched_by=collections.defaultdict(list);sp=BASE/'roster_tenure/regular_season_games.jsonl.gz'
    with gzip.open(sp,'rt',encoding='utf-8') as f:
        for line in f:
            if not line.strip():continue
            g=json.loads(line); season=str(g.get('season') or '');gd=day(g.get('game_date'))
            if gd is None:continue
            for z in ('home_team_id','away_team_id'):
                try:t=tid(g.get(z))
                except:continue
                sched_by[(season,t)].append((gd,gid(g.get('game_id'))))

    ledger=collections.defaultdict(set);lp=BASE/'roster_tenure_v3/player_game_roster_ledger.csv.gz'
    with gzip.open(lp,'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            try:k=key(r)
            except:continue
            if k in wanted:ledger[k].add(gid(r.get('game_id')))

    rows=[];counts=collections.Counter(); exact_noid=0;exact_val=0
    for k in sorted(wanted):
        dr=noid.get(k) or val.get(k); cls=dr.get('status'); t=targets[k]; expected=int(float(t.get('team_games_in_tenure') or 0));ws=windows.get(k,[])
        game_union=set();bad=[];prov=[]
        if not ws:bad.append('NO_RETAINED_AUDITED_WINDOW')
        for w in ws:
            aa=day(w.get('tenure_start'));bb=day(w.get('tenure_end'))
            if aa is None or bb is None or aa>bb:bad.append('INVALID_OR_MISSING_WINDOW_DATES');continue
            seg={g for gd,g in sched_by.get((k[0],k[1]),[]) if aa<=gd<=bb}
            try:audited_count=int(float(w.get('team_games_in_window')))
            except:audited_count=None
            if audited_count is None:bad.append('NO_AUDITED_TEAM_GAME_COUNT')
            elif len(seg)!=audited_count:bad.append('AUDITED_COUNT_DRIFT')
            flags=w.get('audit_flags') or []
            if isinstance(flags,str):
                try:flags=json.loads(flags)
                except:flags=[flags]
            if any(str(x)=='invalid_boundary_order' for x in flags):bad.append('INVALID_BOUNDARY_ORDER')
            game_union|=seg
            prov.append({'tenure_start':str(w.get('tenure_start') or ''),'tenure_end':str(w.get('tenure_end') or ''),'team_games_in_window':audited_count,'start_source':str(w.get('start_source') or ''),'end_source':str(w.get('end_source') or ''),'confidence':str(w.get('confidence') or ''),'same_day_resolution':str(w.get('same_day_resolution') or ''),'audit_flags':flags})
        extra=sorted(ledger[k]-game_union)
        if bad:status='REJECT:'+','.join(sorted(set(bad)))
        elif len(game_union)!=expected:status='TARGET_GAME_COUNT_MISMATCH'
        elif extra:status='LEDGER_OUTSIDE_AUDITED_WINDOWS'
        elif expected<=0:status='NONPOSITIVE_EXPECTED_GAME_COUNT'
        else:status='EXACT_RETAINED_SCHEDULE_AUDITED_TENURE_IDENTITY'
        counts[(cls,status.split(':',1)[0])]+=1
        if status.startswith('EXACT_'):
            if cls=='NO_EXACT_TENURE_IDENTITY': exact_noid+=1
            if cls=='BLOCKED_VALIDATION': exact_val+=1
        rows.append({'season':k[0],'team_id':k[1],'player_id':k[2],'prior_status':cls,'prior_tenure_identity_source':dr.get('tenure_identity_source',''),'status':status,'expected_team_games':expected,'matched_windows':len(ws),'reconstructed_games':len(game_union),'ledger_games':len(ledger[k]),'extra_ledger_games':len(extra),'game_ids':'|'.join(sorted(game_union)),'window_provenance':json.dumps(prov,separators=(',',':'))})
    fields=sorted({z for r in rows for z in r});
    with (OUT/'TREB_CURRENT_40_SCHEDULE_AUDITED_TENURE_PROOF.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    proven=[r for r in rows if r['status'].startswith('EXACT_')]
    with (OUT/'TREB_CURRENT_40_PROVEN_SCHEDULE_TENURES.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(proven)
    qa={'status':'PASS_DIAGNOSTIC','authoritative_no_identity_keys':21,'authoritative_validation_keys':19,'exact_current_no_identity_tenures':exact_noid,'exact_current_validation_tenures':exact_val,'proven_total':len(proven),'reason_counts':{f'{a}|{b}':n for (a,b),n in sorted(counts.items())},'promotion_performed':False,'integrity':{'dynamic_current_ids':True,'retained_schedule_audited_windows_only':True,'exact_team_schedule_only':True,'target_team_game_count_equality_required':True,'existing_ledger_subset_required':True,'ambiguity_fails_closed':True,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'partial_tenure_whole_team_subtraction_used':False},'next':'Use only EXACT rows as current tenure proofs; validation rows then require exact minute-delta diagnosis before TREB reclosure.'}
    (OUT/'TREB_CURRENT_40_SCHEDULE_AUDITED_TENURE_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(qa,indent=2,sort_keys=True))
if __name__=='__main__':main()
