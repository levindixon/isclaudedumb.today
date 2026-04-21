# Variance probe — investigation notes

## Question from Allen Philip
1. Are the CI deltas just temperature variance?
2. What happens with more samples?
3. Are the failing tasks actually harder?
4. How far off are the responses?

## Setup
- Harness: `bench/variance_probe.py` (reuses `run_benchmark.py` task setup/runner)
- Probed tasks: HumanEval/97, /141, /84, /154 (the top 2 losses + top 2 wins for 4.7 in CI history)
- Trials: 6 × 2 models × 4 tasks = 48
- Solutions archived under `bench/probe_solutions/<task>/<model>/trial_NN.py`

## What we verified
- **Claude CLI has no `--temperature` flag.** Can't force temp=0 from user side.
- **`--model` overrides `ANTHROPIC_MODEL` env var.** Verified with a one-shot check.
- **4.7 on HumanEval/97 produced byte-identical code across 6 trials** → systematic, not sampling.
- **"Failing tasks are harder" is false** — HumanEval/97 has a 1-line canonical, same as HumanEval/84 which 4.7 wins.
- **4.7 has a distinctive `error_max_turns` failure mode** on tasks with ambiguous specs (3/6 on /97, 3/6 on /154). 4.6 never hits max_turns.

## Unexpected finding
Isolated probe results diverge from full-benchmark history for the SAME tasks + models.
E.g. 4.6 on /97: history 6/7 pass, probe 0/6 pass. 4.7 on /154: history 7/7 pass, probe 0/6 pass.
Hypotheses (unverified):
- CI uses CLI 2.1.114; local is 2.1.116.
- Local env has `ANTHROPIC_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL=max`, etc. Some may not be overridden by flags.
- OAuth auth (local) vs `ANTHROPIC_API_KEY` (CI).
- Cache / session state.
`bench/clean_replay.sh` strips personal CLAUDE_*/ANTHROPIC_* env for a clean replay.

## Core interpretation (survives the discrepancy)
The 4.6 ↔ 4.7 delta on the CI dashboard is about **which interpretation of ambiguous specs each model prefers**, not about capacity.

HumanEval/97 case study:
- 4.7 writes: `(abs(a) % 10) * (abs(b) % 10)` — unit digit of absolute value.
- Canonical: `abs(a % 10) * abs(b % 10)` — abs of Python's signed modulo.
- Both are defensible for "product of unit digits." EvalPlus hard-coded the Python-quirk reading.
- So 4.7 is "wrong" only in the sense it chose the mathematical reading over the Python-quirk reading.

## It's a trade, not a regression
Over 7 paired CI runs:
- 4.7 loses on: /97 (7/7 vs 1/7), /141 (6/7 vs 0/7)
- 4.7 wins on: /84 (0/7 vs 6/7), /154 (0/7 vs 7/7)
This shape rules out "smaller/quantized 4.7" — capacity regressions don't swap directions.
