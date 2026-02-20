QueueCTL — Background Job Queue CLI

QueueCTL is a command-line tool for managing background jobs with worker processes, retries, and a Dead Letter Queue (DLQ).
It’s written in Python and uses SQLite for persistence. The goal is to keep it minimal but production-ready — easy to understand, extend, and run anywhere.
Demo video link with explanation -https://drive.google.com/drive/folders/1SxKEwlfJyyRgxQVk4CGZNZwEs26MmO4O

Folder Structure

queuectl/
├── queuectl.py                # main CLI application
├── increment_counter.py       # will use this when testing race condition(included in testing)
├── test_script.sh             # full testing automation
├── README.md                  # documentation
├── design.md                  # architecture overview
├── requirements.txt           # dependencies (e.g., click)

1. Setup Instructions
Requirements

Python 3.8 or newer

pip

SQLite3 (bundled with Python)

A Linux or macOS shell (tested on Kali Linux)

1.1 Clone the Repository
git clone https://github.com/Gunateet/queuectl.git
cd queuectl

1.2 Create a Virtual Environment
python3 -m venv venv
source venv/bin/activate


(Windows users: venv\Scripts\activate)

1.3 Install Dependencies
pip install -r requirements.txt


If the file isn’t there, just run:

pip install click

1.4 Initialize the Database
python3 queuectl.py init


This command creates:

queue.db – job and DLQ storage

config.json – retry and backoff settings

worker_state.json / worker_control.json – track and control workers

You should see:

Initialized database and default config.

1.5 Make It a Global Command

Instead of typing python3 queuectl.py, you can make it executable:

chmod +x queuectl.py
sudo mv queuectl.py /usr/local/bin/queuectl


Now run it like any other CLI tool:

queuectl --help

1.6 Test (to see if it is working as command)
queuectl enqueue '{"id":"hello","command":"echo Hello QueueCTL"}'
queuectl worker start --count 1


Output should look like:

Starting 1 worker(s)...
Worker picked job hello: echo Hello QueueCTL
Hello QueueCTL
Job hello completed successfully

1.7 Common Maintenance

Reset the environment at any time:

rm -f queue.db worker_state.json worker_control.json config.json
queuectl init


2. Usage Examples

Once QueueCTL is set up, you can use it directly from your terminal to enqueue jobs, start workers, monitor status, and manage the Dead Letter Queue.

2.1 Enqueue a Job

Adds a new background job to the queue.

queuectl enqueue '{"id":"job1","command":"echo Hello from QueueCTL"}'


Output:

Enqueued job job1


You can enqueue multiple jobs back-to-back:

queuectl enqueue '{"id":"job2","command":"sleep 3 && echo Done"}'
queuectl enqueue '{"id":"job3","command":"bash -c \"exit 1\""}'

2.2 Start Worker(s)

Start one or more workers to process queued jobs.

queuectl worker start --count 2


Sample Output:

Starting 2 worker(s)... (Press Ctrl+C to stop)
Worker picked job job1: echo Hello from QueueCTL
Hello from QueueCTL
Job job1 completed successfully
Worker picked job job2: sleep 3 && echo Done
Done
Job job2 completed successfully
Worker picked job job3: bash -c "exit 1"
Retrying job job3 after 4s (attempt 1)...
Job job3 moved to DLQ after 3 attempts.


Each worker executes one job at a time and automatically retries failed jobs using exponential backoff (delay = base ^ attempts).

2.3 Stop Workers

Gracefully stop any active workers.

queuectl worker stop


Output:

External stop signal received. Exiting worker...
Workers stopped.


Workers finish any in-progress job before stopping.

2.4 Check System Status

View a quick summary of job states and active workers.
Dont stop workers if the active workers needs to be shown, as below.

queuectl status


Output:

Pending: 0
Processing: 0
Completed: 5
Failed: 0
Dead: 1
Active workers (manager): 2

2.5 List Jobs by State

You can view jobs filtered by their state.

Example – list completed jobs:

queuectl list --state completed


Example – list jobs still pending:

queuectl list --state pending

2.6 Manage the Dead Letter Queue (DLQ)

To check which jobs failed permanently:

queuectl dlq list


Output:

{'id': 'job_fail', 'command': 'exit_code=1', 'reason': 'Failed after 3 attempts', 'failed_at': '2025-11-08T18:10:54.974244+00:00'}


To retry a specific DLQ job:

queuectl dlq retry job_fail

job_fail is job id

