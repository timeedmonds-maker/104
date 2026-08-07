#!/usr/bin/env python3
"""Twitter/X-first Career Team Impact renderer for tenure-corrected TREB data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from funakistats_headshots import DEFAULT_REGISTRY, resolve_headshot


def read_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path)


def fmt(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return "—"
    if not np.isfinite(x):
        return "—"
    if abs(x) >= 100:
        return f"{x:.0f}"
    return f"{x:.1f}"


def metric_label(metric: str) -> str:
    replacements = {
        "TotalReboundPct": "TREB%",
        "OffReboundPct": "OREB%",
        "DefReboundPct": "DREB%",
        "OffRating": "OFF RTG",
        "DefRating": "DEF RTG",
        "NetRating": "NET RTG",
        "EffectiveFgPct": "eFG%",
        "TsPct": "TS%",
        "TurnoverPct": "TOV%",
        "AssistPct": "AST%",
        "Pace": "PACE",
    }
    if metric in replacements:
        return replacements[metric]
    out = str(metric).replace("Pct", "%").replace("Rating", " RTG")
    return out.upper()[:22]


def select_player(career: pd.DataFrame, player_id: str) -> pd.DataFrame:
    frame = career.copy()
    frame["player_id"] = frame["player_id"].astype("string").str.replace(r"\.0$", "", regex=True)
    chosen = frame[frame.player_id == str(player_id)].copy()
    if chosen.empty:
        raise ValueError(f"player_id {player_id} not found")
    for col in ("on", "off_corrected", "on_minus_off_corrected", "minutes_on"):
        chosen[col] = pd.to_numeric(chosen[col], errors="coerce")
    return chosen


def rank_pool(career: pd.DataFrame, metric: str, threshold: float) -> tuple[int, pd.Series]:
    f = career[career.metric.astype(str) == str(metric)].copy()
    f["minutes_on"] = pd.to_numeric(f["minutes_on"], errors="coerce")
    f["on_minus_off_corrected"] = pd.to_numeric(f["on_minus_off_corrected"], errors="coerce")
    f = f[(f.minutes_on >= threshold) & f.on_minus_off_corrected.notna()]
    return len(f), f.on_minus_off_corrected


def build_tiles(career: pd.DataFrame, player: pd.DataFrame, threshold: float, n_each: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    eligible = player[player.minutes_on >= threshold].dropna(subset=["on_minus_off_corrected"]).copy()
    if eligible.empty:
        eligible = player.dropna(subset=["on_minus_off_corrected"]).copy()
    top = eligible.sort_values("on_minus_off_corrected", ascending=False, kind="mergesort").head(n_each).copy()
    bottom = eligible.sort_values("on_minus_off_corrected", ascending=True, kind="mergesort").head(n_each).copy()
    for frame in (top, bottom):
        pools, ranks, pcts = [], [], []
        for _, row in frame.iterrows():
            pool, values = rank_pool(career, str(row.metric), threshold)
            value = float(row.on_minus_off_corrected)
            rank = int((values > value).sum() + 1) if pool else 0
            pct = float((values <= value).mean() * 100.0) if pool else np.nan
            pools.append(pool); ranks.append(rank); pcts.append(pct)
        frame["pool"] = pools; frame["rank"] = ranks; frame["percentile"] = pcts
    return top, bottom


def tenure_strip(team_frame: pd.DataFrame | None, player_id: str) -> list[dict[str, Any]]:
    if team_frame is None or team_frame.empty:
        return []
    f = team_frame.copy()
    f["player_id"] = f["player_id"].astype("string").str.replace(r"\.0$", "", regex=True)
    f = f[f.player_id == str(player_id)].copy()
    if f.empty:
        return []
    cols = [c for c in ("season", "team_id", "team_abbr") if c in f.columns]
    f = f[cols].drop_duplicates()
    result = []
    for team_id, g in f.groupby("team_id", sort=False):
        seasons = sorted(g.season.astype(str).unique()) if "season" in g else []
        abbr = None
        if "team_abbr" in g and g.team_abbr.notna().any():
            abbr = str(g.team_abbr.dropna().iloc[0])
        result.append({"team_id": int(team_id), "label": abbr or str(team_id), "first": seasons[0] if seasons else "", "last": seasons[-1] if seasons else "", "seasons": len(seasons)})
    return result


def render(career: pd.DataFrame, player_id: str, output: Path, threshold: float = 10000,
           team_frame: pd.DataFrame | None = None, registry: Path = DEFAULT_REGISTRY,
           allow_remote_headshot: bool = False) -> dict[str, Any]:
    import matplotlib.pyplot as plt
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage
    from PIL import Image

    player = select_player(career, player_id)
    top, bottom = build_tiles(career, player, threshold)
    name = str(player.player.iloc[0]) if "player" in player.columns else str(player_id)
    minutes = float(pd.to_numeric(player.minutes_on, errors="coerce").max())
    teams = tenure_strip(team_frame, player_id)

    fig = plt.figure(figsize=(12, 15), dpi=240)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    fig.text(.065, .945, name.upper(), fontsize=31, fontweight="bold", va="top")
    fig.text(.067, .902, "CAREER TEAM IMPACT", fontsize=14.5, fontweight="semibold", va="top")
    fig.text(.067, .875, f"Regular season · 2000-01 to 2025-26 · {minutes:,.0f} career minutes", fontsize=10.5, va="top", alpha=.72)

    headshot_path, headshot_audit = resolve_headshot(player_id, registry, allow_remote=allow_remote_headshot)
    if headshot_path:
        image = np.asarray(Image.open(headshot_path).convert("RGBA"))
        box = OffsetImage(image, zoom=.33)
        fig.add_artist(AnnotationBbox(box, (.84, .88), xycoords="figure fraction", frameon=False, zorder=10))

    if teams:
        x = .067
        for t in teams:
            label = t["label"]
            detail = t["first"] if t["first"] == t["last"] else f"{t['first']}–{t['last'][-2:]}"
            text = f"{label}  {detail}"
            fig.text(x, .83, text, fontsize=8.6, fontweight="semibold", va="top")
            x += min(.22, .042 + .0088 * len(text))

    fig.text(.067, .785, "STRONGEST POSITIVE SWINGS", fontsize=12.4, fontweight="bold", va="top")
    fig.text(.515, .785, "LOWEST RELATIVE SWINGS", fontsize=12.4, fontweight="bold", va="top")

    def draw_column(frame: pd.DataFrame, x0: float, y0: float, positive: bool) -> list[dict[str, Any]]:
        audit_rows = []
        h = .060
        for i, (_, row) in enumerate(frame.iterrows()):
            y = y0 - i * h
            fig.text(x0, y, metric_label(str(row.metric)), fontsize=9.4, fontweight="bold", va="top")
            fig.text(x0 + .245, y, f"{float(row.on_minus_off_corrected):+.1f}", fontsize=14.8, fontweight="bold", ha="right", va="top")
            fig.text(x0, y - .021, f"ON {fmt(row.on)}   OFF {fmt(row.off_corrected)}", fontsize=7.8, va="top", alpha=.72)
            rank_text = f"#{int(row['rank'])}/{int(row['pool'])}" if int(row["pool"]) else "rank —"
            fig.text(x0 + .245, y - .021, rank_text, fontsize=7.8, ha="right", va="top", alpha=.72)
            ax.plot([x0, x0 + .245], [y - .044, y - .044], transform=fig.transFigure, linewidth=.45, alpha=.16)
            audit_rows.append({"metric": str(row.metric), "on": float(row.on), "off_corrected": float(row.off_corrected), "swing": float(row.on_minus_off_corrected), "rank_high_to_low": int(row["rank"]), "pool": int(row["pool"]), "percentile": float(row["percentile"]) if np.isfinite(row["percentile"]) else None, "column": "positive" if positive else "lowest"})
        return audit_rows

    audit_tiles = draw_column(top, .067, .748, True) + draw_column(bottom, .515, .748, False)
    fig.text(.067, .095, f"Qualification for historical rank: ≥{int(threshold):,} career minutes · Swing = career ON minus roster-tenure-corrected OFF", fontsize=8.4, va="top", alpha=.72)
    fig.text(.067, .071, "Lowest relative swings are the player's lowest historical ON−OFF marks; they are not automatically negative or 'bad'.", fontsize=8.1, va="top", alpha=.64)

    output.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg", "pdf"):
        fig.savefig(output.with_suffix("." + ext), dpi=320 if ext == "png" else None, bbox_inches="tight", pad_inches=.12)
    plt.close(fig)

    audit = {
        "player_id": str(player_id), "player": name, "threshold": threshold,
        "source_rows": int(len(player)), "teams": teams, "tiles": audit_tiles,
        "headshot": headshot_audit,
        "selection": "top 10 highest and bottom 10 lowest career corrected ON-minus-OFF metrics for this player",
        "rank_definition": "high-to-low historical rank within metric among players meeting the career-minute threshold",
        "outputs": {e: str(output.with_suffix('.' + e)) for e in ("png", "svg", "pdf")},
    }
    output.with_name(output.name + "_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def self_test() -> None:
    rng = np.random.default_rng(104)
    metrics = [f"Metric{i:02d}" for i in range(28)]
    rows = []
    for p in range(40):
        for i, metric in enumerate(metrics):
            swing = rng.normal(0, 3) + (3 if p == 0 and i < 3 else 0)
            rows.append({"player_id": str(1000 + p), "player": f"Player {p}", "metric": metric, "minutes_on": 12000 + p * 10, "on": 50 + swing / 2, "off_corrected": 50 - swing / 2, "on_minus_off_corrected": swing})
    career = pd.DataFrame(rows)
    top, bottom = build_tiles(career, select_player(career, "1000"), 10000)
    assert len(top) == 10 and len(bottom) == 10
    assert top.iloc[0].on_minus_off_corrected >= top.iloc[-1].on_minus_off_corrected
    assert bottom.iloc[0].on_minus_off_corrected <= bottom.iloc[-1].on_minus_off_corrected
    audit = render(career, "1000", Path("/tmp/career_impact_selftest"), allow_remote_headshot=False)
    assert len(audit["tiles"]) == 20
    for ext in ("png", "svg", "pdf"):
        assert Path(f"/tmp/career_impact_selftest.{ext}").exists()
    print("CAREER IMPACT RENDERER SELF-TEST PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--career-input", type=Path)
    parser.add_argument("--team-input", type=Path)
    parser.add_argument("--player-id")
    parser.add_argument("--output-prefix", type=Path, default=Path("outputs/funakistats_career_impact"))
    parser.add_argument("--minutes-threshold", type=float, default=10000)
    parser.add_argument("--headshot-registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--fetch-headshot", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if not args.career_input or not args.player_id:
        raise SystemExit("--career-input and --player-id are required")
    career = read_frame(args.career_input)
    team = read_frame(args.team_input) if args.team_input else None
    print(json.dumps(render(career, args.player_id, args.output_prefix, args.minutes_threshold, team, args.headshot_registry, args.fetch_headshot), indent=2))


if __name__ == "__main__":
    main()
