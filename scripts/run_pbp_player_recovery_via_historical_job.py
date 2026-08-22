#!/usr/bin/env python3
import csv, gzip, pathlib, subprocess, sys

src=pathlib.Path('scripts/treb_player_game_pbpstats_recovery.py').read_text()
old="    obj=js.get(side)"
new="    obj=(js.get('stats') or {}).get(side)"
if old not in src:
    raise SystemExit('expected parser line not found')
tmp=pathlib.Path('/tmp/treb_player_game_pbpstats_recovery_fixed.py')
tmp.write_text(src.replace(old,new,1))
print('TREB_PBP_FULL_GATE_START', flush=True)
rc=subprocess.run([sys.executable,'-u',str(tmp)]).returncode
print('TREB_PBP_FULL_GATE_RC',rc,flush=True)
if rc!=0:
    raise SystemExit(rc)

pg=pathlib.Path('/tmp/shared/player_gated')
if not (pg/'PASS_GATE').exists():
    print('TREB_PBP_FULL_GATE_FAIL_CLOSED',flush=True)
    raise SystemExit(0)

base=pathlib.Path('/tmp/shared/base/RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz')
newp=pg/'RECOVERED_RESIDUAL_SHARED_PLAYER_GAME_PRIMITIVES.csv.gz'
keys=('game_id','team_id','player_id')
vals=('seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on')
rows={}; fields=[]; added=0
for p,isnew in ((base,False),(newp,True)):
    with gzip.open(p,'rt',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f)
        for c in rd.fieldnames or []:
            if c not in fields: fields.append(c)
        for r in rd:
            k=tuple(str(r[x]).strip().removesuffix('.0') for x in keys)
            vv=tuple(float(r[x]) for x in vals)
            if k in rows:
                ov=tuple(float(rows[k][x]) for x in vals)
                if ov!=vv: raise SystemExit(f'PLAYER_CONFLICT {k} {ov} {vv}')
            elif isnew: added+=1
            rows[k]=r
with gzip.open(base,'wt',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader()
    for k in sorted(rows): w.writerow({c:rows[k].get(c,'') for c in fields})

team_base=pathlib.Path('/tmp/shared/base/RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz')
gated=pathlib.Path('/tmp/shared/gated'); gated.mkdir(parents=True,exist_ok=True)
team_gate=gated/team_base.name
with gzip.open(team_base,'rt',encoding='utf-8',newline='') as f:
    rd=csv.DictReader(f); tf=rd.fieldnames
with gzip.open(team_gate,'wt',encoding='utf-8',newline='') as f:
    csv.DictWriter(f,fieldnames=tf).writeheader()
(gated/'PASS_GATE').write_text(f'PLAYER_ONLY:{added}\n')
print('TREB_PBP_PLAYER_PROMOTED_TO_BASE',added,flush=True)
