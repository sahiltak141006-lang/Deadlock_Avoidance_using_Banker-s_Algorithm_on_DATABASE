"""Oracle-backed persistence for the dispatcher.

The repository deliberately keeps the method names used by the scheduler/UI so
storage can change without coupling the scheduling layers to SQL details.
"""
import os
from datetime import datetime

import oracledb

from models import Transaction, from_row, REQUEST_FIELDS, RESULT_FIELDS, TXN_TYPES

HOLDERS = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Sana"]


class OracleRepository:
    """Small repository around Oracle connections and parameterized SQL."""

    def __init__(self, user=None, password=None, dsn=None):
        self.user = user or os.getenv("ORACLE_USER")
        self.password = password or os.getenv("ORACLE_PASSWORD")
        self.dsn = dsn or os.getenv("ORACLE_DSN")
        if not all((self.user, self.password, self.dsn)):
            raise RuntimeError(
                "Oracle configuration missing. Set ORACLE_USER, ORACLE_PASSWORD, "
                "and ORACLE_DSN in your environment or .env file."
            )
        self._check_connection()

    def _connect(self):
        return oracledb.connect(user=self.user, password=self.password, dsn=self.dsn)

    def _check_connection(self):
        # Fail early with a useful message if Oracle is not reachable.
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM dual")
                    cur.fetchone()
        except Exception as exc:
            raise RuntimeError(f"Could not connect to Oracle: {exc}") from exc

    @staticmethod
    def _event(cur, txn_id, event, detail="", time_value=None):
        """Persist one lifecycle event in the same database transaction."""
        cur.execute(
            """INSERT INTO event_log
               (transaction_id, event_type, event_details, event_time)
               VALUES (:txn_id, :event_type, :details, :event_time)""",
            txn_id=txn_id, event_type=event[:40], details=str(detail)[:1000],
            event_time=time_value if isinstance(time_value, datetime) else datetime.now(),
        )

    @staticmethod
    def _txn_from_record(row):
        # Column names are selected in the exact shape expected by from_row().
        keys = REQUEST_FIELDS + RESULT_FIELDS[len(REQUEST_FIELDS):]
        return from_row(dict(zip(keys, row)))

    def _account_records(self, cur):
        cur.execute(
            """SELECT a.account_id, u.user_name, a.balance
               FROM accounts a JOIN users u ON u.user_id = a.user_id
               ORDER BY a.account_id"""
        )
        return cur.fetchall()

    def load_accounts(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                return {str(account): {"holder": str(holder), "balance": float(balance)}
                        for account, holder, balance in self._account_records(cur)}

    def load_balances(self):
        return {account: data["balance"] for account, data in self.load_accounts().items()}

    def reset_accounts(self, seed=7):
        """Reset demo balances only when explicitly requested from the UI."""
        # Deterministic demo values; this does not delete users or accounts.
        values = [12000, 8500, 17400, 9200, 15600, 6300]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT account_id FROM accounts ORDER BY account_id")
                ids = [r[0] for r in cur.fetchall()]
                for i, account_id in enumerate(ids):
                    cur.execute("UPDATE accounts SET balance = :balance WHERE account_id = :account_id",
                                balance=values[i % len(values)], account_id=account_id)
                conn.commit()

    def _validate(self, data, accounts):
        txn_type = str(data["txn_type"]).upper()
        if txn_type not in TXN_TYPES:
            raise ValueError(f"Unknown transaction type: {txn_type}")
        if not 1 <= int(data["base_priority"]) <= 5:
            raise ValueError("Priority must be between 1 and 5")
        needed = {"DEPOSIT": ("dst",), "WITHDRAW": ("src",),
                  "TRANSFER": ("src", "dst"), "BALANCE": ("src",)}[txn_type]
        for key in needed:
            if not data.get(key):
                raise ValueError(f"{txn_type} needs '{key}' account")
            if data[key] not in accounts:
                raise ValueError(f"Account {data[key]} does not exist")
        if txn_type == "TRANSFER" and data["src"] == data["dst"]:
            raise ValueError("Source and destination must be different")
        if txn_type != "BALANCE" and float(data["amount"]) <= 0:
            raise ValueError("Amount must be positive")
        if int(data.get("duration", 1)) < 1:
            raise ValueError("Duration must be at least 1 tick")

    def add_request(self, data):
        """Insert a request and its REGISTERED event atomically."""
        accounts = self.load_accounts()
        defaults = {"src": "", "dst": "", "amount": 0, "duration": 1, "user_id": "",
                    "arrival_time": 0, **data}
        defaults["txn_type"] = str(defaults["txn_type"]).upper()
        self._validate(defaults, accounts)
        if defaults["txn_type"] == "BALANCE":
            defaults["amount"] = 0
        if not defaults["user_id"]:
            acc = defaults["src"] or defaults["dst"]
            defaults["user_id"] = accounts[acc]["holder"]

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT NVL(MAX(seq_no), 0) + 1 FROM transactions")
                seq_no = int(cur.fetchone()[0])
                txn_id = str(defaults.get("txn_id") or f"T{seq_no:04d}")
                cur.execute("SELECT COUNT(*) FROM transactions WHERE transaction_id = :txn_id",
                            txn_id=txn_id)
                if cur.fetchone()[0]:
                    return None
                cur.execute("SELECT user_id FROM users WHERE user_name = :user_name",
                            user_name=defaults["user_id"])
                user_ref = cur.fetchone()
                if not user_ref:
                    raise ValueError(f"User {defaults['user_id']} does not exist in Oracle")
                user_ref = str(user_ref[0])
                cur.execute(
                    """INSERT INTO transactions
                       (transaction_id, seq_no, user_id, transaction_type, source_account_id,
                        destination_account_id, amount, base_priority, arrival_time, duration,
                        transaction_status, effective_priority, blocked_count, notes)
                       VALUES (:txn_id, :seq_no,
                         (SELECT user_id FROM users WHERE user_name = :user_name),
                         :txn_type, :src, :dst, :amount, :priority, :arrival, :duration,
                         'QUEUED', 0, 0, NULL)""",
                    txn_id=txn_id, seq_no=seq_no, user_name=defaults["user_id"],
                    txn_type=defaults["txn_type"], src=defaults["src"] or None,
                    dst=defaults["dst"] or None, amount=float(defaults["amount"]),
                    priority=int(defaults["base_priority"]),
                    arrival=int(defaults["arrival_time"]), duration=int(defaults["duration"]),
                )
                self._event(cur, txn_id, "REGISTERED",
                            f"{defaults['txn_type']} queued; priority={defaults['base_priority']}")
                conn.commit()
        return Transaction(txn_id=txn_id, seq_no=seq_no, user_id=user_ref,
                           txn_type=defaults["txn_type"], src=defaults["src"], dst=defaults["dst"],
                           amount=float(defaults["amount"]), base_priority=int(defaults["base_priority"]),
                           arrival_time=int(defaults["arrival_time"]), duration=int(defaults["duration"]))

    def _load_transactions(self, where="", binds=None):
        sql = """SELECT transaction_id, seq_no, user_id, transaction_type, source_account_id,
                        destination_account_id, amount, base_priority, arrival_time, duration,
                        transaction_status, effective_priority, start_time, finish_time,
                        waiting_time, worker_id, blocked_count, notes
                 FROM transactions """
        if where:
            sql += " WHERE " + where
        sql += " ORDER BY seq_no"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, binds or {})
                rows = cur.fetchall()
        names = ["txn_id", "seq_no", "user_id", "txn_type", "src", "dst", "amount",
                 "base_priority", "arrival_time", "duration", "status", "dispatch_priority",
                 "start_time", "finish_time", "waiting_time", "worker", "blocked_count", "note"]
        transactions = []
        for row in rows:
            values = dict(zip(names, row))
            # Oracle stores unused source/destination accounts as NULL; the model
            # uses empty strings so existing scheduling code stays compatible.
            values["src"] = values["src"] or ""
            values["dst"] = values["dst"] or ""
            values["note"] = values["note"] or ""
            transactions.append(from_row(values))
        return transactions

    def load_requests(self):
        return self._load_transactions()

    def load_queued_requests(self):
        return self._load_transactions("transaction_status = 'QUEUED'")

    def last_arrival(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT NVL(MAX(arrival_time), 0) FROM transactions")
                return int(cur.fetchone()[0])

    def clear_requests(self):
        """Remove only not-yet-executed requests; preserve completed history."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM event_log WHERE transaction_id IN
                       (SELECT transaction_id FROM transactions WHERE transaction_status = 'QUEUED')"""
                )
                cur.execute("DELETE FROM transaction_resources WHERE transaction_id IN "
                            "(SELECT transaction_id FROM transactions WHERE transaction_status = 'QUEUED')")
                cur.execute("DELETE FROM transactions WHERE transaction_status = 'QUEUED'")
                conn.commit()

    def save_results(self, txns):
        """Persist simulation outputs without reapplying account effects."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                for txn in txns:
                    cur.execute(
                        """UPDATE transactions SET transaction_status = :status,
                           effective_priority = :eff, start_time = :started, finish_time = :finished,
                           waiting_time = :waited, worker_id = :worker, blocked_count = :blocked,
                           notes = :notes, updated_at = SYSTIMESTAMP
                           WHERE transaction_id = :txn_id""",
                        status=txn.status, eff=int(txn.dispatch_priority),
                        started=txn.start_time if txn.start_time >= 0 else None,
                        finished=txn.finish_time if txn.finish_time >= 0 else None,
                        waited=int(txn.waiting_time), worker=txn.worker if txn.worker >= 0 else None,
                        blocked=int(txn.blocked_count), notes=txn.note[:1000] if txn.note else None,
                        txn_id=txn.txn_id,
                    )
                    self._event(cur, txn.txn_id, txn.status,
                                txn.note or f"simulation finished; waited={txn.waiting_time}")
                conn.commit()

    def save_events(self, events):
        with self._connect() as conn:
            with conn.cursor() as cur:
                for event in events:
                    logical_time = event.get("time")
                    detail = event.get("detail", "")
                    if logical_time is not None:
                        detail = f"[logical_time={logical_time}] {detail}"
                    self._event(cur, event.get("txn_id") or None, event.get("event", "EVENT"), detail)
                conn.commit()

    def load_results(self):
        return self._load_transactions("transaction_status IN ('COMPLETED', 'FAILED', 'DEADLOCKED')")

    def load_events(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT event_time, transaction_id, event_type, event_details
                       FROM event_log ORDER BY event_time, event_id"""
                )
                return [{"time": str(t), "txn_id": str(txn_id or ""),
                         "event": event, "detail": detail or ""}
                        for t, txn_id, event, detail in cur.fetchall()]

    def persist_balances(self, balances):
        """Persist final simulator balances after a run; account effects are not re-applied."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                for account_id, balance in balances.items():
                    cur.execute("UPDATE accounts SET balance = :balance WHERE account_id = :account_id",
                                balance=float(balance), account_id=account_id)
                conn.commit()


    def load_resource_totals_and_claims(self, transactions):
        """Load resource capacities and transaction maximum claims from Oracle.

        Transactions without explicit claims receive a conservative account-lock
        claim against ACCOUNT_RESOURCE when that resource exists. This keeps the
        optional Banker admission control usable with the supplied schema.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT resource_id, resource_name, total_units FROM resource_types ORDER BY resource_id")
                resources = cur.fetchall()
                cur.execute("SELECT transaction_id, resource_id, max_claim FROM transaction_resources")
                claim_rows = cur.fetchall()
        names = [str(row[1]) for row in resources]
        totals = [int(row[2]) for row in resources]
        indexes = {int(row[0]): i for i, row in enumerate(resources)}
        by_txn = {}
        for txn_id, resource_id, claim in claim_rows:
            vector = by_txn.setdefault(str(txn_id), [0] * len(totals))
            vector[indexes[int(resource_id)]] = int(claim)
        account_resource = names.index("ACCOUNT_RESOURCE") if "ACCOUNT_RESOURCE" in names else None
        for txn in transactions:
            if txn.txn_id not in by_txn:
                vector = [0] * len(totals)
                if account_resource is not None:
                    vector[account_resource] = len(txn.accounts())
                by_txn[txn.txn_id] = vector
        return totals, by_txn, names

    def persist_run(self, result, initial_balances):
        """Atomically persist statuses, balance snapshots, ledger entries, and events.

        Account rows are locked and successful effects are revalidated against the
        current database balances before committing. A transfer's debit, credit,
        ledger rows, status updates, and audit events commit or roll back together.
        """
        final_balances = {}
        # Work from Oracle's current locked balances rather than overwriting newer
        # balances with a stale simulation snapshot.
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT account_id, balance FROM accounts ORDER BY account_id FOR UPDATE")
                    final_balances = {str(a): float(b) for a, b in cur.fetchall()}

                    ordered = sorted(result.txns,
                                     key=lambda item: (item.finish_time if item.finish_time >= 0 else 10**12,
                                                       item.seq_no))
                    changed_status = {}
                    for txn in ordered:
                        if txn.status != "COMPLETED":
                            continue

                        cur.execute("SELECT COUNT(*) FROM ledger WHERE transaction_id = :txn_id",
                                    txn_id=txn.txn_id)
                        if int(cur.fetchone()[0]) > 0:
                            txn.status = "FAILED"
                            txn.note = "Duplicate execution prevented: ledger entries already exist"
                            changed_status[txn.txn_id] = txn.note
                            continue

                        deltas = []
                        if txn.txn_type == "DEPOSIT":
                            deltas = [(txn.dst, float(txn.amount), "DEPOSIT")]
                        elif txn.txn_type == "WITHDRAW":
                            deltas = [(txn.src, -float(txn.amount), "WITHDRAWAL")]
                        elif txn.txn_type == "TRANSFER":
                            deltas = [(txn.src, -float(txn.amount), "TRANSFER_OUT"),
                                      (txn.dst, float(txn.amount), "TRANSFER_IN")]
                        elif txn.txn_type == "BALANCE":
                            deltas = []

                        proposed = dict(final_balances)
                        valid = True
                        for account_id, delta, _ in deltas:
                            if account_id not in proposed:
                                valid = False
                                txn.note = f"Account {account_id} does not exist in Oracle"
                                break
                            proposed[account_id] += delta
                            if proposed[account_id] < 0:
                                valid = False
                                txn.note = f"Insufficient funds in account {account_id} at database commit"
                                break
                        if not valid:
                            txn.status = "FAILED"
                            changed_status[txn.txn_id] = txn.note
                            continue

                        final_balances = proposed
                        for account_id, delta, entry_type in deltas:
                            cur.execute(
                                """INSERT INTO ledger
                                   (transaction_id, account_id, entry_type, amount, balance_after)
                                   VALUES (:txn_id, :account_id, :entry_type, :amount, :balance_after)""",
                                txn_id=txn.txn_id, account_id=account_id, entry_type=entry_type,
                                amount=abs(delta), balance_after=final_balances[account_id],
                            )

                    # Persist every transaction's final state in the same DB transaction.
                    for txn in result.txns:
                        cur.execute(
                            """UPDATE transactions SET transaction_status = :status,
                               effective_priority = :eff, start_time = :started, finish_time = :finished,
                               waiting_time = :waited, worker_id = :worker, blocked_count = :blocked,
                               notes = :notes, updated_at = SYSTIMESTAMP
                               WHERE transaction_id = :txn_id""",
                            status=txn.status, eff=int(txn.dispatch_priority),
                            started=txn.start_time if txn.start_time >= 0 else None,
                            finished=txn.finish_time if txn.finish_time >= 0 else None,
                            waited=int(txn.waiting_time), worker=txn.worker if txn.worker >= 0 else None,
                            blocked=int(txn.blocked_count), notes=txn.note[:1000] if txn.note else None,
                            txn_id=txn.txn_id,
                        )
                    for account_id, balance in final_balances.items():
                        cur.execute("UPDATE accounts SET balance = :balance WHERE account_id = :account_id",
                                    balance=balance, account_id=account_id)

                    # Persist the event stream exactly once. Add explicit DB-time
                    # failures if revalidation changed an otherwise completed result.
                    for event in result.events:
                        txn_id = event.get("txn_id") or None
                        event_name = event.get("event", "EVENT")
                        detail = event.get("detail", "")
                        if txn_id in changed_status and event_name in ("COMPLETED", "FAILED"):
                            continue
                        logical_time = event.get("time")
                        if logical_time is not None:
                            detail = f"[logical_time={logical_time}] {detail}"
                        self._event(cur, txn_id, event_name, detail)
                    for txn_id, detail in changed_status.items():
                        self._event(cur, txn_id, "DATABASE_REVALIDATION_FAILED", detail)
                        self._event(cur, txn_id, "FAILED", detail)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        # Keep the in-memory result and UI event stream consistent with the
        # authoritative database snapshot if commit-time validation changed a status.
        result.balances = final_balances
        if changed_status:
            adjusted = []
            for event in result.events:
                txn_id = event.get("txn_id")
                if txn_id in changed_status and event.get("event") in ("COMPLETED", "FAILED"):
                    continue
                adjusted.append(event)
            for txn_id, detail in changed_status.items():
                adjusted.append({"time": next((t.finish_time for t in result.txns
                                                if t.txn_id == txn_id), result.makespan),
                                 "txn_id": txn_id, "event": "DATABASE_REVALIDATION_FAILED",
                                 "detail": detail})
                adjusted.append({"time": next((t.finish_time for t in result.txns
                                                if t.txn_id == txn_id), result.makespan),
                                 "txn_id": txn_id, "event": "FAILED", "detail": detail})
            result.events = adjusted

    def make_banker_admission(self, transactions):
        """Return optional admission callback based on Oracle resource claims."""
        from bankers import banker_admission_callback
        totals, claims, _ = self.load_resource_totals_and_claims(transactions)
        return banker_admission_callback(totals, claims)


    def load_transaction_history(self):
        """Joined transaction history with user and source/destination account owners."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT t.transaction_id, t.seq_no, u.user_name,
                              t.transaction_type, t.source_account_id, su.user_name,
                              t.destination_account_id, du.user_name, t.amount,
                              t.base_priority, t.effective_priority, t.transaction_status,
                              t.arrival_time, t.start_time, t.finish_time, t.waiting_time,
                              t.worker_id, t.blocked_count, t.notes, t.created_at, t.updated_at
                       FROM transactions t
                       JOIN users u ON u.user_id = t.user_id
                       LEFT JOIN accounts sa ON sa.account_id = t.source_account_id
                       LEFT JOIN users su ON su.user_id = sa.user_id
                       LEFT JOIN accounts da ON da.account_id = t.destination_account_id
                       LEFT JOIN users du ON du.user_id = da.user_id
                       ORDER BY t.seq_no"""
                )
                cols = ["transaction_id", "seq_no", "request_user", "type",
                        "source_account", "source_owner", "destination_account",
                        "destination_owner", "amount", "base_priority",
                        "effective_priority", "status", "arrival", "start", "finish",
                        "waiting", "worker", "blocked_count", "notes", "created_at", "updated_at"]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def load_pending_view(self):
        """Read the simplified pending queue view defined in the Oracle schema."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT transaction_id, seq_no, transaction_type, source_account_id,
                              destination_account_id, amount, base_priority, effective_priority,
                              arrival_time, duration, transaction_status
                       FROM pending_transactions_v ORDER BY arrival_time, seq_no"""
                )
                cols = ["transaction_id", "seq_no", "type", "source_account", "destination_account",
                        "amount", "base_priority", "effective_priority", "arrival", "duration", "status"]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def load_status_audit(self):
        """Load database-triggered old/new status history."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT audit_id, transaction_id, old_status, new_status, changed_at
                       FROM transaction_status_audit ORDER BY audit_id DESC"""
                )
                cols = ["audit_id", "transaction_id", "old_status", "new_status", "changed_at"]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def load_ledger(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ledger_id, transaction_id, account_id, entry_type, amount, balance_after, created_at
                       FROM ledger ORDER BY ledger_id DESC"""
                )
                return [{"ledger_id": r[0], "transaction_id": r[1], "account_id": r[2],
                         "entry_type": r[3], "amount": float(r[4]), "balance_after": float(r[5]),
                         "created_at": str(r[6])} for r in cur.fetchall()]

    def load_users(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, user_name FROM users ORDER BY user_id")
                return [{"user_id": r[0], "user_name": r[1]} for r in cur.fetchall()]

    def connection_status(self):
        try:
            self._check_connection()
            return "Connected"
        except Exception as exc:
            return f"Disconnected: {exc}"