Output:
Retried job job_fail from DLQ.


This will move the job back to the main queue with state=pending and retry the job, you have to give the job id after writing- queuectl dlq retry 


2.7 Configuration Management

Adjust system-level settings like max retries and backoff base.

Set max retries:

queuectl config set max-retries 5


Set exponential backoff base (default is 2):

queuectl config set backoff-base 3


View current configuration:

queuectl config show


Output:

{
  "max_retries": 5,
  "backoff_base": 3
}

2.8 Simulate a Race Condition (Handled Safely)

This example verifies that the system handles shared resource access safely.

Create a file:

echo 0 > shared_counter.txt


Enqueue multiple jobs that modify it:

queuectl enqueue '{"id":"safe1","command":"python3 increment_counter.py"}'
queuectl enqueue '{"id":"safe2","command":"python3 increment_counter.py"}'
queuectl enqueue '{"id":"safe3","command":"python3 increment_counter.py"}'


Start workers:

queuectl worker start --count 3


After completion:

cat shared_counter.txt


Expected result:

3


(Each job increments the file value once — no race conditions.)

2.9 End-to-End Test Script

For convenience, you can also run the prebuilt test script to validate all features automatically:

bash test_script.sh


3. Architecture Overview

QueueCTL is designed around three key goals: reliability, simplicity, and persistence.
It provides a minimal background job processing system with retry and failure handling similar to production-grade queue systems.

3.1 Core Components
Component	Description
queuectl.py	Main CLI entry point. Handles commands like enqueue, worker start, status, and DLQ management.
SQLite Database (queue.db)	Stores all job data including their states, attempts, and timestamps. Ensures persistence across restarts.
Worker Threads	Execute jobs concurrently. Each worker runs one job at a time and updates the database safely using locking.
Dead Letter Queue (DLQ)	A separate table that stores permanently failed jobs after all retries are exhausted.
Config Store (config.json)	Maintains runtime configuration such as max_retries and exponential backoff base.
Worker State/Control Files	worker_state.json tracks active worker counts; worker_control.json handles external stop signals.
3.2 Job Lifecycle

Every job in QueueCTL moves through well-defined states stored in the database:

State	Description
pending	Job waiting to be picked up by a worker.
processing	Job currently being executed.
completed	Job finished successfully (exit code 0).
failed	Job failed but can still be retried.
dead	Job permanently failed and is moved to the Dead Letter Queue.

Lifecycle flow:

pending → processing → completed
                 ↳ failed → retry (pending again)
                              ↳ dead (DLQ)

3.3 Worker Execution Model

When you start workers with queuectl worker start --count N, each worker thread:

Fetches one pending job atomically (SELECT ... FOR UPDATE-style locking).

Updates its state to processing.

Executes the job’s shell command using Python’s subprocess module.

Checks the command’s exit code.

On success → marks the job as completed.
On failure → increments attempts, applies exponential backoff (delay = base ^ attempts), and retries automatically.

When attempts exceed max_retries, the job is moved to the DLQ and marked dead.

All database updates are done inside transactions to prevent race conditions.

The queue is persistent: even if the system restarts, jobs remain in their last known state.

3.4 Retry and Backoff Strategy

Each job uses exponential backoff to avoid flooding the system with retries:

delay = backoff_base ^ attempts


If backoff_base = 2, retry delays will be 2s, 4s, 8s, 16s …
The values are configurable through:

queuectl config set backoff-base 3
queuectl config set max-retries 5

3.5 Dead Letter Queue (DLQ)

Jobs that fail after the maximum retry limit are automatically moved to a separate DLQ table.
The DLQ stores:

Job ID

Original command

Number of attempts

Last error message

You can inspect and re-queue DLQ jobs using:

queuectl dlq list
queuectl dlq retry <job_id>


This helps diagnose recurring job failures and retry them once the underlying issue is resolved.

3.6 Concurrency & Race-Condition Handling

The queue uses SQLite row-level locking to ensure that a job is never processed by two workers at once.

A global in-process mutex (threading.Lock) prevents concurrent access conflicts when executing jobs that touch shared resources (like files).

Testing with increment_counter.py and shared_counter.txt confirms safe behavior under concurrent loads.

3.7 Persistence Model

All jobs, states, and retry information are stored in queue.db.

The database is committed after every job state transition.

On restart, pending jobs remain pending, failed jobs can retry, and completed or dead jobs are retained for history.

3.8 Graceful Shutdown

Workers handle Ctrl+C or an external stop command via:

