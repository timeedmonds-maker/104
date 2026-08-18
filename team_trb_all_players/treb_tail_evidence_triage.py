#!/usr/bin/env python3
from __future__ import annotations
import csv, json, os, re, sys
from pathlib import Path

# Current authoritative tail, verified from artifact 9275527991.
VALIDATION = [
('2001-02','ATL','2750'),('2001-02','CLE','2753'),('2001-02','GSW','1897'),
('2002-03','TOR','2408'),('2003-04','BOS','1718'),
('2004-05','BOS','2679'),('2004-05','BOS','2484'),('2004-05','CHA','2237'),('2004-05','SAS','2199'),
('2005-06','DEN','2406'),('2005-06','DEN','1883'),('2005-06','DET','1503'),('2005-06','NJN','2501'),('2005-06','PHI','2774'),
('2006-07','HOU','200748'),('2006-07','MIA','2456'),('2006-07','PHI','2222'),('2006-07','PHI','2768'),('2006-07','SAS','951')]
TENURE = [
('2001-02','ATL','120','431010406'),('2001-02','ATL','115','431010081'),('2001-02','ATL','1926','431010285'),
('2001-02','CHI','1763','431010425'),('2001-02','CHI','1749','431010686'),('2001-02','CHI','2009','431010528'),
('2001-02','CLE','1711',''),('2001-02','CLE','1731',''),('2001-02','CLE','1831',''),('2001-02','CLE','1734',''),
('2001-02','GSW','672',''),('2001-02','GSW','1717',''),('2001-02','GSW','1945',''),
('2001-02','LAC','1716',''),('2001-02','LAC','1514',''),('2001-02','LAC','1907',''),
('2001-02','MEM','2211',''),('2001-02','MIL','1548',''),('2001-02','PHX','714',''),
('2004-05','GSW','2716','761c8675'),('2006-07','SAC','200797','431010386')]

KEYWORDS = ('minute','minutes','roster','transaction','schedule','ledger','tenure','participation','player_game','player-game','game_log','gamelog','uniform')
EXTS = {'.csv','.tsv','.json','.jsonl','.txt','.md','.py','.yml','.yaml'}
SKIP = {'.git','node_modules','.venv','venv','__pycache__'}
MAX_BYTES = 80 * 1024 * 1024

def main():
    root = Path(sys.argv[1] if len(sys.argv)>1 else '.')
    auth = Path(sys.argv[2] if len(sys.argv)>2 else 'auth')
    out = Path(sys.argv[3] if len(sys.argv)>3 else 'tail_evidence')
    out.mkdir(parents=True, exist_ok=True)

    # Fail closed unless the authoritative artifact still agrees with the expected 40-row partition.
    unresolved = auth/'TREB_UNRESOLVED_AFTER_MATERIALITY.csv'
    if not unresolved.exists():
        raise SystemExit('missing authoritative unresolved manifest')
    text = unresolved.read_text(errors='ignore')
    missing = []
    for season,team,pid in VALIDATION:
        if pid not in text or season not in text:
            missing.append(('validation',season,team,pid))
    for season,team,pid,pro in TENURE:
        if pid not in text or season not in text:
            missing.append(('tenure',season,team,pid))
    if missing:
        raise SystemExit(f'authoritative tail drift: {missing[:5]}')

    targets = []
    for s,t,p in VALIDATION:
        targets.append({'lane':'VALIDATION_MINUTES','season':s,'team':t,'player_id':p,'pro_id':''})
    for s,t,p,pro in TENURE:
        targets.append({'lane':'TENURE_IDENTITY','season':s,'team':t,'player_id':p,'pro_id':pro})
    token_to_targets = {}
    for x in targets:
        for tok in (x['player_id'],x['pro_id']):
            if tok:
                token_to_targets.setdefault(tok,[]).append(x)

    inventory=[]; matches=[]
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fn in files:
            p=Path(base)/fn
            rel=str(p.relative_to(root))
            low=rel.lower()
            if p.suffix.lower() not in EXTS: continue
            if not any(k in low for k in KEYWORDS): continue
            try: size=p.stat().st_size
            except OSError: continue
            if size>MAX_BYTES: continue
            inventory.append({'path':rel,'bytes':size})
            try:
                with p.open('r',errors='ignore') as fh:
                    for lineno,line in enumerate(fh,1):
                        for tok, tgs in token_to_targets.items():
                            if tok in line:
                                for tg in tgs:
                                    matches.append({**tg,'path':rel,'line':lineno,'token':tok,'evidence':line.strip()[:1500]})
            except Exception as e:
                inventory[-1]['read_error']=repr(e)

    with (out/'TREB_TAIL_SOURCE_INVENTORY.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=sorted({k for r in inventory for k in r} or {'path','bytes'})); w.writeheader(); w.writerows(inventory)
    fields=['lane','season','team','player_id','pro_id','path','line','token','evidence']
    with (out/'TREB_TAIL_EVIDENCE_MATCHES.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(matches)

    coverage=[]
    for tg in targets:
        ms=[m for m in matches if m['lane']==tg['lane'] and m['season']==tg['season'] and m['team']==tg['team'] and m['player_id']==tg['player_id']]
        coverage.append({**tg,'evidence_matches':len(ms),'evidence_files':len(set(m['path'] for m in ms))})
    with (out/'TREB_TAIL_EVIDENCE_COVERAGE.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(coverage[0])); w.writeheader(); w.writerows(coverage)
    qa={'authoritative_artifact_id':9275527991,'validation_targets':len(VALIDATION),'tenure_targets':len(TENURE),'inventory_files':len(inventory),'matches':len(matches),'targets_with_evidence':sum(c['evidence_matches']>0 for c in coverage),'promotion_allowed':False,'purpose':'exact-source triage only; no values promoted'}
    (out/'TREB_TAIL_EVIDENCE_QA.json').write_text(json.dumps(qa,indent=2))
    print(json.dumps(qa,indent=2))

if __name__=='__main__': main()
