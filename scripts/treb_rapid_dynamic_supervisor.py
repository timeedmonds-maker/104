import csv, io, json, math, os, re, urllib.request, urllib.error, zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = os.environ.get('GITHUB_REPOSITORY', 'timeedmonds-maker/104')
TOKEN = os.environ['GH_TOKEN']
BASE = f'https://api.github.com/repos/{REPO}'
HDR = {'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'treb-rapid-dynamic-supervisor'}
ROOT = Path('/tmp/treb_rapid_dynamic')
OUT = ROOT / 'out'
CACHE = ROOT / 'cache'
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
OPENER = urllib.request.build_opener(NoRedirect)

def api_json(path):
    req = urllib.request.Request(BASE + path, headers=HDR)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def artifact_bytes(aid):
    p = CACHE / f'{aid}.zip'
    if p.exists():
        return p.read_bytes()
    req = urllib.request.Request(BASE + f'/actions/artifacts/{aid}/zip', headers=HDR)
    try:
        with OPENER.open(req, timeout=60) as r:
            b = r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        u = e.headers.get('Location')
        with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'treb-rapid-dynamic-supervisor'}), timeout=180) as r:
            b = r.read()
    p.write_bytes(b)
    return b

def artifact_zip(aid):
    return zipfile.ZipFile(io.BytesIO(artifact_bytes(aid)))

def list_artifacts(limit_pages=8):
    out = []
    for page in range(1, limit_pages + 1):
        d = api_json(f'/actions/artifacts?per_page=100&page={page}')
        xs = d.get('artifacts', [])
        out.extend(xs)
        if len(xs) < 100:
            break
    return out

def norm_pid(v):
    s = str(v).strip()
    return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s

def norm_tid(v):
    return str(int(float(str(v).strip())))

def key_of(r):
    return (str(r['season']).strip(), norm_tid(r['team_id']), norm_pid(r['player_id']))

def csv_rows_from_bytes(b):
    text = b.decode('utf-8-sig', errors='replace')
    return list(csv.DictReader(io.StringIO(text)))

def candidate_manifest(z):
    preferred = ['AUTONOMOUS_BLOCKER_MANIFEST.csv', 'TREB_POST_MATERIALITY_BLOCKER_MANIFEST.csv', 'MATERIALITY_REMAINING.csv']
    names = z.namelist()
    for base in preferred:
        hits = [n for n in names if n.endswith(base)]
        for n in hits:
            try:
                rows = csv_rows_from_bytes(z.read(n))
            except Exception:
                continue
            if not rows:
                continue
            if {'season','team_id','player_id'} <= set(rows[0]):
                seasons = len({str(r['season']).strip() for r in rows})
                return n, rows, seasons
    return None

def heartbeat(**kw):
    d = {'utc': datetime.now(timezone.utc).isoformat(), **kw}
    (OUT / 'SUPERVISOR_PROGRESS.json').write_text(json.dumps(d, indent=2) + '\n')
    print('PROGRESS', json.dumps(d, sort_keys=True), flush=True)

def norm(c):
    return re.sub(r'[^a-z0-9]+', '_', str(c).strip().lower()).strip('_')

def fact_col(c):
    n = norm(c)
    if n in {'game_id','gameid','team_id','teamid','player_id','playerid','person_id','personid','season','game_date','date'}:
        return False
    return any(x in n for x in ('rebound','reb','second','minute','poss','treb'))

arts = list_artifacts()
arts.sort(key=lambda a: a.get('created_at',''), reverse=True)
heartbeat(phase='ARTIFACT_INDEX', artifacts=len(arts))

# Fail closed: current user-approved residual is in the ~500 band. Select the newest GLOBAL
# manifest in that band, never a tiny per-season shard or the stale 1,169/1,253 states.
manifest_candidates = []
name_gate = re.compile(r'treb.*(consolid|state|block|material|resid|closure|supervis)', re.I)
for i, a in enumerate(arts):
    if i >= 260:
        break
    if not name_gate.search(a.get('name','')):
        continue
    try:
        z = artifact_zip(int(a['id']))
        hit = candidate_manifest(z)
        if not hit:
            continue
        fn, rows, seasons = hit
        n = len(rows)
        if 400 <= n <= 700 and seasons >= 8:
            manifest_candidates.append({'artifact': a, 'file': fn, 'rows': rows, 'seasons': seasons})
            # newest valid global ~500 state is authoritative for this supervisor
            break
    except Exception as e:
        print('MANIFEST_SCAN_ERROR', a.get('id'), repr(e), flush=True)