queuectl worker stop


When a stop signal is received:

Current jobs finish processing.

Workers update their state to inactive.

The worker count is decremented safely in worker_state.json.

3.9 Configuration and Extensibility

The configuration system is file-based for simplicity but can easily be replaced by environment variables or a database config table.
Potential extensions:

Job priority queues

Scheduled (“run at”) jobs

Job timeout enforcement

Web dashboard for monitoring

3.10 Design Philosophy

QueueCTL focuses on being:

Minimal – Simple SQLite backend, no external services.

Transparent – Each command shows what the system is doing.

Safe – Retries and locking built-in to prevent job loss or duplication.

Extendable – Easy to add priority, scheduling, or metrics later.

4. Assumptions & Trade-offs
4.1 Design Assumptions

Single machine setup
QueueCTL is designed to run locally or on a single server. It doesn’t depend on external brokers like Redis or RabbitMQ.
SQLite is used because it’s file-based, reliable, and ideal for a lightweight queue.

Command-based jobs
Every job executes a shell command. This keeps the interface simple and flexible but assumes that the system shell is available and commands are safe to run.

At-least-once execution
Jobs are retried automatically on failure. A job may run more than once if the system restarts mid-execution, but it will never be silently dropped.

Exponential backoff reliability
Retry delays follow delay = base ^ attempts, assuming short-lived jobs. Long-running or dependent tasks would need scheduling enhancements.

Graceful shutdown
Workers are expected to finish their current job before stopping. Abrupt termination (e.g., power loss) can leave a job in processing state until restart.

4.2 Trade-offs & Rationale
Decision	Benefit	Limitation
SQLite for persistence	Easy to set up, portable, reliable for single-host	Not ideal for very high concurrency or distributed use
Threaded workers	Simple parallelism without extra processes	Limited by Python’s GIL; not optimal for heavy CPU tasks
File-based config and control	Transparent, easy to inspect and edit	Not centralized or secured for multi-user systems
CLI interface only	Minimal dependencies, simple testing	No built-in web dashboard or API layer
Retry + DLQ logic inside worker	Self-contained reliability	Harder to monitor or control externally
4.3 Simplifications

Job priority queues, scheduling, and timeouts were left out to keep the system minimal.

Output logs are printed to console instead of being stored.

There is no authentication or multi-tenant control layer, since this is meant for local use.

4.4 Future Improvements

Replace SQLite with Redis or PostgreSQL for multi-worker, multi-host setups.

Add job priority and scheduling (run_at field).

Implement job timeout and output logging.

Add a lightweight web dashboard or REST API for monitoring.

Introduce metrics (success/failure rates, average processing time).

5. Testing Instructions

To run all tests automatically:

chmod +x test_script.sh

bash test_script.sh


This script sequentially validates:

Job enqueue and completion

Retry and DLQ

Race condition safety

Config updates

Sequential + parallel worker execution

Worker stop and persistence






To run tests manually follow these:

5.1 Reset & Initialize

Always start with a clean environment:

rm -f queue.db worker_state.json worker_control.json config.json
queuectl init


Expected:

Initialized database and default config.

 5.2 Enqueue Jobs (Pending State)

Add a few jobs to the queue:

queuectl enqueue '{"id":"job1","command":"echo Job 1"}'
queuectl enqueue '{"id":"job2","command":"echo Job 2"}'
queuectl enqueue '{"id":"job3","command":"echo Job 3"}'


Check current state:

queuectl status


Expected:

Pending: 3
Processing: 0
Completed: 0
Failed: 0
Dead: 0
Active workers (manager): 0

 State: pending — jobs are waiting to be picked up.

 5.3 Single Worker — Sequential Processing

Start one worker to verify it picks up one job at a time:

queuectl worker start --count 1


Expected:

Starting 1 worker(s)... (Press Ctrl+C to stop)
Worker picked job job1: echo Job 1
Job 1
✅ Job job1 completed successfully
Worker picked job job2: echo Job 2
Job 2
✅ Job job2 completed successfully
Worker picked job job3: echo Job 3
Job 3
✅ Job job3 completed successfully


Stop the worker (Ctrl + C).

Check status:

queuectl status


Expected:

Pending: 0
Processing: 0
Completed: 3
Failed: 0
Dead: 0
Active workers (manager): 0


 State transitions: pending → processing → completed

 5.4 Failed Job & Retry Logic

Enqueue a job that fails intentionally:

