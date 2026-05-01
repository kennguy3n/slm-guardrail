#!/usr/bin/env bash
# Orchestrate the full benchmark-record-commit workflow for the
# slm-guardrail pipeline.
#
# Steps:
#   1. Verify Python deps and (optionally) check llama-server health.
#      If llama-server is not reachable, fall back to --mock.
#   2. Run the pipeline benchmark via tools/run_guardrail_demo.py
#      and (when applicable) the deterministic mock reference.
#   3. Run the cross-community / cross-country demo via
#      tools/demo_guardrail.py — writes ISO-8601 timestamped
#      JSON + Markdown to results/.
#   4. Print a summary and (unless --no-commit) git-commit the
#      result files. With --push, also git push.
#
# Exits non-zero if the 250 ms p95 target is breached on any of the
# committed runs (matches kchat-skills/compiler/benchmark.py
# P95_LATENCY_TARGET_MS).
#
# Usage:
#   tools/run_benchmark.sh                # real Bonsai-1.7B + mock + commit
#   tools/run_benchmark.sh --mock         # mock only, commit
#   tools/run_benchmark.sh --mock --no-commit
#   tools/run_benchmark.sh --mock --push
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults / argument parsing.
# ---------------------------------------------------------------------------
MOCK_ONLY=0
NO_COMMIT=0
PUSH=0
ITERATIONS=100
WARMUP=5
SERVER_URL="${LLAMA_SERVER_URL:-http://localhost:8080}"
P95_TARGET_MS="250"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--mock] [--no-commit] [--push]
                       [--iterations N] [--warmup N]
                       [--server-url URL]

Runs the pipeline benchmark + cross-community/cross-country demo,
writes results to kchat-skills/benchmarks/ and results/, and
(by default) commits them. Exits non-zero if the 250 ms p95 target
is breached on any committed run.

