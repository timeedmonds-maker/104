#!/usr/bin/env python3
from __future__ import annotations
import csv,gzip,pathlib
import audit_current_residual_materiality as audit

_original=audit.load_facts

def _patched_load_facts(args):
    facts,overrides,injected=_original(args)
    root=pathlib.Path(args.current_dir)
    pp=list(root.rglob('RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'))
    tp=list(root.rglob('RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
    if len(pp)!=1 or len(tp)!=1:
        raise RuntimeError(f'expected one current cumulative shared player/team file, got player={len(pp)} team={len(tp)}')
    injected['current_cumulative_shared_player']=audit.inject_player_file(facts,pp[0],'current_cumulative_shared_player')
    n=0
    with gzip.open(tp[0],'rt',encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            k=(audit.gid(r['game_id']),audit.tid(r['team_id']))
            vals={z:float(r[z]) for z in audit.REQT}
            old=overrides.get(k)
            if old is not None and any(abs(float(old[z])-vals[z])>1e-9 for z in audit.REQT):
                raise RuntimeError(f'CONFLICT_CURRENT_SHARED_TEAM {k}')
            overrides[k]=vals;n+=1
    injected['current_cumulative_shared_team']=n
    print('CURRENT_SHARED_FACTS_INJECTED',injected,flush=True)
    return facts,overrides,injected

audit.load_facts=_patched_load_facts

if __name__=='__main__':
    audit.main()
