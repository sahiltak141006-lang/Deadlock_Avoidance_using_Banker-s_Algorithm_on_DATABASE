"""Banker's safety check. Priority only chooses among safe candidates."""

from dataclasses import dataclass, field
from scheduler import AgingScheduler


@dataclass
class SafetyResult:
    safe: bool
    safe_sequence: list
    available_after: list
    reason: str = ""
    schedule: list = field(default_factory=list)


def safety_check(total, allocation, maximum, process_ids=None,
                 priorities=None, aging=None):
    total = [int(x) for x in total]
    allocation = [[int(x) for x in row] for row in allocation]
    maximum = [[int(x) for x in row] for row in maximum]
    n, m = len(maximum), len(total)

    if len(allocation) != n or any(
        len(row) != m for row in allocation + maximum
    ):
        raise ValueError("Allocation and maximum matrices must match resources")
    if any(x < 0 for x in total + [v for row in allocation + maximum for v in row]):
        raise ValueError("Resource counts cannot be negative")
    if any(allocation[i][j] > maximum[i][j]
           for i in range(n) for j in range(m)):
        raise ValueError("Allocation cannot exceed maximum claim")

    process_ids = process_ids if process_ids is not None else list(range(n))
    priorities = priorities if priorities is not None else [1] * n
    if len(process_ids) != n or len(priorities) != n:
        raise ValueError("One ID and priority are required for each transaction")
    aging = aging or AgingScheduler(every=1, cap=5)
    if any(int(p) < 1 or int(p) > 5 for p in priorities):
        raise ValueError("Base priorities must be between 1 and 5")

    work = [total[j] - sum(allocation[i][j] for i in range(n)) for j in range(m)]
    if any(x < 0 for x in work):
        return SafetyResult(False, [], work, "Allocated resources exceed total resources")

    need = [[maximum[i][j] - allocation[i][j] for j in range(m)] for i in range(n)]
    waiting = set(range(n))
    sequence, schedule = [], []
    completed = 0

    while waiting:
        ready = [i for i in waiting if all(need[i][j] <= work[j] for j in range(m))]
        if not ready:
            return SafetyResult(False, [], work,
                                "No remaining transaction can safely complete", schedule)

        chosen = max(ready, key=lambda i: (aging.priority(priorities[i], completed), -i))
        effective = aging.priority(priorities[chosen], completed)
        sequence.append(process_ids[chosen])
        schedule.append({"txn_id": process_ids[chosen], "priority": effective})
        work = [work[j] + allocation[chosen][j] for j in range(m)]
        waiting.remove(chosen)
        completed += 1

    return SafetyResult(True, sequence, work, "Safe sequence found", schedule)


def banker_admission_callback(total, claims_by_txn, resource_names=None):
    claims_by_txn = {str(k): [int(x) for x in v] for k, v in claims_by_txn.items()}

    def admit(candidate, context):
        claim = claims_by_txn.get(str(candidate.txn_id))
        if claim is None:
            return True
        if len(claim) != len(total):
            return "REJECT"
        return True
    return admit
