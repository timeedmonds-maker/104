#!/usr/bin/env python3
import argparse,csv,json,pathlib,tarfile,subprocess,tempfile,io
from collections import defaultdict

UPSTREAM_COMMIT='e829d4678be1e075f99e5d41a1c5f97089be446b'
UPSTREAM='https://github.com/shufinskiy/nba_data.git'

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]

def prepare_upstream(root):
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'remote','add','origin',UPSTREAM],check=True)
    subprocess.run(['git','-C',str(root),'-c','http.version=HTTP/1.1','fetch','--depth=1','--no-tags','origin',UPSTREAM_COMMIT],check=True,timeout=600)
    return pathlib.Path(root)

def game_field(fs):
    norm=lambda s:s.upper().replace('_','')
    return next((f for f in fs if norm(f) in ('GAMEID','GAME_ID')),None)

def archive_rows(kind,year,wanted,repo,tmpdir):
    archive=pathlib.Path(tmpdir)/f'{kind}_{year}.tar.xz'
    spec=f'{UPSTREAM_COMMIT}:datasets/{kind}_{year}.tar.xz'
    with open(archive,'wb') as f:
        subprocess.run(['git','-C',str(repo),'show',spec],stdout=f,check=True,timeout=600)
    if archive.stat().st_size==0: raise RuntimeError(f'empty archive {kind} {year}')
    with tarfile.open(archive,'r:xz') as tf:
        names=[n for n in tf.getnames() if n.lower().endswith('.csv')]
        if not names: raise RuntimeError(f'no csv in {kind} {year}')
        fh=tf.extractfile(names[0])
        if fh is None: raise RuntimeError(f'cannot read csv in {kind} {year}')
        rd=csv.DictReader(io.TextIOWrapper(fh,encoding='utf-8-sig',errors='replace',newline=''))
        fields=rd.fieldnames or []
        gf=game_field(fields)
        if not gf: raise RuntimeError(f'no game id in {kind} {year}: {fields[:30]}')
        rows=[r for r in rd if gid(r.get(gf,'')) in wanted]
    archive.unlink(missing_ok=True)
    return fields,rows,spec,gf

def first_field(fs,cands):
    norm={f.upper().replace('_',''):f for f in fs}
    for c in cands:
        k=c.upper().replace('_','')
        if k in norm:return norm[k]
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    targets=defaultdict(set)
    for r in reg: targets[r['season']].add(gid(r['game_id']))
    qa={'status':'PASS','target_games':sum(map(len,targets.values())),'target_seasons':len(targets),'upstream_commit':UPSTREAM_COMMIT,'seasons':{},'errors':[]}
    with tempfile.TemporaryDirectory(prefix='treb_upstream_') as gd,tempfile.TemporaryDirectory(prefix='treb_archives_') as td:
        try: repo=prepare_upstream(gd)
        except Exception as e:
            qa['status']='FAIL_TRANSPORT';qa['errors'].append({'scope':'upstream_fetch','error':repr(e)})
            (out/'STATIC_HYBRID_LINEUP_FEASIBILITY_QA.json').write_text(json.dumps(qa,indent=2));print(json.dumps(qa,indent=2));return
        for season in sorted(targets):
            s={'games':{}}
            try:
                nf,nrows,nspec,ngf=archive_rows('nbastats',season[:4],targets[season],repo,td)
                pf,prows,pspec,pgf=archive_rows('pbpstats',season[:4],targets[season],repo,td)
                msgf=first_field(nf,['EVENTMSGTYPE','EVENT_MSG_TYPE','EVENTTYPE'])
                per=first_field(nf,['PERIOD'])
                clock=first_field(nf,['PCTIMESTRING','PCTIME','CLOCK'])
                descs=[f for f in [first_field(nf,['HOMEDESCRIPTION']),first_field(nf,['VISITORDESCRIPTION']),first_field(nf,['NEUTRALDESCRIPTION']),first_field(nf,['DESCRIPTION'])] if f]
                p1=first_field(nf,['PLAYER1_ID','PLAYER1ID']);p2=first_field(nf,['PLAYER2_ID','PLAYER2ID'])
                s['nbastats_source']=nspec;s['pbpstats_source']=pspec;s['nbastats_fields']=nf;s['pbpstats_fields']=pf
                for g in sorted(targets[season]):
                    ng=[r for r in nrows if gid(r.get(ngf,''))==g]
                    pg=[r for r in prows if gid(r.get(pgf,''))==g]
                    subs=[];periods=[]
                    for i,r in enumerate(ng):
                        txt=' | '.join(str(r.get(f,'') or '') for f in descs)
                        msg=str(r.get(msgf,'') or '').strip() if msgf else ''
                        rec={'row':i,'msg':msg,'period':r.get(per,'') if per else '','clock':r.get(clock,'') if clock else '','p1':r.get(p1,'') if p1 else '','p2':r.get(p2,'') if p2 else '','text':txt}
                        if msg=='8' or 'SUB:' in txt.upper() or 'SUBSTITUTION' in txt.upper(): subs.append(rec)
                        if msg in ('12','13') or ('START OF' in txt.upper()) or ('END OF' in txt.upper()): periods.append(rec)
                    s['games'][g]={'nbastats_rows':len(ng),'pbpstats_rows':len(pg),'sub_count':len(subs),'period_marker_count':len(periods),'sub_examples':subs[:12],'period_examples':periods[:8]}
            except Exception as e:
                qa['errors'].append({'season':season,'error':repr(e)})
            qa['seasons'][season]=s
    if qa['errors']: qa['status']='PARTIAL'
    games=[v for s in qa['seasons'].values() for v in s.get('games',{}).values()]
    qa['summary']={
      'games_examined':len(games),
      'games_with_nbastats_rows':sum(x['nbastats_rows']>0 for x in games),
      'games_with_pbpstats_rows':sum(x['pbpstats_rows']>0 for x in games),
      'games_with_substitutions':sum(x['sub_count']>0 for x in games),
      'games_with_period_markers':sum(x['period_marker_count']>0 for x in games),
      'total_substitutions':sum(x['sub_count'] for x in games)
    }
    (out/'STATIC_HYBRID_LINEUP_FEASIBILITY_QA.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps({'status':qa['status'],'summary':qa['summary'],'errors':qa['errors']},indent=2))
if __name__=='__main__': main()