if not manifest_candidates:
    raise SystemExit('FAIL_CLOSED: no authoritative global blocker manifest in 400..700 keys across >=8 seasons')
cur = manifest_candidates[0]
cur_art = cur['artifact']
blockers = cur['rows']
blocker_keys = {key_of(r) for r in blockers}
if len(blocker_keys) != len(blockers):
    raise SystemExit('FAIL_CLOSED: duplicate blocker keys in selected authoritative manifest')
with open(OUT / 'CURRENT_BLOCKERS.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(blockers[0].keys()))
    w.writeheader(); w.writerows(blockers)
heartbeat(phase='CURRENT_STATE', artifact_id=cur_art['id'], artifact_name=cur_art['name'], residual=len(blockers), seasons=cur['seasons'])

# Find the retained blocker->game map that covers the greatest number of CURRENT keys.
map_candidates = []
for i, a in enumerate(arts):
    if i >= 320:
        break
    if not re.search(r'treb.*(evidence|game|block|resid|forensic|closure|plan)', a.get('name',''), re.I):
        continue
    try:
        z = artifact_zip(int(a['id']))
        hits = [n for n in z.namelist() if n.endswith('BLOCKER_KEY_GAME_MAP.json')]
        if not hits:
            continue
        km = json.loads(z.read(hits[0]))
        filt = []
        for x in km:
            try:
                k = (str(x['season']).strip(), norm_tid(x['team_id']), norm_pid(x['player_id']))
            except Exception:
                continue
            if k in blocker_keys:
                filt.append(x)
        cover = len({(str(x['season']).strip(), norm_tid(x['team_id']), norm_pid(x['player_id'])) for x in filt})
        if cover:
            dl_hits = [n for n in z.namelist() if n.endswith('DOWNLOADED_ARTIFACTS.json')]
            map_candidates.append({'artifact': a, 'map_file': hits[0], 'map': filt, 'cover': cover, 'download_file': dl_hits[0] if dl_hits else None, 'zip': z})
    except Exception as e:
        print('MAP_SCAN_ERROR', a.get('id'), repr(e), flush=True)

if not map_candidates:
    raise SystemExit('FAIL_CLOSED: no blocker-to-game evidence map intersects current residual')
map_candidates.sort(key=lambda x: (x['cover'], x['artifact'].get('created_at','')), reverse=True)
best = map_candidates[0]
km = best['map']
linked_keys = {(str(x['season']).strip(), norm_tid(x['team_id']), norm_pid(x['player_id'])) for x in km if x.get('game_ids')}
g2b = defaultdict(list)
for x in km:
    k = (str(x['season']).strip(), norm_tid(x['team_id']), norm_pid(x['player_id']))
    for g in x.get('game_ids', []):
        try: g2b[int(g)].append(k)
        except Exception: pass
ranked_games = sorted(g2b, key=lambda g: (-len(g2b[g]), g))
(OUT / 'CURRENT_BLOCKER_KEY_GAME_MAP.json').write_text(json.dumps(km, indent=2) + '\n')
heartbeat(phase='GAME_MAP', evidence_artifact_id=best['artifact']['id'], mapped_current_keys=len(linked_keys), current_residual=len(blocker_keys), unique_games=len(ranked_games), unmapped_current_keys=len(blocker_keys-linked_keys))

# Pull the retained artifacts recorded by the evidence package and search only current implicated games.
source_ids = []
if best['download_file']:
    try:
        dl = json.loads(best['zip'].read(best['download_file']))
        recs = dl.get('downloaded', []) if isinstance(dl, dict) else dl
        for r in recs:
            try: source_ids.append(int(r['id']))
            except Exception: pass
    except Exception as e:
        print('DOWNLOAD_LIST_ERROR', repr(e), flush=True)
source_ids = list(dict.fromkeys(source_ids))

consensus = []
conflicts = []
if source_ids and ranked_games:
    target_games = set(ranked_games)
    facts = defaultdict(lambda: defaultdict(list))
    files_scanned = files_hit = 0
    for ai, aid in enumerate(source_ids, 1):
        try:
            z = artifact_zip(aid)
        except Exception as e:
            print('SOURCE_ARTIFACT_ERROR', aid, repr(e), flush=True)
            continue
        for name in z.namelist():
            low = name.lower()
            if not (low.endswith('.csv') or low.endswith('.jsonl')):
                continue
            files_scanned += 1
            try:
                raw = z.read(name)
                if low.endswith('.csv'):
                    rows = csv_rows_from_bytes(raw)
                else:
                    rows = [json.loads(x) for x in raw.decode('utf-8', errors='ignore').splitlines() if x.strip()]
                if not rows:
                    continue
                cols = {norm(c): c for c in rows[0].keys()}
                gc = cols.get('game_id') or cols.get('gameid')
                if not gc:
                    continue
                local_hit = False
                for r in rows:
                    try: gid = int(float(str(r.get(gc,''))))
                    except Exception: continue
                    if gid not in target_games:
                        continue
                    local_hit = True
                    tid = str(r.get(cols.get('team_id') or cols.get('teamid'), '')).strip()
                    pid = str(r.get(cols.get('player_id') or cols.get('playerid') or cols.get('person_id') or cols.get('personid'), '')).strip()
                    ident = (gid, tid, pid)
                    for c, val in r.items():
                        if not fact_col(c) or val in (None, ''):
                            continue
                        try:
                            x = float(str(val).replace('%',''))
                            if math.isfinite(x): facts[ident][norm(c)].append((round(x,9), f'{aid}:{name}'))
                        except Exception:
                            pass
                if local_hit: files_hit += 1
            except Exception:
                continue
        if ai % 10 == 0 or ai == len(source_ids):
            heartbeat(phase='RETAINED_SCAN', source_artifacts=len(source_ids), artifacts_processed=ai, files_scanned=files_scanned, files_hit=files_hit, target_games=len(target_games))

    for (gid, tid, pid), fd in facts.items():
        for field, vals in fd.items():
            by = defaultdict(set)
            for v, s in vals: by[v].add(s)
            if len(by) == 1 and len(next(iter(by.values()))) >= 2:
                v = next(iter(by))
                consensus.append({'game_id':gid,'team_id':tid,'player_id':pid,'field':field,'value':v,'independent_files':len(by[v])})
            elif len(by) > 1:
                conflicts.append({'game_id':gid,'team_id':tid,'player_id':pid,'field':field,'values':sorted(by),'source_counts':{str(k):len(v) for k,v in by.items()}})

if consensus:
    with open(OUT / 'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(consensus[0].keys())); w.writeheader(); w.writerows(consensus)
else:
    (OUT / 'PROMOTABLE_RETAINED_FACT_CONSENSUS.csv').write_text('game_id,team_id,player_id,field,value,independent_files\n')
(OUT / 'RETAINED_FACT_CONFLICTS.json').write_text(json.dumps(conflicts, indent=2) + '\n')

yield_map = []
cc = Counter(x['game_id'] for x in consensus)
xc = Counter(x['game_id'] for x in conflicts)
for g in ranked_games:
    yield_map.append({'game_id':g,'blocker_keys_affected':len(g2b[g]),'consensus_facts':cc[g],'conflict_facts':xc[g]})
(OUT / 'HIGH_YIELD_GAME_MAP.json').write_text(json.dumps(yield_map, indent=2) + '\n')

summary = {
    'status': 'PASS',
    'authoritative_manifest_artifact_id': int(cur_art['id']),
    'authoritative_manifest_artifact_name': cur_art['name'],
    'residual_keys': len(blocker_keys),
    'seasons': cur['seasons'],
    'evidence_map_artifact_id': int(best['artifact']['id']),
    'mapped_keys': len(linked_keys),
    'unmapped_keys': len(blocker_keys-linked_keys),
    'unique_implicated_games': len(ranked_games),
    'retained_source_artifacts': len(source_ids),
    'multi_source_consensus_facts': len(consensus),
    'conflicting_facts': len(conflicts),
    'next': 'PROMOTE_EXACT_CONSENSUS_THEN_REMEASURE' if consensus else 'EXTERNAL_EXACT_TEAM_GAME_RECOVERY',
    'no_hourly_polling': True,
    'fail_closed': True
}
(OUT / 'RAPID_DYNAMIC_SUPERVISOR_SUMMARY.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
heartbeat(phase='COMPLETE', **summary)
print(json.dumps(summary, indent=2, sort_keys=True))
