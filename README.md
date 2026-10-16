# Priority Scheduler + Aging — Oracle-backed Transaction Dispatcher

This project simulates banking transactions using FIFO, Static Priority, and Priority + Aging scheduling. The application stores users, accounts, transactions, ledger entries, and lifecycle events in Oracle Database. CSV persistence has been removed from the runtime path.

## Layers covered in this version

1. **Transaction model (`models.py`)** — transaction fields, model validation, statuses, and validated lifecycle transitions.
2. **Priority scheduler (`scheduler.py`)** — FIFO, static priority, priority aging, validated aging configuration, and FIFO tie-breaking.
3. **Execution engine (`executor.py`)** — logical clock, non-preemptive workers, all-or-nothing account locks, blocked/unblocked transitions, event generation, and simulated banking effects.
4. **Lock/resource manager** — explicit waiting registry, wake-up eligibility, wait-for graph, and cycle detection.
5. **Banker's safety admission** — standalone safety algorithm plus optional admission callback; disabled by default.
6. **Oracle storage** — row-locked, atomic persistence of status changes, account snapshots, ledger effects, and event history.
7. **Metrics/tests/UI** — metrics and policy comparisons, expanded tests, and Oracle-backed record views.

Banker's safety admission is available as an **optional, disabled-by-default** sidebar setting. With it off, the original scheduler behaviour is preserved. When enabled, the app loads Oracle resource totals and transaction claims, checks safety before dispatch, and records rejected unsafe requests as FAILED with `ADMISSION_REJECTED` audit events. Transactions without explicit claims receive an `ACCOUNT_RESOURCE` claim based on their locked account count. The standalone `bankers.py` module can also be tested independently.

Layer 4 adds an explicit lock wait registry, wake-up eligibility checks, wait-for graph diagnostics, and cycle detection. Because the simulator acquires all account locks at once, its current lock strategy does not naturally produce classic circular-wait deadlocks; the detector is available for diagnostics and future lock-policy changes.

## Architecture

```text
Streamlit UI
    |
    v
OracleRepository  <----> Oracle Database
    |                    USERS / ACCOUNTS / TRANSACTIONS
    |                    LEDGER / EVENT_LOG / RESOURCE_TYPES
    v
Transaction Model -> Scheduler + Aging -> Execution Engine / LockManager
                                             |
                                             v
                                  Simulation result + lifecycle events
                                             |
                                             v
                           Oracle status/balance/ledger/audit persistence
```

## Setup

1. Install Oracle Database and connect to the application schema.
2. Run `Transaction_Dispatcher_Schema/dispatcher_schema.sql` in SQL*Plus or a compatible Oracle SQL tool while connected as the application schema. The script creates tables, constraints, indexes, views, and six demo users/accounts. It is a fresh-schema setup script; if the older schema has already been created, do not rerun it over existing tables. Use a fresh application schema or back up and perform a deliberate migration first.
3. Create a `.env` file by copying `.env.example`, then set:
   - `ORACLE_USER`
   - `ORACLE_PASSWORD`
   - `ORACLE_DSN` (for example, `localhost:1521/FREEPDB1`; use the service name for your installation)
4. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Start the UI from the project directory:

   ```bash
   streamlit run app.py
   ```

The app stops with a visible error if Oracle configuration or connectivity is unavailable. It does not silently fall back to local files.

## Run tests

Run pure unit tests from the project directory:

```bash
python -m unittest -v
```

These tests exercise the transaction model, scheduler, aging, executor, lock contention, wait-for graph cycle detection, Banker's safety algorithm, workload generator, and metrics without requiring a live Oracle server. Oracle integration needs a configured Oracle instance and should be tested separately. The Banker's admission option is off by default.

## Scheduler rules

- Priority `5` is highest and `1` is lowest.
- Effective priority for Priority + Aging is `min(cap, base_priority + step * floor(waited / interval))`, while never lowering the base priority.
- Equal effective priorities are ordered by `(arrival_time, seq_no)`.
- Scheduling is non-preemptive.
- Transactions acquire all required account locks together. A blocked transaction stays in the ready set, is marked eligible again when the locks become available, and can be dispatched then.
- Blocking does not itself mean deadlock; all-or-nothing lock acquisition limits the simulator's ability to reproduce classic deadlocks.

## Database behavior

- New requests and their registration event are inserted in Oracle.
- Running queued requests updates transaction statuses and timing fields.
- Completed deposits, withdrawals, and transfers are revalidated against Oracle's current account balances under row locks. Balance snapshots, ledger entries, transaction statuses, and audit events are committed atomically; an error rolls the database transaction back.
- Event history is stored in `EVENT_LOG`; the UI reads users, accounts, transactions, ledger, and events from Oracle.
- Policy comparison runs are simulations only; they are not persisted as executed transactions.
- The “Clear pending requests” action deletes only queued requests and their events. Completed history is retained.
- “Reset sample account balances” is an explicit demo-only action. Use it carefully because it resets account snapshots independently of historical ledger entries.

