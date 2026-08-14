#!/usr/bin/env python3
import argparse, json, re
from pathlib import Path
import pandas as pd

KEYS = ['season','team_id','player_id']
EXPECTED_CANONICAL = 14524
EXPECTED_EXACT = 4877
EXPECTED_COMPLEMENT = 9647

DATA_SUFFIXES = ('.csv', '.csv.gz', '.parquet', '.json', '.json.gz', '.jsonl', '.jsonl.gz')


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', str(s).lower())


def is_reboundish(s):
    n = norm(s)
    return ('rebound' in n) or ('oreb' in n) or ('dreb' in n) or bool(re.search(r'(^|[^a-z])reb', str(s).lower()))


def is_pctish(s):
    n = norm(s)
    return any(x in n for x in ('pct','percent','percentage','rate','display'))


def onoff_signal(s):
    x = str(s).lower()
    n = norm(s)
    on = bool(re.search(r'(^|[^a-z])on([^a-z]|$)', x)) or n.startswith('on') or n.endswith('on')
    off = bool(re.search(r'(^|[^a-z])off([^a-z]|$)', x)) or n.startswith('off') or n.endswith('off')
    return on, off


def read_sample(path, n=50):
    s = str(path)
    try:
        if s.endswith('.csv') or s.endswith('.csv.gz'):
            return pd.read_csv(path, nrows=n, low_memory=False)
        if s.endswith('.parquet'):
            return pd.read_parquet(path).head(n)
        if s.endswith('.json') or s.endswith('.json.gz'):
            try:
                return pd.read_json(path).head(n)
            except Exception:
                obj = json.loads(path.read_text()) if s.endswith('.json') else None
                if isinstance(obj, list): return pd.DataFrame(obj).head(n)
                if isinstance(obj, dict): return pd.json_normalize(obj).head(n)
        if s.endswith('.jsonl') or s.endswith('.jsonl.gz'):
            return pd.read_json(path, lines=True).head(n)
    except Exception:
        return None
    return None


def canonicalize(df):
    x = df.copy()
    if 'season' in x.columns: x['season'] = x['season'].astype(str)
    if 'team_id' in x.columns: x['team_id'] = pd.to_numeric(x['team_id'], errors='coerce').astype('Int64')
    if 'player_id' in x.columns:
        x['player_id'] = x['player_id'].astype(str).str.replace(r'\.0$', '', regex=True)
    return x


