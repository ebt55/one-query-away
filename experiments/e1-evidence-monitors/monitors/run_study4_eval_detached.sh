#!/usr/bin/env bash
# Detached wrapper (not hashed): runs the frozen run_study4_eval.sh independently of the Claude session.
cd "/c/Users/ebin/claude-ground/apart-research/swarm-allignment-nov-2026/experiments" || exit 2
bash e1-evidence-monitors/monitors/run_study4_eval.sh > e1-evidence-monitors/study4_eval.log 2>&1
echo "run_study4_eval.sh exit=$?" >> e1-evidence-monitors/study4_eval.log
