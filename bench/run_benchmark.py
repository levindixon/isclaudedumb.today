#!/usr/bin/env python3
"""Run the HumanEvalPlus-CC164 benchmark against Claude Code CLI.

For each of 164 tasks:
  1. Set up workspace (prompt.md, solution.py stub, hidden tests)
  2. Invoke Claude CLI in headless mode to implement the function
  3. Run hidden tests (original HumanEval + EvalPlus edge cases) to check correctness
  4. Record result (passed, attempts, turns, cost, model usage)

Model and effort are controlled via env vars so CI can run the same harness
for multiple models (e.g. shipping model + a reference baseline).

Outputs:
  - docs/data/YYYY-MM-DD-HHMM-<tag>.json  (per-run results, model-tagged)
  - docs/data/latest.json                  (most recent PRIMARY_MODEL run)
  - docs/data/history.json                 (append summary row for charting)
"""

import json
import os
import re
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
TASK_FILE = BENCH_DIR / "data" / "humaneval_plus_cc164.json"

MAX_TURNS = 3
MAX_BUDGET_USD = 1.00
DISALLOWED_TOOLS = "Bash,WebFetch,WebSearch,Task,NotebookEdit,Write"
MAX_ATTEMPTS = 1

# The "shipping" model the dashboard verdict tracks. Runs with this model
# overwrite latest.json; runs with any other model only append to history.
PRIMARY_MODEL = "claude-opus-4-7"
MODEL = os.environ.get("BENCH_MODEL", PRIMARY_MODEL)
EFFORT = os.environ.get("BENCH_EFFORT", "high")


def model_tag(model: str) -> str:
    """Short, filename-safe tag for a model id (e.g. claude-opus-4-7 -> opus47)."""
    m = re.match(r"^claude-([a-z]+)-(\d+)-(\d+)", model)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    return re.sub(r"[^a-z0-9]+", "", model.lower()) or "model"

_logged_cli_keys = False


