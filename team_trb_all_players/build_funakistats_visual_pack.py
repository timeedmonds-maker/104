from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnnotationBbox, OffsetImage

from funakistats_headshots import DEFAULT_REGISTRY, load_registry, resolve_headshot

BASE = Path(__file__).resolve().parent
SRC = BASE / "impact_database" / "corrected_off"
FINAL = SRC / "final_export"
OUT = SRC / "visual_pack"

BG = "#0b0d10"
TEXT = "#f4f1e8"
MUTED = "#aeb3bb"
POINT_ALL = "#d5d8dc"
POINT_QUAL = "#979da6"
ACCENT = "#c8a96a"
LEADER = "#c4c7cc"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_headshots() -> dict[str, dict[str, Any]]:
    return load_registry(DEFAULT_REGISTRY)


def validated_headshot(player_id: str, item: dict[str, Any]) -> Path | None:
    # Publication graphics use only explicitly licensed registry entries. Remote fallback
    # is disabled here; user-approved/local source assets or already-audited cache only.
    if item.get("licensed_for_publication") is not True or item.get("ai_generated") is not False:
        return None
    if not str(item.get("source") or item.get("source_reference") or "").strip():
        return None
    try:
        path, audit = resolve_headshot(player_id, DEFAULT_REGISTRY, allow_remote=False)
    except Exception:
        return None
    return path if path and audit.get("ok") is True else None


def robust_z(values: pd.Series) -> pd.Series:
    v = pd.to_numeric(values, errors="coerce")
    median = float(v.median())
    mad = float((v - median).abs().median())
    if not math.isfinite(mad) or mad <= 1e-12:
        std = float(v.std(ddof=0))
        return (v - median) / std if std > 0 else pd.Series(np.zeros(len(v)), index=v.index)
    return (v - median) / (1.4826 * mad)


