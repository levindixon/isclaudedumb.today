# isclaudedumb.today

<p align="center">
  <img src="docs/claude_not_dumb.png" alt="Claude: Not Dumb" width="200" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="docs/claude_dumb.png" alt="Claude: Dumb" width="200" />
</p>

<p align="center"><b>Is Claude dumb today?</b></p>

Automated benchmark tracking Claude Code (Opus 5.5) quality on HumanEval + EvalPlus edge-case coding tasks.

## What is this?

A static site at [isclaudedumb.today](https://isclaudedumb.today) that answers one question every day: **is Claude dumb today?**

It runs the full 164-task [HumanEval](https://github.com/openai/human-eval) suite with [EvalPlus](https://github.com/evalplus/evalplus) edge-case tests via the Claude Code CLI in headless mode. Each scheduled job runs three models back-to-back on the same task set:

- **Opus 5.5** — the primary model the verdict tracks (primary since 2026-09-23)
- **Opus 5** — a reference baseline, the prior flagship
- **Opus 4.8** — a second reference baseline, so drift in Opus 5.5 can be distinguished from a bad day across the fleet

Opus 4.7 and 4.6 were reference baselines until 2026-07-28 and are no longer run. Their historical results stay in `history.json` and on the chart.

All scheduled runs use `--effort high`. GitHub Actions runs twice daily (7 AM GMT and 7 AM PST), commits results as JSON, and GitHub Pages serves a dashboard that visualizes the data — including a per-task divergence view highlighting tasks where the models disagree. One-off runs at other effort levels go through the [effort probe workflow](#effort-probes); they are tagged and kept out of the verdict.

## How the benchmark works

1. **164 HumanEval tasks** (HumanEval/0–163) are presented to Claude Code one at a time
2. Each task gives Claude a function signature + docstring in `solution.py` and asks it to implement the function
3. Claude has **no shell access** (`Bash`, `WebFetch`, `WebSearch`, `Task`, `NotebookEdit`, `Write` are disabled) — it can only Read, Edit, Glob, and Grep
4. Claude **cannot see the tests** (`.claude/settings.json` denies read access to `tests_hidden/`)
5. After Claude finishes, the harness runs hidden unit tests — both the original HumanEval tests and ~16 [EvalPlus](https://github.com/evalplus/evalplus) edge-case tests per task (empty inputs, large inputs, boundary conditions, etc.)
6. Each task records two pass flags: **Base** (original HumanEval tests) and **EvalPlus** (edge cases). A task counts as passing only when both pass. The dashboard shows the combined score plus the per-side breakdown, so edge-case regressions stand out from overall quality drops.

### Verdict logic

The site compares the latest Opus 5.5 run against a rolling average of the prior 14 Opus 5.5 entries (≈ 7 days at 2 runs/day). Runs from any other model are kept out of the verdict window so baselines can't skew the average:
- **YES** (dumb): score is 5+ points below the average
- **MAYBE**: score is 2–5 points below the average
- **NO** (not dumb): score is no more than 2 points below the average

### Divergence view

The dashboard also flags HumanEval tasks where the currently-benchmarked models (Opus 5.5, Opus 5, Opus 4.8) have different pass rates over the recent window, sorted by *spread* — the gap between the best- and worst-performing model on that task. A task that's consistently green for one model and red for another reveals a real tradeoff, not noise — e.g. `HumanEval/97` (Python signed-modulo semantics) or `HumanEval/141` (Unicode `.isalpha()` vs literal `a–z` range).

This view is scoped to `ACTIVE_MODELS` in `docs/app.js` rather than everything in history: it advertises "recent paired runs", and a retired model's frozen final window would read as current. Retired models remain on the score-history chart.

### Safety constraints

| Constraint | Value |
|---|---|
| Primary model | `claude-opus-5-5` |
| Reference baselines | `claude-opus-5`, `claude-opus-4-8` |
| Thinking effort | `high` on scheduled runs (probes at other levels are tagged and excluded from the verdict) |
| Max turns per attempt | 3 |
| Max cost per attempt | $1.00 |
| Max attempts per task | 1 |
| Allowed tools | Read, Edit, Glob, Grep |
| Test visibility | Denied via permissions |

### Effort probes

The verdict only means something if the configuration stays fixed, so scheduled runs are pinned to `--effort high`. To measure what another effort level does, run the `Effort Probe` workflow by hand:

```bash
gh workflow run effort-probe.yml -f model=claude-opus-5-5 -f efforts='["medium","xhigh","max"]' -f repeats=3
```

Each probe run writes `YYYY-MM-DD-HHMM-<tag>-<effort>.json` and a `history.json` row with an `effort` field. Probe rows never overwrite `latest.json`, and the dashboard drops every non-`high` row before computing the verdict, chart, or divergence table. Rows without an `effort` field predate probes and were all `high` runs.

The probe shares the scheduled benchmark's concurrency group, so the two never push at the same time. GitHub keeps only one queued run per group: start a probe when no scheduled run is waiting, or the queued one gets cancelled.

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
  app.js                  # Fetches JSON, renders verdict/chart/divergence/table
  CNAME                   # Custom domain
  data/                   # Benchmark results (auto-committed by CI)
    latest.json           # Most recent primary-model (Opus 5.5) run's full results
    history.json          # Summary rows for charting (keyed by run_id, tagged per model)
    YYYY-MM-DD-HHMM-<tag>.json  # Per-run snapshots, e.g. -opus55 / -opus5 / -opus48 (6 files/day)
    YYYY-MM-DD-HHMM-<tag>-<effort>.json  # Effort-probe snapshots, e.g. -opus55-xhigh
.github/workflows/
  benchmark.yml           # Twice-daily cron + manual trigger
  effort-probe.yml        # Manual: one model at chosen effort levels, N repeats each
```

## Methodology note

This benchmark uses Claude Code CLI (`--effort high`, `--permission-mode acceptEdits`) with a standard Anthropic API key (pay-as-you-go). Each scheduled job runs `claude-opus-5-5` (primary), `claude-opus-5`, and `claude-opus-4-8` (reference baselines) against the same task workspaces, sharing a single `run_id` so the runs are directly comparable. All raw results are published as JSON for full transparency.

HumanEval tasks are from OpenAI's [human-eval](https://github.com/openai/human-eval) dataset (MIT license). Edge-case tests are from [EvalPlus](https://github.com/evalplus/evalplus) (Apache-2.0 license).
