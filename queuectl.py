#!/usr/bin/env python3
"""
queuectl.py - CLI for QueueCTL (single-file edition)

Features:
- enqueue jobs (JSON)
- worker start/stop (multi-threaded inside same process)
- internal atomic job claiming to avoid race conditions between threads
- retry with exponential backoff
- Dead Letter Queue (DLQ)
- persistent storage using SQLite (queuectl.db)
- simple config persisted to config.json
"""

import os
import json
import sqlite3
import subprocess
import time
import uuid
import threading
from datetime import datetime, timezone
import click

DB_FILE = "queuectl.db"
CONFIG_FILE = "config.json"

# Lock used to serialize job-claiming operations in this process.
# This prevents multiple threads from racing to claim the same job.
db_lock = threading.Lock()
exec_lock= threading.Lock()

WORKER_STATE_FILE = "worker_state.json"
WORKER_CONTROL_FILE = "worker_control.json"

def save_worker_count(count):
    """Persist active worker count to a file."""
    with open(WORKER_STATE_FILE, "w") as f:
        json.dump({"active_workers": count}, f)

def get_worker_count():
    """Read active worker count from file."""
    try:
        with open(WORKER_STATE_FILE, "r") as f:
            data = json.load(f)
            return data.get("active_workers", 0)
    except FileNotFoundError:
        return 0



def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    """Open DB connection and ensure schema exists. We return a connection that is safe for multithreaded use."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Enable WAL for better concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    # Create tables
    conn.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        command TEXT NOT NULL,
        state TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        max_retries INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        next_attempt_at TEXT,
        last_error TEXT
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS dlq (
        id TEXT PRIMARY KEY,
        command TEXT NOT NULL,
        reason TEXT,
        failed_at TEXT
    );
    """)
    return conn


def load_config():
    if not os.path.exists(CONFIG_FILE):
        cfg = {"max_retries": 3, "backoff_base": 2}
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        return cfg
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


@click.group()
def cli():
    """queuectl - Background Job Queue System"""
    pass


# ---------------- Enqueue ----------------
@cli.command()
@click.argument("job_json")
def enqueue(job_json):
    """Enqueue a job. Pass a JSON string: e.g.
    queuectl.py enqueue '{"id":"job1","command":"echo hi","max_retries":3}'
    """
    conn = init_db()
    try:
        payload = json.loads(job_json)
    except Exception as e:
        click.echo(f"Invalid JSON: {e}", err=True)
        raise SystemExit(1)

    job_id = payload.get("id") or str(uuid.uuid4())
    command = payload.get("command")
    if not command:
        click.echo("Job must include 'command' field", err=True)
        raise SystemExit(1)
    max_retries = int(payload.get("max_retries", payload.get("maxRetries", 3)))
    attempts = int(payload.get("attempts", 0))
    now = now_iso()

    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs (id,command,state,attempts,max_retries,created_at,updated_at,next_attempt_at,last_error) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, command, "pending", attempts, max_retries, now, now, None, None),
        )
    click.echo(f"Enqueued job {job_id}")


