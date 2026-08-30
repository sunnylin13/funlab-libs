#!/usr/bin/env python
"""
Phase 1c: Boot Benchmark - Measure Deduplication Impact
========================================================

Runs the Finfun app startup 5 times and collects metrics:
1. Total app creation time
2. Prewarm execution breakdown per task
3. Resource dedup effectiveness
4. Queue delay analysis

Expected improvements from Phase 1a (resource_key dedup):
- TWSE calendar warming deduplicated: -99s (one of the two should be skipped)
- Total boot time reduction
- More efficient resource utilization (no duplicate I/O)

Usage:
    python test_phase_1c_boot_benchmark.py [--iterations 5] [--output results.json]
"""
import sys
import json
import time
import subprocess
import statistics
from pathlib import Path
from datetime import datetime

# Configuration
DEFAULT_ITERATIONS = 5
TIMEOUT_PER_RUN = 180  # 3 minutes per run


def run_single_boot(iteration: int) -> dict | None:
    """Run a single app boot cycle and collect metrics."""
    print(f"\n{'='*80}")
    print(f"ITERATION {iteration}/{DEFAULT_ITERATIONS}")
    print(f"{'='*80}")

    start_time = time.time()

    try:
        # Run finfun app startup (adjust the command based on your actual setup)
        print(f"Starting app... ", end="", flush=True)

        # This runs the finfun app with a timeout and captures the prewarm output
        cmd = [
            sys.executable, "-c",
            """
import sys
sys.path.insert(0, 'd:\\\\_oneDrive\\\\OneDrive_Mirle\\\\_workspaces\\\\fund13\\\\funlab-libs')
sys.path.insert(0, 'd:\\\\_oneDrive\\\\OneDrive_Mirle\\\\_workspaces\\\\fund13\\\\finfun')
sys.path.insert(0, 'd:\\\\_oneDrive\\\\OneDrive_Mirle\\\\_workspaces\\\\fund13')

import os
os.chdir('d:\\\\_oneDrive\\\\OneDrive_Mirle\\\\_workspaces\\\\fund13\\\\finfun')
import time

from funlab.core.prewarm import status

# Create app (this triggers prewarm.run())
app_start = time.perf_counter()
try:
    from funlab.flaskr.app import create_app
    app = create_app(
        configfile='d:\\\\_oneDrive\\\\OneDrive_Mirle\\\\_workspaces\\\\fund13\\\\finfun\\\\config.toml',
        envfile='d:\\\\_oneDrive\\\\OneDrive_Mirle\\\\_workspaces\\\\fund13\\\\finfun\\\\.env',
    )
    app_elapsed = time.perf_counter() - app_start
    print(f"\\nApp creation time: {app_elapsed:.3f}s")

    # Get prewarm status
    prewarm_status = status()

    # Print metrics
    print("\\n--- Prewarm Task Breakdown ---")
    total_elapsed = 0
    for task_name, task_info in sorted(prewarm_status.items()):
        elapsed = task_info.get('elapsed') or 0
        total_elapsed += elapsed
        status_val = task_info['status']
        print(f"{task_name:40s} | {status_val:15s} | {elapsed:7.3f}s")

    print(f"\\nTotal prewarm time: {total_elapsed:.3f}s")
    print(f"\\n--- Dedup Report ---")
    skipped = sum(1 for t in prewarm_status.values() if t['status'] == 'skipped_shared')
    print(f"Tasks deduplicated (skipped_shared): {skipped}")

    # Output JSON for parsing
    print("\\n--- JSON OUTPUT ---")
    import json
    output = {
        'app_creation_time': app_elapsed,
        'total_prewarm_time': total_elapsed,
        'tasks_deduplicated': skipped,
        'task_details': prewarm_status,
    }
    print(json.dumps(output))

except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()

import time
time.sleep(1)  # Brief pause
"""
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_RUN,
        )

        elapsed = time.time() - start_time

        # Parse output
        output = result.stdout
        err_output = result.stderr
        print(f"completed in {elapsed:.1f}s")
        if result.returncode != 0:
            print(f"⚠ Subprocess return code: {result.returncode}")

        # Try to extract JSON from output
        json_start = output.rfind("--- JSON OUTPUT ---")
        if json_start > 0:
            json_str = output[json_start + len("--- JSON OUTPUT ---"):].strip()
            json_lines = [line for line in json_str.split('\n') if line.startswith('{') or line.startswith('}') or '"' in line]
            json_text = '\n'.join(json_lines)
            try:
                metrics = json.loads(json_text)
                metrics['total_iteration_time'] = elapsed
                return metrics
            except json.JSONDecodeError as e:
                print(f"⚠ Failed to parse JSON: {e}")
                print(f"JSON text: {json_text[:200]}")
                return None
        else:
            print("⚠ No JSON output found in app output")
            # Still try to extract from text
            print("--- Raw Output ---")
            print(output[-500:] if len(output) > 500 else output)
            if err_output:
                print("--- Raw STDERR ---")
                print(err_output[-1000:] if len(err_output) > 1000 else err_output)
            return None

    except subprocess.TimeoutExpired:
        print(f"✗ TIMEOUT after {TIMEOUT_PER_RUN}s")
        return None
    except Exception as e:
        print(f"✗ ERROR: {e}")
        return None