Flags:
  --mock          Skip the llama-server requirement and only run the
                  deterministic MockSLMAdapter benchmark.
  --no-commit     Run benchmarks but skip the git commit step.
  --push          After committing, also git push the new commit.
  --iterations N  Per-case benchmark iterations (default: ${ITERATIONS}).
  --warmup N      Per-case warm-up iterations (default: ${WARMUP}).
  --server-url U  llama-server base URL (default: ${SERVER_URL}).
  -h, --help      Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mock) MOCK_ONLY=1 ;;
    --no-commit) NO_COMMIT=1 ;;
    --push) PUSH=1 ;;
    --iterations)
      [[ $# -ge 2 ]] || { echo "error: --iterations requires a value" >&2; exit 2; }
      ITERATIONS="$2"; shift ;;
    --warmup)
      [[ $# -ge 2 ]] || { echo "error: --warmup requires a value" >&2; exit 2; }
      WARMUP="$2"; shift ;;
    --server-url)
      [[ $# -ge 2 ]] || { echo "error: --server-url requires a value" >&2; exit 2; }
      SERVER_URL="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Resolve repo root and cd there.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${PYTHON:-python3}"

log() {
  printf '>>> %s\n' "$*"
}

warn() {
  printf 'warn: %s\n' "$*" >&2
}

err() {
  printf 'error: %s\n' "$*" >&2
}

# ---------------------------------------------------------------------------
# Prerequisite checks.
# ---------------------------------------------------------------------------
log "Checking Python prerequisites..."
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  err "${PYTHON} not found in PATH"
  exit 2
fi
if ! "${PYTHON}" -c "import yaml, jsonschema, cryptography" >/dev/null 2>&1; then
  err "missing Python deps. Install with: pip install -r requirements.txt"
  exit 2
fi

is_server_alive() {
  local url="${1%/}/health"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 2 "${url}" >/dev/null 2>&1
  else
    "${PYTHON}" - "${url}" <<'PY' >/dev/null 2>&1
import sys, urllib.request, urllib.error
url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=2) as resp:
        sys.exit(0 if 200 <= resp.status < 300 else 1)
except Exception:
    sys.exit(1)
PY
  fi
}

if [[ "${MOCK_ONLY}" -eq 0 ]]; then
  log "Checking llama-server at ${SERVER_URL} ..."
  if ! is_server_alive "${SERVER_URL}"; then
    warn "llama-server not reachable at ${SERVER_URL}; falling back to --mock."
    warn "Real Bonsai-1.7B benchmark will be SKIPPED."
    MOCK_ONLY=1
  else
    log "llama-server is alive."
  fi
fi

# ---------------------------------------------------------------------------
# Run benchmarks.
# ---------------------------------------------------------------------------
BENCH_FILES=()
RAN_REAL=0

if [[ "${MOCK_ONLY}" -eq 0 ]]; then
  log "Running real Bonsai-1.7B benchmark (LlamaCppSLMAdapter)..."
  "${PYTHON}" tools/run_guardrail_demo.py \
    --server-url "${SERVER_URL}" \
    --benchmark --commit-results \
    --benchmark-iterations "${ITERATIONS}" \
    --benchmark-warmup "${WARMUP}"
  BENCH_FILES+=("kchat-skills/benchmarks/bonsai_1.7b_results.json")
  RAN_REAL=1
fi

log "Running deterministic mock benchmark (MockSLMAdapter)..."
"${PYTHON}" tools/run_guardrail_demo.py \
  --mock \
  --benchmark --commit-results \
  --benchmark-iterations "${ITERATIONS}" \
  --benchmark-warmup "${WARMUP}"
BENCH_FILES+=("kchat-skills/benchmarks/bonsai_1.7b_mock_results.json")

# ---------------------------------------------------------------------------
# Cross-community / cross-country demo.
# ---------------------------------------------------------------------------
log "Running cross-community / cross-country demo..."
DEMO_RC=0
"${PYTHON}" tools/demo_guardrail.py || DEMO_RC=$?

# ---------------------------------------------------------------------------
# Summarize.
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "Benchmark summary  (p95 target: ${P95_TARGET_MS} ms)"
echo "======================================================================"

OVERALL_PASSED=1

read_bench_summary() {
  # Print "<adapter>\t<p95>\t<passed:0|1>" for a run_guardrail_demo.py JSON.
  "${PYTHON}" - "$1" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    j = json.load(fh)
adapter = j.get("adapter", "?")
report = j.get("report", {}) or {}
p95 = report.get("p95_ms", "?")
passed = bool(report.get("passed", False))
print(f"{adapter}\t{p95}\t{1 if passed else 0}")
PY
}

for f in "${BENCH_FILES[@]}"; do
  if [[ ! -f "${f}" ]]; then
    warn "expected ${f} not found"
    OVERALL_PASSED=0
    continue
  fi
  IFS=$'\t' read -r adapter p95 passed <<<"$(read_bench_summary "${f}")"
  printf '  %s\n' "${f}"
  printf '    adapter : %s\n' "${adapter}"
  printf '    p95_ms  : %s\n' "${p95}"
  if [[ "${passed}" == "1" ]]; then
    printf '    passed  : yes\n'
  else
    printf '    passed  : no\n'
    OVERALL_PASSED=0
  fi
done

# Latest cross-community demo artefacts.
LATEST_DEMO_JSON=""
LATEST_DEMO_MD=""
if [[ -d results ]]; then
  # Filenames are ISO-8601 timestamped (alphanumeric + ':T-Z'), so ls is safe.
  # shellcheck disable=SC2012
  LATEST_DEMO_JSON="$(ls -1t results/demo_results_*.json 2>/dev/null | head -n1 || true)"
  # shellcheck disable=SC2012
  LATEST_DEMO_MD="$(ls -1t results/demo_results_*.md 2>/dev/null | head -n1 || true)"
fi

echo ""
echo "Cross-community demo:"
if [[ -n "${LATEST_DEMO_JSON}" ]]; then
  IFS=$'\t' read -r demo_p95 demo_passed <<<"$("${PYTHON}" - "${LATEST_DEMO_JSON}" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    j = json.load(fh)
summary = j.get("performance_summary", {}) or {}
p95 = summary.get("p95_actual_ms", "?")
passed = summary.get("passed")
if passed is None:
    overall = (j.get("latency_report", {}) or {}).get("overall", {}) or {}
    p95 = overall.get("p95_ms", p95)
    passed = bool(isinstance(p95, (int, float)) and p95 <= 250.0)
print(f"{p95}\t{1 if passed else 0}")
PY
)"
  printf '  %s\n' "${LATEST_DEMO_JSON}"
  [[ -n "${LATEST_DEMO_MD}" ]] && printf '  %s\n' "${LATEST_DEMO_MD}"
  printf '    p95_ms : %s\n' "${demo_p95}"
  if [[ "${demo_passed}" == "1" ]]; then
    printf '    passed : yes\n'
  else
    printf '    passed : no\n'
    OVERALL_PASSED=0
  fi
else
  warn "no results/demo_results_*.json found"
  OVERALL_PASSED=0
fi

# demo_guardrail.py exits non-zero when its overall p95 breaches the
# target — surface that into the overall verdict.
if [[ "${DEMO_RC}" -ne 0 ]]; then
  OVERALL_PASSED=0
fi

# ---------------------------------------------------------------------------
# Git commit / push.
# ---------------------------------------------------------------------------
if [[ "${NO_COMMIT}" -eq 0 ]]; then
  echo ""
  log "Staging result files..."
  git add kchat-skills/benchmarks/*.json results/ 2>/dev/null || true
  if git diff --cached --quiet; then
    log "No new result files to commit."
  else
    msg="bench: record benchmark results $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    log "Committing: ${msg}"
    git commit -m "${msg}"
    if [[ "${PUSH}" -eq 1 ]]; then
      log "Pushing..."
      git push
    fi
  fi
else
  echo ""
  log "--no-commit: skipping git commit."
fi

# ---------------------------------------------------------------------------
# Final verdict.
# ---------------------------------------------------------------------------
echo ""
if [[ "${RAN_REAL}" -eq 0 ]]; then
  log "Note: real Bonsai-1.7B run was skipped (mock-only mode)."
fi

if [[ "${OVERALL_PASSED}" -eq 1 ]]; then
  echo "RESULT: PASS — p95 target (${P95_TARGET_MS} ms) met across all runs."
  exit 0
fi

echo "RESULT: FAIL — p95 target (${P95_TARGET_MS} ms) breached." >&2
exit 1