def _get_first(data: dict, default, *keys):
    """Return the value of the first key that exists in data, else default.

    Unlike `data.get(k1) or data.get(k2)`, this correctly handles falsy
    values like 0, False, and {} — it only falls through when the key is
    truly absent from the dict.
    """
    for key in keys:
        if key in data:
            return data[key]
    return default


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

    Writes two separate test files (base HumanEval + EvalPlus edge cases)
    so the runner can record per-bucket pass/fail. Structurally matches
    generate_tasks.setup_workspace so local dev and CI behave identically.
    """
    task_dir_name = task["task_id"].replace("/", "_")
    workspace = WORKSPACE_DIR / task_dir_name

    if workspace.exists():
        shutil.rmtree(workspace)

    workspace.mkdir(parents=True)
    (workspace / "tests_hidden").mkdir()
    (workspace / ".claude").mkdir()

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
    (workspace / "solution.py").write_text(task["prompt"].rstrip() + "\n    pass\n")

    # Two independent test files: test_base.py for the original HumanEval
    # asserts, test_evalplus.py for the EvalPlus edge-case asserts. The
    # runner invokes each file separately so a failure on one bucket does
    # not mask the other.
    (workspace / "tests_hidden" / "test_base.py").write_text(build_base_test_source(task))
    (workspace / "tests_hidden" / "test_evalplus.py").write_text(
        build_evalplus_test_source(task)
    )
    (workspace / "tests_hidden" / "__init__.py").write_text("")

    settings = {"permissions": {"deny": ["Read(tests_hidden/**)"]}}
    (workspace / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n"
    )

    return workspace


# Delegate test-file construction to generate_tasks so local dev and CI use
# one authoritative implementation. Importing at module top avoids the cost
# of doing it per-task.
from generate_tasks import build_base_test_file as build_base_test_source
from generate_tasks import build_evalplus_test_file as _build_evalplus_test_source


def build_evalplus_test_source(task: dict) -> str:
    """Wrap generate_tasks.build_evalplus_test_file to accept the task dict directly."""
    return _build_evalplus_test_source(task, task.get("evalplus_tests", []))


def build_test_file(task: dict) -> str:
    """Legacy combined test file (base + evalplus). Used by the variance
    probe and any other tooling that predates the base/plus split."""
    from generate_tasks import transform_tests
    return transform_tests(task, evalplus_tests=task.get("evalplus_tests"))


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
        "--model", MODEL,
        "--effort", EFFORT,
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
            timeout=600,  # 10 minute timeout per invocation
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
        print(f"    [debug] Failed to parse CLI JSON output")
        if result.stdout:
            print(f"    [debug] stdout (first 500 chars): {result.stdout[:500]}")
        if result.stderr:
            print(f"    [debug] stderr (first 500 chars): {result.stderr[:500]}")
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

    # Log CLI output keys once for diagnostics
    global _logged_cli_keys
    if not _logged_cli_keys:
        print(f"    [debug] CLI JSON top-level keys: {sorted(data.keys())}")
        cost_val = data.get("total_cost_usd", "MISSING")
        print(f"    [debug] total_cost_usd = {cost_val}")
        _logged_cli_keys = True

    # Extract fields from Claude CLI JSON output.
    # The CLI uses snake_case for top-level fields (session_id, num_turns,
    # is_error, total_cost_usd) but camelCase for modelUsage.
    is_error = _get_first(data, False, "is_error", "isError")
    subtype = _get_first(data, None, "subtype", "errorType")
    if is_error and not subtype:
        subtype = "claude_error"

    return {
        "session_id": _get_first(data, session_id, "session_id", "sessionId"),
        "duration_ms": duration_ms,
        "num_turns": _get_first(data, 0, "num_turns", "numTurns"),
        "total_cost_usd": _get_first(data, 0, "total_cost_usd", "costUSD", "cost_usd"),
        "model_usage": _get_first(data, {}, "modelUsage", "model_usage"),
        "is_error": is_error,
        "error_subtype": subtype,
        "raw_result": data,
    }


def _run_test_module(workspace: Path, module: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a single test module (e.g. test_base) and return (passed, output)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", f"tests_hidden.{module}"],
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=timeout,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Test execution timed out ({timeout}s)"
    except Exception as e:
        return False, f"Test execution error: {e}"


def run_tests(workspace: Path) -> tuple[bool, bool, str]:
    """Run base + evalplus tests independently.

    Returns (passed_base, passed_evalplus, combined_output). If the EvalPlus
    test file is empty (no plus_input for this task), passed_evalplus defaults
    to True so it doesn't count as a false failure against the model.
    """
    passed_base, out_base = _run_test_module(workspace, "test_base")

    evalplus_path = workspace / "tests_hidden" / "test_evalplus.py"
    if evalplus_path.exists() and "test_plus_" in evalplus_path.read_text():
        passed_plus, out_plus = _run_test_module(workspace, "test_evalplus")
    else:
        passed_plus, out_plus = True, "(no evalplus tests for this task)"

    combined = (
        f"--- BASE ({'PASS' if passed_base else 'FAIL'}) ---\n{out_base}\n"
        f"--- EVALPLUS ({'PASS' if passed_plus else 'FAIL'}) ---\n{out_plus}"
    )
    return passed_base, passed_plus, combined


def _read_solution(workspace: Path) -> str:
    """Read the solution.py Claude produced. Returns '' if missing."""
    path = workspace / "solution.py"
    if not path.exists():
        return ""
    try:
        return path.read_text()
    except Exception:
        return ""


def run_task(task: dict) -> dict:
    """Run a single benchmark task through the full lifecycle.

    Returns a per-task result dict with separate base/evalplus pass flags,
    the generated solution.py contents, and all runtime metrics.
    """
    task_id = task["task_id"]
    entry_point = task["entry_point"]
    print(f"\n{'='*60}")
    print(f"Task: {task_id} ({entry_point})")
    print(f"{'='*60}")

    workspace = setup_workspace(task)
    print(f"  Workspace: {workspace}")

    total_turns = 0
    total_cost = 0.0
    total_duration_ms = 0
    merged_model_usage = {}
    session_id = None
    error_type = None
    passed_base = False
    passed_plus = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n  Attempt {attempt}/{MAX_ATTEMPTS}")
        prompt = build_prompt(task) if attempt == 1 else build_retry_prompt(test_output)

        print(f"    Invoking Claude CLI...")
        result = run_claude(prompt, workspace, session_id=session_id)

        session_id = result["session_id"]
        total_turns += result["num_turns"]
        total_cost += result["total_cost_usd"]
        total_duration_ms += result["duration_ms"]

        for model, usage in result["model_usage"].items():
            if model not in merged_model_usage:
                merged_model_usage[model] = {"inputTokens": 0, "outputTokens": 0}
            for key in ("inputTokens", "outputTokens"):
                merged_model_usage[model][key] += usage.get(key, 0)

        if result["is_error"]:
            error_type = result["error_subtype"] or "claude_error"
            print(f"    Claude error: {error_type}")

        # Always run tests — even after a Claude error the workspace may have
        # a usable solution.py from earlier turns.
        print(f"    Running tests...")
        passed_base, passed_plus, test_output = run_tests(workspace)
        passed_all = passed_base and passed_plus

        bucket = f"base={'P' if passed_base else 'F'} plus={'P' if passed_plus else 'F'}"
        if passed_all:
            print(f"    PASSED (attempt {attempt}, {bucket})")
            error_type = None if not result["is_error"] else error_type
            return _build_result(
                task, passed_base, passed_plus, attempt, total_turns,
                total_duration_ms, total_cost, merged_model_usage,
                None if passed_all else error_type, _read_solution(workspace),
            )

        print(f"    FAILED ({bucket})")
        if attempt >= MAX_ATTEMPTS:
            if not error_type:
                error_type = "tests_failed"
            return _build_result(
                task, passed_base, passed_plus, attempt, total_turns,
                total_duration_ms, total_cost, merged_model_usage,
                error_type, _read_solution(workspace),
            )
        print(f"    Will retry with feedback...")

    # Fallthrough for safety (MAX_ATTEMPTS=0 or similar pathological config)
    return _build_result(
        task, passed_base, passed_plus, MAX_ATTEMPTS, total_turns,
        total_duration_ms, total_cost, merged_model_usage,
        error_type or "tests_failed", _read_solution(workspace),
    )


def _build_result(
    task: dict,
    passed_base: bool,
    passed_plus: bool,
    attempts: int,
    turns: int,
    duration_ms: int,
    cost: float,
    model_usage: dict,
    error_type: str | None,
    solution: str,
) -> dict:
    """Build the per-task result dict.

    `passed` (aggregate) is preserved for backward compatibility with the
    dashboard; new fields `passed_base` and `passed_evalplus` let viewers
    separate pure-capability failures from interpretation-edge-case failures.
    """
    return {
        "task_id": task["task_id"],
        "function_name": task["entry_point"],
        "passed": passed_base and passed_plus,
        "passed_base": passed_base,
        "passed_evalplus": passed_plus,
        "attempts_used": attempts,
        "num_turns_total": turns,
        "duration_ms_total": duration_ms,
        "total_cost_usd_total": round(cost, 6),
        "modelUsage": model_usage,
        "error_type": error_type,
        "solution": solution,
    }


def _pct(num: int, denom: int) -> float:
    """Percentage with 1dp precision, guarding against zero denom."""
    return round((num / denom) * 100, 1) if denom else 0.0


def _bitmap(flags: list[bool]) -> str:
    """Compact 0/1 string of pass flags — one char per task, index-aligned
    with aggregate_results['task_ids']. Used by the dashboard to build the
    per-task divergence view without loading every per-run JSON."""
    return "".join("1" if f else "0" for f in flags)


def aggregate_results(
    task_results: list[dict],
    run_id: str,
    started_at: str,
    finished_at: str,
    claude_version: str,
    quarantined: list[str] | None = None,
) -> dict:
    """Build the full daily results JSON.

    `run_id` is the shared-schedule identifier (same value for all models
    benchmarked by one cron fire). `started_at`/`finished_at` are the true
    wall-clock bounds of this specific invocation.

    `quarantined` is a list of task_ids excluded from the benchmark because
    their canonical solution fails its own tests (broken EvalPlus inputs).
    They contribute no results but are listed so the dashboard can call
    out what was skipped.
    """
    total_count = len(task_results)
    passed_count = sum(1 for r in task_results if r["passed"])
    passed_base = sum(1 for r in task_results if r.get("passed_base"))
    passed_plus = sum(1 for r in task_results if r.get("passed_evalplus"))

    total_cost = round(sum(r["total_cost_usd_total"] for r in task_results), 4)
    total_duration_ms = sum(r["duration_ms_total"] for r in task_results)

    merged_usage = {}
    for r in task_results:
        for model, usage in r["modelUsage"].items():
            if model not in merged_usage:
                merged_usage[model] = {"inputTokens": 0, "outputTokens": 0}
            for key in ("inputTokens", "outputTokens"):
                merged_usage[model][key] += usage.get(key, 0)

    primary_model = "unknown"
    if merged_usage:
        primary_model = max(
            merged_usage,
            key=lambda m: merged_usage[m]["inputTokens"] + merged_usage[m]["outputTokens"],
        )

    return {
        "date": run_id[:10],
        "run_id": run_id,
        "suite": "HumanEvalPlus-CC164",
        "score": _pct(passed_count, total_count),
        "score_base": _pct(passed_base, total_count),
        "score_evalplus": _pct(passed_plus, total_count),
        "passed": passed_count,
        "passed_base": passed_base,
        "passed_evalplus": passed_plus,
        "total": total_count,
        "quarantined": quarantined or [],
        "total_cost_usd": total_cost,
        "total_duration_ms": total_duration_ms,
        "primary_model": primary_model,
        "claude_version": claude_version,
        "modelUsage": merged_usage,
        "started_at": started_at,
        "finished_at": finished_at,
        "task_ids": [r["task_id"] for r in task_results],
        "pass_bitmap_base": _bitmap([r.get("passed_base", False) for r in task_results]),
        "pass_bitmap_evalplus": _bitmap([r.get("passed_evalplus", False) for r in task_results]),
        "tasks": task_results,
    }


def update_history(today_result: dict) -> None:
    """Append today's summary to history.json."""
    history_file = DATA_DIR / "history.json"

    if history_file.exists():
        history = json.loads(history_file.read_text())
    else:
        history = {"entries": []}

    # Build summary entry. The pass_bitmap_* fields are ~164 chars each and
    # let the dashboard compute per-task divergence without loading every
    # per-run JSON. Keeping them in history.json (rather than a separate
    # file) keeps the client's network fetches unchanged.
    run_id = today_result.get("run_id") or today_result["started_at"]
    entry = {
        "date": today_result["date"],
        "run_id": run_id,
        "score": today_result["score"],
        "score_base": today_result.get("score_base"),
        "score_evalplus": today_result.get("score_evalplus"),
        "passed": today_result["passed"],
        "passed_base": today_result.get("passed_base"),
        "passed_evalplus": today_result.get("passed_evalplus"),
        "total": today_result["total"],
        "quarantined": today_result.get("quarantined", []),
        "total_cost_usd": today_result["total_cost_usd"],
        "total_duration_ms": today_result["total_duration_ms"],
        "primary_model": today_result["primary_model"],
        "claude_version": today_result["claude_version"],
        "task_ids": today_result.get("task_ids", []),
        "pass_bitmap_base": today_result.get("pass_bitmap_base", ""),
        "pass_bitmap_evalplus": today_result.get("pass_bitmap_evalplus", ""),
    }

    # Remove any existing entry for the same (run_id, primary_model) pair
    # so reruns are idempotent but multiple models at the same timestamp
    # (e.g. 4.6 and 4.7 both running in the same CI job) both persist.
    # Legacy entries without run_id fall back to date-based dedup.
    history["entries"] = [
        e for e in history["entries"]
        if not (
            e.get("run_id", e["date"]) == entry["run_id"]
            and e.get("primary_model") == entry.get("primary_model")
        )
    ]
    history["entries"].append(entry)

    # Sort by run_id (ISO timestamps sort lexicographically).
    # Legacy entries without run_id sort by date.
    history["entries"].sort(key=lambda e: e.get("run_id", e["date"]))

    history_file.write_text(json.dumps(history, indent=2) + "\n")
    print(f"Updated {history_file} ({len(history['entries'])} entries)")


