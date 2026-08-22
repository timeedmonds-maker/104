import argparse,csv,gzip,json,pathlib,time,urllib.request,urllib.error,collections

def gid(x):
    s=str(x).strip().removesuffix('.0')
    try:return str(int(float(s))).zfill(10)
    except:return s.zfill(10)
def tid(x): return str(int(float(x)))
def read(p):
    p=pathlib.Path(p)
    if p.suffix=='.gz':
        with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
    with open(p,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def one(root,pat):
    m=list(pathlib.Path(root).rglob(pat)); assert len(m)==1,(root,pat,len(m)); return m[0]
def fetch_box(g):
    url=f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{g}.json'
    headers={'User-Agent':'Mozilla/5.0 (compatible; TREB exact recovery/1.0)','Referer':'https://www.nba.com/','Accept':'application/json'}
    err=''
    for a in range(5):
        try:
            req=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(req,timeout=20) as r:
                obj=json.load(r)
            game=obj.get('game') or obj
            out={}
            for side in ('homeTeam','awayTeam'):
                t=game.get(side) or {}
                if not t: continue
                team=tid(t.get('teamId'))
                st=t.get('statistics') or {}
                po=st.get('reboundsOffensive'); pd=st.get('reboundsDefensive')
                to=st.get('reboundsOffensiveTeam',0); td=st.get('reboundsDefensiveTeam',0)
                if po is None or pd is None:
                    raise ValueError(f'missing rebound stats {g} {team}: {sorted(st)}')
                # TREB exact layer counts player rebounds plus official team/dead-ball rebounds.
                out[team]=(float(po)+float(to or 0),float(pd)+float(td or 0))
            if len(out)!=2: raise ValueError(f'expected two teams {g}, got {out}')
            return out,url,''
        except Exception as e:
            err=f'{type(e).__name__}: {e}'
            time.sleep(2*(a+1))
    return None,url,err

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--current',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    OUT=pathlib.Path(a.out); OUT.mkdir(parents=True,exist_ok=True)
    reg=read(one(a.current,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'))
    shared=read(one(a.current,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
    game_season={gid(r['game_id']):str(r['season']) for r in reg}
    targets=set()
    for r in reg:
        if int(str(r['season']).split('-')[0]) < 2021: continue
        g=gid(r['game_id'])
        for x in str(r.get('team_ids') or '').split('|'):
            if x.strip() and x.strip().lower()!='nan': targets.add((g,tid(x)))
    exact={}
    for r in shared:
        g=gid(r['game_id']); t=tid(r['team_id']); season=str(r.get('season') or game_season.get(g,''))
        exact[(g,t)]={'season':season,'team_oreb':float(r['team_oreb']),'team_dreb':float(r['team_dreb']),'opponent_oreb':float(r['opponent_oreb']),'opponent_dreb':float(r['opponent_dreb'])}
    control_pairs=[k for k,v in exact.items() if int(str(v['season']).split('-')[0])>=2021 and k not in targets]
    control_games=sorted({g for g,t in control_pairs})
    target_games=sorted({g for g,t in targets})
    all_games=sorted(set(control_games)|set(target_games))
    fetched={}; failures=[]; source_rows=[]
    for g in all_games:
        box,url,err=fetch_box(g)
        if box is None:
            failures.append({'game_id':g,'season':game_season.get(g,''),'error':err,'url':url})
            continue
        fetched[g]=box
        for t,(o,d) in box.items(): source_rows.append({'game_id':g,'season':game_season.get(g,''),'team_id':t,'team_oreb':o,'team_dreb':d,'url':url})
    mismatches=[]; controls=[]; control_seasons=set()
    for g,t in control_pairs:
        if g not in fetched or t not in fetched[g]: continue
        box=fetched[g]; opp=[x for x in box if x!=t]
        if len(opp)!=1: continue
        o,dv=box[t]; oo,od=box[opp[0]]; e=exact[(g,t)]
        row={'game_id':g,'season':e['season'],'team_id':t,'expected_team_oreb':e['team_oreb'],'expected_team_dreb':e['team_dreb'],'expected_opponent_oreb':e['opponent_oreb'],'expected_opponent_dreb':e['opponent_dreb'],'cdn_team_oreb':o,'cdn_team_dreb':dv,'cdn_opponent_oreb':oo,'cdn_opponent_dreb':od}
        controls.append(row); control_seasons.add(e['season'])
        if any(abs(float(row[k])-float(row['expected_'+k.removeprefix('cdn_')]))>1e-9 for k in ('cdn_team_oreb','cdn_team_dreb','cdn_opponent_oreb','cdn_opponent_dreb')):
            mismatches.append(row)
    gate=(len(mismatches)==0 and len(controls)>=12 and len(control_seasons)>=2)
    recovered=[]
    if gate:
        for g,t in sorted(targets):
            if (g,t) in exact or g not in fetched or t not in fetched[g]: continue
            box=fetched[g]; opp=[x for x in box if x!=t]
            if len(opp)!=1: continue
            o,dv=box[t]; oo,od=box[opp[0]]
            recovered.append({'season':game_season[g],'game_id':g,'team_id':t,'team_oreb':o,'team_dreb':dv,'opponent_oreb':oo,'opponent_dreb':od,'provenance':'official NBA CDN liveData final boxscore player + reboundsOffensiveTeam/reboundsDefensiveTeam; zero-mismatch exact controls'})
    def write(name,rows,fields=None):
        p=OUT/name
        if fields is None: fields=sorted({k for r in rows for k in r}) if rows else []
        with open(p,'w',newline='',encoding='utf-8') as f:
            if fields:
                w=csv.DictWriter(f,fieldnames=list(fields)); w.writeheader(); w.writerows(rows)
    write('CDN_LIVEDATA_TEAM_FACTS.csv',recovered,['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance'])
    write('CDN_LIVEDATA_CONTROLS.csv',controls); write('CDN_LIVEDATA_CONTROL_MISMATCHES.csv',mismatches); write('CDN_LIVEDATA_FAILURES.csv',failures); write('CDN_LIVEDATA_SOURCE_ROWS.csv',source_rows)
    qa={'status':'PASS' if gate and recovered else 'FAIL_CLOSED','source':'official NBA CDN liveData final boxscore player plus official team rebound fields','target_team_facts':len(targets),'target_games':len(target_games),'control_comparisons':len(controls),'control_seasons':sorted(control_seasons),'control_mismatches':len(mismatches),'source_failures':len(failures),'promoted_target_team_facts':len(recovered),'targets_promoted':len(recovered),'integrity':{'official_final_boxscore_fields_only':True,'official_team_rebound_fields_included':True,'zero_control_mismatch_required':True,'raw_exact_counts_only':True,'model_used':False,'rounded_backsolve_used':False,'opponent_inference_used':False}}
    (OUT/'TEAM_FACT_SOURCE_RACE_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n'); print(json.dumps(qa,indent=2,sort_keys=True))
    if not gate or not recovered: raise SystemExit(2)
if __name__=='__main__': main()
