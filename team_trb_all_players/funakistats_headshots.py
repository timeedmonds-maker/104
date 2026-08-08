#!/usr/bin/env python3
"""Auditable player-headshot asset resolution for @funakistats graphics.

The resolver prefers explicit local/manual overrides, then an explicit registry URL,
and finally the official NBA CDN transparent PNG convention keyed by NBA player ID.
Remote assets are cached by player ID and source URL. Rendering code never synthesizes
or recreates a player's likeness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image

BASE = Path(__file__).resolve().parent
DEFAULT_ROOT = BASE / "impact_database" / "graphics" / "headshots"
DEFAULT_REGISTRY = DEFAULT_ROOT / "registry.json"
NBA_CDN = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"
HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/131 Safari/537.36",
}


def clean_id(value: Any) -> str:
    value = str(value or "").strip()
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    if not value.isdigit() or value == "0":
        raise ValueError(f"invalid NBA player id: {value!r}")
    return value


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("headshot registry must be a JSON object keyed by player ID")
    return {clean_id(k): dict(v) for k, v in data.items() if isinstance(v, dict)}


def source_for(player_id: str, registry: dict[str, dict[str, Any]], root: Path) -> dict[str, Any]:
    pid = clean_id(player_id)
    entry = dict(registry.get(pid) or {})
    local = entry.get("local_path")
    if local:
        path = Path(local)
        if not path.is_absolute():
            path = root / path
        if path.exists():
            return {"kind": "local_override", "path": str(path), "source": entry.get("source") or "manual_override"}
    if entry.get("url"):
        return {"kind": "registry_url", "url": str(entry["url"]), "source": entry.get("source") or "registry_url"}
    return {"kind": "nba_cdn", "url": NBA_CDN.format(player_id=pid), "source": "official_nba_cdn_latest"}


def validate_image_bytes(content: bytes) -> Image.Image:
    if not content:
        raise ValueError("empty image response")
    image = Image.open(BytesIO(content))
    image.load()
    if image.width < 100 or image.height < 100:
        raise ValueError(f"headshot unexpectedly small: {image.size}")
    return image.convert("RGBA")


def trim_and_normalize(image: Image.Image, size: int = 512, padding: float = 0.04) -> Image.Image:
    """Trim transparent margins and place the subject consistently on a square RGBA canvas."""
    image = image.convert("RGBA")
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        image = image.crop(bbox)
    pad = max(1, int(size * padding))
    target = max(1, size - 2 * pad)
    scale = min(target / image.width, target / image.height)
    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(new_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - image.width) // 2
    y = size - pad - image.height
    canvas.alpha_composite(image, (x, y))
    return canvas


def resolve_headshot(player_id: Any, registry_path: Path = DEFAULT_REGISTRY, cache_root: Path = DEFAULT_ROOT,
                     allow_remote: bool = True, timeout: float = 20.0) -> tuple[Path | None, dict[str, Any]]:
    pid = clean_id(player_id)
    registry = load_registry(registry_path)
    spec = source_for(pid, registry, cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    normalized = cache_root / "normalized" / f"{pid}.png"
    audit_path = cache_root / "audit" / f"{pid}.json"
    if normalized.exists() and audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if audit.get("ok") is True:
                return normalized, audit
        except Exception:
            pass

    audit: dict[str, Any] = {"player_id": pid, "ok": False, **spec}
    try:
        if spec["kind"] == "local_override":
            raw = Path(spec["path"]).read_bytes()
        else:
            if not allow_remote:
                audit["reason"] = "remote_fetch_disabled"
                return None, audit
            response = requests.get(spec["url"], headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            raw = response.content
            audit["http_status"] = response.status_code
        image = validate_image_bytes(raw)
        normalized.parent.mkdir(parents=True, exist_ok=True)
        trim_and_normalize(image).save(normalized, "PNG", optimize=True)
        audit.update({
            "ok": True,
            "output": str(normalized),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "normalized_size": [512, 512],
            "policy": "real supplied/retrieved image only; no generated or reconstructed player likeness",
        })
    except Exception as exc:
        audit["reason"] = repr(exc)
        normalized = None
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return normalized, audit


def self_test() -> None:
    root = Path("/tmp/funakistats_headshot_test")
    root.mkdir(parents=True, exist_ok=True)
    src = root / "manual.png"
    image = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    for x in range(40, 160):
        for y in range(30, 290):
            image.putpixel((x, y), (200, 100, 50, 255))
    image.save(src)
    reg = root / "registry.json"
    reg.write_text(json.dumps({"123": {"local_path": "manual.png", "source": "self_test"}}), encoding="utf-8")
    out, audit = resolve_headshot("123", reg, root, allow_remote=False)
    assert out and out.exists() and audit["ok"] is True
    normalized = Image.open(out)
    assert normalized.size == (512, 512) and normalized.mode == "RGBA"
    assert clean_id("123.0") == "123"
    print("HEADSHOT RESOLVER SELF-TEST PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--player-id")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--no-remote", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.player_id:
        raise SystemExit("--player-id is required")
    path, audit = resolve_headshot(args.player_id, args.registry, args.cache_root, not args.no_remote)
    print(json.dumps({"path": str(path) if path else None, "audit": audit}, indent=2))


if __name__ == "__main__":
    main()