def validate_canonicals(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    """Run each task's canonical solution against its own tests.

    Returns (usable_tasks, quarantined_task_ids). A task whose canonical
    solution fails its own generated tests has a broken test spec (e.g.
    HumanEval/70's impossible assertion on empty input) and contributes
    noise to the benchmark rather than signal. We skip those and record
    which ones were skipped so the dashboard can surface it.
    """
    import tempfile

    usable, quarantined = [], []
    for task in tasks:
        task_id = task["task_id"]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "solution.py").write_text(
                task["prompt"].rstrip() + "\n" + task["canonical_solution"] + "\n"
            )
            (td / "tests_hidden").mkdir()
            (td / "tests_hidden" / "__init__.py").write_text("")
            (td / "tests_hidden" / "test_base.py").write_text(build_base_test_source(task))
            (td / "tests_hidden" / "test_evalplus.py").write_text(build_evalplus_test_source(task))
            base_ok, _, _ = run_tests(td)
            evalplus_exists = "test_plus_" in (td / "tests_hidden" / "test_evalplus.py").read_text()
            plus_ok = True
            if evalplus_exists:
                plus_ok, _ = _run_test_module(td, "test_evalplus")
        if base_ok and plus_ok:
            usable.append(task)
        else:
            quarantined.append(task_id)
            reason = []
            if not base_ok:
                reason.append("base")
            if not plus_ok:
                reason.append("evalplus")
            print(f"  QUARANTINED {task_id}: canonical fails its own {'+'.join(reason)} tests")
    return usable, quarantined


