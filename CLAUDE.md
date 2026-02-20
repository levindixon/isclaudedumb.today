# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An automated benchmark that runs 164 HumanEval coding tasks with EvalPlus edge-case tests against the Claude Code CLI (Opus 4.6) and publishes results to [isclaudedumb.today](https://isclaudedumb.today). GitHub Actions runs the benchmark twice daily (7 AM GMT / 7 AM PST), commits JSON results, and GitHub Pages serves a dashboard.

## Commands

```bash
# Generate task workspaces (downloads HumanEval dataset, creates bench/workspace/)
python bench/generate_tasks.py

# Run the full benchmark (requires ANTHROPIC_API_KEY env var)
ANTHROPIC_API_KEY=sk-... python bench/run_benchmark.py
```

Results are written to `docs/data/` as `YYYY-MM-DD-HHMM.json` (per-run), `latest.json`, and appended to `history.json` (with `run_id` timestamps for dedup).

## Architecture

**Benchmark harness** (`bench/`):
- `generate_tasks.py` — Downloads HumanEval dataset + EvalPlus edge-case inputs, pre-computes test assertions, creates per-task workspace directories under `bench/workspace/` with `prompt.md`, `solution.py` stub, hidden tests (original + EvalPlus), and a `.claude/settings.json` that denies Read access to `tests_hidden/`
- `run_benchmark.py` — Iterates all 164 tasks, invokes `claude -p --model opus` in headless mode per workspace, runs hidden unit tests. Each run outputs a timestamped `YYYY-MM-DD-HHMM.json` file and appends to `history.json` keyed by `run_id` (ISO timestamp)
- `data/humaneval_plus_cc164.json` — Pre-generated dataset (164 tasks with prompts, canonical solutions, original tests, and EvalPlus edge-case tests)

**Static dashboard** (`docs/`):
- Vanilla HTML/CSS/JS site served by GitHub Pages
- `app.js` fetches `data/latest.json` and `data/history.json`, computes a verdict (YES/MAYBE/NO) by comparing today's score against a 7-day rolling average, renders a Chart.js line chart and a sortable per-task results table
- Dark theme, responsive, no build step

**CI** (`.github/workflows/benchmark.yml`):
- Cron twice daily (7 AM UTC, 3 PM UTC) + manual `workflow_dispatch`
- Installs Claude Code CLI, generates workspaces, runs benchmark, commits results to `docs/data/`

## Key Design Constraints

- Claude gets only `Read`, `Edit`, `Glob`, and `Grep` tools (Bash, WebFetch, WebSearch, Task, NotebookEdit, Write are disabled via `--disallowedTools`)
- Tests are hidden from Claude via permission deny rules in each workspace's `.claude/settings.json`
- Each task: max 3 turns, max $1.00 budget, 1 attempt (no retry)
- `--permission-mode acceptEdits` auto-approves file edits

## Verdict Logic

The dashboard compares the latest run's score against a rolling average of the prior 14 entries (≈ 7 days at 2 runs/day):
- **YES** (dumb): score is 5+ points below average
- **MAYBE**: score is 2–5 points below average
- **NO** (not dumb): score is no more than 2 points below average
