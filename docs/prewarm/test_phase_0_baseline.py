#!/usr/bin/env python
"""
Phase 0 Baseline Measurement Test
==================================

Test the new observability fields added to prewarm framework:
- category, resource_key, owner, budget_sec
- start_ts, end_ts, queue_delay, budget_exceeded

This test registers mock tasks with various configurations and verifies
that the new fields are properly populated.

Expected output: structured log / JSON report showing each task's timing breakdown.
"""
import sys
import json
import time
from pathlib import Path

# Add funlab-libs to path so we can import prewarm
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / "funlab-libs"))

import funlab.core.prewarm as pw


def test_phase_0_observability():
    """Test new observability fields in prewarm framework."""
    print("=" * 80)
    print("PHASE 0: Baseline Measurement & Observability Test")
    print("=" * 80)
    print()

    # Reset before test
    pw.reset()

    # Register test tasks
    print("1. Registering test tasks...")

    # Task 1: Simple import (fast)
    def _warmup_stdlib():
        import os
        import sys

    pw.register(
        "test.stdlib",
        _warmup_stdlib,
        category="import",
        resource_key="stdlib",
        owner="test_plugin",
        budget_sec=1.0,
    )
    print("  ✓ test.stdlib (import, resource_key=stdlib, budget=1.0s)")

    # Task 2: Another import (fast) - should be skipped_shared
    def _warmup_stdlib_dupe():
        import time

    pw.register(
        "test.stdlib_dupe",
        _warmup_stdlib_dupe,
        category="import",
        resource_key="stdlib",  # SAME resource_key as above
        owner="test_plugin",
        budget_sec=1.0,
    )
    print("  ✓ test.stdlib_dupe (import, resource_key=stdlib - should skip)")

    # Task 3: Heavy import with delay
    def _warmup_numpy():
        time.sleep(0.5)  # Simulate import cost
        import math

    pw.register(
        "test.numpy",
        _warmup_numpy,
        category="import",
        delay=1.0,  # Wait 1s before start
        owner="test_plugin",
        budget_sec=2.0,
    )
    print("  ✓ test.numpy (import, delay=1.0s, budget=2.0s)")

    # Task 4: Service connect (external I/O)
    def _warmup_broker():
        time.sleep(0.3)  # Simulate broker login

    pw.register(
        "test.broker",
        _warmup_broker,
        category="service_connect",
        resource_key="broker_login",
        owner="quote_service",
        budget_sec=5.0,
    )
    print("  ✓ test.broker (service_connect, budget=5.0s)")

    # Task 5: Budget-exceeded scenario
    def _warmup_slow():
        time.sleep(0.7)  # Exceed budget

    pw.register(
        "test.slow",
        _warmup_slow,
        category="import",
        owner="test_plugin",
        budget_sec=0.5,  # This WILL be exceeded
    )
    print("  ✓ test.slow (import, budget=0.5s - will exceed)")

    print()
    print("2. Executing prewarm tasks...")
    pw.run(app=None)

    # Wait for background tasks to complete
    print("   (waiting for background tasks to complete...)")
    time.sleep(3.0)

    print()
    print("3. Collecting status report...")
    status = pw.status()

    # Build report
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "phase": "phase_0_baseline",
        "tasks": status,
    }

    # Print human-readable summary
    print()
    print("-" * 80)
    print("TASK EXECUTION SUMMARY")
    print("-" * 80)
    print()

    total_elapsed = 0
    max_queue_delay = 0
    budget_exceeded_count = 0
    skipped_shared_count = 0

    for task_name, task_status in sorted(status.items()):
        status_val = task_status["status"]
        elapsed = task_status.get("elapsed") or 0
        queue_delay = task_status.get("queue_delay") or 0
        budget_exceeded = task_status.get("budget_exceeded", False)

        total_elapsed += elapsed
        max_queue_delay = max(max_queue_delay, queue_delay)

        if budget_exceeded:
            budget_exceeded_count += 1
        if status_val == "skipped_shared":
            skipped_shared_count += 1

        status_icon = "✓" if status_val == "done" else ("⊘" if status_val == "skipped_shared" else "✗" if status_val == "failed" else "●")
        budget_marker = " ⚠ BUDGET_EXCEEDED" if budget_exceeded else ""
        print(
            f"{status_icon} {task_name:30s} | {status_val:15s} | {elapsed:7.3f}s "
            f"(queue={queue_delay:6.3f}s){budget_marker}"
        )

    print()
    print("-" * 80)
    print("KEY METRICS")
    print("-" * 80)
    print(f"Total elapsed (sum of all tasks): {total_elapsed:.3f}s")
    print(f"Max queue delay: {max_queue_delay:.3f}s")
    print(f"Tasks with budget exceeded: {budget_exceeded_count}")
    print(f"Tasks skipped (resource dedup): {skipped_shared_count}")
    print(f"Total tasks: {len(status)}")
    print()

    # JSON output
    print("-" * 80)
    print("JSON REPORT")
    print("-" * 80)
    print(json.dumps(report, indent=2))
    print()

    # Verify expectations
    print("-" * 80)
    print("VERIFICATION CHECKLIST")
    print("-" * 80)

    checks = [
        ("test.stdlib completed", status["test.stdlib"]["status"] == "done"),
        ("test.stdlib_dupe skipped_shared", status["test.stdlib_dupe"]["status"] == "skipped_shared"),
        ("test.numpy completed", status["test.numpy"]["status"] == "done"),
        ("test.broker completed", status["test.broker"]["status"] == "done"),
        ("test.slow budget exceeded", status["test.slow"].get("budget_exceeded", False)),
        ("Queue delay recorded", all(s.get("queue_delay") is not None for s in status.values() if s["status"] in ("done", "running"))),
        ("Resource dedup worked (1 skipped)", skipped_shared_count == 1),
    ]

    all_passed = True
    for check_name, check_result in checks:
        marker = "✓" if check_result else "✗"
        print(f"{marker} {check_name}")
        if not check_result:
            all_passed = False

    print()
    if all_passed:
        print("✓ ALL CHECKS PASSED - Phase 0 baseline measurement successful!")
        return 0
    else:
        print("✗ SOME CHECKS FAILED - Please review the output above.")
        return 1


if __name__ == "__main__":
    exit_code = test_phase_0_observability()
    sys.exit(exit_code)
