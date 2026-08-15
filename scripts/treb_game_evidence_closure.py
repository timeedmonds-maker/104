import os, io, json, zipfile, urllib.request, urllib.error, math
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timezone
import pandas as pd

REPO='timeedmonds-maker/104'; EVID=9252389189
BASE=f'https://api.github.com/repos/{REPO}'; TOK=os.environ['GH_TOKEN']
HDR={'Authorization':f'Bearer {TOK}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'treb-game-closure'}
ROOT=Path('/tmp/treb_game_closure'); OUT=ROOT/'out'; ARTS=ROOT/'artifacts'; EDIR=ROOT/'evidence'
for p in (OUT,ARTS,EDIR): p.mkdir(parents=True,exist_ok=True)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None
OPENER=urllib.request.build_opener(NoRedirect)

def artbytes(aid):
    req=urllib.request.Request(BASE+f'/actions/artifacts/{aid}/zip',headers=HDR)
    try:
        with OPENER.open(req,timeout=60) as r:return r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (301,302,303,307,308): raise
        u=e.headers.get('Location')
        with urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'treb-game-closure'}),timeout=180) as r:return r.read()

def extract(aid,dest):
    z=zipfile.ZipFile(io.BytesIO(artbytes(aid))); dest.mkdir(parents=True,exist_ok=True); z.extractall(dest); return z.namelist()

def prog(**kw):
    d={'heartbeat_utc':datetime.now(timezone.utc).isoformat(),**kw}; (OUT/'GAME_CLOSURE_PROGRESS.json').write_text(json.dumps(d,indent=2)+'\n'); print('PROGRESS',json.dumps(d),flush=True)

extract(EVID,EDIR)
km=json.loads((EDIR/'BLOCKER_KEY_GAME_MAP.json').read_text())
dl=json.loads((EDIR/'DOWNLOADED_ARTIFACTS.json').read_text())
linked=sum(x.get('game_id_count',0)>0 for x in km)
if linked!=1250: raise SystemExit(f'fail closed: expected 1250 linked blockers, got {linked}')
g2b=defaultdict(list)
for x in km:
    for g in x.get('game_ids',[]): g2b[int(g)].append((str(x['season']),str(x['team_id']),str(x['player_id'])))
ranked=sorted(g2b,key=lambda g:(-len(g2b[g]),g)); target=set(ranked[:300])
prog(phase='SEED',linked_blockers=linked,unlinked_blockers=1252-linked,implicated_games=len(g2b),target_games=len(target),max_blockers_per_game=max(map(len,g2b.values())))

recs=dl.get('downloaded',[]) if isinstance(dl,dict) else dl
ids=[]
for r in recs:
    try: ids.append(int(r['id']))
    except: pass
ids=list(dict.fromkeys(ids)); failures=[]; ok=0
for i,aid in enumerate(ids,1):
    try: extract(aid,ARTS/str(aid)); ok+=1
    except Exception as e: failures.append({'id':aid,'error':repr(e)})
    if i%20==0 or i==len(ids): prog(phase='DOWNLOAD',artifact_total=len(ids),artifact_processed=i,artifact_extracted=ok,artifact_failed=len(failures),target_games=len(target))

