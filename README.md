# isclaudedumb.today

Automated benchmark tracking Claude Code (Opus 4.6) quality on HumanEval + EvalPlus edge-case coding tasks.

## What this is

A static site at [isclaudedumb.today](https://isclaudedumb.today) that answers one question every day: **has Claude Code's default model gotten worse?**

It runs the full 164-task [HumanEval](https://github.com/openai/human-eval) suite with [EvalPlus](https://github.com/evalplus/evalplus) edge-case tests via the Claude Code CLI (`--model opus`) in headless mode. GitHub Actions runs the benchmark every 8 hours, commits results as JSON, and GitHub Pages serves a dashboard that visualizes the data.

## How the benchmark works

1. **164 HumanEval tasks** (HumanEval/0–163) are presented to Claude Code one at a time
2. Each task gives Claude a function signature + docstring in `solution.py` and asks it to implement the function
3. Claude has **no shell access** (`Bash`, `WebFetch`, `WebSearch`, etc. are disabled) — it can only Read and Edit files
4. Claude **cannot see the tests** (`.claude/settings.json` denies read access to `tests_hidden/`)
5. After Claude finishes, the harness runs hidden unit tests — both the original HumanEval tests and ~16 [EvalPlus](https://github.com/evalplus/evalplus) edge-case tests per task (empty inputs, large inputs, boundary conditions, etc.)
6. Results are scored as pass/fail per task, aggregated into a per-run score (0–100%)

### Verdict logic

The site compares the latest run's score against a rolling average of the prior 21 entries (≈ 7 days at 3 runs/day):
- **YES** (dumb): score is 5+ points below the average
- **MAYBE**: score is 2–5 points below the average
- **NO** (not dumb): score is within 2 points of the average

### Safety constraints

| Constraint | Value |
|---|---|
| Max turns per attempt | 3 |
| Max cost per attempt | $1.00 |
| Max attempts per task | 1 |
| Allowed tools | Read, Edit only |
| Test visibility | Denied via permissions |
| Worst-case cost per run | ~$164 (typical: $10–12) |

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
# Install EvalPlus (needed for dataset generation)
pip install evalplus

# Generate task workspaces (downloads HumanEval + EvalPlus datasets)
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
    humaneval_plus_cc164.json  # Pre-generated 164-task dataset with EvalPlus tests
docs/
  index.html              # Dashboard page
  style.css               # Dark-theme styles
  app.js                  # Fetches JSON, renders verdict/chart/table
  CNAME                   # Custom domain
  data/                   # Benchmark results (auto-committed by CI)
    latest.json           # Most recent run's full results
    history.json          # Summary rows for charting (keyed by run_id)
    YYYY-MM-DD-HHMM.json # Per-run snapshots (3x daily)
.github/workflows/
  benchmark.yml           # Every-8-hours cron + manual trigger
```

## Cost

Typical run: **$10–12**. Worst case (all 164 tasks at max budget): ~$164.

The benchmark uses `--model opus`, `--max-budget-usd 1.00` per invocation and `--max-turns 3`, so costs are bounded. Runs 3x daily (~$600–900/month).

## Methodology note

This benchmark uses Claude Code CLI with `--model opus` and a standard Anthropic API key (pay-as-you-go). All raw results are published as JSON for full transparency.

HumanEval tasks are from OpenAI's [human-eval](https://github.com/openai/human-eval) dataset (MIT license). Edge-case tests are from [EvalPlus](https://github.com/evalplus/evalplus) (Apache-2.0 license).
