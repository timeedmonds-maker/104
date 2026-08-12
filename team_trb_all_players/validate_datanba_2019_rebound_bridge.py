#!/usr/bin/env python3
"""Validate archived data.nba as an exact fallback for 2019 rebound semantics.

The three source-missing 2019 games exist in NBA Stats V3 but not the retained
legacy nbastats archive.  The independent `data.nba` archive carries the older
compact event schema (evt/cl/de/mtype/etype/pid/tid).  This script maps that
schema into exactly the fields used by the locked historical `_nba_real_rebound`
rule and validates the resulting live/dead labels against every overlapping
legacy 2019 rebound event before it may be used on the three missing games.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import local_treb_rebuild as core

TARGET_GAMES = [21901316, 21901317, 21901318]


def nseries(df: pd.DataFrame, name: str, default=0) -> pd.Series:
    if name not in df:
        return pd.Series(default, index=df.index)
    return pd.to_numeric(df[name], errors="coerce").fillna(default)


def prepare_legacy(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    for c in ("GAME_ID", "EVENTNUM", "EVENTMSGTYPE", "EVENTMSGACTIONTYPE", "PERIOD", "PLAYER1_ID"):
        x[c] = pd.to_numeric(x[c], errors="coerce").fillna(0).astype("int64")
    x["DESCRIPTION_NORM"] = core.nba_description(x)
    x["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(x.PERIOD, x.PCTIMESTRING)]
    return x.sort_values(["GAME_ID", "PERIOD", "EVENTNUM"], kind="stable")


def prepare_datanba(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    rename = {c: c.strip() for c in x.columns}
    x = x.rename(columns=rename)
    for c in ("GAME_ID", "evt", "etype", "mtype", "PERIOD", "pid", "tid", "ord"):
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    x["GAME_ID"] = x.GAME_ID.fillna(0).astype("int64")
    x["evt"] = x.evt.fillna(0).astype("int64")
    x["etype"] = x.etype.fillna(0).astype("int64")
    x["mtype"] = x.mtype.fillna(0).astype("int64")
    x["PERIOD"] = x.PERIOD.fillna(0).astype("int64")
    x["pid"] = x.pid.fillna(0).astype("int64")
    x["DESCRIPTION_NORM"] = x.de.fillna("").astype(str).map(core.normalize_description)
    x["ELAPSED"] = [core.elapsed_seconds(int(p), c) for p, c in zip(x.PERIOD, x.cl)]
    return x.sort_values(["GAME_ID", "PERIOD", "evt"], kind="stable")


def datanba_as_core_game(game: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame({
        "EVENTNUM": game.evt.astype("int64"),
        "EVENTMSGTYPE": game.etype.astype("int64"),
        "EVENTMSGACTIONTYPE": game.mtype.astype("int64"),
        "PERIOD": game.PERIOD.astype("int64"),
        "PLAYER1_ID": game.pid.astype("int64"),
        "DESCRIPTION_NORM": game.DESCRIPTION_NORM.astype(str),
        "ELAPSED": game.ELAPSED.astype("int64"),
    }, index=game.index)
    return x


def rebound_labels_from_legacy(legacy: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for gid, game in legacy.groupby("GAME_ID", sort=False):
        game = game.sort_values(["PERIOD", "EVENTNUM"], kind="stable")
        for idx, r in game[game.EVENTMSGTYPE.eq(4)].iterrows():
            rows.append({
                "GAME_ID": int(gid),
                "EVENTNUM": int(r.EVENTNUM),
                "LEGACY_REAL": bool(core._nba_real_rebound(game, idx)),
                "LEGACY_PLAYER1_ID": int(r.PLAYER1_ID),
                "LEGACY_ACTION": int(r.EVENTMSGACTIONTYPE),
                "LEGACY_DESCRIPTION": str(r.DESCRIPTION_NORM),
            })
    return pd.DataFrame(rows)


def rebound_labels_from_datanba(datanba: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for gid, game_raw in datanba.groupby("GAME_ID", sort=False):
        game_raw = game_raw.sort_values(["PERIOD", "evt"], kind="stable")
        game = datanba_as_core_game(game_raw)
        for idx, r in game[game.EVENTMSGTYPE.eq(4)].iterrows():
            src = game_raw.loc[idx]
            rows.append({
                "GAME_ID": int(gid),
                "EVENTNUM": int(r.EVENTNUM),
                "DATANBA_REAL": bool(core._nba_real_rebound(game, idx)),
                "DATANBA_PLAYER1_ID": int(r.PLAYER1_ID),
                "DATANBA_ACTION": int(r.EVENTMSGACTIONTYPE),
                "DATANBA_DESCRIPTION": str(r.DESCRIPTION_NORM),
                "clock": str(src.cl),
                "period": int(src.PERIOD),
                "tid": int(src.tid) if pd.notna(src.get("tid")) else 0,
            })
    return pd.DataFrame(rows)


def clean_records(df: pd.DataFrame, n=500) -> list[dict]:
    x = df.head(n).copy()
    return x.where(pd.notna(x), None).to_dict("records")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", type=Path, required=True)
    ap.add_argument("--datanba", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    a = ap.parse_args()

    legacy = prepare_legacy(pd.read_csv(a.legacy, low_memory=False))
    datanba = prepare_datanba(pd.read_csv(a.datanba, low_memory=False))
    legacy_labels = rebound_labels_from_legacy(legacy)
    data_labels = rebound_labels_from_datanba(datanba)

    overlap = data_labels.merge(
        legacy_labels,
        on=["GAME_ID", "EVENTNUM"],
        how="inner",
        validate="one_to_one",
    )
    overlap["same_real"] = overlap.DATANBA_REAL.eq(overlap.LEGACY_REAL)
    overlap["same_player"] = overlap.DATANBA_PLAYER1_ID.eq(overlap.LEGACY_PLAYER1_ID)
    overlap["same_action"] = overlap.DATANBA_ACTION.eq(overlap.LEGACY_ACTION)
    mism = overlap[~overlap.same_real].copy()

    target_rows = data_labels[data_labels.GAME_ID.isin(TARGET_GAMES)].copy()
    target_game_counts = {
        str(gid): {
            "datanba_event_rows": int((datanba.GAME_ID == gid).sum()),
            "datanba_rebound_rows": int((target_rows.GAME_ID == gid).sum()),
            "datanba_real_rebounds": int(target_rows.loc[target_rows.GAME_ID == gid, "DATANBA_REAL"].sum()),
        }
        for gid in TARGET_GAMES
    }

    payload = {
        "legacy_rebound_labels": int(len(legacy_labels)),
        "datanba_rebound_labels": int(len(data_labels)),
        "overlap_eventnum_rebounds": int(len(overlap)),
        "live_dead_exact_matches": int(overlap.same_real.sum()),
        "live_dead_mismatches": int((~overlap.same_real).sum()),
        "live_dead_accuracy": float(overlap.same_real.mean()) if len(overlap) else None,
        "player_id_exact_matches": int(overlap.same_player.sum()),
        "action_type_exact_matches": int(overlap.same_action.sum()),
        "target_game_counts": target_game_counts,
        "target_rebound_rows": clean_records(target_rows),
        "mismatch_samples": clean_records(mism, 200),
        "gate": "PASS" if len(overlap) and not len(mism) else "FAIL",
        "policy": "data.nba fallback is production-eligible only if live/dead labels are exact against overlapping legacy 2019 events",
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k not in {"target_rebound_rows", "mismatch_samples"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
