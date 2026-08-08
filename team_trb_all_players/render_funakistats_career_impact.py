#!/usr/bin/env python3
"""Twitter/X-first Career Team Impact renderer for tenure-corrected TREB data.

Default presentation is fan-first: a highlights-only card using an adaptive historical
minutes threshold. A full profile remains available as an explicit mode.

The comparison threshold is independent of the subject's minutes when manually
overridden. This allows, for example, a 3,800-minute player to be compared with a
10,000+ minute cohort. In that case the graphic explicitly describes the result as a
comparison position rather than falsely saying the subject belongs to that cohort.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from funakistats_headshots import DEFAULT_REGISTRY, resolve_headshot

AUTO_THRESHOLDS = (10000.0, 5000.0, 2500.0, 1000.0, 500.0)
DEFAULT_HIGHLIGHT_COUNT = 16
DEFAULT_FULL_COUNT_EACH = 10


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


def player_minutes(player: pd.DataFrame) -> float:
    values = pd.to_numeric(player.get("minutes_on"), errors="coerce").dropna()
    if values.empty:
        raise ValueError("player has no valid career minutes")
    return float(values.max())


def adaptive_threshold(minutes: float) -> float:
    """Choose the strongest standard cohort the subject actually qualifies for."""
    if not np.isfinite(minutes) or minutes < 0:
        raise ValueError(f"invalid career minutes: {minutes}")
    for threshold in AUTO_THRESHOLDS:
        if minutes >= threshold:
            return threshold
    if minutes >= 100:
        return float(math.floor(minutes / 100.0) * 100.0)
    return 0.0


def resolve_threshold(minutes: float, requested: float | None) -> tuple[float, str, bool]:
    """Resolve comparison threshold.

    Auto mode always chooses a cohort containing the subject. Manual mode accepts any
    non-negative threshold, including zero (all players) and thresholds above the
    subject's own minutes. The returned boolean records whether the subject belongs to
    the selected comparison cohort.
    """
    if requested is None:
        threshold = adaptive_threshold(minutes)
        return threshold, "auto", True
    threshold = float(requested)
    if not np.isfinite(threshold) or threshold < 0:
        raise ValueError("minutes threshold must be a finite value >= 0")
    return threshold, "manual", bool(minutes + 1e-9 >= threshold)


def rank_pool(career: pd.DataFrame, metric: str, threshold: float) -> tuple[int, pd.Series]:
    f = career[career.metric.astype(str) == str(metric)].copy()
    f["player_id"] = f["player_id"].astype("string").str.replace(r"\.0$", "", regex=True)
    f["minutes_on"] = pd.to_numeric(f["minutes_on"], errors="coerce")
    f["on_minus_off_corrected"] = pd.to_numeric(f["on_minus_off_corrected"], errors="coerce")
    f = f[(f.minutes_on >= threshold) & f.on_minus_off_corrected.notna()]
    f = f.drop_duplicates("player_id", keep="first")
    return len(f), f.on_minus_off_corrected


def add_rank_fields(career: pd.DataFrame, frame: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Rank subject values against the selected comparison pool."""
    frame = frame.copy()
    pools, ranks, pcts, subject_flags = [], [], [], []
    for _, row in frame.iterrows():
        pool, values = rank_pool(career, str(row.metric), threshold)
        value = float(row.on_minus_off_corrected)
        subject_in_cohort = bool(float(row.minutes_on) + 1e-9 >= threshold)
        rank = int((values > value).sum() + 1)
        pct = float((values <= value).mean() * 100.0) if pool else np.nan
        pools.append(pool)
        ranks.append(rank)
        pcts.append(pct)
        subject_flags.append(subject_in_cohort)
    frame["pool"] = pools
    frame["rank"] = ranks
    frame["percentile"] = pcts
    frame["subject_in_cohort"] = subject_flags
    return frame


def _subject_metrics(player: pd.DataFrame) -> pd.DataFrame:
    eligible = player[player.on_minus_off_corrected.notna()].copy()
    if eligible.empty:
        raise ValueError("player has no rankable metrics")
    return eligible


def build_highlights(career: pd.DataFrame, player: pd.DataFrame, threshold: float,
                     count: int = DEFAULT_HIGHLIGHT_COUNT) -> pd.DataFrame:
    eligible = _subject_metrics(player)
    positive = eligible[eligible.on_minus_off_corrected > 0].copy()
    source = positive if not positive.empty else eligible
    chosen = source.sort_values("on_minus_off_corrected", ascending=False, kind="mergesort").head(count)
    return add_rank_fields(career, chosen, threshold)


