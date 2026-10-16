"""Standalone Banker's Algorithm utilities and optional admission callback.

The module is deliberately independent from the scheduler. A caller may opt in
by supplying the returned callback to ``simulate(..., admission=callback)``.
"""
from dataclasses import dataclass


@dataclass
class SafetyResult:
    safe: bool
    safe_sequence: list
    available_after: list
    reason: str = ""


def safety_check(total, allocation, maximum, process_ids=None):
    """Return whether a resource state is safe.

    ``total`` is a vector; ``allocation`` and ``maximum`` are process-by-resource
    matrices. A safe sequence is returned when one exists.
    """
    total = [int(v) for v in total]
    allocation = [[int(v) for v in row] for row in allocation]
    maximum = [[int(v) for v in row] for row in maximum]
    if len(allocation) != len(maximum):
        raise ValueError("Allocation and maximum matrices must have equal row counts")
    n, m = len(allocation), len(total)
    if any(len(row) != m for row in allocation + maximum):
        raise ValueError("Every matrix row must match the resource vector length")
    if any(v < 0 for v in total) or any(v < 0 for row in allocation + maximum for v in row):
        raise ValueError("Resource counts cannot be negative")
    if process_ids is None:
        process_ids = list(range(n))
    if len(process_ids) != n:
        raise ValueError("process_ids must contain one ID per process")
    for i in range(n):
        if any(allocation[i][j] > maximum[i][j] for j in range(m)):
            raise ValueError("Allocation cannot exceed maximum claim")
    allocated = [sum(allocation[i][j] for i in range(n)) for j in range(m)]
    available = [total[j] - allocated[j] for j in range(m)]
    if any(v < 0 for v in available):
        return SafetyResult(False, [], available, "Allocated resources exceed total resources")
    need = [[maximum[i][j] - allocation[i][j] for j in range(m)] for i in range(n)]
    finish = [False] * n
    sequence = []
    changed = True
    while changed:
        changed = False
        for i in range(n):
            if not finish[i] and all(need[i][j] <= available[j] for j in range(m)):
                available = [available[j] + allocation[i][j] for j in range(m)]
                finish[i] = True
                sequence.append(process_ids[i])
                changed = True
    safe = all(finish)
    return SafetyResult(safe, sequence if safe else [],
                        available, "" if safe else "No safe sequence exists")


def banker_admission_callback(total, claims_by_txn, resource_names=None):
    """Create an optional conservative admission callback.

    ``claims_by_txn`` maps transaction IDs to maximum-claim vectors. Active
    transactions in the executor context are treated as holding their full
    claim; this is conservative and suitable for this simulator's all-or-nothing
    resource acquisition model. Missing claims are admitted for backwards
    compatibility; supply claims for every transaction to enforce the policy.
    """
    total = [int(v) for v in total]
    claims_by_txn = {str(k): [int(x) for x in v] for k, v in claims_by_txn.items()}

    def admit(candidate, context):
        candidate_claim = claims_by_txn.get(candidate.txn_id)
        if candidate_claim is None:
            return True
        if len(candidate_claim) != len(total):
            return "REJECT"
        active = list(context.get("running", []))
        active_ids = {txn.txn_id for txn in active}
        processes = []
        allocation = []
        maximum = []
        # Running transactions are considered to have acquired their full claim.
        for txn in active:
            claim = claims_by_txn.get(txn.txn_id, [0] * len(total))
            processes.append(txn.txn_id)
            allocation.append(claim[:])
            maximum.append(claim[:])
        # The candidate is provisionally granted its full claim.
        processes.append(candidate.txn_id)
        allocation.append(candidate_claim[:])
        maximum.append(candidate_claim[:])
        # Waiting transactions remain unallocated and retain their maximum claim.
        for txn in context.get("ready", []):
            if txn.txn_id == candidate.txn_id or txn.txn_id in active_ids:
                continue
            claim = claims_by_txn.get(txn.txn_id)
            if claim is not None:
                processes.append(txn.txn_id)
                allocation.append([0] * len(total))
                maximum.append(claim[:])
        try:
            result = safety_check(total, allocation, maximum, processes)
        except ValueError:
            return "REJECT"
        return True if result.safe else "REJECT"

    return admit