# ---------------- Worker Management ----------------
class WorkerManager:
    def __init__(self, conn, config):
        self.conn = conn
        self.config = config
        self._threads = []
        self._stop = threading.Event()

    def start_workers(self, count=1):
        """Start worker threads and persist worker count."""
        self._stop.clear()
        self._threads = []
        save_worker_count(count)  # ✅ persist worker count immediately

        for i in range(count):
            t = threading.Thread(target=self._worker_loop, name=f"worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop_workers(self):
        """Stop all workers and reset active count."""
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)
        self._threads = []
        save_worker_count(0) 

    def is_running(self):
        return any(t.is_alive() for t in self._threads)

    def worker_count(self):
        return len(self._threads)

    def _claim_job_atomic(self):
        """
        Atomically claim one pending job.
        Approach:
         - Acquire a Python-level lock (db_lock) to serialize claim attempts in this process.
         - Use a small transaction to SELECT one pending job id then UPDATE it to processing.
         - Return the full job row if success, or None.
        This prevents two threads in the same process from racing to claim the same job.
        """
        with db_lock:
            cur = self.conn.cursor()
            try:
                # Begin immediate to acquire a reserved lock (prevent other writers)
                cur.execute("BEGIN IMMEDIATE;")
                # Pick candidate id
                now = now_iso()
                row = cur.execute(
                    "SELECT id FROM jobs WHERE state='pending' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY created_at LIMIT 1",
                    (now,),
                ).fetchone()
                if not row:
                    cur.execute("COMMIT;")
                    return None
                candidate_id = row["id"]
                # Try to atomically update that job to processing
                cur.execute(
                    "UPDATE jobs SET state='processing', updated_at=? WHERE id=? AND state='pending'",
                    (now, candidate_id),
                )
                if cur.rowcount == 0:
                    # someone else updated it; rollback and return None
                    cur.execute("ROLLBACK;")
                    return None
                # fetch the full job row
                job_row = cur.execute("SELECT * FROM jobs WHERE id=?", (candidate_id,)).fetchone()
                cur.execute("COMMIT;")
                return dict(job_row) if job_row else None
            except Exception:
                try:
                    cur.execute("ROLLBACK;")
                except Exception:
                    pass
                return None

    def _worker_loop(self):
        while not self._stop.is_set():
        # 🧠 Check for external stop signal (from 'queuectl worker stop')
            if os.path.exists(WORKER_CONTROL_FILE):
                try:
                    with open(WORKER_CONTROL_FILE, "r") as f:
                        ctrl = json.load(f)
                        if ctrl.get("stop"):
                            click.echo(" External stop signal received. Exiting worker...")
                            return  # gracefully exit this worker thread
                except Exception:
                    pass

            #  Pick up the next pending job
            job = self._claim_job_atomic()
            if not job:
                time.sleep(0.5)
                continue

            job_id = job["id"]
            cmd = job["command"]
            attempts = int(job.get("attempts", 0))
            max_retries = int(job.get("max_retries", self.config.get("max_retries", 3)))

            # Execute the command (print output to terminal)
            try:
                click.echo(f"Worker picked job {job_id}: {cmd}")
                # run command; allow output directly to terminal
                with exec_lock:
                    proc = subprocess.run(cmd, shell=True)
                rc = proc.returncode

                if rc == 0:
                    # success
                    with self.conn:
                        self.conn.execute(
                            "UPDATE jobs SET state='completed', updated_at=? WHERE id=?",
                            (now_iso(), job_id),
                        )
                    click.echo(f"✅ Job {job_id} completed successfully")
                else:
                    # failure – schedule retry or DLQ
                    attempts += 1
                    base = int(self.config.get("backoff_base", 2))
                    backoff_seconds = int(base ** attempts)
                    self._handle_failure(job_id, attempts, max_retries, f"exit_code={rc}", backoff_seconds)

            except Exception as e:
                attempts += 1
                base = int(self.config.get("backoff_base", 2))
                backoff_seconds = int(base ** attempts)
                self._handle_failure(job_id, attempts, max_retries, str(e), backoff_seconds)

            # small sleep to allow graceful shutdown to take effect
            time.sleep(0.05)

    def _handle_failure(self, job_id, attempts, max_retries, error_message, backoff_seconds):
        if attempts >= max_retries:
            # move to DLQ
            with self.conn:
                self.conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                self.conn.execute("INSERT OR REPLACE INTO dlq (id,command,reason,failed_at) VALUES (?,?,?,?)",
                                  (job_id, error_message, f"Failed after {attempts} attempts", now_iso()))
            click.echo(f"💀 Job {job_id} moved to DLQ after {attempts} attempts.")
        else:
            # schedule retry with backoff (set next_attempt_at)
            next_time = (datetime.now(timezone.utc).timestamp() + backoff_seconds)
            # store as iso string
            next_iso = datetime.fromtimestamp(next_time, tz=timezone.utc).isoformat()
            with self.conn:
                self.conn.execute("UPDATE jobs SET state='pending', attempts=?, next_attempt_at=?, last_error=?, updated_at=? WHERE id=?",
                                  (attempts, next_iso, error_message, now_iso(), job_id))
            click.echo(f"⏳ Retrying job {job_id} after {backoff_seconds}s (attempt {attempts})...")


# CLI commands to manage workers
_worker_manager = None  # created when needed


@cli.group()
def worker():
    """Worker management"""
    pass


