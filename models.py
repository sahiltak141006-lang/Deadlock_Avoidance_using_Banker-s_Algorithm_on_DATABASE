"""Shared transaction models used by the Streamlit app and dispatcher."""

from dataclasses import dataclass, field
from enum import Enum

TXN_TYPES = ("DEPOSIT", "WITHDRAW", "TRANSFER", "BALANCE")


class Status(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class Transaction:
    txn_id: str
    txn_type: str
    src: str = ""
    dst: str = ""
    amount: float = 0.0
    base_priority: int = 1
    arrival_time: int = 0
    duration: int = 1
    status: str = "QUEUED"
    start_time: int = -1
    finish_time: int = -1
    waiting_time: int = 0
    turnaround_time: int = 0
    worker: int = -1
    blocked_count: int = 0
    note: str = ""
    dispatch_priority: int = 1
    seq_no: int = 0

    def transition_to(self, status):
        self.status = status.value if isinstance(status, Status) else str(status)

    def accounts(self):
        return [x for x in (self.src, self.dst) if x]

    def to_row_request(self):
        return {
            "txn_id": self.txn_id,
            "txn_type": self.txn_type,
            "src": self.src,
            "dst": self.dst,
            "amount": self.amount,
            "base_priority": self.base_priority,
            "arrival_time": self.arrival_time,
            "duration": self.duration,
        }


REQUEST_FIELDS = [
    "txn_id", "txn_type", "src", "dst", "amount",
    "base_priority", "arrival_time", "duration"
]
RESULT_FIELDS = REQUEST_FIELDS + [
    "status", "start_time", "finish_time", "waiting_time",
    "turnaround_time", "worker", "blocked_count", "note",
    "dispatch_priority", "seq_no"
]


def from_row(row):
    """Build a Transaction from a dictionary containing model field names."""
    values = dict(row)
    allowed = Transaction.__dataclass_fields__.keys()
    values = {key: value for key, value in values.items() if key in allowed}
    if values.get("txn_id") is not None:
        values["txn_id"] = str(values["txn_id"])
    if values.get("src") is None:
        values["src"] = ""
    if values.get("dst") is None:
        values["dst"] = ""
    return Transaction(**values)