def analyze_results(results: list[dict]) -> dict:
    """Analyze benchmark results across all iterations."""
    if not results:
        return {}

    # Extract metrics
    app_times = []
    prewarm_times = []
    dedup_counts = []

    for r in results:
        if r:
            if 'app_creation_time' in r:
                app_times.append(r['app_creation_time'])
            if 'total_prewarm_time' in r:
                prewarm_times.append(r['total_prewarm_time'])
            if 'tasks_deduplicated' in r:
                dedup_counts.append(r['tasks_deduplicated'])

    return {
        'app_creation': {
            'count': len(app_times),
            'mean': statistics.mean(app_times) if app_times else 0,
            'min': min(app_times) if app_times else 0,
            'max': max(app_times) if app_times else 0,
            'stdev': statistics.stdev(app_times) if len(app_times) > 1 else 0,
        },
        'prewarm_total': {
            'count': len(prewarm_times),
            'mean': statistics.mean(prewarm_times) if prewarm_times else 0,
            'min': min(prewarm_times) if prewarm_times else 0,
            'max': max(prewarm_times) if prewarm_times else 0,
            'stdev': statistics.stdev(prewarm_times) if len(prewarm_times) > 1 else 0,
        },
        'dedup': {
            'mean_skipped': statistics.mean(dedup_counts) if dedup_counts else 0,
        }
    }


def main():
    print("=" * 80)
    print("PHASE 1c: Boot Benchmark - Measure Deduplication Impact")
    print("=" * 80)
    print()
    print(f"Configuration:")
    print(f"  Iterations: {DEFAULT_ITERATIONS}")
    print(f"  Timeout per iteration: {TIMEOUT_PER_RUN}s")
    print()

    results = []

    for i in range(1, DEFAULT_ITERATIONS + 1):
        result = run_single_boot(i)
        results.append(result)
        if i < DEFAULT_ITERATIONS:
            print("\nWaiting 2 seconds before next iteration...")
            time.sleep(2)

    # Analyze
    print()
    print("=" * 80)
    print("RESULTS SUMMARY")
    print("=" * 80)
    print()

    analysis = analyze_results(results)

    if analysis:
        print("App Creation Time:")
        for key, value in analysis['app_creation'].items():
            if isinstance(value, float):
                print(f"  {key:10s}: {value:8.3f}s")
            else:
                print(f"  {key:10s}: {value}")

        print()
        print("Prewarm Total Time:")
        for key, value in analysis['prewarm_total'].items():
            if isinstance(value, float):
                print(f"  {key:10s}: {value:8.3f}s")
            else:
                print(f"  {key:10s}: {value}")

        print()
        print("Deduplication:")
        print(f"  Mean tasks skipped: {analysis['dedup']['mean_skipped']:.1f}")

    # Save full results
    output_file = Path("phase_1c_benchmark_results.json")
    output_data = {
        'timestamp': datetime.now().isoformat(),
        'iterations': DEFAULT_ITERATIONS,
        'results': results,
        'analysis': analysis,
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print()
    print(f"✓ Full results saved to: {output_file}")

    # Success check
    if all(r is not None for r in results):
        print()
        print("✓ All iterations completed successfully!")
        return 0
    else:
        failed = sum(1 for r in results if r is None)
        print()
        print(f"⚠ {failed} iteration(s) failed or timed out")
        return 1


if __name__ == "__main__":
    sys.exit(main())
