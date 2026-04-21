#!/usr/bin/env python3
"""Variance probe: resample a targeted set of tasks many times per model.

Purpose: disentangle temperature variance from systematic model differences
on the tasks where Opus 4.6 and 4.7 diverge. Also captures every generated
solution.py so we can inspect *how* responses differ, not just pass/fail.

Usage:
  ANTHROPIC_API_KEY=... python bench/variance_probe.py \
    --models claude-opus-4-7 claude-opus-4-6 \
    --tasks HumanEval/97 HumanEval/141 \
    --trials 10 \
    --out bench/variance_results.json
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse the existing harness
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_benchmark as rb

BENCH_DIR = Path(__file__).resolve().parent
PROBE_WORKSPACES = BENCH_DIR / "probe_workspace"
PROBE_SOLUTIONS = BENCH_DIR / "probe_solutions"


def run_one_trial(task: dict, model: str, trial_idx: int) -> dict:
    """Run a single trial of (task, model) and capture the solution.

    `run_task` internally calls `setup_workspace` (which wipes and rebuilds
    the task workspace), runs Claude, and runs tests. We read solution.py
    out of the workspace afterward — the harness-level cleanup that removes
    WORKSPACE_DIR only happens at the end of run_benchmark's main(), not
    per-task, so the file is still there.
    """
    original_model = rb.MODEL
    rb.MODEL = model
    try:
        result = rb.run_task(task)

        task_dir_name = task["task_id"].replace("/", "_")
        workspace = rb.WORKSPACE_DIR / task_dir_name
        solution_path = workspace / "solution.py"
        solution_text = solution_path.read_text() if solution_path.exists() else "(missing)"

        # Archive the solution by (task, model, trial)
        tag = rb.model_tag(model)
        task_slug = task["task_id"].replace("/", "_")
        archive_dir = PROBE_SOLUTIONS / task_slug / tag
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / f"trial_{trial_idx:02d}.py").write_text(solution_text)

        return {
            "task_id": task["task_id"],
            "model": model,
            "trial": trial_idx,
            "passed": result["passed"],
            "error_type": result["error_type"],
            "num_turns": result["num_turns_total"],
            "cost_usd": result["total_cost_usd_total"],
            "duration_ms": result["duration_ms_total"],
            "solution": solution_text,
        }
    finally:
        rb.MODEL = original_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True,
                        help="Models to test (e.g. claude-opus-4-7 claude-opus-4-6)")
    parser.add_argument("--tasks", nargs="+", required=True,
                        help="Task IDs to probe (e.g. HumanEval/97)")
    parser.add_argument("--trials", type=int, default=10,
                        help="Number of trials per (task, model) pair")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args()

    # Load the task dataset
    task_data = json.loads(rb.TASK_FILE.read_text())
    all_tasks = {t["task_id"]: t for t in task_data["tasks"]}
    missing = [tid for tid in args.tasks if tid not in all_tasks]
    if missing:
        print(f"Error: unknown task ids: {missing}", file=sys.stderr)
        sys.exit(1)

    # Clean archived solutions from prior probes
    if PROBE_SOLUTIONS.exists():
        shutil.rmtree(PROBE_SOLUTIONS)
    PROBE_SOLUTIONS.mkdir(parents=True)

    started_at = datetime.now(timezone.utc).isoformat()
    trials = []
    total_trials = len(args.tasks) * len(args.models) * args.trials
    counter = 0

    for task_id in args.tasks:
        task = all_tasks[task_id]
        for model in args.models:
            for trial_idx in range(args.trials):
                counter += 1
                print(f"\n[{counter}/{total_trials}] task={task_id} model={model} trial={trial_idx}")
                trial = run_one_trial(task, model, trial_idx)
                trials.append(trial)

                # Incrementally checkpoint results (this run is going to be
                # expensive — don't lose everything on interrupt)
                Path(args.out).write_text(json.dumps({
                    "started_at": started_at,
                    "models": args.models,
                    "tasks": args.tasks,
                    "trials_per_pair": args.trials,
                    "trials": trials,
                }, indent=2) + "\n")

    # Print summary table
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Task':<20} {'Model':<22} {'Pass rate':<12}")
    print("-" * 70)
    for task_id in args.tasks:
        for model in args.models:
            matching = [t for t in trials if t["task_id"] == task_id and t["model"] == model]
            passed = sum(1 for t in matching if t["passed"])
            print(f"{task_id:<20} {model:<22} {passed}/{len(matching)}")

    # Clean up workspaces
    if rb.WORKSPACE_DIR.exists():
        shutil.rmtree(rb.WORKSPACE_DIR)

    print(f"\nSolutions archived to {PROBE_SOLUTIONS}")
    print(f"Results written to {args.out}")


if __name__ == "__main__":
    main()
