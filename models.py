"""Transaction data model (shared by every layer)."""
from dataclasses import dataclass, asdict, fields
from enum import Enum


class Status(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEADLOCKED = "DEADLOCKED"  # reserved for the later deadlock phase


TXN_TYPES = ("DEPOSIT", "WITHDRAW", "TRANSFER", "BALANCE")


@dataclass
class Transaction:
    txn_id: str
    seq_no: int            # submission order, used for FIFO tie-break
    user_id: str
    txn_type: str          # DEPOSIT / WITHDRAW / TRANSFER / BALANCE
    src: str               # source account ("" if none)
    dst: str               # destination account ("" if none)
    amount: float
    base_priority: int     # 5 = highest, 1 = lowest
    arrival_time: int      # logical clock ticks
    duration: int          # simulated service time (ticks)
    # --- filled by the executor ---
    status: str = Status.QUEUED.value
    dispatch_priority: int = 0   # effective priority at the moment it was dispatched
    start_time: int = -1
    finish_time: int = -1
    waiting_time: int = 0
    worker: int = -1
    blocked_count: int = 0
    note: str = ""

    def accounts(self):
        """Return unique account resources in stable order for locking."""
        return sorted({a for a in (self.src, self.dst) if a})

    def validate(self):
        """Validate model-level fields before a transaction enters the queue."""
        if not self.txn_id:
            raise ValueError("Transaction ID is required")
        if self.txn_type not in TXN_TYPES:
            raise ValueError(f"Unknown transaction type: {self.txn_type}")
        if not 1 <= int(self.base_priority) <= 5:
            raise ValueError("Priority must be between 1 and 5")
        if int(self.arrival_time) < 0 or int(self.duration) < 1:
            raise ValueError("Arrival time must be non-negative and duration at least 1")
        if self.txn_type != "BALANCE" and float(self.amount) <= 0:
            raise ValueError("Amount must be positive")
        if self.txn_type == "TRANSFER" and (not self.src or not self.dst or self.src == self.dst):
            raise ValueError("Transfer requires different source and destination accounts")
        if self.txn_type == "WITHDRAW" and not self.src:
            raise ValueError("Withdrawal requires a source account")
        if self.txn_type == "DEPOSIT" and not self.dst:
            raise ValueError("Deposit requires a destination account")
        if self.txn_type == "BALANCE" and not self.src:
            raise ValueError("Balance enquiry requires a source account")
        return self

    def transition_to(self, new_status):
        """Validate a lifecycle transition; BLOCKED is recoverable, not a failure."""
        new_status = new_status.value if isinstance(new_status, Status) else str(new_status)
        allowed = {
            Status.QUEUED.value: {Status.BLOCKED.value, Status.RUNNING.value, Status.FAILED.value},
            Status.BLOCKED.value: {Status.QUEUED.value, Status.RUNNING.value, Status.FAILED.value},
            Status.RUNNING.value: {Status.COMPLETED.value, Status.FAILED.value},
            Status.COMPLETED.value: set(),
            Status.FAILED.value: set(),
            Status.DEADLOCKED.value: set(),
        }
        if new_status == self.status:
            return self
        if new_status not in allowed.get(self.status, set()):
            raise ValueError(f"Invalid transaction status transition: {self.status} -> {new_status}")
        self.status = new_status
        return self

    def to_row(self):
        return asdict(self)

    def to_row_request(self):
        row = asdict(self)
        return {k: row[k] for k in REQUEST_FIELDS}


REQUEST_FIELDS = [f.name for f in fields(Transaction)][:10]
RESULT_FIELDS = [f.name for f in fields(Transaction)]

_INT = {"seq_no", "base_priority", "arrival_time", "duration", "dispatch_priority",
        "start_time", "finish_time", "waiting_time", "worker", "blocked_count"}


def from_row(row: dict) -> Transaction:
    kw = {}
    for f in fields(Transaction):
        if f.name not in row or row[f.name] is None:
            continue
        v = row[f.name]
        if f.name in _INT:
            v = int(float(v))
        elif f.name == "amount":
            v = float(v)
        kw[f.name] = v
    return Transaction(**kw)