def full_read(path):
    s = str(path)
    if s.endswith('.csv') or s.endswith('.csv.gz'):
        return pd.read_csv(path, low_memory=False)
    if s.endswith('.parquet'):
        return pd.read_parquet(path)
    if s.endswith('.jsonl') or s.endswith('.jsonl.gz'):
        return pd.read_json(path, lines=True)
    if s.endswith('.json') or s.endswith('.json.gz'):
        return pd.read_json(path)
    raise ValueError(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default='team_trb_all_players/impact_database/outputs')
    ap.add_argument('--canonical', required=True)
    ap.add_argument('--exact-detail', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    root = Path(args.root)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    canonical = canonicalize(pd.read_json(args.canonical, lines=True, compression='gzip'))[KEYS].drop_duplicates()
    assert len(canonical) == EXPECTED_CANONICAL, len(canonical)
    exact = canonicalize(pd.read_csv(args.exact_detail, low_memory=False))[KEYS].drop_duplicates()
    assert len(exact) == EXPECTED_EXACT, len(exact)
    complement = canonical.merge(exact.assign(_exact=1), on=KEYS, how='left').query('_exact != 1').drop(columns='_exact')
    assert len(complement) == EXPECTED_COMPLEMENT, len(complement)

    records = []
    by_family = {}
    for y in range(2000, 2026):
        season = f'{y}-{(y+1)%100:02d}'
        d = root / season
        if not d.exists(): continue
        for p in d.rglob('*'):
            if not p.is_file() or not str(p).endswith(DATA_SUFFIXES): continue
            sample = read_sample(p)
            if sample is None: continue
            cols = [str(c) for c in sample.columns]
            rebound_cols = [c for c in cols if is_reboundish(c)]
            raw_rebound_cols = [c for c in rebound_cols if not is_pctish(c)]
            on_cols = []; off_cols = []
            for c in raw_rebound_cols:
                on, off = onoff_signal(c)
                if on: on_cols.append(c)
                if off: off_cols.append(c)
            long_value_cols = [c for c in cols if norm(c) in {'on','off','onvalue','offvalue','valueon','valueoff','oncount','offcount'}]
            label_cols = [c for c in cols if norm(c) in {'metric','stat','statname','metricname','name','type','component'}]
            rebound_labels = {}
            for c in label_cols:
                try:
                    vals = sample[c].dropna().astype(str)
                    hits = sorted(set(v for v in vals if is_reboundish(v)))[:30]
                    if hits: rebound_labels[c] = hits
                except Exception:
                    pass
            score = len(raw_rebound_cols) + 3*len(on_cols) + 3*len(off_cols) + 4*len(long_value_cols) + 4*sum(len(v) for v in rebound_labels.values())
            rel = str(p.relative_to(root/season))
            family = rel
            rec = {
                'season': season, 'relative_path': rel, 'columns': cols,
                'rebound_columns': rebound_cols, 'raw_rebound_columns': raw_rebound_cols,
                'raw_on_columns': on_cols, 'raw_off_columns': off_cols,
                'long_onoff_value_columns': long_value_cols,
                'rebound_metric_labels_in_sample': rebound_labels,
                'sample_rows': int(len(sample)), 'candidate_score': int(score),
            }
            records.append(rec)
            by_family.setdefault(family, []).append(rec)

    family_rows = []
    for family, rs in by_family.items():
        seasons = sorted({r['season'] for r in rs})
        union_cols = sorted(set().union(*(set(r['columns']) for r in rs)))
        union_reb = sorted(set().union(*(set(r['rebound_columns']) for r in rs)))
        union_raw = sorted(set().union(*(set(r['raw_rebound_columns']) for r in rs)))
        union_on = sorted(set().union(*(set(r['raw_on_columns']) for r in rs)))
        union_off = sorted(set().union(*(set(r['raw_off_columns']) for r in rs)))
        max_score = max(r['candidate_score'] for r in rs)
        family_rows.append({
            'relative_path': family, 'seasons_found': len(seasons), 'seasons': seasons,
            'columns': union_cols, 'rebound_columns': union_reb, 'raw_rebound_columns': union_raw,
            'raw_on_columns': union_on, 'raw_off_columns': union_off,
            'max_candidate_score': max_score,
        })
    family_rows.sort(key=lambda r: (-r['max_candidate_score'], -r['seasons_found'], r['relative_path']))

    # Coverage-test the strongest families that have canonical keys and an ON/OFF signal.
    coverage = []
    for fam in family_rows[:40]:
        if not (fam['raw_on_columns'] and fam['raw_off_columns']):
            # Still test long-form structures with literal on/off value columns.
            rs = by_family[fam['relative_path']]
            if not any(r['long_onoff_value_columns'] and r['rebound_metric_labels_in_sample'] for r in rs):
                continue
        frames = []
        errors = []
        for y in range(2000, 2026):
            season = f'{y}-{(y+1)%100:02d}'
            p = root / season / fam['relative_path']
            if not p.exists(): continue
            try:
                d = full_read(p)
                if 'season' not in d.columns: d['season'] = season
                frames.append(d)
            except Exception as e:
                errors.append(f'{season}: {type(e).__name__}: {e}')
        if not frames: continue
        allx = canonicalize(pd.concat(frames, ignore_index=True, sort=False))
        have_keys = all(k in allx.columns for k in KEYS)
        if have_keys:
            src_keys = allx[KEYS].dropna().drop_duplicates()
            canon_cov = len(canonical.merge(src_keys, on=KEYS, how='inner'))
            comp_cov = len(complement.merge(src_keys, on=KEYS, how='inner'))
            dup = int(allx.duplicated(KEYS).sum())
        else:
            canon_cov = comp_cov = dup = None
        coverage.append({
            'relative_path': fam['relative_path'], 'rows': int(len(allx)), 'has_canonical_keys': have_keys,
            'canonical_coverage_keys': canon_cov, 'complement_coverage_keys': comp_cov,
            'duplicate_key_rows': dup, 'read_errors': errors,
            'raw_on_columns': fam['raw_on_columns'], 'raw_off_columns': fam['raw_off_columns'],
            'rebound_columns': fam['rebound_columns'],
        })

    release_candidates = [c for c in coverage if c['canonical_coverage_keys']==EXPECTED_CANONICAL and c['complement_coverage_keys']==EXPECTED_COMPLEMENT]
    report = {
        'status': 'PASS',
        'canonical_keys': EXPECTED_CANONICAL,
        'exact_keys': EXPECTED_EXACT,
        'complement_keys': EXPECTED_COMPLEMENT,
        'files_scanned': len(records),
        'file_families_scanned': len(family_rows),
        'coverage_tested_families': len(coverage),
        'release_candidate_families': release_candidates,
        'top_candidate_families': family_rows[:25],
        'conclusion': 'DIRECT_ONOFF_RAW_SOURCE_FOUND' if release_candidates else 'NO_SINGLE_FULL_COVERAGE_DIRECT_ONOFF_RAW_SOURCE_FOUND',
    }
    (out/'RETAINED_ONOFF_REBOUND_ACCUMULATOR_AUDIT.json').write_text(json.dumps(report, indent=2)+'\n')
    (out/'RETAINED_ONOFF_REBOUND_ACCUMULATOR_FILES.json').write_text(json.dumps(records, indent=2)+'\n')
    (out/'RETAINED_ONOFF_REBOUND_ACCUMULATOR_COVERAGE.json').write_text(json.dumps(coverage, indent=2)+'\n')
    md = [
        '# Retained ON/OFF rebound accumulator audit','',
        f'- Files scanned: **{len(records)}**',
        f'- File families: **{len(family_rows)}**',
        f'- Coverage-tested candidate families: **{len(coverage)}**',
        f'- Full canonical/complement release candidates: **{len(release_candidates)}**',
        f'- Conclusion: **{report["conclusion"]}**','',
        '## Release candidates',
    ]
    if release_candidates:
        for c in release_candidates:
            md += [f'- `{c["relative_path"]}` — canonical {c["canonical_coverage_keys"]}/{EXPECTED_CANONICAL}; complement {c["complement_coverage_keys"]}/{EXPECTED_COMPLEMENT}; ON fields {c["raw_on_columns"]}; OFF fields {c["raw_off_columns"]}']
    else:
        md += ['- None found as a single full-coverage source. See JSON for ranked candidate schemas.']
    (out/'RETAINED_ONOFF_REBOUND_ACCUMULATOR_AUDIT.md').write_text('\n'.join(md)+'\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
