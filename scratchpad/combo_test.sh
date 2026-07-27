#!/bin/bash
# Test every feature-flag combination in demo mode locally.
cd /Users/beyza.yalavac/projects/finops-command-center
source .venv/bin/activate 2>/dev/null
PORT=8112
PASS=0; FAIL=0
for G in true false; do for A in true false; do for D in true false; do for W in true false; do
  kill %1 2>/dev/null; sleep 0.5
  USE_LIVE=false FINOPS_FEATURE_GENIE=$G FINOPS_FEATURE_AI_NARRATION=$A FINOPS_FEATURE_DQM=$D FINOPS_FEATURE_WRITEBACK=$W \
    uvicorn app:app --host 127.0.0.1 --port $PORT >/dev/null 2>&1 &
  # wait for up
  for i in $(seq 1 20); do curl -s http://127.0.0.1:$PORT/api/config >/dev/null 2>&1 && break; sleep 0.5; done
  OUT=$(python3 scratchpad/smoke.py http://127.0.0.1:$PORT tok 2>&1)
  if echo "$OUT" | grep -q "ALL GREEN"; then
    echo "OK   G=$G A=$A D=$D W=$W"; PASS=$((PASS+1))
  else
    echo "FAIL G=$G A=$A D=$D W=$W"; echo "$OUT" | grep -E "^\[FAIL\]|FAILURES" ; FAIL=$((FAIL+1))
  fi
done; done; done; done
kill %1 2>/dev/null
echo "=== combos: $PASS ok / $FAIL failed ==="