def select_labels(frame: pd.DataFrame, mode: str, label_count: int) -> pd.DataFrame:
    q = frame.copy()
    q["swing_z"] = robust_z(q["treb_swing_mid"])
    if mode == "best":
        selected = q[q.swing_z >= 1.5].sort_values(["treb_swing_mid", "minutes_on"], ascending=[False, False]).head(label_count)
        minimum = min(12, label_count, len(q))
        if len(selected) < minimum:
            selected = q.sort_values(["treb_swing_mid", "minutes_on"], ascending=[False, False]).head(minimum)
        return selected
    half = max(1, label_count // 2)
    high = q.sort_values(["treb_swing_mid", "minutes_on"], ascending=[False, False]).head(half)
    low = q.sort_values(["treb_swing_mid", "minutes_on"], ascending=[True, False]).head(label_count - len(high))
    return pd.concat([high, low]).drop_duplicates("player_id").head(label_count)


def spread_positions(points: pd.DataFrame, ymin: float, ymax: float) -> dict[str, float]:
    if points.empty:
        return {}
    gap = max((ymax - ymin) * 0.035, 0.08)
    ordered = points.sort_values("treb_pct_on_mid").copy()
    placed = ordered.treb_pct_on_mid.to_numpy(dtype=float).copy()
    for i in range(1, len(placed)):
        placed[i] = max(placed[i], placed[i - 1] + gap)
    overflow = placed[-1] - (ymax - gap * 0.25)
    if overflow > 0:
        placed -= overflow
    for i in range(len(placed) - 2, -1, -1):
        placed[i] = min(placed[i], placed[i + 1] - gap)
    under = (ymin + gap * 0.25) - placed[0]
    if under > 0:
        placed += under
    return {str(pid): float(y) for pid, y in zip(ordered.player_id.astype(str), placed)}


def add_headshot(ax: plt.Axes, x: float, y: float, path: Path, side: str, xspan: float) -> None:
    image = Image.open(path).convert("RGBA")
    image.thumbnail((220, 220), Image.Resampling.LANCZOS)
    artist = OffsetImage(np.asarray(image), zoom=0.24)
    hx = x + 0.026 * xspan if side == "left" else x - 0.026 * xspan
    ax.add_artist(AnnotationBbox(artist, (hx, y), frameon=False, box_alignment=(0.5, 0.5), zorder=8))


def chart(frame: pd.DataFrame, threshold: float, mode: str, label_count: int, aspect: str, headshots: dict[str, dict[str, Any]], stem: str) -> list[Path]:
    portrait = aspect == "portrait"
    fig, ax = plt.subplots(figsize=(12, 15) if portrait else (12, 12), dpi=400)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    plot = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["treb_pct_off_mid", "treb_pct_on_mid", "minutes_on"]).copy()
    qual = plot[plot.minutes_on >= threshold].copy()
    if qual.empty:
        raise RuntimeError(f"no career players meet minutes threshold {threshold}")
    all_x, all_y = plot.treb_pct_off_mid.to_numpy(float), plot.treb_pct_on_mid.to_numpy(float)
    qx, qy = qual.treb_pct_off_mid.to_numpy(float), qual.treb_pct_on_mid.to_numpy(float)
    low = float(np.nanpercentile(np.concatenate([all_x, all_y]), 0.5))
    high = float(np.nanpercentile(np.concatenate([all_x, all_y]), 99.5))
    span = max(high - low, 1.0)
    xmin, xmax = low - span * 0.09, high + span * 0.09
    ymin, ymax = xmin, xmax
    ax.scatter(all_x, all_y, s=10, c=POINT_ALL, alpha=0.14, linewidths=0, zorder=1)
    ax.scatter(qx, qy, s=16, c=POINT_QUAL, alpha=0.62, linewidths=0, zorder=2)
    ax.plot([xmin, xmax], [xmin, xmax], color=ACCENT, lw=1.2, alpha=0.55, zorder=0)

    selected = select_labels(qual, mode, label_count)
    center = (xmin + xmax) / 2
    selected = selected.assign(side=np.where(selected.treb_pct_off_mid <= center, "left", "right"))
    left_positions = spread_positions(selected[selected.side == "left"], ymin, ymax)
    right_positions = spread_positions(selected[selected.side == "right"], ymin, ymax)
    xspan = xmax - xmin
    tx_left, tx_right, elbow_pad = xmin + xspan * 0.025, xmax - xspan * 0.025, xspan * 0.018
    used_assets: list[Path] = []
    for _, row in selected.iterrows():
        pid = str(row.player_id)
        x, y, side = float(row.treb_pct_off_mid), float(row.treb_pct_on_mid), str(row.side)
        ly = left_positions.get(pid, y) if side == "left" else right_positions.get(pid, y)
        line_end = tx_left + elbow_pad if side == "left" else tx_right - elbow_pad
        ax.plot([x, x, line_end], [y, ly, ly], color=LEADER, lw=0.85, alpha=0.72, zorder=4)
        label_x, align = (tx_left, "left") if side == "left" else (tx_right, "right")
        item = headshots.get(pid)
        path = validated_headshot(pid, item) if item else None
        if path:
            used_assets.append(path)
            add_headshot(ax, label_x, ly, path, side, xspan)
            label_x += xspan * 0.050 if side == "left" else -xspan * 0.050
        ax.text(label_x, ly, f"{row.player}  {float(row.treb_swing_mid):+.2f}", ha=align, va="center", color=TEXT, fontsize=8.7 if portrait else 8.2, fontweight="medium", zorder=9)
        ax.scatter([x], [y], s=30, c=ACCENT, alpha=0.95, linewidths=0, zorder=6)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=8, length=0, pad=6)
    ax.set_xlabel("Team total rebound % with player OFF court", color=MUTED, fontsize=10, labelpad=12)
    ax.set_ylabel("Team total rebound % with player ON court", color=MUTED, fontsize=10, labelpad=12)
    title_mode = "Career impact highlights" if mode == "best" else "Career impact — full profile"
    fig.text(0.075, 0.955, "WHO CHANGED THEIR TEAM'S REBOUNDING MOST?", color=TEXT, fontsize=20 if portrait else 18, fontweight="bold", ha="left", va="top")
    fig.text(0.075, 0.925, f"{title_mode} • 2000-01 to 2025-26 • {int(threshold):,}+ career minutes", color=MUTED, fontsize=10.5, ha="left", va="top")
    fig.text(0.075, 0.035, "Source: PBP Stats • OFF court restricted to exact roster tenure • diagonal = no on/off change", color=MUTED, fontsize=8.2, ha="left", va="bottom")
    plt.subplots_adjust(left=0.12, right=0.94, top=0.88, bottom=0.10)

    master_png, final_png = OUT / f"{stem}_master.png", OUT / f"{stem}.png"
    svg, pdf = OUT / f"{stem}.svg", OUT / f"{stem}.pdf"
    meta = {"Creator": "@funakistats deterministic matplotlib renderer"}
    fig.savefig(master_png, dpi=400, facecolor=BG, metadata=meta)
    fig.savefig(svg, facecolor=BG, metadata=meta)
    fig.savefig(pdf, facecolor=BG, metadata=meta)
    plt.close(fig)
    target = (2400, 3000) if portrait else (2400, 2400)
    image = Image.open(master_png).convert("RGB").resize(target, Image.Resampling.LANCZOS)
    image.save(final_png, format="PNG", optimize=True)
    master_png.unlink()
    return [final_png, svg, pdf, *used_assets]


def build_rankings(career_treb: pd.DataFrame, career_metrics: pd.DataFrame, threshold: float) -> list[Path]:
    rankings = OUT / "rankings"
    rankings.mkdir(parents=True, exist_ok=True)
    qualifying = career_treb[career_treb.minutes_on >= threshold].copy()
    paths = [rankings / "treb_top20_on_court.csv", rankings / "treb_top20_corrected_swing.csv"]
    qualifying.sort_values(["treb_pct_on_mid", "minutes_on"], ascending=[False, False]).head(20).to_csv(paths[0], index=False)
    qualifying.sort_values(["treb_swing_mid", "minutes_on"], ascending=[False, False]).head(20).to_csv(paths[1], index=False)
    generic = career_metrics[pd.to_numeric(career_metrics.minutes_on, errors="coerce") >= threshold].dropna(subset=["on_minus_off_corrected"]).copy()
    generic = generic.sort_values(["metric", "on_minus_off_corrected", "minutes_on"], ascending=[True, False, False])
    generic["rank"] = generic.groupby("metric").cumcount() + 1
    path = rankings / "all_metrics_top20_corrected_swing.csv.gz"
    generic[generic["rank"] <= 20].to_csv(path, index=False, compression="gzip")
    paths.append(path)
    return paths


