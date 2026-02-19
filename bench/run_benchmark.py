#!/usr/bin/env python3
"""Run the HumanEval-CC40 benchmark against Claude Code CLI.

For each of 40 tasks:
  1. Set up workspace (prompt.md, solution.py stub, hidden tests)
  2. Invoke Claude CLI in headless mode to implement the function
  3. Run hidden tests to check correctness
  4. On failure, retry once with test output as feedback
  5. Record result (passed, attempts, turns, cost, model usage)

Outputs:
  - docs/data/YYYY-MM-DD.json  (full daily results)
  - docs/data/latest.json      (copy of today's results)
  - docs/data/history.json     (append summary row for charting)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_DIR.parent
WORKSPACE_DIR = BENCH_DIR / "workspace"
DATA_DIR = PROJECT_ROOT / "docs" / "data"
TASK_FILE = BENCH_DIR / "data" / "humaneval_cc40.json"

MAX_TURNS = 6
MAX_BUDGET_USD = 0.10
DISALLOWED_TOOLS = "Bash,WebFetch,WebSearch,Task,NotebookEdit,Write"
MAX_ATTEMPTS = 2


def get_claude_version() -> str:
    """Get the Claude CLI version string."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip() or result.stderr.strip() or "unknown"
    except Exception as e:
        print(f"Warning: Could not get Claude version: {e}")
        return "unknown"


def setup_workspace(task: dict) -> Path:
    """Create workspace directory for a task.

    Delegates to generate_tasks.py logic but can also reconstruct
    from the task data if workspaces were cleaned.
    """
    task_dir_name = task["task_id"].replace("/", "_")
    workspace = WORKSPACE_DIR / task_dir_name

    if workspace.exists():
        shutil.rmtree(workspace)

    workspace.mkdir(parents=True)
    (workspace / "tests_hidden").mkdir()
    (workspace / ".claude").mkdir()

    # Build prompt.md
    prompt_md = f"""\
# Task: {task['task_id']}

Implement the following function in `solution.py`.

```python
{task['prompt'].rstrip()}
```

**Instructions:**
- Implement the function body to satisfy the docstring specification.
- Only edit `solution.py`. Do not create new files.
- The function signature is already provided — fill in the implementation.
"""
    (workspace / "prompt.md").write_text(prompt_md)

    # Build solution.py stub
    (workspace / "solution.py").write_text(task["prompt"].rstrip() + "\n    pass\n")

    # Build test file (import from generate_tasks for consistency)
    # We re-use the transform logic inline to avoid import complications
    test_code = build_test_file(task)
    (workspace / "tests_hidden" / "test_solution.py").write_text(test_code)
    (workspace / "tests_hidden" / "__init__.py").write_text("")

    # Claude settings: deny read on tests_hidden
    settings = {"permissions": {"deny": ["Read(tests_hidden/**)"]}}
    (workspace / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n"
    )

    return workspace


def build_test_file(task: dict) -> str:
    """Build unittest test file from HumanEval task data."""
    entry_point = task["entry_point"]
    test_code = task["test"]

    lines = test_code.split("\n")
    in_check = False
    check_body = []
    for line in lines:
        if "def check(candidate)" in line:
            in_check = True
            continue
        if in_check:
            stripped = line.strip()
            if stripped.startswith("METADATA") or (
                stripped.startswith("def ") and "check" not in stripped
            ):
                break
            if stripped:
                transformed = line.replace("candidate", entry_point)
                if transformed.startswith("    "):
                    transformed = transformed[4:]
                check_body.append(transformed)

    top_level = [l for l in check_body if not l.startswith(" ")]
    is_simple = all(l.startswith("assert") for l in top_level if l.strip())

    if is_simple and check_body:
        methods = []
        idx = 0
        current = []
        for line in check_body:
            if line.startswith("assert"):
                if current:
                    methods.append((idx, current))
                    idx += 1
                    current = []
                current.append(line)
            else:
                current.append(line)
        if current:
            methods.append((idx, current))
    else:
        methods = [(0, check_body)] if check_body else [(0, ["pass"])]

    methods_code = []
    for idx, method_lines in methods:
        body = "\n        ".join(method_lines)
        methods_code.append(f"    def test_{idx}(self):\n        {body}")

    methods_str = "\n\n".join(methods_code)

    return f"""\
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from solution import *

class TestSolution(unittest.TestCase):
{methods_str}


if __name__ == '__main__':
    unittest.main()
"""


