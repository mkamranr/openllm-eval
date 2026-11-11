#!/usr/bin/env bash
# Post-deployment smoke test for a Docker Compose installation.
#
#   ./scripts/smoke-test.sh                       # defaults to localhost
#   API_URL=http://server:8000 FRONTEND_URL=http://server:3000 ./scripts/smoke-test.sh
#   SMOKE_TOKEN=oel_... ./scripts/smoke-test.sh   # without docker access: an admin's API token
#
# Checks: migrations exited 0, API liveness/readiness (DB + Redis + worker), sign-in (token, session
# cookie, CSRF, roles), version, dataset
# import/export hash, frontend served, /api proxied and, with the mock model server, benchmark,
# quality, RAG, report, comparison, and context-window + telemetry runs. Exits non-zero on the first failure.
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
  # wait_for DESCRIPTION CHECK [ON_TIMEOUT]: ON_TIMEOUT is run (e.g. to print a hint) before failing.
  local description="$1" check="$2" on_timeout="${3:-}" deadline=$((SECONDS + TIMEOUT_SECONDS))
  until eval "$check" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      if [[ -n "$on_timeout" ]]; then eval "$on_timeout"; fi
      fail "$description (timed out after ${TIMEOUT_SECONDS}s)"
    fi
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

# --- Authentication (Milestone 8) ---
# Every later check uses an API token: SMOKE_TOKEN if given, otherwise a short-lived token for a
# temporary service account created inside the API container (removed again on exit).
code_of() { command curl -s -o /dev/null -w '%{http_code}' "$@"; }
SMOKE_SERVICE_USER=""
SMOKE_VIEWER=""
cleanup_auth() {
  local u
  for u in "$SMOKE_VIEWER" "$SMOKE_SERVICE_USER"; do
    if [[ -n "$u" ]]; then
      $COMPOSE exec -T api python -m app.cli.admin delete-user "$u" >/dev/null 2>&1 || true
    fi
  done
}
trap cleanup_auth EXIT
if printf '%s' "$ready" | grep -q '"auth_enabled":false'; then
  echo "  WARN sign-in is disabled (AUTH_ENABLED=false): everyone is an administrator"
