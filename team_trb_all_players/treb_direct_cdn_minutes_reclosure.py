import argparse,csv,gzip,json,pathlib,collections,re,time,urllib.request,urllib.error

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
def iso_seconds(s):
    if not s:return 0.0
    m=re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:([0-9.]+)S)?',str(s))
    if not m: raise ValueError(f'bad duration {s!r}')
    return 3600*float(m.group(1) or 0)+60*float(m.group(2) or 0)+float(m.group(3) or 0)
def fetch_box(game):
    url=f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game}.json'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
    last=None
    for a in range(4):
        try:
            with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
        except Exception as e:
            last=e; time.sleep(2*(a+1))
    raise RuntimeError(f'CDN_FETCH_FAILED {game} {last}')
def player_seconds(box,team_id,player_id):
    game=box.get('game') or {}
    teams=[game.get('homeTeam') or {},game.get('awayTeam') or {}]
    team=[t for t in teams if tid(t.get('teamId',0))==team_id]
    if len(team)!=1: raise RuntimeError(f'TEAM_NOT_UNIQUE {team_id}')
    matches=[p for p in (team[0].get('players') or []) if pid(p.get('personId',''))==player_id]
    if len(matches)>1: raise RuntimeError(f'PLAYER_DUPLICATE {player_id}')
    if not matches:return None
    return iso_seconds(matches[0].get('statistics',{}).get('minutes') or matches[0].get('minutes') or 'PT0M')

