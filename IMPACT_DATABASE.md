# NBA historical player-impact database

This collector covers the 2000-01 through 2025-26 NBA regular seasons.

## Database layers

1. **Player-team-season totals**
   - Every field returned by the PBP Stats player totals endpoint.
   - Stored without selecting only a predefined set of statistics.

2. **Complete team on/off metrics**
   - Every metric and every player row returned by the team on/off endpoint.
   - Includes `On`, `Off`, `On-Off`, `MinutesOn`, `MinutesOff`, and any additional source fields.

3. **Complete teammate interaction metrics**
   - For each focal player-team-season, every metric returned for each teammate with and without the focal player.
   - This supports two-player and teammate-impact analysis without re-downloading the historical API data.

4. **Derived rebound validation**
   - Exact team offensive and defensive rebounds while each player is on court.
   - Opponent rebound totals reconstructed from integer counts and displayed rebound rates.
   - Ambiguous low-volume segments are preserved as candidate ranges rather than silently rounded.

## Storage design

Checkpoint files are gzip-compressed JSON. They retain:

- the full player totals rows;
- the complete metric-to-row maps returned by both on/off endpoints;
- response metadata, headers, and request diagnostics;
- player and team identifiers;
- retry state and validation results.

Final outputs are gzip-compressed CSV files, partitioned by season:

- `player_team_totals.csv.gz`
- `team_on_off_metrics.csv.gz`
- `player_pair_metrics.csv.gz`
- `team_rebound_derived.csv.gz`

Career rebound outputs and the complete manifest are written under:

`team_trb_all_players/impact_database/outputs/`

## Run

From the repository root:

```bash
bash team_trb_all_players/run_impact_database_build.sh
```

The runner first performs a Houston 2025-26 preflight. It must exactly reproduce Steven Adams' 43,821.9 seconds, 905 team rebounds, and 620 opponent rebounds, and confirm that broad team and teammate metric responses are present. Production starts only after that validation passes.

The build is resumable. Completed team-seasons and completed focal-player responses are skipped on subsequent runs. Progress is committed and pushed throughout the build.
