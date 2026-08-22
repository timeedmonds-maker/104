import argparse,csv,gzip,json,pathlib,time,urllib.request,collections

def gid(x):
    s=str(x).strip().removesuffix('.0')
    try:return str(int(float(s))).zfill(10)
    except:return s.zfill(10)
def tid(x): return str(int(float(x)))
def pid(x):
    try:return str(int(float(x)))
    except:return str(x or '0').strip() or '0'
def read(p):
    p=pathlib.Path(p)
    if p.suffix=='.gz':
        with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
    with open(p,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def one(root,pat):
    m=list(pathlib.Path(root).rglob(pat)); assert len(m)==1,(root,pat,len(m)); return m[0]
def fetch_pbp(g):
    url=f'https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{g}.json'
    headers={'User-Agent':'Mozilla/5.0 (compatible; TREB exact recovery/1.0)','Referer':'https://www.nba.com/','Accept':'application/json'}
    err=''
    for a in range(5):
        try:
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=20) as r: obj=json.load(r)
            acts=(obj.get('game') or obj).get('actions') or []
            if not acts: raise ValueError(f'no actions {g}')
            # CDN liveData exposes game-cumulative rebound totals for the rebound actor.
            # Sum each actor's final offensive/defensive totals, including personId=0 team rebounds.
            per=collections.defaultdict(lambda:[0.0,0.0])
            team_seen=set(); rebound_rows=0
            for arow in acts:
                if str(arow.get('actionType') or '').lower()!='rebound': continue
                team=arow.get('teamId')
                if team in (None,'',0,'0'): continue
                team=tid(team); person=pid(arow.get('personId'))
                o=arow.get('reboundOffensiveTotal'); d=arow.get('reboundDefensiveTotal')
                if o is None or d is None: continue
                try:o=float(o); d=float(d)
                except:continue
                k=(team,person); per[k][0]=max(per[k][0],o); per[k][1]=max(per[k][1],d)
                team_seen.add(team); rebound_rows+=1
            if len(team_seen)!=2 or rebound_rows==0: raise ValueError(f'incomplete rebound actions {g}: teams={team_seen} rows={rebound_rows}')
            out={t:[0.0,0.0] for t in team_seen}
            for (t,p),(o,d) in per.items(): out[t][0]+=o; out[t][1]+=d
            return out,url,'',rebound_rows
        except Exception as e:
            err=f'{type(e).__name__}: {e}'; time.sleep(2*(a+1))
    return None,url,err,0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--current',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    OUT=pathlib.Path(a.out); OUT.mkdir(parents=True,exist_ok=True)
    reg=read(one(a.current,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv')); shared=read(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
    game_season={gid(r['game_id']):str(r['season']) for r in reg}; targets=set()
    for r in reg:
        if int(str(r['season']).split('-')[0])<2021: continue
        g=gid(r['game_id'])
        for x in str(r.get('team_ids') or '').split('|'):
            if x.strip() and x.strip().lower()!='nan': targets.add((g,tid(x)))
    exact={}
    for r in shared:
        g=gid(r['game_id']); t=tid(r['team_id']); season=str(r.get('season') or game_season.get(g,''))
        exact[(g,t)]={'season':season,'team_oreb':float(r['team_oreb']),'team_dreb':float(r['team_dreb']),'opponent_oreb':float(r['opponent_oreb']),'opponent_dreb':float(r['opponent_dreb'])}
    controls=[k for k,v in exact.items() if int(str(v['season']).split('-')[0])>=2021 and k not in targets]
    all_games=sorted({g for g,t in controls}|{g for g,t in targets}); fetched={}; failures=[]; source=[]
    for g in all_games:
        box,url,err,n=fetch_pbp(g)
        if box is None: failures.append({'game_id':g,'season':game_season.get(g,''),'error':err,'url':url}); continue
        fetched[g]=box
        for t,(o,dv) in box.items(): source.append({'game_id':g,'season':game_season.get(g,''),'team_id':t,'team_oreb':o,'team_dreb':dv,'rebound_event_rows':n,'url':url})
    checked=[]; mism=[]; seasons=set()
    for g,t in controls:
        if g not in fetched or t not in fetched[g]: continue
        opp=[x for x in fetched[g] if x!=t]
        if len(opp)!=1: continue
        o,dv=fetched[g][t]; oo,od=fetched[g][opp[0]]; e=exact[(g,t)]
        z={'game_id':g,'season':e['season'],'team_id':t,'expected_team_oreb':e['team_oreb'],'expected_team_dreb':e['team_dreb'],'expected_opponent_oreb':e['opponent_oreb'],'expected_opponent_dreb':e['opponent_dreb'],'cdn_team_oreb':o,'cdn_team_dreb':dv,'cdn_opponent_oreb':oo,'cdn_opponent_dreb':od}; checked.append(z); seasons.add(e['season'])
        if any(abs(z[a]-z[b])>1e-9 for a,b in [('cdn_team_oreb','expected_team_oreb'),('cdn_team_dreb','expected_team_dreb'),('cdn_opponent_oreb','expected_opponent_oreb'),('cdn_opponent_dreb','expected_opponent_dreb')]): mism.append(z)
    gate=(len(mism)==0 and len(checked)>=12 and len(seasons)>=2)
    recovered=[]
    if gate:
        for g,t in sorted(targets):
            if (g,t) in exact or g not in fetched or t not in fetched[g]: continue
            opp=[x for x in fetched[g] if x!=t]
            if len(opp)!=1: continue
            o,dv=fetched[g][t]; oo,od=fetched[g][opp[0]]
            recovered.append({'season':game_season[g],'game_id':g,'team_id':t,'team_oreb':o,'team_dreb':dv,'opponent_oreb':oo,'opponent_dreb':od,'provenance':'official NBA CDN liveData PBP cumulative rebound actor totals incl team rebounds; zero-mismatch exact controls'})
    def write(name,rs,fields=None):
        if fields is None: fields=sorted({k for r in rs for k in r}) if rs else []
        with open(OUT/name,'w',newline='',encoding='utf-8') as f:
            if fields:
                w=csv.DictWriter(f,fieldnames=list(fields)); w.writeheader(); w.writerows(rs)
    write('CDN_PBP_TEAM_FACTS.csv',recovered,['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance']); write('CDN_PBP_CONTROLS.csv',checked); write('CDN_PBP_CONTROL_MISMATCHES.csv',mism); write('CDN_PBP_FAILURES.csv',failures); write('CDN_PBP_SOURCE_ROWS.csv',source)
    qa={'status':'PASS' if gate and recovered else 'FAIL_CLOSED','source':'official NBA CDN liveData play-by-play cumulative rebound totals','target_team_facts':len(targets),'control_comparisons':len(checked),'control_seasons':sorted(seasons),'control_mismatches':len(mism),'source_failures':len(failures),'promoted_target_team_facts':len(recovered),'targets_promoted':len(recovered),'integrity':{'event_definition_matches_treb_controls':gate,'team_rebounds_included_via_person_zero_actor':True,'zero_control_mismatch_required':True,'raw_exact_counts_only':True,'model_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}}
    (OUT/'TEAM_FACT_SOURCE_RACE_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n'); print(json.dumps(qa,indent=2,sort_keys=True))
    if not gate or not recovered: raise SystemExit(2)
if __name__=='__main__': main()
