#!/usr/bin/env python3
"""One-off backfill: add per-task bitmaps + sub-scores to history.json.

Historical per-run JSONs have task-level pass/fail but history.json only
stored the headline score. The new dashboard's divergence view needs
bitmaps; without a backfill it would start empty for ~2 weeks until fresh
runs accumulate. This script walks every *-opusXX.json under docs/data and
populates the bitmap fields on the matching history entry.

Safe to run multiple times: it only ADDS fields to entries that lack them.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"


def infer_passed_base(task: dict) -> bool:
    """Legacy entries don't record the base/evalplus split — fall back to
    the combined `passed` flag when the per-run file pre-dates the split."""
    if "passed_base" in task:
        return bool(task["passed_base"])
    return bool(task.get("passed", False))


def infer_passed_plus(task: dict) -> bool:
    if "passed_evalplus" in task:
        return bool(task["passed_evalplus"])
    # Before the split we only know "both pass." If both passed then
    # evalplus passed; if the combined failed, we can't tell which bucket
    # caused it — conservatively assume evalplus failed so the view leans
    # toward surfacing tasks as "possibly interpretation-driven."
    return bool(task.get("passed", False))


def bitmap(flags):
    return "".join("1" if f else "0" for f in flags)


def main():
    history_file = DATA_DIR / "history.json"
    history = json.loads(history_file.read_text()) if history_file.exists() else {"entries": []}

    # Index history entries by (run_id, primary_model) for fast updates
    index = {}
    for e in history["entries"]:
        key = (e.get("run_id", e["date"]), e.get("primary_model"))
        index[key] = e

    touched = 0
    for per_run in sorted(DATA_DIR.glob("*.json")):
        if per_run.name in ("history.json", "latest.json"):
            continue
        try:
            data = json.loads(per_run.read_text())
        except json.JSONDecodeError:
            continue
        if "tasks" not in data or "primary_model" not in data:
            continue

        tasks = data["tasks"]
        task_ids = [t["task_id"] for t in tasks]
        base_flags = [infer_passed_base(t) for t in tasks]
        plus_flags = [infer_passed_plus(t) for t in tasks]

        passed_base = sum(base_flags)
        passed_plus = sum(plus_flags)
        total = len(tasks)

        key = (data.get("run_id", data.get("date")), data.get("primary_model"))
        entry = index.get(key)
        if entry is None:
            continue

        updated = False
        if "task_ids" not in entry:
            entry["task_ids"] = task_ids
            updated = True
        if "pass_bitmap_base" not in entry:
            entry["pass_bitmap_base"] = bitmap(base_flags)
            updated = True
        if "pass_bitmap_evalplus" not in entry:
            entry["pass_bitmap_evalplus"] = bitmap(plus_flags)
            updated = True
        if "score_base" not in entry and total:
            entry["score_base"] = round((passed_base / total) * 100, 1)
            entry["score_evalplus"] = round((passed_plus / total) * 100, 1)
            entry["passed_base"] = passed_base
            entry["passed_evalplus"] = passed_plus
            updated = True
        if updated:
            touched += 1

    history_file.write_text(json.dumps(history, indent=2) + "\n")
    print(f"Backfilled {touched} history entries with bitmaps + sub-scores")
    print(f"Wrote {history_file}")


if __name__ == "__main__":
    main()
