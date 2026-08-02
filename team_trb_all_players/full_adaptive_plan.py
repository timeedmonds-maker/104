from __future__ import annotations

import json
import os

TEAM_IDS = [
    1610612737, 1610612738, 1610612739, 1610612740, 1610612741,
    1610612742, 1610612743, 1610612744, 1610612745, 1610612746,
    1610612747, 1610612748, 1610612749, 1610612750, 1610612751,
    1610612752, 1610612753, 1610612754, 1610612755, 1610612756,
    1610612757, 1610612758, 1610612759, 1610612760, 1610612761,
    1610612762, 1610612763, 1610612764, 1610612765, 1610612766,
]


def season_dates(start_year: int) -> tuple[str, str]:
    if start_year == 2019:
        return "2019-10-01", "2020-08-31"
    if start_year == 2020:
        return "2020-12-01", "2021-05-31"
    return f"{start_year}-10-01", f"{start_year + 1}-04-30"


def main() -> None:
    include = []
    for start_year in range(2000, 2026):
        season = f"{start_year}-{str(start_year + 1)[-2:]}"
        from_date, to_date = season_dates(start_year)
        for group_index in range(6):
            team_ids = TEAM_IDS[group_index * 5:(group_index + 1) * 5]
            include.append({
                "season": season,
                "from_date": from_date,
                "to_date": to_date,
                "group": group_index + 1,
                "team_ids": ",".join(str(team_id) for team_id in team_ids),
            })
    matrix = json.dumps({"include": include}, separators=(",", ":"))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"matrix={matrix}\n")
    else:
        print(matrix)


if __name__ == "__main__":
    main()
