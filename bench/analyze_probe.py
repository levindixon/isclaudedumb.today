#!/usr/bin/env python3
"""Analyze variance probe results.

Reports:
  1. Pass-rate per (task, model) with variance measure
  2. Binomial significance test on the 4.7 vs 4.6 delta per task
  3. Unique generated solutions per (task, model) — shows *what* changed
  4. Failure mode breakdown
"""

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from math import comb
from pathlib import Path


def canonicalize(src: str) -> str:
    """Collapse whitespace so near-identical solutions cluster together."""
    # Strip docstring + blank lines, normalize whitespace
    lines = []
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') == 2 or stripped.count("'''") == 2:
                continue  # single-line docstring
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        lines.append(re.sub(r"\s+", " ", stripped))
    return "\n".join(lines)


def two_sided_fisher(a_pass, a_fail, b_pass, b_fail):
    """Fisher's exact test p-value for a 2x2 table.

    Returns p-value for H0: same pass rate between the two groups.
    Good enough for n~10; we're not publishing, just sanity-checking.
    """
    n1, n2 = a_pass + a_fail, b_pass + b_fail
    total_pass = a_pass + b_pass
    n = n1 + n2

    def prob(k):
        # P(a_pass = k) under hypergeometric
        return comb(total_pass, k) * comb(n - total_pass, n1 - k) / comb(n, n1)

    observed = prob(a_pass)
    p = 0.0
    for k in range(max(0, total_pass - n2), min(total_pass, n1) + 1):
        pk = prob(k)
        if pk <= observed + 1e-12:
            p += pk
    return min(1.0, p)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="bench/variance_results.json")
    args = parser.parse_args()

    data = json.loads(Path(args.results).read_text())
    trials = data["trials"]
    models = data["models"]
    task_ids = data["tasks"]

    # Bucket trials by (task, model)
    buckets = defaultdict(list)
    for t in trials:
        buckets[(t["task_id"], t["model"])].append(t)

    # 1. Pass rate table + significance
    print("=" * 90)
    print("PASS RATES")
    print("=" * 90)
    header = f"{'Task':<16}"
    for m in models:
        header += f"{m:<28}"
    header += "Fisher p"
    print(header)
    print("-" * 90)
    for tid in task_ids:
        row = f"{tid:<16}"
        rates = []
        for m in models:
            group = buckets[(tid, m)]
            passed = sum(1 for t in group if t["passed"])
            total = len(group)
            rate = passed / total if total else 0
            rates.append((passed, total - passed))
            row += f"{passed:>2}/{total:<2} ({rate*100:>5.1f}%)           "
        if len(rates) == 2:
            p = two_sided_fisher(*rates[0], *rates[1])
            row += f"p={p:.3f}"
        print(row)

    # 2. Unique solutions per (task, model) — are they writing the same thing
    # with temp variance, or different approaches?
    print()
    print("=" * 90)
    print("SOLUTION DIVERSITY (# of unique normalized solutions)")
    print("=" * 90)
    print(f"{'Task':<16} {'Model':<24} {'Distinct / Trials':<20} {'Top solutions':<10}")
    print("-" * 90)
    for tid in task_ids:
        for m in models:
            group = buckets[(tid, m)]
            canon_counts = Counter()
            for t in group:
                key = canonicalize(t["solution"])
                canon_counts[key] += 1
            distinct = len(canon_counts)
            # Show the two most common solution shapes and their rates
            top = ", ".join(f"{c}×" for _, c in canon_counts.most_common(3))
            print(f"{tid:<16} {m:<24} {distinct}/{len(group):<18} {top}")

    # 3. Show the actual solution contents grouped by (task, model, variant)
    print()
    print("=" * 90)
    print("SOLUTION VARIANTS")
    print("=" * 90)
    for tid in task_ids:
        print(f"\n--- {tid} ---")
        for m in models:
            group = buckets[(tid, m)]
            canon_counts = Counter()
            first_example = {}
            pass_by_canon = defaultdict(list)
            for t in group:
                key = canonicalize(t["solution"])
                canon_counts[key] += 1
                pass_by_canon[key].append(t["passed"])
                first_example.setdefault(key, t["solution"])
            print(f"\n  {m}:")
            for variant_key, count in canon_counts.most_common():
                passes = sum(pass_by_canon[variant_key])
                total = len(pass_by_canon[variant_key])
                body = _extract_body(first_example[variant_key])
                print(f"    [{count}× | {passes}/{total} pass] {body}")

    # 4. Failure modes
    print()
    print("=" * 90)
    print("FAILURE MODES")
    print("=" * 90)
    for tid in task_ids:
        for m in models:
            group = buckets[(tid, m)]
            error_types = Counter(
                t.get("error_type") or "—" for t in group if not t["passed"]
            )
            if error_types:
                print(f"  {tid} {m}: {dict(error_types)}")

    # 5. Cost + turns per (task, model)
    print()
    print("=" * 90)
    print("COST AND TURNS")
    print("=" * 90)
    print(f"{'Task':<16} {'Model':<24} {'Avg cost':<12} {'Avg turns':<12} {'Avg ms':<10}")
    for tid in task_ids:
        for m in models:
            group = buckets[(tid, m)]
            if not group:
                continue
            avg_cost = sum(t["cost_usd"] for t in group) / len(group)
            avg_turns = sum(t["num_turns"] for t in group) / len(group)
            avg_ms = sum(t["duration_ms"] for t in group) / len(group)
            print(f"{tid:<16} {m:<24} ${avg_cost:<10.3f} {avg_turns:<12.1f} {avg_ms:<10.0f}")


def _extract_body(solution: str) -> str:
    """Pull out just the return/implementation lines, skipping signature + docstring."""
    lines = solution.splitlines()
    body_start = None
    in_docstring = False
    dq = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            dq += stripped.count('"""') + stripped.count("'''")
            if dq >= 2:
                body_start = i + 1
                break
            in_docstring = True
            continue
        if in_docstring:
            continue
    if body_start is None:
        body_start = 0
    body = [l.strip() for l in lines[body_start:] if l.strip()]
    joined = " ↵ ".join(body)
    return joined[:200] + ("…" if len(joined) > 200 else "")


if __name__ == "__main__":
    main()