else
  [[ "$(code_of "$API_URL/api/v1/projects")" == "401" ]] \
    && pass "anonymous API request rejected (401)" || fail "anonymous request was not rejected"
  [[ "$(code_of "$API_URL/api/v1/auth/providers")" == "200" ]] \
    && pass "sign-in providers endpoint is public" || fail "/auth/providers"
  if [[ -z "${SMOKE_TOKEN:-}" ]]; then
    if ! command -v docker >/dev/null 2>&1 || [[ "${SKIP_COMPOSE_CHECKS:-0}" == "1" ]]; then
      fail "sign-in is required: set SMOKE_TOKEN to an administrator's API token"
    fi
    SMOKE_SERVICE_USER="smoke-test-$RANDOM$RANDOM"
    SMOKE_TOKEN="$($COMPOSE exec -T api python -m app.cli.admin create-token \
      --service-user "$SMOKE_SERVICE_USER" --role admin --ttl 1h --name smoke-test | tr -d '\r')" \
      || fail "create a smoke-test token (python -m app.cli.admin create-token)"
  fi
  # From here on, every curl call carries the token.
  curl() { command curl -H "Authorization: Bearer $SMOKE_TOKEN" "$@"; }
  me="$(curl -fsS "$API_URL/api/v1/auth/me")" || fail "token rejected by /auth/me"
  printf '%s' "$me" | grep -q '"role":"admin"' \
    && pass "API token accepted ($(printf '%s' "$me" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p'), admin)" \
    || fail "smoke token is not an administrator's: $me"

  # A temporary local viewer: cookie sign-in, CSRF, role check, sign-out.
  viewer="smoke-viewer-$RANDOM$RANDOM"
  viewer_pw="Smoke$RANDOM$RANDOM$RANDOM"
  created="$(curl -sS -X POST "$API_URL/api/v1/users" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$viewer\",\"role\":\"viewer\",\"password\":\"$viewer_pw\",\"must_change_password\":false}")"
  viewer_id="$(printf '%s' "$created" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$viewer_id" ]] && pass "created temporary viewer $viewer" || { echo "    $created"; fail "create a local user"; }
  SMOKE_VIEWER="$viewer"  # removed by cleanup_auth if a check below fails
  jar="$(mktemp)"
  login_body="{\"username\":\"$viewer\",\"password\":\"$viewer_pw\"}"
  # The cookie checks go through the UI's nginx proxy, which may still be starting (deploy.sh
  # rebuilds the frontend image when it changes).
  wait_for "UI proxy forwards /api ($FRONTEND_URL/api/v1/health)" \
    "command curl -fsS '$FRONTEND_URL/api/v1/health' | grep -q '\"status\":\"ok\"'" \
    "echo \"    last HTTP code: \$(code_of '$FRONTEND_URL/api/v1/health')\"; echo \"    hint: an older frontend may hold the API's previous address; try: $COMPOSE restart frontend\""
  # expect CODE DESCRIPTION CURL-ARGS...: pass if the request returns CODE, else show what it got.
  expect() {
    local want="$1" description="$2" got
    shift 2
    got="$(code_of "$@")"
    [[ "$got" == "$want" ]] && pass "$description" || fail "$description: expected HTTP $want, got $got"
  }
  login_req=(-X POST "$FRONTEND_URL/api/v1/auth/login" -H 'Content-Type: application/json' -d "$login_body")
  expect 403 "sign-in without the CSRF header refused" -c "$jar" "${login_req[@]}"
  expect 200 "password sign-in through the UI proxy, with the browser's Origin header" \
    -c "$jar" -H 'X-OpenLLM-CSRF: 1' -H "Origin: ${FRONTEND_URL%/}" "${login_req[@]}"
  expect 403 "sign-in from a foreign Origin refused" \
    -H 'X-OpenLLM-CSRF: 1' -H 'Origin: https://evil.example' "${login_req[@]}"
  grep -q 'oel_session' "$jar" && pass "HttpOnly session cookie set" || fail "no session cookie"
  command curl -fsS -b "$jar" "$FRONTEND_URL/api/v1/auth/me" | grep -q '"role":"viewer"' \
    && pass "session cookie identifies the viewer" || fail "/auth/me with the session cookie"
  expect 403 "viewer may not create projects" -b "$jar" -X POST "$FRONTEND_URL/api/v1/projects" \
    -H 'Content-Type: application/json' -H 'X-OpenLLM-CSRF: 1' -d '{"name":"smoke-viewer-denied"}'
  expect 204 "sign-out" -b "$jar" -X POST "$FRONTEND_URL/api/v1/auth/logout" -H 'X-OpenLLM-CSRF: 1'
  expect 401 "the session ends at sign-out" -b "$jar" "$FRONTEND_URL/api/v1/auth/me"
  rm -f "$jar"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/users/$viewer_id" && SMOKE_VIEWER=""

  admin_name="$(env_value ADMIN_USERNAME)"
  users="$(curl -fsS "$API_URL/api/v1/users")"
  if printf '%s' "$users" | grep -q "\"username\":\"${admin_name:-admin}\""; then
    pass "bootstrap administrator '${admin_name:-admin}' exists"
  else
    echo "  WARN no '${admin_name:-admin}' user (set ADMIN_INITIAL_PASSWORD, or create one with app.cli.admin)"
  fi
