# TREB 23-key canonical reconciliation

- Authoritative V2 keys: **14524**
- Retained Stage2 keys: **14600**
- Intersection: **14501**
- Missing authoritative keys: **23**
- Stage2-only extra keys: **99**

## Classification counts

- NO_STAGE2_COUNTERPART_FOUND: **9**
- SEASON_MAPPING_MISMATCH: **6**
- TEAM_ID_OR_TENURE_MAPPING_MISMATCH: **3**
- ZERO_MINUTE_OR_TAIL_EXCLUSION: **5**

## Missing keys

| season | team_id | player_id | player | classification | counterpart evidence |
|---|---:|---:|---|---|---|
| 2002-03 | 1610612740 | 2213 | Kirk Haston | NO_STAGE2_COUNTERPART_FOUND |  |
| 2003-04 | 1610612738 | 344 | Dana Barros | NO_STAGE2_COUNTERPART_FOUND |  |
| 2004-05 | 1610612749 | 2590 | Mo Williams | SEASON_MAPPING_MISMATCH | same player-team seasons=2005-06;2006-07;2007-08 |
| 2010-11 | 1610612747 | 201234 | Trey Johnson | TEAM_ID_OR_TENURE_MAPPING_MISMATCH | same player-season teams=1610612761 |
| 2012-13 | 1610612752 | 2853 | Earl Barron | TEAM_ID_OR_TENURE_MAPPING_MISMATCH | same player-season teams=1610612764; same player-team seasons=2009-10 |
| 2013-14 | 1610612745 | 2694 | Josh Powell | NO_STAGE2_COUNTERPART_FOUND |  |
| 2015-16 | 1610612739 | 2563 | Dahntay Jones | NO_STAGE2_COUNTERPART_FOUND |  |
| 2016-17 | 1610612739 | 204002 | Edy Tavares | TEAM_ID_OR_TENURE_MAPPING_MISMATCH | same player-season teams=1610612737 |
| 2016-17 | 1610612739 | 2563 | Dahntay Jones | NO_STAGE2_COUNTERPART_FOUND |  |
| 2017-18 | 1610612739 | 2570 | Kendrick Perkins | SEASON_MAPPING_MISMATCH | same player-team seasons=2014-15 |
| 2017-18 | 1610612745 | 1628935 | Aaron Jackson | NO_STAGE2_COUNTERPART_FOUND |  |
| 2018-19 | 1610612764 | 203895 | Jordan McRae | SEASON_MAPPING_MISMATCH | same player-team seasons=2019-20 |
| 2019-20 | 1610612739 | 1629731 | Dean Wade | SEASON_MAPPING_MISMATCH | same player-team seasons=2020-21;2021-22;2022-23;2023-24;2024-25;2025-26 |
| 2021-22 | 1610612751 | 1630556 | Kessler Edwards | SEASON_MAPPING_MISMATCH | same player-team seasons=2022-23 |
| 2022-23 | 1610612751 | 1630564 | RaiQuan Gray | ZERO_MINUTE_OR_TAIL_EXCLUSION | excluded:zero_minute_tail_exclusions.json |
| 2022-23 | 1610612757 | 1628435 | Chance Comanche | NO_STAGE2_COUNTERPART_FOUND |  |
| 2022-23 | 1610612763 | 1631367 | Jacob Gilyard | ZERO_MINUTE_OR_TAIL_EXCLUSION | excluded:zero_minute_tail_exclusions.json; same player-team seasons=2023-24 |
| 2024-25 | 1610612738 | 1631120 | JD Davison | SEASON_MAPPING_MISMATCH | same player-team seasons=2022-23;2023-24 |
| 2024-25 | 1610612755 | 1630600 | Isaiah Mobley | NO_STAGE2_COUNTERPART_FOUND |  |
| 2025-26 | 1610612741 | 1631338 | Mouhamadou Gueye | ZERO_MINUTE_OR_TAIL_EXCLUSION | excluded:zero_minute_tail_exclusions.json |
| 2025-26 | 1610612747 | 1641733 | Nick Smith Jr. | NO_STAGE2_COUNTERPART_FOUND |  |
| 2025-26 | 1610612752 | 1641794 | Dillon Jones | ZERO_MINUTE_OR_TAIL_EXCLUSION | excluded:zero_minute_tail_exclusions.json |
| 2025-26 | 1610612762 | 1643060 | Hayden Gray | ZERO_MINUTE_OR_TAIL_EXCLUSION | excluded:zero_minute_tail_exclusions.json |
