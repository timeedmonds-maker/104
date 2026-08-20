import argparse,csv,gzip,json,pathlib,collections,shutil

REQP=('seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on')
REQT=('team_oreb','team_dreb','opponent_oreb','opponent_dreb')

def pid(x): return str(x).strip().removesuffix('.0')
def tid(x): return str(int(float(x)))
def gid(x):
    s=str(x).strip().removesuffix('.0')
    try:return str(int(float(s))).zfill(10)
    except:return s.zfill(10)
def key(r): return (str(r['season']),tid(r['team_id']),pid(r['player_id']))
def one(root,pat):
    m=list(pathlib.Path(root).rglob(pat)); assert len(m)==1,(root,pat,len(m)); return m[0]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--current',required=True)
    ap.add_argument('--consensus',required=True)
    ap.add_argument('--pbp',required=True)
    ap.add_argument('--old',required=True)
    ap.add_argument('--mid',required=True)
    ap.add_argument('--newer',required=True)
    ap.add_argument('--game',required=True)
    ap.add_argument('--currentcons',required=True)
    ap.add_argument('--resolution',required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args()
    OUT=pathlib.Path(a.out); OUT.mkdir(parents=True,exist_ok=True)

    current=list(csv.DictReader(open(one(a.current,'AUTONOMOUS_BLOCKER_MANIFEST.csv'),newline='')))
    prior=list(csv.DictReader(open(one(a.current,'TREB_CUMULATIVE_EXACT_PROMOTED.csv'),newline='')))
    mat=list(csv.DictReader(open(one(a.current,'TREB_MATERIALITY_ACCEPTED.csv'),newline='')))
    assert len(current)==563,len(current); assert len(prior)==683,len(prior); assert len(mat)==4,len(mat)
    current_by={key(r):r for r in current}; prior_keys={key(r) for r in prior}; mat_keys={key(r) for r in mat}

    tenure_games={}
    with open(a.resolution,newline='') as f:
        for r in csv.DictReader(f):
            if r.get('verdict')!='EXACT_TRANSACTION_STATE_SCHEDULE_TENURE_IDENTITY': continue
            k=key(r); games={gid(x) for x in str(r.get('accepted_game_ids') or '').split('|') if x.strip()}
            expected=int(float(r['expected']))
            assert len(games)==expected,(k,len(games),expected)
            assert k in current_by,('not_current_blocker',k)
            tenure_games[k]=games
    assert len(tenure_games)==12,len(tenure_games)
    assert not (set(tenure_games)&prior_keys); assert not (set(tenure_games)&mat_keys)

    facts=collections.defaultdict(dict); fsrc=collections.defaultdict(dict)
    team=collections.defaultdict(dict); tsrc=collections.defaultdict(dict)
    stale=[]
    def putp(k,z,v,src,authoritative=False):
        v=float(v); old=facts[k].get(z); oldsrc=fsrc[k].get(z)
        if old is None:
            facts[k][z]=v; fsrc[k][z]=src; return
        if abs(old-v)<=1e-9: return
        if oldsrc=='current_shared':
            stale.append({'kind':'player','game_id':k[0],'team_id':k[1],'player_id':k[2],'field':z,'authoritative_value':old,'stale_value':v,'stale_source':src})
            return
        if authoritative:
            stale.append({'kind':'player','game_id':k[0],'team_id':k[1],'player_id':k[2],'field':z,'authoritative_value':v,'stale_value':old,'stale_source':oldsrc})
            facts[k][z]=v; fsrc[k][z]=src; return
        raise SystemExit(f'NONAUTHORITATIVE_PLAYER_CONFLICT {k} {z} {old} {oldsrc} {v} {src}')
    def putt_field(k,z,v,src,authoritative=False):
        v=float(v); old=team[k].get(z); oldsrc=tsrc[k].get(z)
        if old is None:
            team[k][z]=v; tsrc[k][z]=src; return
        if abs(old-v)<=1e-9:return
        if oldsrc=='current_shared':
            stale.append({'kind':'team','game_id':k[0],'team_id':k[1],'player_id':'','field':z,'authoritative_value':old,'stale_value':v,'stale_source':src}); return
        if authoritative:
            stale.append({'kind':'team','game_id':k[0],'team_id':k[1],'player_id':'','field':z,'authoritative_value':v,'stale_value':old,'stale_source':oldsrc})
            team[k][z]=v; tsrc[k][z]=src; return
        raise SystemExit(f'NONAUTHORITATIVE_TEAM_CONFLICT {k} {z} {old} {oldsrc} {v} {src}')

    # Load the controlling checkpoint first. These exact shared primitives are authoritative on overlap.
    sp=one(a.current,'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz')
    with gzip.open(sp,'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
            for z in REQP: putp(k,z,r[z],'current_shared',True)
    st=one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz')
    with gzip.open(st,'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']))
            for z in REQT: putt_field(k,z,r[z],'current_shared',True)

    # Historical exact layers may fill gaps, but cannot override the controlling checkpoint.
    cp=one(a.consensus,'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv')
    with open(cp,newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']),pid(r.get('player_id','')))
            if r['field'] in REQP: putp(k,r['field'],r['value'],'consensus')
            elif not k[2] and r['field'] in REQT: putt_field((k[0],k[1]),r['field'],r['value'],'consensus')
    def inject_gz(root,pat,src):
        p=one(root,pat)
        with gzip.open(p,'rt',encoding='utf-8',newline='') as f:
            for r in csv.DictReader(f):
                k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
                for z in REQP: putp(k,z,r[z],src)
    inject_gz(a.old,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','old')
    inject_gz(a.mid,'RECOVERED_927_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','mid')
    inject_gz(a.newer,'RECOVERED_2012_2022_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','newer')
    for root,pat,src in [(a.game,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv','game'),(a.currentcons,'RECOVERED_CURRENT_EXACT_PLAYER_GAME_PRIMITIVES.csv','currentcons')]:
        p=one(root,pat)
        with open(p,newline='') as f:
            for r in csv.DictReader(f):
                k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
                for z in REQP: putp(k,z,r[z],src)
    pp=one(a.pbp,'TREB_143_V2_PBP_EXACT_AUDIT.csv')
    with open(pp,newline='') as f:
        for r in csv.DictReader(f):
            if r.get('status')=='PASS_EXACT' and str(r.get('validation_pass')).lower() in {'true','1'}:
                k=(gid(r['game_id']),tid(r['team_id']))
                for z in REQT: putt_field(k,z,r[z],'pbp_exact')
    gp=one(a.game,'RECOVERED_EXACT_TEAM_GAME_FACTS.csv')
    with open(gp,newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']))
            for z in REQT: putt_field(k,z,r[z],'game_exact')

    targets={}
    with gzip.open('team_trb_all_players/impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:
            if line.strip():
                r=json.loads(line); k=key(r)
                if k in tenure_games: targets[k]=r
    assert len(targets)==12,len(targets)
    ledger_seconds={}
    with gzip.open('team_trb_all_players/impact_database/roster_tenure_v3/player_game_roster_ledger.csv.gz','rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            try:k=key(r)
            except:continue
            if k in tenure_games: ledger_seconds[(k,gid(r['game_id']))]=float(r.get('seconds_game') or 0)

    new=[]; diag=[]; reasons=collections.Counter()
    for k,games in sorted(tenure_games.items()):
        t=targets[k]; agg=collections.Counter(); mt=[]; mp=[]; bad=[]; zero=0
        assert len(games)==int(float(t.get('team_games_in_tenure') or 0)),(k,len(games),t.get('team_games_in_tenure'))
        for g in sorted(games):
            tv=team.get((g,k[1]),{})
            if not all(z in tv for z in REQT): mt.append(g); continue
            pv=facts.get((g,k[1],k[2]),{})
            if not all(z in pv for z in REQP):
                sec=ledger_seconds.get((k,g))
                if sec is not None and abs(sec)<=1e-9:
                    pv={z:0.0 for z in REQP}; zero+=1
                else: mp.append(g); continue
            comps=[tv['team_oreb']-pv['team_oreb_on'],tv['team_dreb']-pv['team_dreb_on'],tv['opponent_oreb']-pv['opponent_oreb_on'],tv['opponent_dreb']-pv['opponent_dreb_on']]
            if min(comps)<-1e-9: bad.append(g); continue
            for z in REQP: agg[z]+=pv[z]
            for z in REQT: agg[z]+=tv[z]
        delta=abs(agg['seconds_on']-float(t.get('seconds_on') or 0))
        if mt or mp or bad or delta>60:
            status='BLOCKED_MISSING_PRIMITIVES' if (mt or mp) else 'BLOCKED_VALIDATION'; reasons[status]+=1
            diag.append({'season':k[0],'team_id':k[1],'player_id':k[2],'status':status,'minutes_delta_seconds':delta,'missing_team_count':len(mt),'missing_player_count':len(mp),'bad_count':len(bad),'zero_second_facts_used':zero,'detail':json.dumps({'missing_team':mt,'missing_player':mp,'bad':bad},separators=(',',':'))})
            continue
        tr_on=agg['team_oreb_on']+agg['team_dreb_on']; op_on=agg['opponent_oreb_on']+agg['opponent_dreb_on']; tr=agg['team_oreb']+agg['team_dreb']; op=agg['opponent_oreb']+agg['opponent_dreb']; tr_off=tr-tr_on; op_off=op-op_on
        if min(tr_on,op_on,tr_off,op_off)<-1e-9 or tr_on+op_on<=0 or tr_off+op_off<=0:
            reasons['BLOCKED_DENOMINATOR_OR_NEGATIVE']+=1
            diag.append({'season':k[0],'team_id':k[1],'player_id':k[2],'status':'BLOCKED_DENOMINATOR_OR_NEGATIVE','minutes_delta_seconds':delta,'missing_team_count':0,'missing_player_count':0,'bad_count':0,'zero_second_facts_used':zero,'detail':''}); continue
        on=100*tr_on/(tr_on+op_on); off=100*tr_off/(tr_off+op_off)
        new.append({'metric':'TotalReboundPct','off_corrected':off,'on':on,'on_minus_off_corrected':on-off,'player_id':k[2],'provenance':'exact transaction-state + exact pinned static schedule tenure identity; controlling-checkpoint exact shared primitives preferred on overlap; older exact layers gap-fill only','season':k[0],'seconds_on':agg['seconds_on'],'team_games_in_tenure':len(games),'team_id':k[1]})
        reasons['PROMOTED_EXACT']+=1

    new_keys={key(r) for r in new}; assert new_keys.issubset(current_by)
    cumulative=prior+new; remain=[r for r in current if key(r) not in new_keys]
    assert len(cumulative)==683+len(new); assert len(remain)==563-len(new); assert len(cumulative)+len(remain)+len(mat)==1250
    def write(path,rows,fields=None):
        if fields is None: fields=sorted({x for r in rows for x in r}) if rows else []
        with open(path,'w',newline='') as f:
            if fields:
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    write(OUT/'TREB_NEW_EXACT_PROMOTED.csv',new)
    write(OUT/'TREB_CUMULATIVE_EXACT_PROMOTED.csv',cumulative)
    write(OUT/'AUTONOMOUS_BLOCKER_MANIFEST.csv',remain,current[0].keys())
    write(OUT/'TREB_CURRENT_12_TENURE_RECLOSURE_DIAGNOSTICS.csv',diag)
    write(OUT/'TREB_STALE_SOURCE_CONFLICTS_SHADOWED_BY_CONTROLLING_CHECKPOINT.csv',stale)
    shutil.copy(one(a.current,'TREB_MATERIALITY_ACCEPTED.csv'),OUT/'TREB_MATERIALITY_ACCEPTED.csv')
    qa={'status':'PASS','starting_exact_full_core':9080,'starting_production_resolved':9084,'starting_residual':563,'exact_tenure_candidates_tested':12,'new_exact_promotions':len(new),'ending_exact_full_core':9080+len(new),'ending_production_resolved':9084+len(new),'ending_residual':563-len(new),'reason_counts':dict(sorted(reasons.items())),'shadowed_stale_conflicts':len(stale),'integrity':{'controlling_checkpoint_priority_on_overlap':True,'non_authoritative_conflicts_fail_closed':True,'exact_raw_counts_only':True,'exact_transaction_state_and_schedule_identity_required':True,'minutes_gate_seconds':60.0,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'partial_tenure_whole_team_subtraction_used':False,'unsafe_global_event_ordering_used':False}}
    (OUT/'TREB_CURRENT_12_TENURE_RECLOSURE_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n')
    print(json.dumps(qa,indent=2,sort_keys=True))

if __name__=='__main__': main()
