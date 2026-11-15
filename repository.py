"""Oracle repository for the BANKBUDDY six-table schema."""

import os
from datetime import datetime
from decimal import Decimal

import oracledb

from models import Transaction, from_row, TXN_TYPES


DB_TYPES = {
    "DEPOSIT": "DEPOSIT",
    "WITHDRAW": "WITHDRAWAL",
    "WITHDRAWAL": "WITHDRAWAL",
    "TRANSFER": "TRANSFER",
    "BALANCE": "BALANCE_INQUIRY",
    "BALANCE_INQUIRY": "BALANCE_INQUIRY",
}
APP_TYPES = {
    "DEPOSIT": "DEPOSIT",
    "WITHDRAWAL": "WITHDRAW",
    "TRANSFER": "TRANSFER",
    "BALANCE_INQUIRY": "BALANCE",
}


class OracleRepository:
    def __init__(self, user=None, password=None, dsn=None):
        self.user = user or os.getenv("ORACLE_USER")
        self.password = password or os.getenv("ORACLE_PASSWORD")
        self.dsn = dsn or os.getenv("ORACLE_DSN")
        if not all((self.user, self.password, self.dsn)):
            raise RuntimeError(
                "Oracle configuration missing. Set ORACLE_USER, "
                "ORACLE_PASSWORD and ORACLE_DSN in your .env file."
            )
        self._check_connection()

    def _connect(self):
        return oracledb.connect(
            user=self.user, password=self.password, dsn=self.dsn
        )

    def _check_connection(self):
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM dual")
                    cur.fetchone()
        except Exception as exc:
            raise RuntimeError(f"Could not connect to Oracle: {exc}") from exc

    def connection_status(self):
        try:
            self._check_connection()
            return "Connected"
        except Exception as exc:
            return f"Disconnected: {exc}"

    def load_accounts(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT account_id, account_name, balance
                    FROM accounts
                    ORDER BY account_id
                """)
                return {
                    str(account_id): {
                        "holder": str(name),
                        "balance": float(balance)
                    }
                    for account_id, name, balance in cur.fetchall()
                }

    def load_balances(self):
        return {
            account_id: details["balance"]
            for account_id, details in self.load_accounts().items()
        }

    def _validate(self, data, accounts):
        kind = str(data.get("txn_type", "")).upper()
        if kind not in TXN_TYPES:
            raise ValueError(f"Unknown transaction type: {kind}")
        if int(data.get("base_priority", 1)) != 1:
            raise ValueError("New transactions must start at base priority 1.")

        src = str(data.get("src") or "")
        dst = str(data.get("dst") or "")
        needed = {
            "DEPOSIT": (dst,),
            "WITHDRAW": (src,),
            "TRANSFER": (src, dst),
            "BALANCE": (src,),
        }[kind]

        for account_id in needed:
            if not account_id:
                raise ValueError(f"{kind} requires the relevant account.")
            if account_id not in accounts:
                raise ValueError(f"Account {account_id} does not exist.")

        if kind == "TRANSFER" and src == dst:
            raise ValueError("Source and destination accounts must differ.")

        amount = float(data.get("amount", 0))
        if kind != "BALANCE" and amount <= 0:
            raise ValueError("Amount must be greater than zero.")
        if kind == "BALANCE" and amount != 0:
            raise ValueError("Balance inquiry amount must be zero.")

    def add_request(self, data):
        accounts = self.load_accounts()
        kind = str(data.get("txn_type", "")).upper()
        defaults = {
            "src": "", "dst": "", "amount": 0,
            "base_priority": 1, **data
        }
        defaults["src"] = str(defaults.get("src") or "")
        defaults["dst"] = str(defaults.get("dst") or "")
        defaults["amount"] = 0 if kind == "BALANCE" else float(defaults["amount"])
        defaults["base_priority"] = 1
        self._validate(defaults, accounts)

        db_kind = DB_TYPES[kind]
        src = int(defaults["src"]) if defaults["src"] else None
        dst = int(defaults["dst"]) if defaults["dst"] else None

        with self._connect() as conn:
            with conn.cursor() as cur:
                out_id = cur.var(oracledb.NUMBER)
                cur.execute("""
                    INSERT INTO transactions
                        (transaction_type, source_account_id,
                         destination_account_id, amount, base_priority,
                         transaction_status)
                    VALUES (:kind, :src, :dst, :amount, 1, 'QUEUED')
                    RETURNING transaction_id INTO :out_id
                """, kind=db_kind, src=src, dst=dst,
                     amount=defaults["amount"], out_id=out_id)
                txn_id = str(int(out_id.getvalue()[0]))
                conn.commit()

        return Transaction(
            txn_id=txn_id, txn_type=kind, src=defaults["src"],
            dst=defaults["dst"], amount=float(defaults["amount"]),
            base_priority=1, arrival_time=0, duration=1
        )

    def _load_transactions(self, status_filter=None):
        sql = """
            SELECT transaction_id, transaction_type, source_account_id,
                   destination_account_id, amount, base_priority,
                   transaction_status, error_message, created_at,
                   completed_at
            FROM transactions
        """
        if status_filter:
            sql += " WHERE transaction_status = :status"
        sql += " ORDER BY transaction_id"
        with self._connect() as conn:
            with conn.cursor() as cur:
                if status_filter:
                    cur.execute(sql, status=status_filter)
                else:
                    cur.execute(sql)
                rows = cur.fetchall()

        result = []
        for row in rows:
            tid, db_kind, src, dst, amount, priority, status, error, created, completed = row
            result.append(Transaction(
                txn_id=str(tid),
                txn_type=APP_TYPES[db_kind],
                src=str(src) if src is not None else "",
                dst=str(dst) if dst is not None else "",
                amount=float(amount),
                base_priority=int(priority),
                status=str(status),
                note=error or "",
                start_time=-1,
                finish_time=-1,
                dispatch_priority=int(priority),
                seq_no=int(tid),
            ))
        return result

    def load_requests(self):
        return self._load_transactions()

    def load_queued_requests(self):
        return self._load_transactions("QUEUED")

    def load_resource_totals_and_claims(self, transactions):
        """
        The supplied schema stores maximum claims but has no resource-capacity
        table or current-allocation columns. Until capacities are defined,
        Banker's resource vectors are empty. The safe-sequence algorithm still
        orders the queued banking operations, but this is not resource-contention
        verification for real resource types.
        """
        return [], {str(txn.txn_id): [] for txn in transactions}, []

    def persist_run(self, result, initial_balances):
        """Commit one dispatch run, transaction statuses, history and ledger."""
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    run_id_var = cur.var(oracledb.NUMBER)
                    cur.execute("""
                        INSERT INTO dispatch_runs
                            (total_transactions, completed_transactions,
                             failed_transactions, run_status)
                        VALUES (:total, :completed, :failed, 'RUNNING')
                        RETURNING run_id INTO :run_id
                    """, total=len(result.txns),
                         completed=sum(t.status == "COMPLETED" for t in result.txns),
                         failed=sum(t.status == "FAILED" for t in result.txns),
                         run_id=run_id_var)
                    run_id = int(run_id_var.getvalue()[0])

                    cur.execute("""
                        SELECT account_id, balance
                        FROM accounts
                        ORDER BY account_id
                        FOR UPDATE
                    """)
                    balances = {str(a): Decimal(str(b)) for a, b in cur.fetchall()}

                    ordered = sorted(
                        result.txns,
                        key=lambda t: (t.finish_time if t.finish_time >= 0 else 10**12,
                                       t.seq_no, int(t.txn_id))
                    )

                    for txn in ordered:
                        status = txn.status
                        error_message = txn.note or None
                        changes = []

                        if status == "COMPLETED":
                            amount = Decimal(str(txn.amount))
                            if txn.txn_type == "DEPOSIT":
                                changes = [(txn.dst, amount, "DEPOSIT")]
                            elif txn.txn_type == "WITHDRAW":
                                changes = [(txn.src, -amount, "WITHDRAWAL")]
                            elif txn.txn_type == "TRANSFER":
                                changes = [
                                    (txn.src, -amount, "TRANSFER_OUT"),
                                    (txn.dst, amount, "TRANSFER_IN")
                                ]

                            proposed = dict(balances)
                            for account_id, delta, _ in changes:
                                if account_id not in proposed:
                                    status = "FAILED"
                                    error_message = f"Account {account_id} not found."
                                    break
                                proposed[account_id] += delta
                                if proposed[account_id] < 0:
                                    status = "FAILED"
                                    error_message = (
                                        f"Insufficient funds in account {account_id}."
                                    )
                                    break

                            if status == "COMPLETED":
                                for account_id, delta, operation in changes:
                                    before = balances[account_id]
                                    after = proposed[account_id]
                                    cur.execute("""
                                        INSERT INTO transaction_ledger
                                            (transaction_id, account_id, operation_type,
                                             balance_before, balance_after, change_amount)
                                        VALUES (:txn_id, :account_id, :operation,
                                                :before, :after, :change)
                                    """, txn_id=int(txn.txn_id),
                                         account_id=int(account_id),
                                         operation=operation,
                                         before=float(before), after=float(after),
                                         change=float(delta))
                                balances = proposed

                        txn.status = status
                        txn.note = error_message or ""
                        cur.execute("""
                            UPDATE transactions
                            SET transaction_status = :status,
                                completed_at = CASE
                                    WHEN :status = 'COMPLETED' THEN SYSTIMESTAMP
                                    ELSE completed_at END,
                                error_message = :error
                            WHERE transaction_id = :txn_id
                        """, status=status, error=error_message,
                             txn_id=int(txn.txn_id))

                        cur.execute("""
                            INSERT INTO transaction_history
                                (run_id, transaction_id, effective_priority,
                                 execution_status, waiting_time, turnaround_time,
                                 execution_start, execution_end)
                            VALUES (:run_id, :txn_id, :priority, :status,
                                    :waiting, :turnaround, :started, :ended)
                        """, run_id=run_id, txn_id=int(txn.txn_id),
                             priority=max(1, min(5, int(txn.dispatch_priority))),
                             status=status,
                             waiting=max(0, int(txn.waiting_time)),
                             turnaround=max(0, int(txn.finish_time - txn.arrival_time)),
                             started=None, ended=None)

                    for account_id, balance in balances.items():
                        cur.execute("""
                            UPDATE accounts SET balance = :balance
                            WHERE account_id = :account_id
                        """, balance=float(balance), account_id=int(account_id))

                    completed = sum(t.status == "COMPLETED" for t in result.txns)
                    failed = sum(t.status == "FAILED" for t in result.txns)
                    run_status = "COMPLETED" if failed == 0 else "FAILED"
                    cur.execute("""
                        UPDATE dispatch_runs
                        SET end_time = SYSTIMESTAMP,
                            completed_transactions = :completed,
                            failed_transactions = :failed,
                            run_status = :status
                        WHERE run_id = :run_id
                    """, completed=completed, failed=failed,
                         status=run_status, run_id=run_id)
                conn.commit()
                result.balances = {k: float(v) for k, v in balances.items()}
            except Exception:
                conn.rollback()
                raise

    def load_transaction_history(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT t.transaction_id, t.created_at, t.transaction_type,
                           t.source_account_id, t.destination_account_id,
                           t.amount, t.base_priority, t.transaction_status,
                           t.error_message,
                           h.effective_priority, h.waiting_time, h.turnaround_time,
                           h.execution_start, h.execution_end
                    FROM transactions t
                    LEFT JOIN (
                        SELECT transaction_id, effective_priority, waiting_time,
                               turnaround_time, execution_start, execution_end,
                               ROW_NUMBER() OVER (
                                   PARTITION BY transaction_id
                                   ORDER BY history_id DESC
                               ) AS rn
                        FROM transaction_history
                    ) h ON h.transaction_id = t.transaction_id AND h.rn = 1
                    ORDER BY t.transaction_id DESC
                """)
                rows = cur.fetchall()

        cols = [
            "transaction_id", "created_at", "type", "source_account",
            "destination_account", "amount", "base_priority", "status",
            "notes", "effective_priority", "waiting_time", "turnaround_time",
            "execution_start", "execution_end"
        ]
        output = []
        for row in rows:
            item = dict(zip(cols, row))
            if item["type"] in APP_TYPES:
                item["type"] = APP_TYPES[item["type"]]
            item["amount"] = float(item["amount"])
            output.append(item)
        return output

    def load_ledger(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ledger_id, transaction_id, account_id,
                           operation_type, balance_before, balance_after,
                           change_amount, entry_time
                    FROM transaction_ledger
                    ORDER BY ledger_id DESC
                """)
                rows = cur.fetchall()
        return [{
            "ledger_id": int(r[0]),
            "transaction_id": str(r[1]),
            "account_id": str(r[2]),
            "entry_type": str(r[3]),
            "balance_before": float(r[4]),
            "balance_after": float(r[5]),
            "amount": abs(float(r[6])),
            "created_at": str(r[7])
        } for r in rows]
