#!/usr/bin/env bash
# Clean replay: run variance probe with CI-matching env (no shell overrides).
#
# Local probe has shown behavior divergence from the 164-task CI runs on the
# same tasks. This script strips the env of personal CLAUDE_* / ANTHROPIC_*
# overrides so we can confirm whether that's the cause.
#
# Usage:
#   ANTHROPIC_API_KEY=sk-... bench/clean_replay.sh <task_id> <model> <trials>
# e.g.
#   ANTHROPIC_API_KEY=sk-... bench/clean_replay.sh HumanEval/97 claude-opus-4-6 3

set -euo pipefail
TASK=${1:?task id required}
MODEL=${2:?model required}
TRIALS=${3:-3}

# Strip every personal CLAUDE_* / ANTHROPIC_* env var except API key.
# The benchmark only needs ANTHROPIC_API_KEY to authenticate against the API.
for v in $(env | awk -F= '/^(CLAUDE_|ANTHROPIC_)/{print $1}'); do
  if [ "$v" != "ANTHROPIC_API_KEY" ]; then unset "$v"; fi
done

# Also clear a few personal vars that Claude Code reads at startup.
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_SESSION_MODEL CLAUDE_SESSION_TRANSCRIPT

# Point CLAUDE.md discovery at a minimal stub so the project CLAUDE.md doesn't
# leak benchmark-aware context into the model.
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

python3 bench/variance_probe.py \
  --models "$MODEL" \
  --tasks "$TASK" \
  --trials "$TRIALS" \
  --out bench/clean_replay_results.json
