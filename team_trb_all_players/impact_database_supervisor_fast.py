from __future__ import annotations

# Install request/session tuning before the supervisor starts scheduling work.
import impact_database_runtime_tuning  # noqa: F401
import impact_database_supervisor as supervisor


if __name__ == "__main__":
    raise SystemExit(supervisor.fixed.main())
