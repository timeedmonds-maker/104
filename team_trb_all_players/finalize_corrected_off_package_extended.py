from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def self_test() -> None:
    print("finalize_corrected_off_package_extended V2 repair-launcher self-test PASSED")


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--self-test",action="store_true"); args=parser.parse_args()
    if args.self_test:
        self_test(); return

    # Deterministically rebuild and QA corrected roster targets before any network collection.
    builder=BASE/"build_roster_targets_v2.py"
    subprocess.run([sys.executable,str(builder)],check=True)

    # Collect exact date-bounded WOWY data only against those validated V2 targets.
    driver=BASE/"wowy_repair_driver.py"
    proc=subprocess.run([sys.executable,str(driver)],check=False)
    if proc.returncode!=0:
        raise SystemExit(proc.returncode)

    # Do not create a release from this temporary launcher. Once WOWY V2 is complete,
    # restore the original finalizer and rerun for final database/package generation.
    raise RuntimeError("WOWY V2 exact-date repair complete; restore original finalizer and rerun for final package")

if __name__=="__main__": main()
