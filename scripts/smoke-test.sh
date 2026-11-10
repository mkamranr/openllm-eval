#!/usr/bin/env bash
# Post-deployment smoke test for a Docker Compose installation.
#
#   ./scripts/smoke-test.sh                       # defaults to localhost
#   API_URL=http://server:8000 FRONTEND_URL=http://server:3000 ./scripts/smoke-test.sh
#
# Checks: migrations exited 0, API liveness/readiness (DB + Redis + worker), version, dataset
# import/export hash, frontend served, /api proxied and, with the mock model server, a benchmark run. Exits non-zero on the first failure.
set -euo pipefail

# Read published ports from .env (next to docker-compose.yml) unless URLs are given explicitly.
ENV_FILE="${ENV_FILE:-$(dirname "$0")/../.env}"
env_value() {
  [[ -f "$ENV_FILE" ]] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d "\"'\r"
}
API_PORT="$(env_value API_PORT)"
FRONTEND_PORT="$(env_value FRONTEND_PORT)"
API_URL="${API_URL:-http://localhost:${API_PORT:-8000}}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:${FRONTEND_PORT:-3000}}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-180}"
COMPOSE="${COMPOSE:-docker compose}"

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; exit 1; }

wait_for() {
  local description="$1" check="$2" deadline=$((SECONDS + TIMEOUT_SECONDS))
  until eval "$check" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then fail "$description (timed out after ${TIMEOUT_SECONDS}s)"; fi
    sleep 2
  done
  pass "$description"
}

echo "OpenLLM-Eval smoke test"
echo "  API:      $API_URL"
echo "  Frontend: $FRONTEND_URL"

if command -v docker >/dev/null 2>&1 && [[ "${SKIP_COMPOSE_CHECKS:-0}" != "1" ]]; then
  wait_for "migrate service finished" \
    "[[ \"\$($COMPOSE ps -a migrate --format '{{.State}}')\" == exited ]]"
  code="$($COMPOSE ps -a migrate --format '{{.ExitCode}}')"
  [[ "$code" == "0" ]] && pass "migrations applied (exit 0)" || fail "migrate exited with code $code"
fi

wait_for "API liveness ($API_URL/api/v1/health)" "curl -fsS '$API_URL/api/v1/health'"
wait_for "API readiness: database and redis ok" \
  "curl -fsS '$API_URL/api/v1/health/ready' | grep -q '^{\"status\":\"ok\"'"
wait_for "worker heartbeat visible" \
  "curl -fsS '$API_URL/api/v1/health/ready' | grep -q '\"active_workers\":[1-9]'"

ready="$(curl -fsS "$API_URL/api/v1/health/ready")"
echo "    readiness: $ready"

version="$(curl -fsS "$API_URL/api/v1/version")" || fail "version endpoint"
pass "version: $version"

curl -fsS "$API_URL/api/openapi.json" | grep -q '"/api/v1/health/ready"' \
  && pass "OpenAPI schema served locally" || fail "OpenAPI schema"

wait_for "frontend served ($FRONTEND_URL)" "curl -fsS '$FRONTEND_URL/' | grep -q '<div id=\"root\">'"
curl -fsS "$FRONTEND_URL/runs" | grep -q '<div id="root">' \
  && pass "frontend SPA fallback routes" || fail "SPA fallback for /runs"
curl -fsS "$FRONTEND_URL/api/v1/health/ready" | grep -q '"database"' \
  && pass "frontend proxies /api to the API" || fail "frontend /api proxy"

# --- Datasets: import (multipart), export, verify the content hash, delete ---
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1; else shasum -a 256 | cut -d' ' -f1; fi; }
ds_name="smoke-dataset-$RANDOM$RANDOM"
ds_file="$(mktemp)"
printf 'id,input,reference_answer,tags\nq1,What is DNS?,It maps names to addresses,net\nq2,What is TCP?,A reliable transport,"net,basic"\n' > "$ds_file"
created="$(curl -sS -X POST "$API_URL/api/v1/datasets" -F "name=$ds_name" -F dataset_type=qa \
  -F "file=@$ds_file;filename=smoke.csv")"
rm -f "$ds_file"
ds_id="$(printf '%s' "$created" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
if [[ -z "$ds_id" ]]; then
  echo "    response: $created"
  fail "dataset import (is this an older API without dataset support? rebuild with --build)"
fi
stored_hash="$(printf '%s' "$created" | sed -n 's/.*"content_hash":"\([0-9a-f]\{64\}\)".*/\1/p')"
export_hash="$(curl -fsS "$API_URL/api/v1/datasets/$ds_id/export" | sha256)"
curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/datasets/$ds_id"
[[ -n "$stored_hash" && "$stored_hash" == "$export_hash" ]] \
  && pass "dataset import/export: sha256 ${stored_hash:0:16}… reproducible" \
  || fail "dataset export hash ($export_hash) != stored hash ($stored_hash)"