rows=[]; scanned=hits=0
for fp in [p for root in ARTS.glob('*') for p in root.rglob('*') if p.is_file()]:
    scanned+=1
    try:
        n=fp.name.lower()
        if n.endswith('.csv'): df=pd.read_csv(fp,dtype=str,low_memory=False)
        elif n.endswith('.csv.gz'): df=pd.read_csv(fp,dtype=str,low_memory=False,compression='gzip')
        elif n.endswith('.jsonl'):
            rr=[json.loads(x) for x in fp.read_text(errors='ignore').splitlines() if x.strip()]
            if not rr: continue
            df=pd.DataFrame(rr)
        else: continue
        cm={str(c).strip().lower():c for c in df.columns}; gc=cm.get('game_id') or cm.get('gameid')
        if not gc: continue
        gids=pd.to_numeric(df[gc],errors='coerce'); mask=gids.isin(target)
        if not mask.any(): continue
        hits+=1; sub=df.loc[mask].copy(); sub['_gid']=gids.loc[mask].astype('int64')
        wanted={'team_id','player_id','person_id','seconds','minutes','player_seconds','team_reb','opp_reb','team_rebounds','opponent_rebounds','player_reb_on','player_rebounds_on','on_reb','off_reb','treb_on_num','treb_on_den','treb_off_num','treb_off_den'}
        keep=[c for c in sub.columns if str(c).strip().lower() in wanted]
        for _,r in sub.iterrows():
            rec={'game_id':int(r['_gid']),'source_file':str(fp)}
            for c in keep:
                if pd.notna(r[c]): rec[str(c).strip().lower()]=str(r[c])
            rows.append(rec)
    except Exception: pass
    if scanned%250==0: prog(phase='SCAN',files_scanned=scanned,files_hit=hits,retained_rows=len(rows),target_games=len(target))

num={'seconds','minutes','player_seconds','team_reb','opp_reb','team_rebounds','opponent_rebounds','player_reb_on','player_rebounds_on','on_reb','off_reb','treb_on_num','treb_on_den','treb_off_num','treb_off_den'}
facts=defaultdict(lambda:defaultdict(list))
for rec in rows:
    ident=(rec['game_id'],rec.get('team_id',''),rec.get('player_id',rec.get('person_id','')))
    for f in num:
        if f in rec:
            try:
                x=float(rec[f]);
                if math.isfinite(x): facts[ident][f].append((round(x,9),rec['source_file']))
            except: pass
cons=[]; conf=[]
for (gid,tid,pid),fd in facts.items():
    for field,vals in fd.items():
        by=defaultdict(set)
        for v,s in vals: by[v].add(s)
        if len(by)==1 and len(next(iter(by.values())))>=2:
            v=next(iter(by)); cons.append({'game_id':gid,'team_id':tid,'player_id':pid,'field':field,'value':v,'independent_files':len(by[v])})
        elif len(by)>1: conf.append({'game_id':gid,'team_id':tid,'player_id':pid,'field':field,'values':sorted(by),'source_counts':{str(k):len(v) for k,v in by.items()}})
cb=Counter(x['game_id'] for x in cons); xb=Counter(x['game_id'] for x in conf)
report=[{'game_id':g,'blocker_keys_affected':len(g2b[g]),'consensus_facts':cb[g],'conflict_facts':xb[g],'blocker_keys':[{'season':a,'team_id':b,'player_id':c} for a,b,c in g2b[g]]} for g in ranked[:300]]
report.sort(key=lambda x:(-x['blocker_keys_affected'],-x['consensus_facts'],x['conflict_facts'],x['game_id']))
pd.DataFrame(cons).to_csv(OUT/'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv',index=False)
(OUT/'RETAINED_FACT_CONFLICTS.json').write_text(json.dumps(conf,indent=2)+'\n')
(OUT/'HIGH_YIELD_GAME_CLOSURE_MAP.json').write_text(json.dumps(report,indent=2)+'\n')
(OUT/'DOWNLOAD_FAILURES.json').write_text(json.dumps(failures,indent=2)+'\n')
summary={'status':'PASS','linked_blockers':linked,'unlinked_blockers':1252-linked,'total_implicated_games':len(g2b),'target_games':len(target),'artifacts_attempted':len(ids),'artifacts_extracted':ok,'artifact_failures':len(failures),'files_scanned':scanned,'files_with_target_games':hits,'retained_rows_for_target_games':len(rows),'multi_source_consensus_facts':len(cons),'conflicting_facts':len(conf),'top_game_blocker_yield':max(map(len,g2b.values()))}
(OUT/'GAME_CLOSURE_SUMMARY.json').write_text(json.dumps(summary,indent=2)+'\n'); prog(phase='COMPLETE',**summary)
