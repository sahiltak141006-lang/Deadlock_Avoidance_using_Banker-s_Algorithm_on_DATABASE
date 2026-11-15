"""Serial executor: the safe sequence determines transaction order."""

from copy import deepcopy
from dataclasses import dataclass

from models import Status
from bankers import safety_check
from scheduler import AgingScheduler


@dataclass
class RunResult:
    txns: list
    events: list
    balances: dict
    makespan: int
    safe: bool
    sequence: list
    message: str


def run_transactions(transactions, balances, totals, claims, scheduler=None):
    scheduler = scheduler or AgingScheduler(every=1, cap=5)
    txns = deepcopy(transactions)
    ids = [t.txn_id for t in txns]
    maximum = [claims.get(str(t.txn_id), [0] * len(totals)) for t in txns]
    allocation = [[0] * len(totals) for _ in txns]

    check = safety_check(
        totals, allocation, maximum, ids,
        [t.base_priority for t in txns], scheduler
    )

    if not check.safe:
        return RunResult(
            txns, [], dict(balances), 0, False,
            check.safe_sequence, check.reason
        )

    by_id = {t.txn_id: t for t in txns}
    events = []
    clock = 0

    for item in check.schedule:
        txn = by_id[item["txn_id"]]
        txn.waiting_time = max(0, clock - txn.arrival_time)
        txn.start_time = clock
        txn.finish_time = clock + txn.duration
        txn.turnaround_time = txn.finish_time - txn.arrival_time
        txn.dispatch_priority = item["priority"]
        txn.transition_to(Status.RUNNING)
        events.append({
            "txn_id": txn.txn_id, "event": "RUNNING",
            "time": clock,
            "detail": f"Effective priority {item['priority']}"
        })
        txn.transition_to(Status.COMPLETED)
        clock = txn.finish_time
        events.append({
            "txn_id": txn.txn_id, "event": "COMPLETED",
            "time": clock, "detail": "Completed in Banker's safe sequence"
        })

    events.append({
        "txn_id": "", "event": "SAFE_SEQUENCE",
        "time": clock, "detail": " -> ".join(check.safe_sequence)
    })

    return RunResult(
        txns, events, dict(balances), clock, True,
        check.schedule, "Safe sequence found"
    )
