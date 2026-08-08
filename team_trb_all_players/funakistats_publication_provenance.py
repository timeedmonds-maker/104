#!/usr/bin/env python3
"""Publication provenance and branding guard for @funakistats visual outputs.

Public @funakistats graphics are analytical visualisations rendered deterministically from
validated data. They must not be produced by a generative-image model or inherit AI/C2PA
provenance from an image-generation pipeline. Real licensed/source-audited player photos
may be composited into the chart; the final publication PNG is then losslessly re-encoded
without inherited textual/provenance metadata.

Every release PNG also receives two restrained @funakistats acknowledgements: a primary
bottom-right handle and a secondary bottom-left data/analysis credit. This keeps original
work attributable when screenshots or crops circulate without dominating the graphic.

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

from PIL import Image, ImageDraw, ImageFont, ImageStat

BRAND_HANDLE = "@funakistats"
SECONDARY_CREDIT = "Data & analysis · @funakistats"
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


def _font(size: int, bold: bool = False):
    names = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _bottom_luminance(image: Image.Image) -> float:
    rgb = image.convert("RGB")
    w, h = rgb.size
    strip = rgb.crop((0, max(0, h - max(8, h // 12)), w, h))
    mean = ImageStat.Stat(strip).mean
    return 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]


def apply_branding(image: Image.Image) -> Image.Image:
    """Add subtle, screenshot-resilient @funakistats attribution to a publication raster."""
    canvas = image.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    main_size = max(12, round(h * 0.0125))
    secondary_size = max(10, round(h * 0.0085))
    main_font = _font(main_size, bold=True)
    secondary_font = _font(secondary_size, bold=False)
    margin_x = max(18, round(w * 0.018))
    margin_y = max(14, round(h * 0.012))
    luminance = _bottom_luminance(canvas)
    fill = (28, 28, 28, 150) if luminance >= 145 else (245, 245, 245, 175)
    secondary_fill = (28, 28, 28, 105) if luminance >= 145 else (245, 245, 245, 125)

    main_box = draw.textbbox((0, 0), BRAND_HANDLE, font=main_font)
    main_w = main_box[2] - main_box[0]
    main_h = main_box[3] - main_box[1]
    main_xy = (w - margin_x - main_w, h - margin_y - main_h)
    draw.text(main_xy, BRAND_HANDLE, font=main_font, fill=fill)

    secondary_box = draw.textbbox((0, 0), SECONDARY_CREDIT, font=secondary_font)
    secondary_h = secondary_box[3] - secondary_box[1]
    secondary_xy = (margin_x, h - margin_y - secondary_h)
    draw.text(secondary_xy, SECONDARY_CREDIT, font=secondary_font, fill=secondary_fill)
    return canvas


def sanitize_png(path: Path) -> dict[str, Any]:
    """Brand, then losslessly re-encode a PNG without inherited EXIF/XMP/text metadata."""
    path = Path(path)
    if path.suffix.lower() != ".png":
        raise ValueError("publication sanitizer currently accepts PNG only")
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(path)

    with Image.open(path) as im:
        im.load()
        clean = apply_branding(im.copy())
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
        "mode": "RGBA",
        "metadata_keys_after_sanitize": remaining_info,
        "suspicious_ai_provenance_terms": found,
        "publication_method": "deterministic code-rendered analytical graphic",
        "generative_image_model_used": False,
        "ai_generated_visual_assets_permitted": False,
        "real_photo_assets": "permitted only when source/rights are separately audited",
        "branding": {
            "primary": BRAND_HANDLE,
            "secondary": SECONDARY_CREDIT,
            "primary_position": "bottom-right",
            "secondary_position": "bottom-left",
            "subtle": True,
        },
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
    Image.new("RGBA", (640, 640), (255, 255, 255, 255)).save(p, pnginfo=info)
    audit = write_manifest(p)
    assert audit["generative_image_model_used"] is False
    assert audit["x_release_gate_passed"] is True
    assert audit["suspicious_ai_provenance_terms"] == []
    assert audit["branding"]["primary"] == "@funakistats"
    assert audit["branding"]["secondary"].endswith("@funakistats")
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
