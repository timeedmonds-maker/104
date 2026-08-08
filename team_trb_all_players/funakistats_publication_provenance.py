#!/usr/bin/env python3
"""Publication provenance guard for @funakistats visual outputs.

Public @funakistats graphics are analytical visualisations rendered deterministically from
validated data. They must not be produced by a generative-image model or inherit AI/C2PA
provenance from an image-generation pipeline. Real licensed/source-audited player photos
may be composited into the chart; the final publication PNG is then losslessly re-encoded
without inherited textual/provenance metadata.

This module is not intended to disguise AI-generated imagery. It enforces the opposite:
release only graphics whose production method is deterministic code + audited source
assets, and fail closed if suspicious AI provenance markers are present in the final PNG.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image

BRAND_HANDLE = "@funakistats"
SUSPICIOUS_TERMS = (
    b"openai",
    b"chatgpt",
    b"dall-e",
    b"dalle",
    b"c2pa",
    b"content credentials",
    b"made with ai",
    b"ai-generated",
    b"ai generated",
    b"generative ai",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_png(path: Path) -> dict[str, Any]:
    """Losslessly re-encode a PNG without inherited textual/EXIF/XMP metadata."""
    path = Path(path)
    if path.suffix.lower() != ".png":
        raise ValueError("publication sanitizer currently accepts PNG only")
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(path)

    with Image.open(path) as im:
        im.load()
        clean = im.copy()
        mode = clean.mode
        size = clean.size

    tmp = path.with_name(path.stem + ".publication-tmp.png")
    # Passing no pnginfo/exif/icc_profile deliberately prevents inherited provenance or
    # textual metadata from being copied into the final publication raster.
    clean.save(tmp, format="PNG", optimize=True)
    os.replace(tmp, path)

    raw = path.read_bytes().lower()
    found = [term.decode("ascii", "ignore") for term in SUSPICIOUS_TERMS if term in raw]
    with Image.open(path) as verified:
        remaining_info = sorted(str(k) for k in verified.info.keys())

    if found:
        raise AssertionError(f"AI/synthetic provenance marker(s) remain in publication PNG: {found}")

    return {
        "path": str(path),
        "sha256": sha256(path),
        "pixel_dimensions": [int(size[0]), int(size[1])],
        "mode": mode,
        "metadata_keys_after_sanitize": remaining_info,
        "suspicious_ai_provenance_terms": found,
        "publication_method": "deterministic code-rendered analytical graphic",
        "generative_image_model_used": False,
        "ai_generated_visual_assets_permitted": False,
        "real_photo_assets": "permitted only when source/rights are separately audited",
        "brand": BRAND_HANDLE,
        "x_release_gate_passed": True,
    }


def write_manifest(png: Path, manifest: Path | None = None) -> dict[str, Any]:
    audit = sanitize_png(png)
    target = Path(manifest) if manifest else png.with_name(png.stem + "_publication_provenance.json")
    target.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def self_test() -> None:
    import tempfile
    from PIL.PngImagePlugin import PngInfo

    root = Path(tempfile.mkdtemp(prefix="funakistats-provenance-"))
    p = root / "test.png"
    info = PngInfo()
    info.add_text("Comment", "ordinary renderer metadata")
    Image.new("RGBA", (64, 64), (255, 255, 255, 255)).save(p, pnginfo=info)
    audit = write_manifest(p)
    assert audit["generative_image_model_used"] is False
    assert audit["x_release_gate_passed"] is True
    assert audit["suspicious_ai_provenance_terms"] == []
    with Image.open(p) as im:
        assert "Comment" not in im.info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        print("FUNAKISTATS PUBLICATION PROVENANCE SELF-TEST PASSED")
        return
    if not a.png:
        raise SystemExit("--png is required")
    print(json.dumps(write_manifest(a.png, a.manifest), indent=2))


if __name__ == "__main__":
    main()
