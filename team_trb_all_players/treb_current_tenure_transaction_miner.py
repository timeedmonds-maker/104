#!/usr/bin/env python3
"""Mine retained transaction evidence for current 21 NO_EXACT_TENURE_IDENTITY rows.
Diagnostic only: never promotes or changes production.
"""
from __future__ import annotations
import argparse,csv,gzip,json,re
from pathlib import Path
from collections import defaultdict

def norm(x): return str(x or '').strip()
def canon_name(s): return re.sub(r'[^a-z0-9]+',' ',norm(s).casefold()).strip()
PLAYER_KEYS={'player_id','playerid','person_id','personid'}
NAME_KEYS=('player_name','playername','person_name','personname','name','full_name','display_first_last','player')

def targets(path):
    out=[]
    for r in csv.DictReader(path.open(encoding='utf-8-sig')):
        out.append((norm(r['season']),norm(r['player_id']),norm(r['team_id'])))
    return out

def scan_name_maps(root,wanted_ids):
    names=defaultdict(set); sources=defaultdict(set)
    for p in root.rglob('*.csv'):
        ps=str(p).lower()
        if 'treb_recovery_status' in ps or p.stat().st_size>150_000_000: continue
        try:
            with p.open(encoding='utf-8-sig',errors='replace',newline='') as f:
                rd=csv.DictReader(f); cols=rd.fieldnames or []; low={c.lower():c for c in cols}
                pc=next((low[k] for k in PLAYER_KEYS if k in low),None)
                nc=next((low[k] for k in NAME_KEYS if k in low),None)
                if not pc or not nc: continue
                for r in rd:
                    pid=norm(r.get(pc)); nm=norm(r.get(nc))
                    if pid in wanted_ids and nm and not nm.isdigit():
                        names[pid].add(nm); sources[pid].add(str(p.relative_to(root)))
        except Exception: pass
    for p in root.rglob('*.json'):
        ps=str(p).lower()
        if 'treb_recovery_status' in ps or p.stat().st_size>50_000_000: continue
        try:
            obj=json.loads(p.read_text(encoding='utf-8',errors='replace'))
        except Exception: continue
        stack=[obj]
        while stack:
            x=stack.pop()
            if isinstance(x,dict):
                low={str(k).lower():k for k in x}
                pc=next((low[k] for k in PLAYER_KEYS if k in low),None)
                nc=next((low[k] for k in NAME_KEYS if k in low),None)
                if pc is not None and nc is not None:
                    pid=norm(x.get(pc)); nm=norm(x.get(nc))
                    if pid in wanted_ids and nm and not nm.isdigit():
                        names[pid].add(nm); sources[pid].add(str(p.relative_to(root)))
                for v in x.values():
                    if isinstance(v,(dict,list)): stack.append(v)
            elif isinstance(x,list): stack.extend(x)
    return names,sources

def iter_transaction_records(root):
    for p in root.rglob('*'):
        if not p.is_file(): continue
        ps=str(p).lower()
        if 'transaction' not in ps: continue
        if 'treb_recovery_status' in ps or p.stat().st_size>250_000_000: continue
        try:
            if p.suffix=='.gz':
                with gzip.open(p,'rt',encoding='utf-8',errors='replace') as f:
                    for i,line in enumerate(f,1):
                        if line.strip(): yield p,i,line.strip()
            elif p.suffix.lower() in {'.jsonl','.txt','.csv','.json'}:
                with p.open(encoding='utf-8',errors='replace') as f:
                    for i,line in enumerate(f,1):
                        if line.strip(): yield p,i,line.strip()
        except Exception: continue

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',required=True); ap.add_argument('--targets',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    root=Path(a.repo_root); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    t=targets(Path(a.targets)); ids={x[1] for x in t}; names,name_sources=scan_name_maps(root,ids)
    hits=defaultdict(list)
    aliases={pid:{canon_name(n) for n in ns if len(canon_name(n))>=5} for pid,ns in names.items()}
    for p,i,line in iter_transaction_records(root):
        cl=canon_name(line)
        for pid,als in aliases.items():
            if any(a and a in cl for a in als):
                if len(hits[pid])<250:
                    hits[pid].append({'path':str(p.relative_to(root)),'line':i,'text':line[:4000]})
    rows=[]
    for sea,pid,tid in t:
        rows.append({'season':sea,'player_id':pid,'team_id':tid,'name_candidates':'|'.join(sorted(names.get(pid,set()))),'name_source_count':len(name_sources.get(pid,set())),'transaction_hit_count':len(hits.get(pid,[])),'name_sources':'|'.join(sorted(name_sources.get(pid,set())))})
    with (out/'TREB_CURRENT_21_TRANSACTION_INDEX.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    ev=[]
    for sea,pid,tid in t:
        for h in hits.get(pid,[]): ev.append({'season':sea,'player_id':pid,'team_id':tid,**h})
    with (out/'TREB_CURRENT_21_TRANSACTION_EVIDENCE.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['season','player_id','team_id','path','line','text']; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(ev)
    summary={'targets':len(t),'players_with_name_mapping':sum(bool(names.get(pid)) for pid in ids),'players_with_transaction_hits':sum(bool(hits.get(pid)) for pid in ids),'transaction_evidence_rows':len(ev)}
    (out/'TREB_CURRENT_21_TRANSACTION_SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
