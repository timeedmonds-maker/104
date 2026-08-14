# TREB targeted 23-key canonical repair

- Status: **FAIL**
- Repaired keys: **8/23**
- Projected canonical coverage: **14509/14524**
- Overlay metric rows: **712**
- Production gate relaxed: **NO**
- Full Stage2 rebuild: **NO**

The repair reuses the pinned Stage2 collector. It never substitutes another season, team, or player. Full-season queries are permitted only when the authoritative V2 target proves the tenure spans every team game; all partial tenures require an exact retained date interval. Zero-tail records are treated as segment evidence, not as permission to null a canonical player-team-season with non-zero authoritative minutes.