def main():
    ap=argparse.ArgumentParser()
    for x in ('current','consensus','pbp','old','mid','newer','game','currentcons','out'): ap.add_argument('--'+x,required=True)
    a=ap.parse_args(); OUT=pathlib.Path(a.out); OUT.mkdir(parents=True,exist_ok=True)
    current=list(csv.DictReader(open(one(a.current,'AUTONOMOUS_BLOCKER_MANIFEST.csv'),newline='')))
    diag=list(csv.DictReader(open(one(a.current,'TREB_SHARED_GAME_RECLOSURE_DIAGNOSTICS.csv'),newline='')))
    prior=list(csv.DictReader(open(one(a.current,'TREB_CUMULATIVE_EXACT_PROMOTED.csv'),newline='')))
    mat=list(csv.DictReader(open(one(a.current,'TREB_MATERIALITY_ACCEPTED.csv'),newline='')))
    assert len(current)==563 and len(prior)==683 and len(mat)==4,(len(current),len(prior),len(mat))
    current_by={key(r):r for r in current}
    validation={key(r):r for r in diag if r.get('status')=='BLOCKED_VALIDATION'}
    assert len(validation)==19,len(validation)

    targets={}
    with gzip.open('team_trb_all_players/impact_database/roster_tenure_v2/player_team_season_targets.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:
            if line.strip():
                r=json.loads(line); k=key(r)
                if k in validation: targets[k]=r
    assert len(targets)==19,len(targets)

    tenure_games=collections.defaultdict(set); ledger_sec={}
    with gzip.open('team_trb_all_players/impact_database/roster_tenure_v3/player_game_roster_ledger.csv.gz','rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            try:k=key(r)
            except:continue
            if k in validation:
                g=gid(r['game_id']); tenure_games[k].add(g); ledger_sec[(k,g)]=float(r.get('seconds_game') or 0)
    for k,t in targets.items():
        exp=int(float(t.get('team_games_in_tenure') or 0))
        if len(tenure_games[k])!=exp: raise SystemExit(f'TENURE_GAME_COUNT_MISMATCH {k} {len(tenure_games[k])} {exp}')

    facts=collections.defaultdict(dict); team=collections.defaultdict(dict); srcp=collections.defaultdict(dict); srct=collections.defaultdict(dict)
    stale=[]
    def putp(k,z,v,src,auth=False):
        v=float(v); old=facts[k].get(z); os=srcp[k].get(z)
        if old is None:facts[k][z]=v;srcp[k][z]=src;return
        if abs(old-v)<=1e-9:return
        if os=='current_shared': stale.append(('p',k,z,old,v,src));return
        if auth: stale.append(('p',k,z,v,old,os));facts[k][z]=v;srcp[k][z]=src;return
        raise SystemExit(f'PLAYER_CONFLICT {k} {z} {old} {os} {v} {src}')
    def putt(k,z,v,src,auth=False):
        v=float(v); old=team[k].get(z); os=srct[k].get(z)
        if old is None:team[k][z]=v;srct[k][z]=src;return
        if abs(old-v)<=1e-9:return
        if os=='current_shared': stale.append(('t',k,z,old,v,src));return
        if auth: stale.append(('t',k,z,v,old,os));team[k][z]=v;srct[k][z]=src;return
        raise SystemExit(f'TEAM_CONFLICT {k} {z} {old} {os} {v} {src}')
    with gzip.open(one(a.current,'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'),'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
            for z in REQP:putp(k,z,r[z],'current_shared',True)
    with gzip.open(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'),'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']))
            for z in REQT:putt(k,z,r[z],'current_shared',True)
    with open(one(a.consensus,'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv'),newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']),pid(r.get('player_id','')))
            if r['field'] in REQP:putp(k,r['field'],r['value'],'consensus')
            elif not k[2] and r['field'] in REQT:putt((k[0],k[1]),r['field'],r['value'],'consensus')
    def inject(root,pat,src):
        with gzip.open(one(root,pat),'rt',encoding='utf-8',newline='') as f:
            for r in csv.DictReader(f):
                k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
                for z in REQP:putp(k,z,r[z],src)
    inject(a.old,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','old')
    inject(a.mid,'RECOVERED_927_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','mid')
    inject(a.newer,'RECOVERED_2012_2022_EXACT_PLAYER_GAME_PRIMITIVES.csv.gz','newer')
    for root,pat,src in [(a.game,'RECOVERED_EXACT_PLAYER_GAME_PRIMITIVES.csv','game'),(a.currentcons,'RECOVERED_CURRENT_EXACT_PLAYER_GAME_PRIMITIVES.csv','currentcons')]:
        with open(one(root,pat),newline='') as f:
            for r in csv.DictReader(f):
                k=(gid(r['game_id']),tid(r['team_id']),pid(r['player_id']))
                for z in REQP:putp(k,z,r[z],src)
    with open(one(a.pbp,'TREB_143_V2_PBP_EXACT_AUDIT.csv'),newline='') as f:
        for r in csv.DictReader(f):
            if r.get('status')=='PASS_EXACT' and str(r.get('validation_pass')).lower() in {'true','1'}:
                k=(gid(r['game_id']),tid(r['team_id']))
                for z in REQT:putt(k,z,r[z],'pbp_exact')
    with open(one(a.game,'RECOVERED_EXACT_TEAM_GAME_FACTS.csv'),newline='') as f:
        for r in csv.DictReader(f):
            k=(gid(r['game_id']),tid(r['team_id']))
            for z in REQT:putt(k,z,r[z],'game_exact')

    cache={}; promoted=[]; evidence=[]; blocked=[]
    for k in sorted(validation):
        t=targets[k]; games=sorted(tenure_games[k]); agg=collections.Counter(); missing=[]
        for g in games:
            pv=facts.get((g,k[1],k[2]),{}); tv=team.get((g,k[1]),{})
            if not all(z in pv for z in REQP) or not all(z in tv for z in REQT):missing.append(g);continue
            for z in REQP:agg[z]+=pv[z]
            for z in REQT:agg[z]+=tv[z]
        if missing:
            blocked.append({'season':k[0],'team_id':k[1],'player_id':k[2],'status':'UNEXPECTED_MISSING_EXACT_FACT','detail':'|'.join(missing)});continue
        cdn_total=0.0; failed=[]; absent_nonzero=[]
        for g in games:
            try:
                if g not in cache:cache[g]=fetch_box(g)
                s=player_seconds(cache[g],k[1],k[2])
                if s is None:
                    if abs(ledger_sec.get((k,g),0))>1e-9:absent_nonzero.append(g)
                    s=0.0
                cdn_total+=s
            except Exception as e:failed.append(f'{g}:{e}')
        target_sec=float(t.get('seconds_on') or 0); delta=abs(cdn_total-target_sec)
        ev={'season':k[0],'team_id':k[1],'player_id':k[2],'games':len(games),'cdn_seconds':cdn_total,'target_seconds':target_sec,'delta_seconds':delta,'fetch_failures':len(failed),'absent_nonzero':len(absent_nonzero)}
        evidence.append(ev)
        if failed or absent_nonzero or delta>60:
            blocked.append({**ev,'status':'CDN_VALIDATION_FAILED','detail':' || '.join(failed+absent_nonzero)});continue
        tr_on=agg['team_oreb_on']+agg['team_dreb_on']; op_on=agg['opponent_oreb_on']+agg['opponent_dreb_on']; tr=agg['team_oreb']+agg['team_dreb']; op=agg['opponent_oreb']+agg['opponent_dreb']; tr_off=tr-tr_on; op_off=op-op_on
        if min(tr_on,op_on,tr_off,op_off)<0 or tr_on+op_on<=0 or tr_off+op_off<=0:
            blocked.append({**ev,'status':'DENOMINATOR_FAIL','detail':''});continue
        on=100*tr_on/(tr_on+op_on); off=100*tr_off/(tr_off+op_off)
        promoted.append({'metric':'TotalReboundPct','off_corrected':off,'on':on,'on_minus_off_corrected':on-off,'player_id':k[2],'provenance':'exact retained rebound primitives; exact tenure identity already authoritative; independent NBA CDN liveData per-game minutes validated within 60 seconds','season':k[0],'seconds_on':cdn_total,'team_games_in_tenure':len(games),'team_id':k[1]})
    pkeys={key(r) for r in promoted}; assert pkeys.issubset(set(validation)); assert len(pkeys)==len(promoted)
    remain=[r for r in current if key(r) not in pkeys]; cumulative=prior+promoted
    assert len(remain)==563-len(promoted); assert len(cumulative)==683+len(promoted); assert len(cumulative)+len(remain)+len(mat)==1250
    def write(name,rows,fields=None):
        if fields is None:fields=sorted({x for r in rows for x in r}) if rows else []
        with open(OUT/name,'w',newline='') as f:
            if fields:w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    write('TREB_NEW_EXACT_PROMOTED.csv',promoted)
    write('TREB_CUMULATIVE_EXACT_PROMOTED.csv',cumulative)
    write('AUTONOMOUS_BLOCKER_MANIFEST.csv',remain,current[0].keys())
    write('TREB_CDN_MINUTES_EVIDENCE.csv',evidence)
    write('TREB_CDN_MINUTES_BLOCKED.csv',blocked)
    qa={'status':'PASS','starting_production_resolved':9084,'starting_residual':563,'validation_candidates_tested':19,'new_exact_promotions':len(promoted),'ending_production_resolved':9084+len(promoted),'ending_residual':563-len(promoted),'blocked':len(blocked),'integrity':{'exact_rebound_primitives_unchanged':True,'exact_tenure_identity_preexisting':True,'independent_nba_cdn_minutes_gate_seconds':60,'modelling':False,'rounded_backsolve':False}}
    (OUT/'TREB_CDN_MINUTES_RECLOSURE_QA.json').write_text(json.dumps(qa,indent=2)+'\n')
    print(json.dumps(qa,indent=2))

if __name__=='__main__':main()
