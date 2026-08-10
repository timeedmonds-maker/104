from __future__ import annotations

import argparse
import json

import run_corrected_off_batch_public_resilient as resilient
import zero_minute_tail_prune as zero_tail


def run(batch_size: int, workers: int, request_interval: float):
    # First classify unresolved tenure windows using the independent team/date stat endpoint.
    # Verified 0:00 windows are removed from the analytical denominator but retained in an audit file.
    zero_report = zero_tail.prune(resilient.public_resilient_request_json)
    summary = resilient.run(batch_size, workers, request_interval)
    summary["zero_minute_tail_policy"] = "verified zero-ON-minute unresolved tenure windows excluded from required Stage2 denominator and retained in audit"
    summary["zero_minute_tail_report"] = zero_report
    resilient.core.SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def self_test() -> None:
    resilient.self_test()
    assert callable(zero_tail.prune)
    print("run_corrected_off_batch_public_resilient_v2 self-test PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=70)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--request-interval", type=float, default=1.0)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        print(json.dumps(run(args.batch_size, args.workers, args.request_interval), indent=2))


if __name__ == "__main__":
    main()
