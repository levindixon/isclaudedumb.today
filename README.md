# isclaudedumb.today

Daily automated benchmark tracking Claude Code's default model quality.

## What this is

A static site at [isclaudedumb.today](https://isclaudedumb.today) that answers one question every day: **has Claude Code's default model gotten worse?**

It runs a fixed 40-task subset of [HumanEval](https://github.com/openai/human-eval) (MIT-licensed) via the Claude Code CLI in headless mode. GitHub Actions runs the benchmark daily at 6 AM UTC, commits results as JSON, and GitHub Pages serves a dashboard that visualizes the data.

## How the benchmark works

1. **40 HumanEval tasks** (HumanEval/0–39) are presented to Claude Code one at a time
2. Each task gives Claude a function signature + docstring in `solution.py` and asks it to implement the function
3. Claude has **no shell access** (`Bash`, `WebFetch`, `WebSearch`, etc. are disabled) — it can only Read and Edit files
4. Claude **cannot see the tests** (`.claude/settings.json` denies read access to `tests_hidden/`)
5. After Claude finishes, the harness runs hidden unit tests
6. On failure, Claude gets one retry attempt with the test output as feedback
7. Results are scored as pass/fail per task, aggregated into a daily score (0–100%)

### Verdict logic

The site compares today's score against the 7-day rolling average:
- **YES** (dumb): score is 5+ points below the average
- **MAYBE**: score is 2–5 points below the average
- **NO** (not dumb): score is within 2 points of the average

### Safety constraints

| Constraint | Value |
|---|---|
| Max turns per attempt | 6 |
| Max cost per attempt | $0.10 |
| Max attempts per task | 2 |
| Allowed tools | Read, Edit only |
| Test visibility | Denied via permissions |
| Worst-case daily cost | ~$8 (typical: $2–4) |

## Setup

### Prerequisites

- GitHub repository with Actions enabled
- An Anthropic API key (pay-as-you-go)

### 1. Add API key

Go to **Settings > Secrets and variables > Actions > New repository secret**

- Name: `ANTHROPIC_API_KEY`
- Value: your Anthropic API key

### 2. Enable GitHub Pages

Go to **Settings > Pages**

- Source: **Deploy from a branch**
- Branch: `main`
- Folder: `/docs`

### 3. Point domain (optional)

Add a CNAME record: `isclaudedumb.today` → `<username>.github.io`

Then enable **Enforce HTTPS** in Pages settings.

### 4. Run locally

```bash
# Generate task workspaces (downloads HumanEval dataset)
python bench/generate_tasks.py

# Run the benchmark (requires ANTHROPIC_API_KEY env var)
ANTHROPIC_API_KEY=sk-... python bench/run_benchmark.py
```

Results are written to `docs/data/`.

## Project structure

```
bench/
  generate_tasks.py       # Downloads HumanEval, creates task workspaces
  run_benchmark.py        # Main benchmark harness
  data/
    humaneval_cc40.json   # Pre-generated 40-task dataset
docs/
  index.html              # Dashboard page
  style.css               # Dark-theme styles
  app.js                  # Fetches JSON, renders verdict/chart/table
  CNAME                   # Custom domain
  data/                   # Benchmark results (auto-committed by CI)
    latest.json           # Today's full results
    history.json          # Summary rows for charting
    YYYY-MM-DD.json       # Daily snapshots
.github/workflows/
  benchmark.yml           # Daily cron + manual trigger
```

## Cost

Typical daily run: **$2–4**. Worst case (all tasks fail + retry at max budget): ~$8.

The benchmark uses `--max-budget-usd 0.10` per invocation and `--max-turns 6`, so costs are bounded.

## Methodology note

This benchmark uses Claude Code CLI with a standard Anthropic API key (pay-as-you-go). The model used is whatever Claude Code's default model is on the day of the run. All raw results are published as JSON for full transparency.

HumanEval tasks are from OpenAI's [human-eval](https://github.com/openai/human-eval) dataset, released under the MIT license.
