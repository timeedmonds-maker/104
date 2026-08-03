# NBA career team TRB% dataset — Codespaces executor

## Start the build

[Open the dataset builder in GitHub Codespaces](https://codespaces.new/timeedmonds-maker/104?quickstart=1)

Choose the default branch (`main`) and the smallest available machine with at least 2 CPU cores, then create the Codespace.

The repository is configured to:

1. install Python dependencies;
2. compile the extraction and validation pipeline;
3. open this guide;
4. start `Build NBA team TRB dataset` in a dedicated terminal automatically.

The terminal should show:

```text
Codespaces executor started with 2 workers
Progress 0/780
START 2000-01-...
```

## What the executor does

- Processes two team-seasons concurrently.
- Splits API requests into smaller date windows whenever responses hit the 500-row cap or fail.
- Preserves every successful raw date window in `team_trb_all_players/codespace_work/` for resumable retries.
- Accepts a checkpoint only when lineup totals match the full-season Team and TeamOpponent control totals.
- Commits and pushes validated checkpoints and retry state after every two-team batch.
- Updates the persistent status comment in issue #9 after each batch.
- Builds the career leaderboard only after all 780 team-season checkpoints validate.

## Stopping and resuming

Closing the browser does not delete the Codespace. When the Codespace is stopped, the current process stops but files in the workspace remain. Reopen the same Codespace and the automatic task resumes from the committed checkpoints and preserved partial windows.

Do not rebuild or delete the Codespace while extraction is underway unless necessary. A normal stop and restart is safe.

## Manual restart

If the terminal task does not start automatically:

1. Open the Command Palette.
2. Run `Tasks: Run Task`.
3. Select `Build NBA team TRB dataset`.

The definitive progress record is the persistent Codespaces executor comment in [issue #9](https://github.com/timeedmonds-maker/104/issues/9).
