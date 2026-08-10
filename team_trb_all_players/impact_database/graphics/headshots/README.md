# @funakistats publication headshots

The automated publication build is deterministic and does not create, recreate, or synthesize player likenesses.

Headshots are optional. If no explicitly approved asset is available, the chart renders a text label only.

For a headshot to be eligible for the automated X-ready build, its `registry.json` entry must explicitly record the publication approval and provenance, for example:

```json
{
  "203500": {
    "local_path": "licensed/203500.png",
    "source": "user-approved licensed source reference",
    "licensed_for_publication": true,
    "ai_generated": false
  }
}
```

Publication rules:

- use a real supplied/source-audited player photograph only;
- `licensed_for_publication` must be `true`;
- `ai_generated` must be explicitly `false`;
- record a source/provenance reference;
- prefer a local user-approved asset for publication;
- remote fetching is disabled by the automated visualization pack;
- missing or unapproved photos never block the data graphic: the renderer falls back to text-only labels;
- final X-ready PNGs are passed through `funakistats_publication_provenance.py`, which adds the agreed @funakistats credits, re-encodes the deterministic raster without inherited text/provenance metadata, and fails closed if suspicious AI/C2PA markers are found.