fi

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
  # --- Reports and comparison (M6): offline HTML report, raw export, re-run comparison ---
  report="$(curl -sS -X POST "$API_URL/api/v1/runs/$run_id/reports" -H 'Content-Type: application/json' -d '{"format":"html"}')"
  report_id="$(printf '%s' "$report" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$report_id" ]] || { echo "    response: $report"; fail "generate HTML report (is this an API before M6?)"; }
  report_file="$(mktemp)"
  headers="$(curl -fsS -D - -o "$report_file" "$FRONTEND_URL/api/v1/reports/$report_id/download?inline=true")" \
    || fail "download the HTML report through the frontend proxy"
  if grep -q 'Methodology</h2>' "$report_file" && grep -q 'Reproduce</h2>' "$report_file" \
    && ! grep -qi '<script' "$report_file" && ! grep -Eq '(src|href)="https?:' "$report_file"; then
    pass "HTML report: methodology + reproduction, no scripts, no external resources ($(wc -c < "$report_file" | tr -d ' ') bytes)"
  else
    fail "HTML report content"
  fi
  rm -f "$report_file"
  csp_count="$(printf '%s' "$headers" | grep -ci '^content-security-policy:')"
  printf '%s' "$headers" | grep -i '^content-security-policy:' | grep -q "style-src 'unsafe-inline'" && [[ "$csp_count" == "1" ]] \
    && pass "report served with its own strict CSP (one policy, through nginx)" || fail "report CSP header (found $csp_count policies)"
  csv_report="$(curl -sS -X POST "$API_URL/api/v1/runs/$run_id/reports" -H 'Content-Type: application/json' -d '{"format":"csv"}')"
  csv_id="$(printf '%s' "$csv_report" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  csv_lines="$(curl -fsS "$API_URL/api/v1/reports/$csv_id/download" | wc -l | tr -d ' ')"
  [[ "$csv_lines" -ge 10 ]] && pass "results CSV export: $((csv_lines - 1)) attempts (warm-up included, marked)" \
    || fail "results CSV export ($csv_lines lines)"
  rerun="$(curl -sS -X POST "$API_URL/api/v1/runs/$run_id/rerun")"
  rerun_id="$(printf '%s' "$rerun" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$rerun_id" ]] || { echo "    response: $rerun"; fail "re-run"; }
  comparison="$(curl -fsS "$API_URL/api/v1/compare?run_ids=$run_id,$rerun_id")"
  printf '%s' "$comparison" | grep -q '"overall":"equivalent_configuration"' \
    && pass "re-run $rerun_id has an equivalent configuration (comparison)" || fail "comparison of a run and its re-run"
  curl -sS -o /dev/null -X POST "$API_URL/api/v1/runs/$rerun_id/cancel"
  for _ in $(seq 1 30); do
    run_id="$rerun_id"; s="$(run_status || true)"
    [[ "$s" =~ ^(completed|completed_with_errors|cancelled|failed)$ ]] && break
    sleep 1
  done
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/runs/$rerun_id"
  run_id="$(printf '%s' "$run" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
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
  # --- Context window + telemetry (M7): generated needle dataset, overflow above the mock's limit,
  #     GPU metrics scraped from the mock's Prometheus /metrics page ---
  MOCK_METRICS_URL="${MOCK_METRICS_URL:-${MOCK_BASE_URL%/v1}/metrics}"
  ctx_ep_body="{\"name\":\"$name-ctx\",\"base_url\":\"$MOCK_BASE_URL\",\"model_identifier\":\"mock-model\","
  ctx_ep_body+="\"request_configuration\":{\"headers\":{\"X-Mock-Mode\":\"needle\",\"X-Mock-Max-Context\":\"3000\",\"X-Mock-First-Token-Delay-Ms\":\"50\"}},"
  ctx_ep_body+="\"model_metadata\":{\"family\":\"mock\",\"quantization_method\":\"none\",\"weight_bits\":16},"
  ctx_ep_body+="\"telemetry\":{\"url\":\"$MOCK_METRICS_URL\",\"interval_seconds\":0.5}}"
  ctx_ep="$(curl -sS -X POST "$API_URL/api/v1/endpoints" -H 'Content-Type: application/json' -d "$ctx_ep_body")"
  ctx_ep_id="$(printf '%s' "$ctx_ep" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$ctx_ep_id" ]] || { echo "    response: $ctx_ep"; fail "register endpoint with telemetry (is this an API before M7?)"; }
  ctx_ds="$(curl -sS -X POST "$API_URL/api/v1/datasets/generate" -H 'Content-Type: application/json' \
    -d "{\"name\":\"smoke-needle-$RANDOM$RANDOM\",\"params\":{\"context_lengths\":[512,1024,4096],\"depths\":[0.1,0.5,0.9]}}")"
  ctx_ds_id="$(printf '%s' "$ctx_ds" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$ctx_ds_id" ]] || { echo "    response: $ctx_ds"; fail "generate needle dataset"; }
  printf '%s' "$ctx_ds" | grep -q '"record_count":9' && pass "generated needle dataset: 9 cases at 3 context lengths" \
    || fail "generated dataset record count"
  ctx_body="{\"name\":\"smoke-context\",\"endpoint_ids\":[\"$ctx_ep_id\"],\"datasets\":[{\"dataset_id\":\"$ctx_ds_id\"}],"
  ctx_body+="\"suites\":[\"context_window\"],\"load\":{\"profile\":\"sequential\"}}"
  ctx_run="$(curl -sS -X POST "$API_URL/api/v1/runs" -H 'Content-Type: application/json' -d "$ctx_body")"
  ctx_run_id="$(printf '%s' "$ctx_run" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
  [[ -n "$ctx_run_id" ]] || { echo "    response: $ctx_run"; fail "create context-window run"; }
  run_id="$ctx_run_id"
  deadline=$((SECONDS + TIMEOUT_SECONDS))
  status="$(run_status || true)"
  until [[ "$status" =~ ^(completed|completed_with_errors|cancelled|failed)$ ]]; do
    (( SECONDS >= deadline )) && fail "context run did not finish within ${TIMEOUT_SECONDS}s (status: $status)"
    sleep 1
    status="$(run_status || true)"
  done
  [[ "$status" == "completed_with_errors" ]] && pass "context run finished: 4096-token cases overflowed as expected" \
    || fail "context run finished with status $status (expected completed_with_errors)"
  ctx_metrics="$(curl -fsS "$API_URL/api/v1/runs/$ctx_run_id/metrics")"
  printf '%s' "$ctx_metrics" | grep -Eq '"context_length":4096,"metric_name":"context.overflow_rate","aggregation":"ratio","metric_value":1(\.0)?,' \
    && pass "context: overflow rate 100% at 4096 requested tokens (classified context_overflow)" || fail "context.overflow_rate at 4096"
  printf '%s' "$ctx_metrics" | grep -Eq '"context_length":1024,"metric_name":"context.retrieval_accuracy","aggregation":"pass_rate","metric_value":1(\.0)?,' \
    && pass "context: needle retrieval 100% at 1024 tokens" || fail "context.retrieval_accuracy at 1024"
  printf '%s' "$ctx_metrics" | grep -q '"server_reported":3' \
    && pass "context: actual input tokens are server-reported (kept apart from the estimate)" || fail "token count source"
  if printf '%s' "$ctx_metrics" | grep -Eq '"metric_name":"gpu.memory_used_mb","aggregation":"max","metric_value":[0-9]'; then
    pass "telemetry: GPU memory scraped from $MOCK_METRICS_URL"
  else
    curl -sS "$API_URL/api/v1/runs/$ctx_run_id/telemetry" | head -c 800; echo
    fail "gpu.memory_used_mb missing (telemetry URL allowed by the network policy?)"
  fi
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/runs/$ctx_run_id"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/datasets/$ctx_ds_id"
  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/endpoints/$ctx_ep_id"

  # --- Integration-mode tool execution (M9): the mock model calls the example calculator tool,
  #     which runs in the sandboxed tool runner; the model then answers with the real result ---
  if printf '%s' "$(curl -fsS "$API_URL/api/v1/tools/status")" | grep -q '"reachable":true'; then
    pass "tool runner reachable (integration mode available)"
    tool_ep_body="{\"name\":\"$name-tools\",\"base_url\":\"$MOCK_BASE_URL\",\"model_identifier\":\"mock-model\","
    tool_ep_body+='"request_configuration":{"stream":false,"headers":{"X-Mock-Mode":"tool_call",'
    tool_ep_body+='"X-Mock-Tool-Name":"calculator","X-Mock-Tool-Args":"{\"expression\":\"6*7\"}"}}}'
    tool_ep="$(curl -sS -X POST "$API_URL/api/v1/endpoints" -H 'Content-Type: application/json' -d "$tool_ep_body")"
    tool_ep_id="$(printf '%s' "$tool_ep" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
    [[ -n "$tool_ep_id" ]] || { echo "    response: $tool_ep"; fail "register tool-calling endpoint"; }
    tool_ds_file="$(mktemp)"
    printf '%s\n' '{"id":"calc","type":"tool_call","input":"What is 6 times 7?","tools":[{"type":"function","function":{"name":"calculator","parameters":{"type":"object","properties":{"expression":{"type":"string"}},"required":["expression"]}}}],"expected_tool_calls":[{"name":"calculator","arguments":{"expression":"6*7"}}],"expected_final_answer":"42"}' > "$tool_ds_file"
    tool_ds="$(curl -sS -X POST "$API_URL/api/v1/datasets" -F "name=smoke-tools-$RANDOM$RANDOM" -F dataset_type=tool_call \
      -F "file=@$tool_ds_file;filename=tools.jsonl")"
    rm -f "$tool_ds_file"
    tool_ds_id="$(printf '%s' "$tool_ds" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
    [[ -n "$tool_ds_id" ]] || { echo "    response: $tool_ds"; fail "create tool_call dataset"; }
    tool_run_body="{\"name\":\"smoke-tools\",\"endpoint_ids\":[\"$tool_ep_id\"],\"datasets\":[{\"dataset_id\":\"$tool_ds_id\"}],"
    tool_run_body+='"suites":["tool_calling"],"evaluation":{"tool_mode":"integration"},"load":{"profile":"sequential"}}'
    tool_run="$(curl -sS -X POST "$API_URL/api/v1/runs" -H 'Content-Type: application/json' -d "$tool_run_body")"
    run_id="$(printf '%s' "$tool_run" | sed -n 's/^{"id":"\([0-9a-f-]*\)".*/\1/p')"
    [[ -n "$run_id" ]] || { echo "    response: $tool_run"; fail "create integration-mode run"; }
    # PostgreSQL (jsonb) reorders object keys, so extract the tool_registry object first and
    # look for its hash and the calculator version in any order.
    registry="$(printf '%s' "$tool_run" | grep -Eo '"tool_registry":\{("[a-z0-9_]+":("[^"]*"|\{[^}]*\}),?)+\}')"
    printf '%s' "$registry" | grep -Eq '"sha256":"[0-9a-f]{64}"' \
      && printf '%s' "$registry" | grep -q '"calculator":"1"' \
      && pass "integration-mode run records the tool registry hash" \
      || { echo "    tool_registry: ${registry:-missing}"; fail "tool registry in the run snapshot"; }
    deadline=$((SECONDS + TIMEOUT_SECONDS))
    status="$(run_status || true)"
    until [[ "$status" =~ ^(completed|completed_with_errors|cancelled|failed)$ ]]; do
      (( SECONDS < deadline )) || fail "integration-mode run did not finish (status: $status)"
      sleep 1
      status="$(run_status || true)"
    done
    [[ "$status" == "completed" ]] || fail "integration-mode run finished with status $status"
    result_id="$(curl -fsS "$API_URL/api/v1/runs/$run_id/results?phase=measured&limit=1" \
      | sed -n 's/^{"items":\[{"id":"\([0-9a-f-]*\)".*/\1/p')"
    detail="$(curl -fsS "$API_URL/api/v1/runs/$run_id/results/$result_id")"
    if printf '%s' "$detail" | grep -q '"mode":"integration"' && printf '%s' "$detail" | grep -q '"executed":true' \
      && printf '%s' "$detail" | grep -q 'result\\": 42'; then
      pass "tool call executed in the sandbox: calculator(6*7) = 42 reached the model"
    else
      printf '%s' "$detail" | head -c 1500; echo
      fail "integration-mode tool execution"
    fi
    curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/runs/$run_id"
    curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/datasets/$tool_ds_id"
    curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/endpoints/$tool_ep_id"
  else
    echo "  SKIP integration-mode tool check (enable with TOOL_EXECUTION_ENABLED=true and deploy.sh --tools)"
  fi

  curl -sS -o /dev/null -X DELETE "$API_URL/api/v1/endpoints/$id"
  pass "smoke run, dataset and endpoint removed"
else
  echo "  SKIP endpoint end-to-end check (start it with: docker compose --profile mock up -d)"
fi

echo "All smoke checks passed."