queuectl enqueue '{"id":"failjob","command":"bash -c \"exit 1\"","max_retries":2}'
queuectl worker start --count 1


Start a worker:

queuectl worker start --count 1

Expected:                                                   

Starting 1 worker(s)... (Press Ctrl+C to stop)
Worker picked job failjob: bash -c "exit 1"
⏳ Retrying job failjob after 2s (attempt 1)...
Worker picked job failjob: bash -c "exit 1"
💀 Job failjob moved to DLQ after 2 attempts.



Check state after:

queuectl status


Expected:

Pending: 0
Processing: 0
Completed: 3
Failed: 0
Dead: 1
Active workers (manager): 0


 State transitions: processing → failed (retry) → dead

 5.5 Dead Letter Queue (DLQ)

List all permanently failed jobs:

queuectl dlq list


Expected:


{'id': 'failjob', 'command': 'exit_code=1', 'reason': 'Failed after 2 attempts', 'failed_at': '2025-11-08T21:08:22.670199+00:00'}



-This is the test result after the job moved to dlq, it shows all failed jobs.


Retry a DLQ job:

queuectl dlq retry failjob

Expected:

Retried job failjob from DLQ.


queuectl status


-if executed successfully the completed count will increase otherwise dead count remains same.

 5.6 Race Condition Test (Safe Concurrent Execution)

Initialize a shared file and enqueue jobs that increment it.
increment_counter.py is given .

echo 0 > shared_counter.txt
queuectl enqueue '{"id":"safe1","command":"python3 increment_counter.py"}'
queuectl enqueue '{"id":"safe2","command":"python3 increment_counter.py"}'
queuectl enqueue '{"id":"safe3","command":"python3 increment_counter.py"}'


Run with multiple workers:

queuectl worker start --count 3


After completion:

cat shared_counter.txt


Expected:

3


 Each job incremented the file exactly once.
No race condition occurred due to thread locking in the code.


 5.7 Persistence Across Restarts

Test that jobs survive restarts:

queuectl enqueue '{"id":"persist","command":"sleep 10 && echo Survived Restart"}'
queuectl worker start --count 1


Stop the worker early (Ctrl + C).

Then restart it:

queuectl worker start --count 1


Expected:

Starting 1 worker(s)... (Press Ctrl+C to stop)
Worker picked job failjob: exit_code=1
✅ Job failjob completed successfully
Worker picked job persist: sleep 10 && echo Survived Restart
Survived Restart
✅ Job persist completed successfully


 5.8 Multiple Workers in Parallel
queuectl enqueue '{"id":"multi1","command":"echo W1"}'
queuectl enqueue '{"id":"multi2","command":"echo W2"}'
queuectl enqueue '{"id":"multi3","command":"echo W3"}'
queuectl worker start --count 3


Expected:

Starting 3 worker(s)... (Press Ctrl+C to stop)
Worker picked job multi1: echo W1
W1
Worker picked job multi2: echo W2
W2
✅ Job multi1 completed successfully
Worker picked job multi3: echo W3
W3
✅ Job multi2 completed successfully
✅ Job multi3 completed successfully


 5.9 Listing Jobs by State

List completed jobs:

queuectl list --state completed


List pending jobs:

queuectl enqueue '{"id":"multi1","command":"echo W1"}'

-do not run worker and it will be in pending till it gets the worker.

queuectl list --state pending


Expected:

{'id': 'multi1', 'command': 'echo W1', 'state': 'pending', 'attempts': 0, 'max_retries': 3, 'created_at': '2025-11-08T21:30:42.223906+00:00', 'updated_at': '2025-11-08T21:30:42.223906+00:00', 'next_attempt_at': None, 'last_error': None}

-we can see the pending job, if there are more ,all pending jobs with all these parameters will be listed here.

queuectl status
Expected:


Pending: 1
Processing: 0
Completed: 7
Failed: 0
Dead: 1
Active workers (manager): 0

-Pending got incremented to 1 , and other parameters are due to previously persisted jobs

 5.10 Worker Stop Command

Start a long-running job:

queuectl enqueue '{"id":"stopdemo","command":"sleep 30 && echo Done"}'
queuectl worker start --count 1


In another terminal:

queuectl worker stop


Expected in first terminal:

  Stop signal sent to all workers (via control file).
ℹ️ No local worker manager found.



 6. Link for Video Demo

    Link= https://drive.google.com/drive/folders/1SxKEwlfJyyRgxQVk4CGZNZwEs26MmO4O