def main():
    print("=" * 60)
    print(f"HumanEvalPlus-CC164 Benchmark  model={MODEL}  effort={EFFORT}")
    print("=" * 60)

    claude_version = get_claude_version()
    print(f"Claude CLI version: {claude_version}")

    if not TASK_FILE.exists():
        print(f"Error: Task file not found: {TASK_FILE}")
        print("Run `python bench/generate_tasks.py` first.")
        sys.exit(1)

    task_data = json.loads(TASK_FILE.read_text())
    all_tasks = task_data["tasks"]
    print(f"Loaded {len(all_tasks)} tasks from {TASK_FILE}")

    # Pre-flight: skip tasks whose canonical fails its own tests. Currently
    # HumanEval/70 has `assert strange_sort_list([]) == [-5, 10, 0, 5]` which
    # is impossible; without this check it contributes ~0.6% noise to every
    # score.
    print("Validating canonical solutions...")
    tasks, quarantined = validate_canonicals(all_tasks)
    if quarantined:
        print(f"Skipping {len(quarantined)} quarantined task(s): {quarantined}")
    else:
        print("All canonicals pass their own tests.")

    started_at = datetime.now(timezone.utc).isoformat()
    run_id = os.environ.get("BENCH_RUN_ID") or started_at
    print(f"Run ID: {run_id}")

    task_results = []
    for i, task in enumerate(tasks):
        print(f"\n[{i+1}/{len(tasks)}]", end="")
        result = run_task(task)
        task_results.append(result)
        passed_so_far = sum(1 for r in task_results if r["passed"])
        print(f"\n  Running score: {passed_so_far}/{len(task_results)}")

    finished_at = datetime.now(timezone.utc).isoformat()

    results = aggregate_results(
        task_results, run_id, started_at, finished_at, claude_version, quarantined,
    )

    # Write output files
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = results["date"]

    # Extract HHMM from run_id so paired runs share a filename prefix.
    hhmm = run_id[11:13] + run_id[14:16]
    tag = model_tag(MODEL)
    daily_file = DATA_DIR / f"{today}-{hhmm}-{tag}.json"
    daily_file.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nWrote {daily_file}")

    # Only the primary model owns latest.json; reference-model runs show up
    # on the dashboard through history.json alone.
    if MODEL == PRIMARY_MODEL:
        latest_file = DATA_DIR / "latest.json"
        latest_file.write_text(json.dumps(results, indent=2) + "\n")
        print(f"Wrote {latest_file}")
    else:
        print(f"Skipped latest.json (non-primary model: {MODEL})")

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
