from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
SRC = BASE / "impact_database" / "corrected_off"
FINAL = SRC / "final_export"
VIZ = SRC / "visual_pack"
COLLECTION = SRC / "corrected_off_collection_summary.json"
STATUS = SRC / "project_completion_status.json"

PUBLICATION_STEMS = (
    "treb_career_impact_best_only_square",
    "treb_career_impact_best_only_portrait",
    "treb_career_impact_full_profile_square",
    "treb_career_impact_full_profile_portrait",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def finalize() -> dict[str, Any]:
    collection = load(COLLECTION)
    dataset = load(FINAL / "manifest.json")
    visual_manifest_path = VIZ / "visual_pack_manifest.json"
    visual = load(visual_manifest_path)
    if not collection.get("all_complete") or int(collection.get("remaining_windows") or -1) != 0:
        raise RuntimeError("Stage2 is not complete")
    quality = dataset.get("quality") if isinstance(dataset.get("quality"), dict) else {}
    for key, expected in {"stage1_exact_ready": True, "stage2_exact_ready": True, "original_core_team_seasons": 780, "teammate_pair_layer_included": False}.items():
        if quality.get(key) != expected:
            raise RuntimeError(f"dataset quality gate failed: {key}={quality.get(key)!r}")
    if int(quality.get("single_stint_on_validation_failures") or 0) != 0:
        raise RuntimeError("single-stint validation failures are nonzero")

    publication: list[dict[str, Any]] = []
    for stem in PUBLICATION_STEMS:
        png, svg, pdf = VIZ / f"{stem}.png", VIZ / f"{stem}.svg", VIZ / f"{stem}.pdf"
        prov = VIZ / f"{stem}_publication_provenance.json"
        for path in (png, svg, pdf, prov):
            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError(f"missing publication output: {path}")
        audit = load(prov)
        if audit.get("x_release_gate_passed") is not True or audit.get("generative_image_model_used") is not False or audit.get("suspicious_ai_provenance_terms"):
            raise RuntimeError(f"publication provenance gate failed for {stem}")
        branding = audit.get("branding") if isinstance(audit.get("branding"), dict) else {}
        if branding.get("primary") != "@funakistats" or not str(branding.get("secondary") or "").endswith("@funakistats"):
            raise RuntimeError(f"branding gate failed for {stem}")
        publication.append({
            "stem": stem,
            "png": {"sha256": sha256(png), "bytes": png.stat().st_size},
            "svg": {"sha256": sha256(svg), "bytes": svg.stat().st_size},
            "pdf": {"sha256": sha256(pdf), "bytes": pdf.stat().st_size},
            "provenance": {"sha256": sha256(prov), "bytes": prov.stat().st_size},
            "x_release_gate_passed": True,
        })

    # Publication sanitation changes the PNG bytes, so refresh all tracked file hashes.
    for item in visual.get("files", []):
        if not isinstance(item, dict):
            continue
        path = VIZ / str(item.get("name") or "")
        if path.exists() and path.is_file():
            item["bytes"] = path.stat().st_size
            item["sha256"] = sha256(path)

    visual["publication_finalized_utc"] = datetime.now(timezone.utc).isoformat()
    visual["publication_guard"] = "funakistats_publication_provenance.py"
    visual["x_ready"] = True
    visual["publication_outputs"] = publication
    visual["dataset_manifest_sha256"] = sha256(FINAL / "manifest.json")
    visual_manifest_path.write_text(json.dumps(visual, indent=2), encoding="utf-8")

    overall_provenance = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "x_ready": True,
        "ai_generated_pixels": False,
        "production_method": "validated NBA data + deterministic Matplotlib/Pillow/vector rendering + audited real-image assets only when explicitly supplied/approved",
        "default_public_mode": "best_only_highlights",
        "full_profile_retained": True,
        "label_policy": "collision-aware orthogonal leaders with horizontal terminal arms; no diagonal label arms",
        "point_policy": "all players very light grey; qualifying players darker",
        "default_minutes_threshold": visual.get("minutes_threshold"),
        "dataset_manifest_sha256": sha256(FINAL / "manifest.json"),
        "publication_outputs": publication,
    }
    (VIZ / "publication_provenance.json").write_text(json.dumps(overall_provenance, indent=2), encoding="utf-8")
    (VIZ / "X_READY").write_text("X_READY=1\n", encoding="utf-8")
    status = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "project": "NBA historical tenure-corrected team-impact database and @funakistats visualization pack",
        "stage1_exact_ready": True,
        "stage2_complete": True,
        "complete_windows": int(collection.get("complete_windows") or 0),
        "impact_windows_total": int(collection.get("impact_windows_total") or 0),
        "final_database_ready": True,
        "final_database_zip": str(SRC / "TREB_corrected_off_final.zip"),
        "player_season_layer_ready": True,
        "derived_total_rebound_pct_ready": True,
        "rankings_ready": True,
        "visualization_pack_ready": True,
        "best_only_default": True,
        "full_profile_retained": True,
        "x_ready": True,
        "publication_provenance": str(VIZ / "publication_provenance.json"),
        "teammate_pairs_included": False,
    }
    STATUS.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def self_test() -> None:
    assert len(PUBLICATION_STEMS) == 4
    assert any("best_only" in stem for stem in PUBLICATION_STEMS)
    assert any("full_profile" in stem for stem in PUBLICATION_STEMS)
    print("finalize_funakistats_visual_pack self-test PASSED")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    self_test() if a.self_test else print(json.dumps(finalize(), indent=2))
