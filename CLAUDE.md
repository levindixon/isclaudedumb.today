# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A daily automated benchmark that runs 40 HumanEval coding tasks against the Claude Code CLI and publishes results to [isclaudedumb.today](https://isclaudedumb.today). GitHub Actions runs the benchmark at 6 AM UTC, commits JSON results, and GitHub Pages serves a dashboard.

## Commands

```bash
# Generate task workspaces (downloads HumanEval dataset, creates bench/workspace/)
python bench/generate_tasks.py

# Run the full benchmark (requires ANTHROPIC_API_KEY env var)
ANTHROPIC_API_KEY=sk-... python bench/run_benchmark.py
```

Results are written to `docs/data/` as `YYYY-MM-DD.json`, `latest.json`, and appended to `history.json`.

## Architecture

**Benchmark harness** (`bench/`):
- `generate_tasks.py` — Downloads HumanEval dataset, creates per-task workspace directories under `bench/workspace/` with `prompt.md`, `solution.py` stub, hidden tests, and a `.claude/settings.json` that denies Read access to `tests_hidden/`
- `run_benchmark.py` — Iterates all 40 tasks, invokes `claude -p` in headless mode per workspace, runs hidden unit tests, retries once on failure with test output as feedback. Outputs aggregated JSON to `docs/data/`
- `data/humaneval_cc40.json` — Pre-generated dataset (40 tasks with prompts, canonical solutions, and tests)

**Static dashboard** (`docs/`):
- Vanilla HTML/CSS/JS site served by GitHub Pages
- `app.js` fetches `data/latest.json` and `data/history.json`, computes a verdict (YES/MAYBE/NO) by comparing today's score against a 7-day rolling average, renders a Chart.js line chart and a sortable per-task results table
- Dark theme, responsive, no build step

**CI** (`.github/workflows/benchmark.yml`):
- Daily cron at 6 AM UTC + manual `workflow_dispatch`
- Installs Claude Code CLI, generates workspaces, runs benchmark, commits results to `docs/data/`

## Key Design Constraints

- Claude gets only `Read` and `Edit` tools (Bash, WebFetch, WebSearch, Write, etc. are disabled via `--disallowedTools`)
- Tests are hidden from Claude via permission deny rules in each workspace's `.claude/settings.json`
- Each task: max 6 turns, max $0.10 budget, max 2 attempts (second attempt includes test failure output)
- `--permission-mode acceptEdits` auto-approves file edits

## Verdict Logic

The dashboard compares today's score against the 7-day rolling average (excluding today):
- **YES** (dumb): score is 5+ points below average
- **MAYBE**: score is 2–5 points below average
- **NO** (not dumb): score is within 2 points of average