## Main files

| File | Purpose |
|---|---|
| `models.py` | Transaction model and status lifecycle |
| `scheduler.py` | FIFO, priority, aging |
| `executor.py` | Logical-clock execution and lock manager |
| `repository.py` | Oracle database operations |
| `generator.py` | Deterministic workload and starvation scenario |
| `metrics.py` | Wait, turnaround, throughput, makespan, starvation metrics |
| `app.py` | Streamlit dashboard and Oracle records |
| `test_dispatcher.py` | Unit tests for model, scheduling, and simulation |
| `Transaction_Dispatcher_Schema/dispatcher_schema.sql` | Fresh Oracle schema, constraints, views, indexes, trigger-backed audit |
| `Transaction_Dispatcher_Schema/migration_status_audit_existing_schema.sql` | Safe additive migration for an existing earlier schema |

## DBMS features and existing-schema update

The fresh schema includes `USERS`, `ACCOUNTS`, `TRANSACTIONS`, `TRANSACTION_RESOURCES`, `LEDGER`, `EVENT_LOG`, `BENCHMARK_RUNS`, and `TRANSACTION_STATUS_AUDIT`. Primary/foreign keys and checks validate IDs, references, transaction type/status, positive monetary amounts (except BALANCE inquiries), valid transfer account pairs, and non-negative balances. Indexes cover status, arrival/sequence, source/destination accounts, ledger account/time, events, and status audit history.

- `TRANSACTION_HISTORY_V` joins transactions with user names; `PENDING_TRANSACTIONS_V` exposes queued/blocked work.
- The UI displays the joined transaction history, pending view, transaction records, accounts/balances, ledger, lifecycle events, and trigger-backed old/new status audit.
- Workload generation inserts requests into Oracle. Running the scheduler updates persisted status, priority, timing, worker and blocked-count fields.
- Successful deposits, withdrawals and transfers update account balances and ledger entries in one Oracle transaction. A transfer's debit, credit, both ledger entries, status and event records commit together or roll back together.
- The database trigger records every actual transaction status change in `TRANSACTION_STATUS_AUDIT`; scheduler lifecycle detail remains in `EVENT_LOG`.
- Banker's admission remains optional and off by default to preserve normal scheduler behaviour. Turn it on to demonstrate resource-safety admission.

### Updating an already-existing Oracle schema

Do **not** rerun `dispatcher_schema.sql` over tables that already exist: it is a fresh-schema setup script. Instead, run `Transaction_Dispatcher_Schema/migration_status_audit_existing_schema.sql` in the existing application schema to add only the status-audit table and trigger. This migration assumes the earlier schema already has `TRANSACTIONS` and `UPDATED_AT`.

### SQL operations for demonstration

```sql
-- Parameterized INSERT example (supply unique IDs and valid FK references)
INSERT INTO transactions
    (transaction_id, seq_no, user_id, transaction_type, destination_account_id,
     amount, base_priority, arrival_time, duration, transaction_status,
     effective_priority, blocked_count)
VALUES
    (:transaction_id, :seq_no, :user_id, 'DEPOSIT', :destination_account_id,
     :amount, 3, 0, 2, 'QUEUED', 0, 0);

-- Parameterized UPDATE example
UPDATE transactions
SET notes = :notes, updated_at = SYSTIMESTAMP
WHERE transaction_id = :transaction_id;

-- Search / retrieve transactions
SELECT transaction_id, transaction_type, amount, transaction_status
FROM transactions
WHERE transaction_status IN ('QUEUED', 'BLOCKED')
ORDER BY arrival_time, seq_no;

-- Join a transaction with the user and account owners
SELECT t.transaction_id, u.user_name, t.transaction_type,
       t.source_account_id, su.user_name AS source_owner,
       t.destination_account_id, du.user_name AS destination_owner,
       t.amount, t.transaction_status
FROM transactions t
JOIN users u ON u.user_id = t.user_id
LEFT JOIN accounts sa ON sa.account_id = t.source_account_id
LEFT JOIN users su ON su.user_id = sa.user_id
LEFT JOIN accounts da ON da.account_id = t.destination_account_id
LEFT JOIN users du ON du.user_id = da.user_id
ORDER BY t.seq_no;

-- Simplified views
SELECT * FROM transaction_history_v;
SELECT * FROM pending_transactions_v;

-- Ledger / audit history
SELECT * FROM ledger ORDER BY created_at DESC;
SELECT * FROM event_log ORDER BY event_time DESC;
SELECT * FROM transaction_status_audit ORDER BY changed_at DESC;
```

The executor is a logical-clock scheduling simulation; the workers do not execute as independent Oracle sessions. Oracle is the source of truth for persisted balances, ledger entries, statuses, and audit history. The live Oracle integration still needs to be smoke-tested using the credentials/service name on your machine.
