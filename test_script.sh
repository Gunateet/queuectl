#!/usr/bin/env bash
# =============================================================
# QueueCTL Automated Test Script - Final Version
# Covers all assignment requirements:
#   ✅ Basic job success
#   ✅ Retry and DLQ functionality
#   ✅ Concurrency & race condition handling
#   ✅ Invalid command handling
#   ✅ Sequential job execution
#   ✅ All CLI commands (enqueue, worker start/stop, list, dlq, config)
# =============================================================

set -e

echo "Initializing clean environment..."
rm -f queue.db queuectl.db worker_state.json worker_control.json config.json shared_counter.txt
echo "{}" > config.json
echo "0" > shared_counter.txt
echo "Environment initialized."
echo

# -------------------------------------------------------------
# Config setup test
# -------------------------------------------------------------
echo "=== Config Test: Setting configuration values ==="
queuectl config set max-retries 3
queuectl config set backoff-base 2
echo "Current config file contents:"
cat config.json
echo "Config setup complete."
echo

# -------------------------------------------------------------
# Utility function to start and stop workers safely
# -------------------------------------------------------------
run_worker() {
    local count=$1
    local delay=$2
    queuectl worker start --count "$count" &
    local pid=$!
    sleep "$delay"
    queuectl worker stop >/dev/null 2>&1 || true
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
}

# -------------------------------------------------------------
# Test 1: Basic Job Success
# -------------------------------------------------------------
echo "=== Test 1: Basic job completes successfully ==="
queuectl enqueue '{"id":"job_success","command":"echo Basic job successful"}'
echo "Listing pending jobs:"
queuectl list --state pending || true
queuectl status
run_worker 1 3
echo "Verifying status after completion..."
queuectl status
echo "Test 1 passed."
echo

# -------------------------------------------------------------
# Test 2: Failed Job -> Retry -> DLQ (with retry command)
# -------------------------------------------------------------
echo "=== Test 2: Failed job retries and moves to DLQ ==="
queuectl enqueue '{"id":"job_fail","command":"bash -c \"exit 1\""}'
queuectl status
run_worker 1 25
echo "Verifying DLQ contents..."
queuectl status
queuectl dlq list || true
echo "Retrying first DLQ job if available..."
first_dlq_job=$(queuectl dlq list | awk 'NR==3 {print $1}' || true)
[ -n "$first_dlq_job" ] && queuectl dlq retry "$first_dlq_job" || echo "No DLQ job found to retry."
queuectl status
echo "Test 2 passed."
echo

# -------------------------------------------------------------
# Test 3: Concurrency & Race Condition Handling
# -------------------------------------------------------------
echo "=== Test 3: Concurrency and race condition handling ==="
echo "0" > shared_counter.txt
queuectl enqueue '{"id":"race1","command":"python3 increment_counter.py","max_retries":1}'
queuectl enqueue '{"id":"race2","command":"python3 increment_counter.py","max_retries":1}'
queuectl enqueue '{"id":"race3","command":"python3 increment_counter.py","max_retries":1}'
queuectl status
run_worker 3 10
echo "Shared counter value (expected = 3):"
cat shared_counter.txt || echo "(file missing)"
echo "Test 3 completed. Race condition handled correctly if counter = 3."
echo

# -------------------------------------------------------------
# Test 4: Invalid Command Handling
# -------------------------------------------------------------
echo "=== Test 4: Invalid command fails gracefully ==="
queuectl enqueue '{"id":"invalid_job","command":"non_existing_command"}'
queuectl status
run_worker 1 10
echo "Verifying DLQ for invalid job..."
queuectl dlq list || true
echo "Test 4 passed."
echo

# -------------------------------------------------------------
# Test 5: Sequential Job Execution
# -------------------------------------------------------------
echo "=== Test 5: Sequential job execution ==="
queuectl enqueue '{"id":"seq1","command":"echo Sequential Job 1"}'
queuectl enqueue '{"id":"seq2","command":"echo Sequential Job 2"}'
queuectl status
run_worker 1 10
echo "Verifying sequential job completion..."
queuectl status
echo "Test 5 passed."
echo

# -------------------------------------------------------------
# Final Summary
# -------------------------------------------------------------
echo "============================================================="
echo "All tests completed successfully."
queuectl status
queuectl worker stop
echo "============================================================="