def build_prompt(task: dict) -> str:
    """Build the initial prompt to send to Claude."""
    return (
        "Read prompt.md and implement the function in solution.py. "
        "Only edit solution.py. Do not create new files."
    )


def build_retry_prompt(test_output: str) -> str:
    """Build the retry prompt with test failure feedback."""
    # Truncate very long test output
    if len(test_output) > 2000:
        test_output = test_output[:2000] + "\n... (truncated)"
    return (
        f"The tests failed with the following output:\n\n{test_output}\n\n"
        "Please fix solution.py to pass the tests."
    )


def run_claude(prompt: str, workspace: Path, session_id: str | None = None) -> dict:
    """Invoke Claude CLI and return parsed result.

    Returns dict with keys:
        session_id, duration_ms, num_turns, total_cost_usd,
        model_usage, is_error, error_subtype, raw_result
    """
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--max-turns", str(MAX_TURNS),
        "--max-budget-usd", str(MAX_BUDGET_USD),
        "--permission-mode", "acceptEdits",
        "--disallowedTools", DISALLOWED_TOOLS,
    ]

    if session_id:
        cmd.extend(["--resume", session_id])

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=300,  # 5 minute timeout per invocation
        )
        duration_ms = int((time.monotonic() - start) * 1000)
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "session_id": session_id,
            "duration_ms": duration_ms,
            "num_turns": 0,
            "total_cost_usd": 0,
            "model_usage": {},
            "is_error": True,
            "error_subtype": "timeout",
            "raw_result": None,
        }

    # Parse JSON output
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {
            "session_id": session_id,
            "duration_ms": duration_ms,
            "num_turns": 0,
            "total_cost_usd": 0,
            "model_usage": {},
            "is_error": True,
            "error_subtype": "claude_error",
            "raw_result": {
                "stdout": result.stdout[:2000] if result.stdout else "",
                "stderr": result.stderr[:2000] if result.stderr else "",
                "returncode": result.returncode,
            },
        }

    # Extract fields from Claude CLI JSON output
    is_error = data.get("isError", False) or data.get("is_error", False)
    subtype = data.get("subtype") or data.get("errorType")
    if is_error and not subtype:
        subtype = "claude_error"

    return {
        "session_id": data.get("sessionId") or data.get("session_id") or session_id,
        "duration_ms": duration_ms,
        "num_turns": data.get("numTurns", 0) or data.get("num_turns", 0),
        "total_cost_usd": data.get("costUSD", 0) or data.get("cost_usd", 0),
        "model_usage": data.get("modelUsage", {}) or data.get("model_usage", {}),
        "is_error": is_error,
        "error_subtype": subtype,
        "raw_result": data,
    }


