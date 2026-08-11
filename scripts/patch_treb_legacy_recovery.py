#!/usr/bin/env python3
"""Apply narrowly-scoped hypothetical TREB recovery fixes in a canary checkout.

This script intentionally edits a disposable checkout only. It refuses to run
unless the exact known-bad block is present exactly once, so a source drift
cannot silently broaden the patch.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_root", type=Path)
    args = ap.parse_args()

    path = args.repo_root / "team_trb_all_players" / "production_treb_engine.py"
    text = path.read_text(encoding="utf-8")

    old = '''    bad_after_horn = []
    for period_no, period in game.groupby("PERIOD", sort=False):
        ordered = period.sort_values("EVENTNUM", kind="stable")
        horns = ordered.loc[ordered.EVENTMSGTYPE.eq(13), "EVENTNUM"]
        if horns.empty:
            continue
        first_horn = int(horns.iloc[0])
        later = ordered[ordered.EVENTNUM.gt(first_horn)]
        for idx, row in later.iterrows():
            if str(row.PCTIMESTRING) not in {"0:00", "00:00", "0:00.0", "00:00.0"} and int(row.EVENTMSGTYPE) != 18:
                bad_after_horn.append(idx)
                repairs.append({"game_id": game_id, "period": int(period_no),
                                "event_num": int(row.EVENTNUM), "type": "post_horn_clock_repair",
                                "clock": str(row.PCTIMESTRING),
                                "evidence": "non-zero-clock row appears after explicit period-ending horn"})
    if bad_after_horn:
        game = game.drop(index=bad_after_horn)
'''

    count = text.count(old)
    if count != 1:
        raise SystemExit(f"refusing patch: expected exact bad post-horn block once, found {count}")

    new = '''    # IMPORTANT: do not generically delete rows whose EVENTNUM follows a period-end\n    # marker. Legacy NBA Stats feeds contain non-chronological EVENTNUM values and\n    # replay/correction inserts; treating EVENTNUM as a strict post-horn chronology\n    # silently deleted legitimate events. Any genuine post-horn anomaly must be\n    # repaired by an explicit game/period/event key backed by evidence.\n'''

    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"PATCHED {path}")
    print("PATCH_ID=remove_overbroad_post_horn_drop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
