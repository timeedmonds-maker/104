from __future__ import annotations

import os
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any

import requests

import impact_database_build as base

# A team-season is safely checkpointed and re-queued when a request fails. It is
# therefore faster and safer to fail one request round promptly than to pin a
# worker for up to nine minutes on six 90-second attempts.
REQUEST_ATTEMPTS = max(1, int(os.getenv("IMPACT_DB_REQUEST_ATTEMPTS", "3")))
CONNECT_TIMEOUT = max(3.0, float(os.getenv("IMPACT_DB_CONNECT_TIMEOUT", "8")))
READ_TIMEOUT = max(10.0, float(os.getenv("IMPACT_DB_READ_TIMEOUT", "45")))
MAX_BACKOFF = max(1.0, float(os.getenv("IMPACT_DB_MAX_BACKOFF", "12")))

_thread_local = threading.local()


def _session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(base.HEADERS)
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        value = response.headers.get("Retry-After", "").strip()
        if value:
            try:
                return min(MAX_BACKOFF, max(0.0, float(value)))
            except ValueError:
                try:
                    delta = parsedate_to_datetime(value).timestamp() - time.time()
                    return min(MAX_BACKOFF, max(0.0, delta))
                except Exception:
                    pass
    return min(MAX_BACKOFF, 2 ** (attempt - 1)) + random.random()


def request_json_fast(
    url: str,
    params: dict[str, str],
    attempts: int | None = None,
) -> dict[str, Any]:
    attempts = REQUEST_ATTEMPTS if attempts is None else max(1, min(int(attempts), REQUEST_ATTEMPTS))
    errors: list[str] = []
    session = _session()

    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        response: requests.Response | None = None
        try:
            response = session.get(
                url,
                params=params,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            if response.status_code == 400:
                return {
                    "ok": False,
                    "absent": True,
                    "status_code": 400,
                    "url": response.url,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "errors": [response.text[:500]],
                }
            if response.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:180]!r}")
            response.raise_for_status()
            payload = response.json()
            if base.REQUEST_PAUSE:
                time.sleep(base.REQUEST_PAUSE)
            return {
                "ok": True,
                "absent": False,
                "status_code": response.status_code,
                "url": response.url,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "payload": payload,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc!r}")
            # A broken keep-alive connection can poison a thread-local session.
            # Replace it before the next attempt without affecting other workers.
            try:
                session.close()
            except Exception:
                pass
            _thread_local.session = None
            session = _session()
            if attempt < attempts:
                time.sleep(_retry_delay(response, attempt))

    return {
        "ok": False,
        "absent": False,
        "elapsed_seconds": None,
        "errors": errors,
    }


# The corrected collector resolves base.request_json at call time, so this
# patch applies to totals, rebound-stat and player-scoped team-profile requests.
base.request_json = request_json_fast