def run_tests(workspace: Path) -> tuple[bool, str]:
    """Run hidden tests and return (passed, output)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests_hidden", "-q"],
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=30,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        passed = result.returncode == 0
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out (30s)"
    except Exception as e:
        return False, f"Test execution error: {e}"


def run_task(task: dict) -> dict:
    """Run a single benchmark task through the full lifecycle."""
    task_id = task["task_id"]
    entry_point = task["entry_point"]
    print(f"\n{'='*60}")
    print(f"Task: {task_id} ({entry_point})")
    print(f"{'='*60}")

    # Setup workspace
    workspace = setup_workspace(task)
    print(f"  Workspace: {workspace}")

    total_turns = 0
    total_cost = 0.0
    total_duration_ms = 0
    merged_model_usage = {}
    session_id = None
    error_type = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n  Attempt {attempt}/{MAX_ATTEMPTS}")

        # Build prompt
        if attempt == 1:
            prompt = build_prompt(task)
        else:
            prompt = build_retry_prompt(test_output)

        # Run Claude
        print(f"    Invoking Claude CLI...")
        result = run_claude(prompt, workspace, session_id=session_id)

        # Accumulate metrics
        session_id = result["session_id"]
        total_turns += result["num_turns"]
        total_cost += result["total_cost_usd"]
        total_duration_ms += result["duration_ms"]

        # Merge model usage
        for model, usage in result["model_usage"].items():
            if model not in merged_model_usage:
                merged_model_usage[model] = {"inputTokens": 0, "outputTokens": 0}
            for key in ("inputTokens", "outputTokens"):
                merged_model_usage[model][key] += usage.get(key, 0)

        if result["is_error"]:
            error_type = result["error_subtype"] or "claude_error"
            print(f"    Claude error: {error_type}")
            # Still try running tests — Claude may have produced partial output
            passed, test_output = run_tests(workspace)
            if passed:
                print(f"    Tests PASSED (despite Claude error)")
                error_type = None
                return _build_result(
                    task, True, attempt, total_turns, total_duration_ms,
                    total_cost, merged_model_usage, None
                )
            print(f"    Tests FAILED")
            if attempt >= MAX_ATTEMPTS:
                return _build_result(
                    task, False, attempt, total_turns, total_duration_ms,
                    total_cost, merged_model_usage, error_type
                )
            continue

        # Run tests
        print(f"    Running tests...")
        passed, test_output = run_tests(workspace)

        if passed:
            print(f"    PASSED (attempt {attempt})")
            return _build_result(
                task, True, attempt, total_turns, total_duration_ms,
                total_cost, merged_model_usage, None
            )
        else:
            print(f"    FAILED")
            if attempt >= MAX_ATTEMPTS:
                error_type = "tests_failed"
                return _build_result(
                    task, False, attempt, total_turns, total_duration_ms,
                    total_cost, merged_model_usage, error_type
                )
            print(f"    Will retry with feedback...")

    # Should not reach here, but just in case
    return _build_result(
        task, False, MAX_ATTEMPTS, total_turns, total_duration_ms,
        total_cost, merged_model_usage, error_type or "tests_failed"
    )


def _build_result(
    task: dict,
    passed: bool,
    attempts: int,
    turns: int,
    duration_ms: int,
    cost: float,
    model_usage: dict,
    error_type: str | None,
) -> dict:
    """Build the per-task result dict."""
    return {
        "task_id": task["task_id"],
        "function_name": task["entry_point"],
        "passed": passed,
        "attempts_used": attempts,
        "num_turns_total": turns,
        "duration_ms_total": duration_ms,
        "total_cost_usd_total": round(cost, 6),
        "modelUsage": model_usage,
        "error_type": error_type,
    }


def aggregate_results(
    task_results: list[dict],
    started_at: str,
    finished_at: str,
    claude_version: str,
) -> dict:
    """Build the full daily results JSON."""
    passed_count = sum(1 for r in task_results if r["passed"])
    total_count = len(task_results)
    score = round((passed_count / total_count) * 100, 1) if total_count else 0

    total_cost = round(sum(r["total_cost_usd_total"] for r in task_results), 4)
    total_duration_ms = sum(r["duration_ms_total"] for r in task_results)

    # Merge all model usage
    merged_usage = {}
    for r in task_results:
        for model, usage in r["modelUsage"].items():
            if model not in merged_usage:
                merged_usage[model] = {"inputTokens": 0, "outputTokens": 0}
            for key in ("inputTokens", "outputTokens"):
                merged_usage[model][key] += usage.get(key, 0)

    # Extract primary model name (the one with most tokens)
    primary_model = "unknown"
    if merged_usage:
        primary_model = max(
            merged_usage,
            key=lambda m: merged_usage[m]["inputTokens"] + merged_usage[m]["outputTokens"],
        )

    return {
        "date": started_at[:10],
        "suite": "HumanEval-CC40",
        "score": score,
        "passed": passed_count,
        "total": total_count,
        "total_cost_usd": total_cost,
        "total_duration_ms": total_duration_ms,
        "primary_model": primary_model,
        "claude_version": claude_version,
        "modelUsage": merged_usage,
        "started_at": started_at,
        "finished_at": finished_at,
        "tasks": task_results,
    }


def update_history(today_result: dict) -> None:
    """Append today's summary to history.json."""
    history_file = DATA_DIR / "history.json"

    if history_file.exists():
        history = json.loads(history_file.read_text())
    else:
        history = {"entries": []}

    # Build summary entry
    entry = {
        "date": today_result["date"],
        "score": today_result["score"],
        "passed": today_result["passed"],
        "total": today_result["total"],
        "total_cost_usd": today_result["total_cost_usd"],
        "total_duration_ms": today_result["total_duration_ms"],
        "primary_model": today_result["primary_model"],
        "claude_version": today_result["claude_version"],
    }

    # Remove existing entry for today (idempotent reruns)
    history["entries"] = [
        e for e in history["entries"] if e["date"] != entry["date"]
    ]
    history["entries"].append(entry)

    # Sort by date
    history["entries"].sort(key=lambda e: e["date"])

    history_file.write_text(json.dumps(history, indent=2) + "\n")
    print(f"Updated {history_file} ({len(history['entries'])} entries)")