def build(minutes_threshold: float, label_count: int) -> dict[str, Any]:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    career_treb = pd.read_parquet(FINAL / "career_total_rebound_pct.parquet")
    career_metrics = pd.read_parquet(FINAL / "career_corrected_on_off.parquet")
    player_season_treb = pd.read_parquet(FINAL / "player_season_total_rebound_pct.parquet")
    headshots = load_headshots()
    data_dir = OUT / "data"
    data_dir.mkdir(parents=True)
    career_path, player_season_path = data_dir / "career_total_rebound_pct.csv.gz", data_dir / "player_season_total_rebound_pct.csv.gz"
    career_treb.to_csv(career_path, index=False, compression="gzip")
    player_season_treb.to_csv(player_season_path, index=False, compression="gzip")
    files: list[Path] = [career_path, player_season_path, *build_rankings(career_treb, career_metrics, minutes_threshold)]
    asset_paths: set[Path] = set()
    for mode in ("best", "full"):
        for aspect in ("square", "portrait"):
            stem = f"treb_career_impact_{'best_only' if mode == 'best' else 'full_profile'}_{aspect}"
            for path in chart(career_treb, minutes_threshold, mode, label_count, aspect, headshots, stem):
                files.append(path) if path.is_relative_to(OUT) else asset_paths.add(path)
    readme = OUT / "README.md"
    readme.write_text(
        "# @funakistats TREB visualization pack\n\n"
        "Automated deterministic publication pack generated only after exact-tenure database QA. Default public view is Best Only / Highlights; Full Profile is retained separately. Scatterplots use a dark no-grid/no-legend treatment, very light grey background players, darker qualifying players, statistically selected outlier labels, and vertical-then-horizontal 90-degree leader lines. The 10,000-minute default is configurable. Licensed real headshots are optional and only used from the existing funakistats headshot registry when explicitly marked licensed_for_publication=true and ai_generated=false; remote fetching is disabled for the automated publication build. Final PNGs use deterministic Matplotlib/Pillow rendering; SVG/PDF masters are retained and the PNGs are passed through funakistats_publication_provenance.py before release.\n",
        encoding="utf-8",
    )
    files.append(readme)
    final_manifest = json.loads((FINAL / "manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "pack": "@funakistats TREB automated visualization pack",
        "renderer": "deterministic matplotlib + pillow",
        "ai_generated_pixels": False,
        "generative_tools_used_for_pixels": False,
        "default_public_mode": "best_only_highlights",
        "full_profile_available": True,
        "minutes_threshold": minutes_threshold,
        "label_count": label_count,
        "outlier_selection": "robust swing z-score; fallback to highest positive swings; full profile includes both tails",
        "point_treatment": "all players very light grey; qualifying players darker",
        "label_treatment": "collision-spread labels with vertical then horizontal 90-degree leaders; no diagonal arms",
        "branding": "publication sanitizer adds primary @funakistats and secondary Data & analysis @funakistats credits; chart carries source/method footer",
        "dataset_manifest_sha256": sha256(FINAL / "manifest.json"),
        "dataset_quality": final_manifest.get("quality"),
        "headshot_policy": "funakistats_headshots registry; local/audited real assets only, licensed_for_publication=true, ai_generated=false; remote fetch disabled for automated publication",
        "headshots_used": [str(p.relative_to(BASE)) for p in sorted(asset_paths)],
        "files": [],
    }
    for path in sorted(set(files)):
        if path.exists() and path.is_file():
            manifest["files"].append({"name": str(path.relative_to(OUT)), "bytes": path.stat().st_size, "sha256": sha256(path)})
    (OUT / "visual_pack_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def self_test() -> None:
    sample = pd.DataFrame({"treb_swing_mid": [-2, -1, 0, 1, 2, 5], "minutes_on": [10000] * 6, "player_id": list(map(str, range(6))), "player": list("ABCDEF")})
    assert "5" in set(select_labels(sample, "best", 3).player_id)
    positions = spread_positions(pd.DataFrame({"player_id": ["1", "2"], "treb_pct_on_mid": [50.0, 50.01]}), 45, 55)
    assert positions["2"] > positions["1"]
    print("build_funakistats_visual_pack self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes-threshold", type=float, default=10_000.0)
    p.add_argument("--label-count", type=int, default=20)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    self_test() if args.self_test else print(json.dumps(build(args.minutes_threshold, args.label_count), indent=2))


if __name__ == "__main__":
    main()