curl -fsS "$API_URL/api/v1/datasets/samples" | grep -q '"general-qa"' \
  && pass "bundled sample datasets available" || fail "sample datasets"

# --- Optional: end-to-end endpoint test and benchmark run against the mock model server ---
# Runs when the mock-llm service is up (docker compose --profile mock up -d) or SMOKE_MOCK=1.
mock_running=""
if command -v docker >/dev/null 2>&1 && [[ "${SKIP_COMPOSE_CHECKS:-0}" != "1" ]]; then
  mock_running="$($COMPOSE ps --status running -q mock-llm 2>/dev/null || true)"
fi
if [[ -n "$mock_running" || "${SMOKE_MOCK:-0}" == "1" ]]; then
  MOCK_BASE_URL="${MOCK_BASE_URL:-http://mock-llm:9000/v1}"
  name="smoke-mock-$RANDOM$RANDOM"
  created="$(curl -sS -X POST "$API_URL/api/v1/endpoints" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$name\",\"base_url\":\"$MOCK_BASE_URL\",\"model_identifier\":\"mock-model\",\"request_configuration\":{\"headers\":{\"X-Mock-First-Token-Delay-Ms\":\"100\"}}}")"
  id="$(printf '%s' "$created" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  if [[ -z "$id" ]]; then
    echo "    response: $created"
    fail "register mock endpoint (allow it with ENDPOINT_ALLOWED_HOSTS=mock-llm)"
  fi
  pass "registered endpoint $name"
  result="$(curl -sS -X POST "$API_URL/api/v1/endpoints/$id/test" -H 'Content-Type: application/json' -d '{}')"
  # Top-level "ok" directly follows "tested_at" in the response (not models_check.ok).
  if printf '%s' "$result" | grep -q '"tested_at":"[^"]*","ok":true' \
     && printf '%s' "$result" | grep -q '"streamed":true' \
     && printf '%s' "$result" | grep -Eq '"ttft_ms":[0-9]'; then
    ttft="$(printf '%s' "$result" | sed -n 's/.*"ttft_ms":\([0-9.]*\).*/\1/p')"
    pass "endpoint test: streaming response, TTFT ${ttft} ms (mock delay 100 ms)"
  else
    echo "    result: $result"
    curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/endpoints/$id"
    fail "endpoint test against mock model server"
  fi

  # --- Benchmark run: 1 warm-up + 8 measured requests at concurrency 2, executed by the worker ---
  run_ds_file="$(mktemp)"
  printf 'id,input,reference_answer\nr1,Say hello,hello\nr2,Name a colour,colour\nr3,Count to three,three\n' > "$run_ds_file"
  run_ds="$(curl -sS -X POST "$API_URL/api/v1/datasets" -F "name=smoke-run-$RANDOM$RANDOM" -F dataset_type=chat \
    -F "file=@$run_ds_file;filename=run.csv")"
  rm -f "$run_ds_file"
  run_ds_id="$(printf '%s' "$run_ds" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$run_ds_id" ]] || { echo "    response: $run_ds"; fail "create run dataset"; }
  run_body="{\"name\":\"smoke-run\",\"endpoint_ids\":[\"$id\"],\"datasets\":[{\"dataset_id\":\"$run_ds_id\"}],"
  run_body+="\"suites\":[\"performance\",\"answer_quality\"],\"load\":{\"profile\":\"fixed_concurrency\",\"requests\":8,"
  run_body+="\"concurrency\":2,\"warmup_requests\":1}}"
  run="$(curl -sS -X POST "$API_URL/api/v1/runs" -H 'Content-Type: application/json' -d "$run_body")"
  run_id="$(printf '%s' "$run" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$run_id" ]] || { echo "    response: $run"; fail "create benchmark run (is this an older API without runs?)"; }
  pass "created benchmark run $run_id"
  run_status() { curl -fsS "$API_URL/api/v1/runs/$run_id" | grep -o '"status":"[a-z_]*","suites"' | cut -d'"' -f4; }
  deadline=$((SECONDS + TIMEOUT_SECONDS))
  status="$(run_status || true)"
  until [[ "$status" =~ ^(completed|completed_with_errors|cancelled|failed)$ ]]; do
    if (( SECONDS >= deadline )); then
      fail "run did not finish within ${TIMEOUT_SECONDS}s (status: $status; is the worker running?)"
    fi
    sleep 1
    status="$(run_status || true)"
  done
  if [[ "$status" == "completed" ]]; then
    pass "run executed by the worker: completed"
  else
    curl -sS "$API_URL/api/v1/runs/$run_id" | head -c 1500; echo
    fail "run finished with status $status"
  fi
  metrics="$(curl -fsS "$API_URL/api/v1/runs/$run_id/metrics")"
  printf '%s' "$metrics" | grep -Eq '"metric_name":"requests","aggregation":"attempted","metric_value":8(\.0)?,' \
    && pass "8 measured requests recorded (warm-up excluded from metrics)" || fail "measured request count in metrics"
  if printf '%s' "$metrics" | grep -Eq '"metric_name":"latency_ms","aggregation":"p50","metric_value":[0-9]' \
     && printf '%s' "$metrics" | grep -Eq '"metric_name":"ttft_ms","aggregation":"p50","metric_value":[0-9]'; then
    p50="$(printf '%s' "$metrics" | sed -n 's/.*"metric_name":"ttft_ms","aggregation":"p50","metric_value":\([0-9.]*\).*/\1/p')"
    pass "run metrics: TTFT p50 ${p50} ms (mock first-token delay 100 ms)"
  else
    fail "latency/TTFT metrics missing"
  fi
  # Answer quality (M4): the mock echoes the prompt, so every reference word is contained in the answer.
  printf '%s' "$metrics" | grep -Eq '"metric_name":"quality.evaluated_requests","aggregation":"count","metric_value":8(\.0)?,'     && pass "quality: 8 successful measured requests evaluated" || fail "quality.evaluated_requests metric (is this an API before M4?)"
  printf '%s' "$metrics" | grep -Eq '"metric_name":"quality.contains_reference","aggregation":"pass_rate","metric_value":1(\.0)?,'     && pass "quality: contains_reference pass rate 100% (deterministic evaluator)" || fail "quality.contains_reference metric"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/runs/$run_id"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/datasets/$run_ds_id"
  # --- RAG + hallucination with the LLM judge (M5): the mock server also answers judge prompts ---
  rag_file="$(mktemp)"
  printf '%s\n' '{"id":"k1","type":"rag","input":"When does the office open?","retrieved_contexts":[{"document_id":"kb-1","chunk_id":"c1","text":"The office opens at 9 am on weekdays."}],"reference_answer":"At 9 am on weekdays."}' > "$rag_file"
  rag_ds="$(curl -sS -X POST "$API_URL/api/v1/datasets" -F "name=smoke-rag-$RANDOM$RANDOM" -F dataset_type=rag \
    -F "file=@$rag_file;filename=rag.jsonl")"
  rm -f "$rag_file"
  rag_ds_id="$(printf '%s' "$rag_ds" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$rag_ds_id" ]] || { echo "    response: $rag_ds"; fail "create RAG dataset"; }
  rag_body="{\"name\":\"smoke-rag\",\"endpoint_ids\":[\"$id\"],\"datasets\":[{\"dataset_id\":\"$rag_ds_id\"}],"
  rag_body+="\"suites\":[\"performance\",\"rag\",\"hallucination\"],\"load\":{\"profile\":\"smoke\",\"requests\":2,\"concurrency\":1},"
  rag_body+="\"evaluation\":{\"judge\":{\"endpoint_id\":\"$id\",\"concurrency\":2}}}"
  rag_run="$(curl -sS -X POST "$API_URL/api/v1/runs" -H 'Content-Type: application/json' -d "$rag_body")"
  rag_run_id="$(printf '%s' "$rag_run" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$rag_run_id" ]] || { echo "    response: $rag_run"; fail "create RAG run (is this an API before M5?)"; }
  printf '%s' "$rag_run" | grep -q '"evidence_method":"judge"' && pass "RAG run $rag_run_id created with an LLM judge" \
    || fail "RAG run snapshot does not record the judge"
  run_id="$rag_run_id"
  deadline=$((SECONDS + TIMEOUT_SECONDS))
  status="$(run_status || true)"
  until [[ "$status" =~ ^(completed|completed_with_errors|cancelled|failed)$ ]]; do
    (( SECONDS >= deadline )) && fail "RAG run did not finish within ${TIMEOUT_SECONDS}s (status: $status)"
    sleep 1
    status="$(run_status || true)"
  done
  [[ "$status" == "completed" ]] && pass "RAG run completed (measured, then judged)" || fail "RAG run finished with status $status"
  rag_metrics="$(curl -fsS "$API_URL/api/v1/runs/$rag_run_id/metrics")"
  printf '%s' "$rag_metrics" | grep -Eq '"metric_name":"quality.rag_context_relevance","aggregation":"pass_rate","metric_value":1(\.0)?,' \
    && pass "rag: context relevance 100% (judge verdicts stored)" || fail "quality.rag_context_relevance metric"
  printf '%s' "$rag_metrics" | grep -q '"metric_name":"hallucination.contradiction_rate"' \
    && pass "hallucination: claim-level metrics computed" || fail "hallucination.contradiction_rate metric"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/runs/$rag_run_id"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/datasets/$rag_ds_id"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/endpoints/$id"
  pass "smoke run, dataset and endpoint removed"
else
  echo "  SKIP endpoint end-to-end check (start it with: docker compose --profile mock up -d)"
fi

echo "All smoke checks passed."
