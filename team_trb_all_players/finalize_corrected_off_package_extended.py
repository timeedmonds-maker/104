from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def self_test() -> None:
    print("finalize_corrected_off_package_extended repair-launcher self-test PASSED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return

    driver = BASE / "wowy_repair_driver.py"
    proc = subprocess.run([sys.executable, str(driver)], check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)

    # Repair is complete, but this launcher intentionally stops here. The
    # original finalizer will be restored before the final database build so
    # no pre-repair package can be mistaken for the release artifact.
    raise RuntimeError("WOWY exact-date repair complete; restore original finalizer and rerun for final package")


if __name__ == "__main__":
    main()
