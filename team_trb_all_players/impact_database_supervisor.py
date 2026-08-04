from __future__ import annotations

import subprocess

import impact_database_build_fixed as fixed


base = fixed.base


def resilient_git_commit_progress(label: str) -> bool:
    """Checkpoint progress without allowing a transient Git failure to stop the build."""
    committed = False
    try:
        subprocess.run(
            ["git", "add", "-A", "--", str(base.DB_ROOT)],
            cwd=base.REPO,
            check=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=base.REPO,
        )
        if diff.returncode not in (0, 1):
            raise subprocess.CalledProcessError(diff.returncode, diff.args)
        if diff.returncode == 1:
            subprocess.run(
                ["git", "commit", "-m", f"Impact database progress: {label}"],
                cwd=base.REPO,
                check=True,
            )
            committed = True

        # Always retry synchronization. This also pushes a checkpoint commit
        # left locally by an earlier transient pull/push failure.
        subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=base.REPO,
            check=True,
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=base.REPO,
            check=True,
        )
        return committed
    except (OSError, subprocess.CalledProcessError) as exc:
        print(
            f"[{base.now()}] Git checkpoint synchronization failed; "
            f"the build will continue and retry later: {exc}",
            flush=True,
        )
        return False


# The orchestrator resolves this global at runtime, so replacing it here makes
# every progress checkpoint non-fatal while retaining the corrected collector.
base.git_commit_progress = resilient_git_commit_progress
fixed.git_commit_progress = resilient_git_commit_progress


if __name__ == "__main__":
    raise SystemExit(fixed.main())