def main():
    print("=" * 60)
    print("HumanEval-CC40 Benchmark")
    print("=" * 60)

    # Get Claude version
    claude_version = get_claude_version()
    print(f"Claude CLI version: {claude_version}")

    # Load tasks
    if not TASK_FILE.exists():
        print(f"Error: Task file not found: {TASK_FILE}")
        print("Run `python bench/generate_tasks.py` first.")
        sys.exit(1)

    cc40 = json.loads(TASK_FILE.read_text())
    tasks = cc40["tasks"]
    print(f"Loaded {len(tasks)} tasks from {TASK_FILE}")

    # Record start time
    started_at = datetime.now(timezone.utc).isoformat()

    # Run all tasks sequentially
    task_results = []
    for i, task in enumerate(tasks):
        print(f"\n[{i+1}/{len(tasks)}]", end="")
        result = run_task(task)
        task_results.append(result)

        # Progress summary
        passed_so_far = sum(1 for r in task_results if r["passed"])
        print(f"\n  Running score: {passed_so_far}/{len(task_results)}")

    # Record finish time
    finished_at = datetime.now(timezone.utc).isoformat()

    # Aggregate results
    results = aggregate_results(task_results, started_at, finished_at, claude_version)

    # Write output files
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = results["date"]

    daily_file = DATA_DIR / f"{today}.json"
    daily_file.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nWrote {daily_file}")

    latest_file = DATA_DIR / "latest.json"
    latest_file.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Wrote {latest_file}")

    update_history(results)

    # Print summary
    print(f"\n{'='*60}")
    print(f"RESULTS: {results['passed']}/{results['total']} passed ({results['score']}%)")
    print(f"Cost: ${results['total_cost_usd']:.4f}")
    print(f"Duration: {results['total_duration_ms'] / 1000:.1f}s")
    print(f"Model: {results['primary_model']}")
    print(f"{'='*60}")

    # Clean up workspaces
    if WORKSPACE_DIR.exists():
        shutil.rmtree(WORKSPACE_DIR)
        print("Cleaned up workspaces")


if __name__ == "__main__":
    main()