def build_full_profile(career: pd.DataFrame, player: pd.DataFrame, threshold: float,
                       n_each: int = DEFAULT_FULL_COUNT_EACH) -> tuple[pd.DataFrame, pd.DataFrame]:
    eligible = _subject_metrics(player)
    top = eligible.sort_values("on_minus_off_corrected", ascending=False, kind="mergesort").head(n_each)
    bottom = eligible.sort_values("on_minus_off_corrected", ascending=True, kind="mergesort").head(n_each)
    return add_rank_fields(career, top, threshold), add_rank_fields(career, bottom, threshold)


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
        result.append({"team_id": int(team_id), "label": abbr or str(team_id),
                       "first": seasons[0] if seasons else "", "last": seasons[-1] if seasons else "",
                       "seasons": len(seasons)})
    return result


def _tile_audit(row: pd.Series, section: str) -> dict[str, Any]:
    return {
        "metric": str(row.metric), "on": float(row.on), "off_corrected": float(row.off_corrected),
        "swing": float(row.on_minus_off_corrected), "rank_high_to_low": int(row["rank"]),
        "pool": int(row["pool"]),
        "percentile": float(row["percentile"]) if np.isfinite(row["percentile"]) else None,
        "subject_in_cohort": bool(row["subject_in_cohort"]), "section": section,
    }


