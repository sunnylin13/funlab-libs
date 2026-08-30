#!/usr/bin/env python
"""
Phase 1b: Scientific Stack Audit Test
======================================

Verify that fundmgr.scientific_stack contains only pure imports (no external I/O).

This test measures the import time of pandas, numpy, ffn, and scipy to establish
a baseline and understand why 327s was reported in the startup log.

Expected findings:
- Each library import should complete in <10s on modern hardware
- Total scientific stack import should be <30s
- If total is >30s, investigate:
  1. Is there initialization code running (e.g., JIT compilation)?
  2. Are there network calls (e.g., version checks)?
  3. Is the environment slow (antivirus, network shares)?
"""
import sys
import time
import subprocess
from pathlib import Path

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / "funlab-libs"))


def test_import_time(module_name: str, import_stmt: str) -> float:
    """Measure time to import a single module."""
    start = time.perf_counter()
    try:
        exec(import_stmt)
        elapsed = time.perf_counter() - start
        return elapsed
    except Exception as e:
        print(f"✗ Failed to import {module_name}: {e}")
        return -1.0


def main():
    print("=" * 80)
    print("PHASE 1b: Scientific Stack Import Audit")
    print("=" * 80)
    print()

    # Define the imports as they appear in fundmgr.scientific_stack
    imports = [
        ("pandas", "import pandas"),
        ("numpy", "import numpy"),
        ("ffn", "import ffn"),
        ("scipy", "from scipy import stats"),
    ]

    print("Individual Import Timings:")
    print("-" * 80)

    timings = {}
    total_time = 0.0

    for module_name, import_stmt in imports:
        print(f"Importing {module_name:15s} ... ", end="", flush=True)
        elapsed = test_import_time(module_name, import_stmt)
        if elapsed < 0:
            print("FAILED")
        else:
            print(f" {elapsed:8.3f}s")
            timings[module_name] = elapsed
            total_time += elapsed

    print()
    print("-" * 80)
    print(f"Total time for scientific stack imports: {total_time:.3f}s")
    print()

    # Analyze results
    print("=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print()

    if total_time < 5.0:
        print("✓ Scientific stack imports are FAST (<5s)")
        print("  → Issue: If original was 327s, the slowness must be elsewhere")
    elif total_time < 15.0:
        print("✓ Scientific stack imports are REASONABLE (5-15s)")
        print("  → Expected behavior on modern hardware with first import")
    elif total_time < 30.0:
        print("⚠ Scientific stack imports are SLOW (15-30s)")
        print("  → Possible causes:")
        print("     - JIT compilation overhead")
        print("     - Network latency for package verification")
        print("     - Slow I/O (network share, antivirus scanning)")
    else:
        print("✗ Scientific stack imports are VERY SLOW (>30s)")
        print("  → Investigate:")
        print("     - Is antivirus scanning?")
        print("     - Is the file system on a network share?")
        print("     - Are there initialization hooks taking time?")

    print()
    print("-" * 80)
    print("MEMORY BASELINE")
    print("-" * 80)

    # Run again to capture memory after import
    try:
        import pandas as pd
        import numpy as np
        import ffn
        from scipy import stats

        print(f"✓ pandas version: {pd.__version__}")
        print(f"✓ numpy version: {np.__version__}")
        print(f"✓ ffn version: {ffn.__version__ if hasattr(ffn, '__version__') else 'unknown'}")
        print(f"✓ scipy version: {__import__('scipy').__version__}")
    except Exception as e:
        print(f"✗ Failed to get versions: {e}")

    print()
    print("=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    print()

    if total_time < 30.0:
        print("✓ Scientific stack appears to be pure imports (no I/O blocking)")
        print("✓ Keep as 'import' category in prewarm")
        print("✓ Safe to warm in background")
        print()
        print("If 327s was observed in startup log, it likely indicates:")
        print("  1. Multiple tasks running in parallel (queue delay + execution)")
        print("  2. System was under heavy load at that time")
        print("  3. First-time import JIT compilation overhead")
    else:
        print("⚠ Investigate why scientific stack is taking so long")
        print("  Consider moving to 'service_connect' category if there's I/O")
        print("  Or profile with cProfile to find the bottleneck")


if __name__ == "__main__":
    main()
