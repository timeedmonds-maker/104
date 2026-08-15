#!/usr/bin/env python3
"""Fail-closed residual supervisor v3.

Adds three finite recovery capabilities without relaxing TREB integrity:
1. reuse every retained exact/partial game fact already produced by prior runners;
2. permit a missing V3 feed only when legacy NBA chronology is provably
   lineup-order-insensitive (every substitution clock is unique);
3. isolate pathological games behind a per-game wall-clock timeout so one game
   cannot suppress all other season results.

All acceptance/materiality/minutes gates remain delegated to
residual_materiality_autopilot.py unchanged.
"""
from __future__ import annotations

import signal
from pathlib import Path

import pandas as pd

import residual_materiality_autopilot as base
import production_treb_engine_v3 as lineup_v3

GAME_TIMEOUT_SECONDS = 300


class GameTimeout(RuntimeError):
    pass


def _alarm(_signum, _frame):
    raise GameTimeout("finite per-game recovery timeout")


def _safe_without_v3(nba_game: pd.DataFrame) -> tuple[bool, str]:
    """Allow V3-free replay only when tie ordering cannot alter a lineup."""
    if nba_game is None or nba_game.empty:
        return False, "missing_nba"
    g, _ = lineup_v3.legacy.prepare_nba_game(nba_game)
    if g.empty:
        return False, "empty_prepared_nba"
    g = g.copy()
    g["EVENTMSGTYPE"] = pd.to_numeric(g["EVENTMSGTYPE"], errors="coerce")
    g["PERIOD"] = pd.to_numeric(g["PERIOD"], errors="coerce")
    if g[["EVENTMSGTYPE", "PERIOD", "PCTIMESTRING"]].isna().any().any():
        return False, "missing_chronology_fields"
    for (_period, _clock), rows in g.groupby(["PERIOD", "PCTIMESTRING"], sort=False):
        if len(rows) > 1 and rows["EVENTMSGTYPE"].eq(8).any():
            return False, "substitution_shares_clock"
    return True, "all_substitution_clocks_unique"


def _read_many(root: Path, names: tuple[str, ...]) -> list[pd.DataFrame]:
    frames = []
    seen_paths = set()
    for name in names:
        for p in root.rglob(name):
            rp = str(p.resolve())
            if rp in seen_paths:
                continue
            seen_paths.add(rp)
            try:
                d = pd.read_csv(p, low_memory=False)
                if not d.empty:
                    d["_retained_source_path"] = str(p)
                    frames.append(d)
            except Exception as exc:
                print(f"PRIOR_FACT_READ_SKIP path={p} error={type(exc).__name__}:{exc}", flush=True)
    return frames


def _conflict_safe_union(frames: list[pd.DataFrame], keys: list[str], value_cols: list[str]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True)
    present = [c for c in value_cols if c in d.columns]
    kept = []
    conflicts = 0
    for _, group in d.groupby(keys, sort=False, dropna=False):
        distinct = group[present].drop_duplicates() if present else group.iloc[:1]
        if len(distinct) > 1:
            conflicts += 1
            continue
        kept.append(group.iloc[0])
    if conflicts:
        print(f"PRIOR_FACT_CONFLICT_KEYS={conflicts} action=drop_fail_closed", flush=True)
    return pd.DataFrame(kept).reset_index(drop=True) if kept else pd.DataFrame(columns=d.columns)


def load_prior_facts_v3(root: Path, season: str):
    team_frames = _read_many(root, ("team_game_treb.csv.gz", "team_game_treb.partial.csv.gz"))
    player_frames = _read_many(root, ("player_game_treb_on.csv.gz", "player_game_treb_on.partial.csv.gz"))
    team_vals = ["game_seconds", "team_oreb", "team_dreb", "opponent_oreb", "opponent_dreb"]
    player_vals = ["seconds_on", "team_oreb_on", "team_dreb_on", "opponent_oreb_on", "opponent_dreb_on"]
    td = _conflict_safe_union(team_frames, ["game_id", "team_id"], team_vals)
    pdx = _conflict_safe_union(player_frames, ["game_id", "team_id", "player_id"], player_vals)
    if not pdx.empty:
        pdx["player_id"] = pdx["player_id"].map(base.sid)
    print(
        f"PRIOR_FACT_UNION season={season} team_games={0 if td.empty else td.game_id.nunique()} "
        f"player_game_rows={len(pdx)}",
        flush=True,
    )
    return td, pdx


_original_game_variants = base.game_variants


def game_variants_v3(gid, ng, vg, pg, candidate_map):
    local_vg = vg
    fallback = None
    if gid in ng and gid in pg and gid not in vg:
        safe, proof = _safe_without_v3(ng[gid])
        if safe:
            local_vg = dict(vg)
            local_vg[gid] = pd.DataFrame(
                columns=["gameId", "period", "actionNumber", "actionId", "personId", "teamId"]
            )
            fallback = proof
        else:
            return [], {
                "status": "SOURCE_SET_GAP",
                "nba": True,
                "v3": False,
                "pbp": True,
                "v3_optional_safe": False,
                "v3_safety_reason": proof,
            }

    old_handler = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(GAME_TIMEOUT_SECONDS)
    try:
        variants, qa = _original_game_variants(gid, ng, local_vg, pg, candidate_map)
    except GameTimeout:
        return [], {
            "status": "GAME_TIMEOUT",
            "timeout_seconds": GAME_TIMEOUT_SECONDS,
            "nba": gid in ng,
            "v3": gid in vg,
            "pbp": gid in pg,
        }
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

    if fallback:
        qa = dict(qa)
        qa["v3_optional_safe"] = True
        qa["v3_safety_proof"] = fallback
        qa["chronology_method"] = "legacy_eventnum_only_after_unique_substitution_clock_proof"
    return variants, qa


def main():
    base.STATE_CAP = 2_000_000
    base.SCENARIO_CAP = 4_096
    base.load_prior_facts = load_prior_facts_v3
    base.game_variants = game_variants_v3
    base.main()


if __name__ == "__main__":
    main()