@worker.command("start")
@click.option("--count", default=1, help="Number of workers to start")
def worker_start(count):
    """Start worker threads (runs in foreground). Use Ctrl+C to stop gracefully."""
    if os.path.exists(WORKER_CONTROL_FILE):
        try:
            os.remove(WORKER_CONTROL_FILE)
        except Exception:
            pass   
    global _worker_manager
    conn = init_db()
    cfg = load_config()
    _worker_manager = WorkerManager(conn, cfg)
    click.echo(f"Starting {count} worker(s)... (Press Ctrl+C to stop)")
    _worker_manager.start_workers(count)
    try:
        while _worker_manager.is_running():
            time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo("Graceful shutdown requested, stopping workers...")
        _worker_manager.stop_workers()
    click.echo("Workers stopped.")


@worker.command("stop")
def worker_stop():
    """Stop workers (only works if running in the same process)"""
    global _worker_manager

    # Signal via file for graceful stop
    WORKER_CONTROL_FILE = "worker_control.json"
    with open(WORKER_CONTROL_FILE, "w") as f:
        json.dump({"stop": True}, f)
    click.echo(" Stop signal sent to all workers (via control file).")

    # Reset active worker count in persistent store
    try:
        with open("worker_state.json", "w") as f:
            json.dump({"active_workers": 0}, f)
    except Exception:
        pass

    # Stop in-process manager if active
    if _worker_manager:
        _worker_manager.stop_workers()
        _worker_manager = None
        click.echo("✅ All workers stopped gracefully.")
    else:
        click.echo("ℹ️ No local worker manager found.")
# ---------------- Status / List / DLQ / Config ----------------

@cli.command()
def status():
    """Show summary of job states & active workers."""
    conn = init_db()
    cur = conn.cursor()

    # Count jobs per state
    res = cur.execute("SELECT state, COUNT(*) AS c FROM jobs GROUP BY state").fetchall()
    stats = {r["state"]: r["c"] for r in res}

    # Get DLQ count and map to 'dead'
    dlq_count = cur.execute("SELECT COUNT(*) FROM dlq").fetchone()[0]
    stats["dead"] = dlq_count

    # Define all job states for consistent display
    states = ["pending", "processing", "completed", "failed", "dead"]

    # Display state counts
    for s in states:
        click.echo(f"{s.capitalize()}: {stats.get(s, 0)}")

    # ✅ Active worker count from persistent store
    active_workers = get_worker_count()
    click.echo(f"Active workers (manager): {active_workers}")

    conn.close()
@cli.command("list")
@click.option("--state", default=None, help="Filter by job state")
def list_jobs(state):
    """List jobs; optional --state"""
    conn = init_db()
    cur = conn.cursor()
    if state:
        rows = cur.execute("SELECT * FROM jobs WHERE state=? ORDER BY created_at", (state,)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
    for r in rows:
        click.echo(dict(r))


@cli.group()
def dlq():
    """Dead Letter Queue operations"""
    pass


@dlq.command("list")
def dlq_list():
    conn = init_db()
    rows = conn.execute("SELECT * FROM dlq").fetchall()
    for r in rows:
        click.echo(dict(r))


@dlq.command("retry")
@click.argument("job_id")
def dlq_retry(job_id):
    conn = init_db()
    row = conn.execute("SELECT * FROM dlq WHERE id=?", (job_id,)).fetchone()
    if not row:
        click.echo(f"No job with id {job_id} found in DLQ.")
        return
    command = row["command"]
    # remove from dlq and re-insert as pending
    now = now_iso()
    with conn:
        conn.execute("DELETE FROM dlq WHERE id=?", (job_id,))
        conn.execute("INSERT OR REPLACE INTO jobs (id,command,state,attempts,max_retries,created_at,updated_at,next_attempt_at,last_error) VALUES (?,?,?,?,?,?,?,?,?)",
                     (job_id, command, "pending", 0, load_config().get("max_retries", 3), now, now, None, None))
    click.echo(f"Retried job {job_id} from DLQ.")


@cli.group()
def config():
    """Configuration management"""
    pass


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    cfg = load_config()
    if key in cfg:
        try:
            v = int(value)
        except Exception:
            v = value
        cfg[key] = v
        save_config(cfg)
        click.echo(f"Updated {key} = {v}")
    else:
        click.echo(f"Unknown config key: {key}. Known keys: {list(cfg.keys())}")


@config.command("show")
def config_show():
    click.echo(json.dumps(load_config(), indent=2))


@cli.command("init")
def init_cmd():
    """Initialize DB and default config"""
    init_db()
    load_config()
    click.echo("Initialized database and default config.")


if __name__ == "__main__":
    cli()

