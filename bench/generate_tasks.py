#!/usr/bin/env python3
"""Download HumanEval and generate the HumanEvalPlus-CC164 benchmark task suite.

Creates bench/data/humaneval_plus_cc164.json with all 164 HumanEval tasks
plus EvalPlus edge-case tests, and sets up workspace directories for each
task with:
  - prompt.md (problem statement)
  - solution.py (stub with function signature)
  - tests_hidden/test_solution.py (unittest-based tests + EvalPlus tests)
  - .claude/settings.json (deny read on tests_hidden)
"""

import gzip
import json
import signal
import shutil
import urllib.request
from pathlib import Path

HUMANEVAL_URL = (
    "https://github.com/openai/human-eval/raw/master/data/HumanEval.jsonl.gz"
)
BENCH_DIR = Path(__file__).resolve().parent
DATA_FILE = BENCH_DIR / "data" / "humaneval_plus_cc164.json"
WORKSPACE_DIR = BENCH_DIR / "workspace"
NUM_TASKS = 164


def download_humaneval() -> list[dict]:
    """Download HumanEval.jsonl.gz and return parsed tasks."""
    print("Downloading HumanEval dataset...")
    req = urllib.request.Request(HUMANEVAL_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
    lines = gzip.decompress(raw).decode("utf-8").strip().split("\n")
    tasks = [json.loads(line) for line in lines]
    print(f"Downloaded {len(tasks)} tasks")
    return tasks


def compute_evalplus_tests(task: dict, evalplus_problem: dict) -> list[dict]:
    """Pre-compute EvalPlus edge-case test assertions for a task.

    Executes the canonical solution against each EvalPlus plus_input,
    captures the expected output, and formats it as an assertion string.
    Only uses plus_input (not base_input, which overlaps with original tests).

    Returns a list of dicts with key: assert_str
    """
    # Build the function from prompt + canonical solution
    func_code = task["prompt"] + task["canonical_solution"]
    entry_point = task["entry_point"]

    # Execute to define the function
    namespace = {}
    try:
        exec(func_code, namespace)
    except Exception:
        return []

    func = namespace.get(entry_point)
    if func is None:
        return []

    plus_inputs = evalplus_problem.get("plus_input", [])
    atol = evalplus_problem.get("atol", 0)

    # Timeout handler for long-running canonical executions (large inputs)
    def _timeout_handler(signum, frame):
        raise TimeoutError

    tests = []
    for inp in plus_inputs:
        try:
            # 5-second timeout per input to avoid hanging on O(n²+) solutions
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(5)
            try:
                result = func(*inp)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            # Build assertion string using repr for exact round-trip
            args_str = ", ".join(repr(a) for a in inp)
            if atol and isinstance(result, float):
                assert_str = f"assert abs({entry_point}({args_str}) - {repr(result)}) <= {atol}"
            else:
                assert_str = f"assert {entry_point}({args_str}) == {repr(result)}"
            tests.append({"assert_str": assert_str})
        except Exception:
            # Skip inputs that cause errors or timeout
            continue

    return tests


def transform_tests(task: dict, evalplus_tests: list[dict] | None = None) -> str:
    """Transform HumanEval check(candidate) tests into unittest format.

    HumanEval tests look like:
        def check(candidate):
            assert candidate(...) == ...
            ...

    We transform these into:
        import unittest
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from solution import *

        class TestSolution(unittest.TestCase):
            def test_0(self):
                assert entry_point(...) == ...
            ...

    If evalplus_tests are provided, they are appended as test_plus_N methods.

    Uses `from solution import *` because some tasks have helper functions
    (e.g., encode_cyclic) defined in the prompt that tests also reference.
    """
    entry_point = task["entry_point"]
    test_code = task["test"]

    # Extract the body of check(candidate) function
    # Find lines after "def check(candidate):" and before METADATA or end
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
            # Keep empty lines to preserve structure
            if stripped:
                # Replace 'candidate' with the actual function name
                transformed = line.replace("candidate", entry_point)
                # De-indent one level (remove first 4 spaces)
                if transformed.startswith("    "):
                    transformed = transformed[4:]
                check_body.append(transformed)

    # Determine if this is a "simple" test (only assert statements at top level)
    # or a "complex" test (has setup code, loops, imports, etc.)
    top_level_lines = [l for l in check_body if not l.startswith(" ")]
    is_simple = all(l.startswith("assert") for l in top_level_lines if l.strip())

    if is_simple and check_body:
        # Split each assert into its own test method for granularity
        test_methods = []
        method_idx = 0
        current_lines = []

        for line in check_body:
            if line.startswith("assert"):
                if current_lines:
                    test_methods.append((method_idx, current_lines))
                    method_idx += 1
                    current_lines = []
                current_lines.append(line)
            else:
                # Continuation of multi-line assert
                current_lines.append(line)

        if current_lines:
            test_methods.append((method_idx, current_lines))
    else:
        # Complex test: keep everything in one test method
        test_methods = [(0, check_body)] if check_body else [(0, ["pass"])]

    methods_code = []
    for idx, method_lines in test_methods:
        body = "\n        ".join(method_lines)
        methods_code.append(f"    def test_{idx}(self):\n        {body}")

    # Append EvalPlus edge-case tests
    if evalplus_tests:
        for i, test in enumerate(evalplus_tests):
            methods_code.append(f"    def test_plus_{i}(self):\n        {test['assert_str']}")

    methods_str = "\n\n".join(methods_code)

    test_file = f"""\
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
    return test_file


def build_prompt_md(task: dict) -> str:
    """Build the prompt.md file content for a task."""
    return f"""\
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


def build_solution_stub(task: dict) -> str:
    """Build the solution.py stub with function signature and pass."""
    prompt = task["prompt"]
    # The prompt typically ends with the function signature + docstring
    # We add 'pass' as placeholder body
    return prompt.rstrip() + "\n    pass\n"


def setup_workspace(task: dict, test_code: str) -> None:
    """Create workspace directory structure for a task."""
    task_dir_name = task["task_id"].replace("/", "_")
    workspace = WORKSPACE_DIR / task_dir_name

    # Clean existing workspace
    if workspace.exists():
        shutil.rmtree(workspace)

    # Create directories
    workspace.mkdir(parents=True)
    (workspace / "tests_hidden").mkdir()
    (workspace / ".claude").mkdir()

    # Write files
    (workspace / "prompt.md").write_text(build_prompt_md(task))
    (workspace / "solution.py").write_text(build_solution_stub(task))
    (workspace / "tests_hidden" / "test_solution.py").write_text(test_code)
    (workspace / "tests_hidden" / "__init__.py").write_text("")

    # Claude settings: deny read on tests_hidden
    settings = {
        "permissions": {
            "deny": ["Read(tests_hidden/**)"]
        }
    }
    (workspace / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n"
    )


def main():
    # Download and select first NUM_TASKS tasks
    all_tasks = download_humaneval()
    selected = all_tasks[:NUM_TASKS]

    # Load EvalPlus edge-case inputs
    from evalplus.data import get_human_eval_plus
    evalplus_data = get_human_eval_plus(mini=True)
    print(f"Loaded EvalPlus data for {len(evalplus_data)} tasks (mini=True)")

    # Build the CC164 dataset with EvalPlus tests
    cc164_tasks = []
    total_plus_tests = 0
    for task in selected:
        task_id = task["task_id"]
        evalplus_problem = evalplus_data.get(task_id, {})
        evalplus_tests = compute_evalplus_tests(task, evalplus_problem)
        total_plus_tests += len(evalplus_tests)

        cc164_tasks.append(
            {
                "task_id": task_id,
                "entry_point": task["entry_point"],
                "prompt": task["prompt"],
                "canonical_solution": task["canonical_solution"],
                "test": task["test"],
                "evalplus_tests": evalplus_tests,
            }
        )

    print(f"Pre-computed {total_plus_tests} EvalPlus edge-case tests across {NUM_TASKS} tasks")

    cc164 = {
        "suite_name": "HumanEvalPlus-CC164",
        "source": "https://github.com/openai/human-eval",
        "evalplus_source": "https://github.com/evalplus/evalplus",
        "license": "MIT / Apache-2.0",
        "task_count": NUM_TASKS,
        "task_ids": [t["task_id"] for t in cc164_tasks],
        "tasks": cc164_tasks,
    }

    # Save dataset JSON
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(cc164, indent=2) + "\n")
    print(f"Saved {NUM_TASKS} tasks to {DATA_FILE}")

    # Create workspaces
    print("Setting up workspaces...")
    for task in cc164_tasks:
        test_code = transform_tests(task, evalplus_tests=task.get("evalplus_tests"))
        setup_workspace(task, test_code)
        plus_count = len(task.get("evalplus_tests", []))
        print(f"  {task['task_id']}: workspace ready (+{plus_count} evalplus tests)")

    print(f"\nDone. {NUM_TASKS} task workspaces created in {WORKSPACE_DIR}")


if __name__ == "__main__":
    main()