def render(career: pd.DataFrame, player_id: str, output: Path, threshold: float | None = None,
           team_frame: pd.DataFrame | None = None, registry: Path = DEFAULT_REGISTRY,
           allow_remote_headshot: bool = False, mode: str = "highlights",
           highlight_count: int = DEFAULT_HIGHLIGHT_COUNT,
           full_count_each: int = DEFAULT_FULL_COUNT_EACH) -> dict[str, Any]:
    import matplotlib.pyplot as plt
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage
    from PIL import Image

    if mode not in {"highlights", "full"}:
        raise ValueError("mode must be 'highlights' or 'full'")
    player = select_player(career, player_id)
    minutes = player_minutes(player)
    effective_threshold, threshold_mode, subject_in_cohort = resolve_threshold(minutes, threshold)
    name = str(player.player.iloc[0]) if "player" in player.columns else str(player_id)
    teams = tenure_strip(team_frame, player_id)

    fig = plt.figure(figsize=(12, 15), dpi=240)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    fig.text(.065, .945, name.upper(), fontsize=31, fontweight="bold", va="top")
    subtitle = "CAREER TEAM IMPACT HIGHLIGHTS" if mode == "highlights" else "CAREER TEAM IMPACT · FULL PROFILE"
    fig.text(.067, .902, subtitle, fontsize=14.5, fontweight="semibold", va="top")
    fig.text(.067, .875, f"Regular season · 2000-01 to 2025-26 · {minutes:,.0f} career minutes",
             fontsize=10.5, va="top", alpha=.72)

    headshot_path, headshot_audit = resolve_headshot(player_id, registry, allow_remote=allow_remote_headshot)
    if headshot_path:
        image = np.asarray(Image.open(headshot_path).convert("RGBA"))
        fig.add_artist(AnnotationBbox(OffsetImage(image, zoom=.33), (.84, .88),
                                      xycoords="figure fraction", frameon=False, zorder=10))

    if teams:
        x = .067
        for t in teams:
            detail = t["first"] if t["first"] == t["last"] else f"{t['first']}–{t['last'][-2:]}"
            text = f"{t['label']}  {detail}"
            fig.text(x, .83, text, fontsize=8.6, fontweight="semibold", va="top")
            x += min(.22, .042 + .0088 * len(text))

    def draw_tile(row: pd.Series, x0: float, y: float, width: float, section: str) -> dict[str, Any]:
        fig.text(x0, y, metric_label(str(row.metric)), fontsize=10.1, fontweight="bold", va="top")
        fig.text(x0 + width, y, f"{float(row.on_minus_off_corrected):+.1f}", fontsize=15.8,
                 fontweight="bold", ha="right", va="top")
        fig.text(x0, y - .022, f"ON {fmt(row.on)}   OFF {fmt(row.off_corrected)}",
                 fontsize=8.1, va="top", alpha=.72)
        pool, rank = int(row["pool"]), int(row["rank"])
        if bool(row["subject_in_cohort"]):
            rank_text = f"#{rank} of {pool}" if pool else "rank —"
        else:
            rank_text = f"would rank #{rank} vs {pool}" if pool else "comparison —"
        fig.text(x0 + width, y - .022, rank_text, fontsize=8.1, ha="right", va="top", alpha=.72)
        ax.plot([x0, x0 + width], [y - .046, y - .046], transform=fig.transFigure,
                linewidth=.45, alpha=.16)
        return _tile_audit(row, section)

    audit_tiles: list[dict[str, Any]] = []
    if mode == "highlights":
        highlights = build_highlights(career, player, effective_threshold, highlight_count)
        fig.text(.067, .785, "STRONGEST CAREER ON−OFF SWINGS", fontsize=12.8, fontweight="bold", va="top")
        rows_per_col = max(1, math.ceil(len(highlights) / 2))
        h = min(.073, .59 / rows_per_col)
        for i, (_, row) in enumerate(highlights.iterrows()):
            col = 0 if i < rows_per_col else 1
            pos = i if col == 0 else i - rows_per_col
            audit_tiles.append(draw_tile(row, .067 if col == 0 else .515, .748 - pos * h, .36, "highlight"))
        footer_note = "Highlights mode shows the subject's highest positive corrected ON−OFF swings when available."
        selection = f"highlights: up to {highlight_count} highest positive career corrected ON-minus-OFF metrics"
    else:
        top, bottom = build_full_profile(career, player, effective_threshold, full_count_each)
        fig.text(.067, .785, "HIGHEST RELATIVE SWINGS", fontsize=12.4, fontweight="bold", va="top")
        fig.text(.515, .785, "LOWEST RELATIVE SWINGS", fontsize=12.4, fontweight="bold", va="top")
        h = .060
        for i, (_, row) in enumerate(top.iterrows()):
            audit_tiles.append(draw_tile(row, .067, .748 - i * h, .245, "highest"))
        for i, (_, row) in enumerate(bottom.iterrows()):
            audit_tiles.append(draw_tile(row, .515, .748 - i * h, .245, "lowest"))
        footer_note = "Lowest relative swings are not automatically negative or 'bad'; role and metric direction still matter."
        selection = f"full profile: top {full_count_each} highest and bottom {full_count_each} lowest career corrected ON-minus-OFF metrics"

    cohort = (f"players with ≥{int(effective_threshold):,} career minutes" if effective_threshold > 0
              else "all players with a valid career value")
    if subject_in_cohort:
        rank_context = f"Historical rank cohort: {cohort}"
        rank_definition = "high-to-low historical rank within metric among players meeting the displayed career-minute threshold"
    else:
        rank_context = f"Manual comparison: subject has {minutes:,.0f} minutes · compared with {cohort}"
        rank_definition = "high-to-low comparison position against players meeting the displayed career-minute threshold; subject is outside that cohort"

    fig.text(.067, .095, f"{rank_context} · threshold: {threshold_mode} · Swing = career ON minus roster-tenure-corrected OFF",
             fontsize=8.4, va="top", alpha=.72)
    fig.text(.067, .071, footer_note, fontsize=8.1, va="top", alpha=.64)

    output.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg", "pdf"):
        fig.savefig(output.with_suffix("." + ext), dpi=320 if ext == "png" else None,
                    bbox_inches="tight", pad_inches=.12)
    plt.close(fig)

    audit = {
        "player_id": str(player_id), "player": name, "career_minutes": minutes, "mode": mode,
        "threshold": effective_threshold, "threshold_mode": threshold_mode,
        "subject_in_comparison_cohort": subject_in_cohort,
        "auto_threshold_tiers": list(AUTO_THRESHOLDS), "source_rows": int(len(player)),
        "teams": teams, "tiles": audit_tiles, "headshot": headshot_audit, "selection": selection,
        "rank_definition": rank_definition,
        "outputs": {e: str(output.with_suffix('.' + e)) for e in ("png", "svg", "pdf")},
    }
    output.with_name(output.name + "_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def self_test() -> None:
    rng = np.random.default_rng(104)
    metrics = [f"Metric{i:02d}" for i in range(28)]
    rows = []
    for p in range(40):
        minutes = 3800 if p == 0 else 12000 + p * 10
        for i, metric in enumerate(metrics):
            swing = rng.normal(1.5, 3) + (3 if p == 0 and i < 3 else 0)
            rows.append({"player_id": str(1000 + p), "player": f"Player {p}", "metric": metric,
                         "minutes_on": minutes, "on": 50 + swing / 2, "off_corrected": 50 - swing / 2,
                         "on_minus_off_corrected": swing})
    career = pd.DataFrame(rows)
    subject = select_player(career, "1000")
    assert adaptive_threshold(player_minutes(subject)) == 2500
    assert adaptive_threshold(12000) == 10000
    assert resolve_threshold(3800, None) == (2500.0, "auto", True)
    assert resolve_threshold(3800, 1000) == (1000.0, "manual", True)
    assert resolve_threshold(3800, 0) == (0.0, "manual", True)
    assert resolve_threshold(3800, 5000) == (5000.0, "manual", False)
    assert parse_threshold("all") == 0.0

    highlights = build_highlights(career, subject, 2500, DEFAULT_HIGHLIGHT_COUNT)
    top, bottom = build_full_profile(career, subject, 2500, DEFAULT_FULL_COUNT_EACH)
    assert 1 <= len(highlights) <= DEFAULT_HIGHLIGHT_COUNT
    assert len(top) == 10 and len(bottom) == 10
    assert highlights.subject_in_cohort.all()

    forced = build_highlights(career, subject, 10000, 4)
    assert len(forced) == 4 and not forced.subject_in_cohort.any() and all(forced["pool"] > 0)

    highlight_audit = render(career, "1000", Path("/tmp/career_impact_selftest"), allow_remote_headshot=False)
    assert highlight_audit["mode"] == "highlights" and highlight_audit["threshold"] == 2500
    assert highlight_audit["subject_in_comparison_cohort"] is True

    forced_audit = render(career, "1000", Path("/tmp/career_impact_forced_selftest"),
                          threshold=10000, allow_remote_headshot=False, highlight_count=4)
    assert forced_audit["threshold"] == 10000 and forced_audit["threshold_mode"] == "manual"
    assert forced_audit["subject_in_comparison_cohort"] is False
    assert forced_audit["rank_definition"].startswith("high-to-low comparison position")

    all_audit = render(career, "1000", Path("/tmp/career_impact_all_selftest"),
                       threshold=0, allow_remote_headshot=False, highlight_count=4)
    assert all_audit["threshold"] == 0 and all_audit["subject_in_comparison_cohort"] is True

    full_audit = render(career, "1000", Path("/tmp/career_impact_full_selftest"),
                        allow_remote_headshot=False, mode="full")
    assert full_audit["mode"] == "full" and len(full_audit["tiles"]) == 20
    for prefix in ("career_impact_selftest", "career_impact_forced_selftest",
                   "career_impact_all_selftest", "career_impact_full_selftest"):
        for ext in ("png", "svg", "pdf"):
            assert Path(f"/tmp/{prefix}.{ext}").exists()
    print("CAREER IMPACT RENDERER SELF-TEST PASSED")


def parse_threshold(raw: str) -> float | None:
    text = str(raw).strip().lower()
    if text == "auto":
        return None
    if text in {"all", "all-minutes", "all_minutes"}:
        return 0.0
    return float(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--career-input", type=Path)
    parser.add_argument("--team-input", type=Path)
    parser.add_argument("--player-id")
    parser.add_argument("--output-prefix", type=Path, default=Path("outputs/funakistats_career_impact"))
    parser.add_argument("--mode", choices=("highlights", "full"), default="highlights")
    parser.add_argument("--minutes-threshold", default="auto",
                        help="auto, all, or any explicit non-negative comparison threshold; manual thresholds may exceed the subject's own career minutes")
    parser.add_argument("--highlight-count", type=int, default=DEFAULT_HIGHLIGHT_COUNT)
    parser.add_argument("--full-count-each", type=int, default=DEFAULT_FULL_COUNT_EACH)
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
    threshold = parse_threshold(args.minutes_threshold)
    print(json.dumps(render(career, args.player_id, args.output_prefix, threshold=threshold, team_frame=team,
                            registry=args.headshot_registry, allow_remote_headshot=args.fetch_headshot,
                            mode=args.mode, highlight_count=args.highlight_count,
                            full_count_each=args.full_count_each), indent=2))


if __name__ == "__main__":
    main()
