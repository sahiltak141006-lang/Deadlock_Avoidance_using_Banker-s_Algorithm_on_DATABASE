"""Transaction execution simulator on a logical clock.

* N workers, non-preemptive: once a transaction starts, it runs to completion.
* Every transaction must hold simulated locks on ALL its accounts (all-or-nothing,
  acquired together). If a lock is held by a running transaction, it becomes
  BLOCKED and the scheduler considers the next candidate instead.
* `admission` is the hook where Banker's Algorithm will plug in later:
  admission(txn, context) -> bool. Default: always admit.
"""
from dataclasses import dataclass, field, replace

from models import Status
from deadlock import find_cycles


class LockManager:
    """All-or-nothing account locks plus an explicit FIFO wait registry.

    Since every transaction requests all its accounts together, the simulator
    does not create circular hold-and-wait deadlocks. The wait-for graph is
    still exposed for diagnostics and future lock-policy experiments.
    """
    def __init__(self):
        self.owner = {}       # account_id -> txn_id
        self.waiting = {}     # txn_id -> requested account IDs
        self.wait_order = []  # transaction IDs in the order they became blocked

    def holders(self, accounts):
        return sorted({self.owner[a] for a in accounts if a in self.owner})

    def can_acquire(self, accounts):
        return not self.holders(accounts)

    def acquire(self, txn_id, accounts):
        accounts = tuple(sorted(set(accounts)))
        if not self.can_acquire(accounts):
            raise RuntimeError(f"Cannot acquire locked resources for {txn_id}")
        for account in accounts:
            self.owner[account] = txn_id
        self.waiting.pop(txn_id, None)
        self.wait_order = [item for item in self.wait_order if item != txn_id]

    def mark_waiting(self, txn_id, accounts):
        """Register/update the blocked transaction's resource wait request."""
        self.waiting[txn_id] = tuple(sorted(set(accounts)))
        if txn_id not in self.wait_order:
            self.wait_order.append(txn_id)

    def release(self, txn_id):
        """Release all locks held by a transaction and return newly eligible waiters."""
        self.owner = {account: owner for account, owner in self.owner.items()
                      if owner != txn_id}
        return self.eligible_waiters()

    def eligible_waiters(self):
        """Return waiters whose complete resource set is currently available."""
        return [txn_id for txn_id in self.wait_order
                if self.can_acquire(self.waiting.get(txn_id, ()))]

    def wait_for_graph(self):
        """Build txn -> lock-holder edges for current blocked requests."""
        graph = {}
        for txn_id, accounts in self.waiting.items():
            graph[txn_id] = sorted(set(self.holders(accounts)))
        return graph

    def detect_deadlocks(self):
        """Return cycles in the current wait-for graph, if any."""
        return find_cycles(self.wait_for_graph())


@dataclass
class SimResult:
    txns: list
    events: list
    balances: dict
    makespan: int
    policy: str
    workers: int
    locks_final: dict = field(default_factory=dict)


def _apply(txn, balances):
    """Execute the banking effect. Returns (ok, note)."""
    t = txn.txn_type
    if t == "DEPOSIT":
        if txn.dst not in balances:
            return False, f"unknown account {txn.dst}"
        balances[txn.dst] += txn.amount
        return True, ""
    if t == "BALANCE":
        if txn.src not in balances:
            return False, f"unknown account {txn.src}"
        return True, f"balance={balances[txn.src]:.2f}"
    if txn.src not in balances:
        return False, f"unknown account {txn.src}"
    if balances[txn.src] < txn.amount:
        return False, "insufficient funds"
    if t == "WITHDRAW":
        balances[txn.src] -= txn.amount
        return True, ""
    if t == "TRANSFER":
        if txn.dst not in balances:
            return False, f"unknown account {txn.dst}"
        balances[txn.src] -= txn.amount
        balances[txn.dst] += txn.amount
        return True, ""
    return False, f"unknown type {t}"


def simulate(requests, balances, scheduler, workers=2, admission=None):
    """Run a deterministic, non-preemptive simulation on a logical clock."""
    if int(workers) < 1:
        raise ValueError("workers must be at least 1")
    # Work on copies so the same request list can be replayed under other policies.
    # Validate the incoming model before the scheduler sees it. Copies keep replay
    # comparisons isolated from the original request objects.
    for request in requests:
        request.validate()
    txns = [replace(t, status=Status.QUEUED.value, dispatch_priority=0, start_time=-1,
                    finish_time=-1, waiting_time=0, worker=-1, blocked_count=0, note="")
            for t in requests]
    balances = dict(balances)
    events = []

    def log(time, txn_id, event, detail=""):
        events.append({"time": time, "txn_id": txn_id, "event": event, "detail": detail})

    pending = sorted(txns, key=lambda t: (t.arrival_time, t.seq_no))
    pi = 0
    ready, running = [], []          # running: list of (finish_time, txn)
    free_workers = list(range(workers))
    locks = LockManager()
    t = pending[0].arrival_time if pending else 0

    while pi < len(pending) or ready or running:
        # 1) completions at time t
        for fin, tx in sorted([r for r in running if r[0] <= t], key=lambda r: (r[0], r[1].seq_no)):
            running.remove((fin, tx))
            ok, note = _apply(tx, balances)
            final_status = Status.COMPLETED.value if ok else Status.FAILED.value
            tx.transition_to(final_status)
            tx.finish_time, tx.note = fin, note
            # Release resources before another transaction is considered for dispatch.
            newly_eligible = locks.release(tx.txn_id)
            free_workers.append(tx.worker)
            log(fin, tx.txn_id, tx.status, note or f"worker {tx.worker} released locks")
            log(fin, tx.txn_id, "LOCKS_RELEASED", f"worker {tx.worker}; resources={','.join(tx.accounts())}")
            for waiting_id in newly_eligible:
                log(fin, waiting_id, "RESOURCE_AVAILABLE",
                    f"required resources are now available after {tx.txn_id} released locks")
        free_workers.sort()

        # 2) arrivals up to time t
        while pi < len(pending) and pending[pi].arrival_time <= t:
            tx = pending[pi]
            ready.append(tx)
            log(tx.arrival_time, tx.txn_id, "QUEUED",
                f"{tx.txn_type} base_priority={tx.base_priority}")
            pi += 1

        # 3) dispatch while there is a free worker and an eligible transaction
        while free_workers and ready:
            picked = None
            for tx in scheduler.order(ready, t):
                accts = tx.accounts()
                if not locks.can_acquire(accts):
                    if tx.status != Status.BLOCKED.value:
                        tx.transition_to(Status.BLOCKED)
                        tx.blocked_count += 1
                        locks.mark_waiting(tx.txn_id, accts)
                        holders = locks.holders(accts)
                        log(t, tx.txn_id, "BLOCKED",
                            f"waiting for {','.join(accts)} held by {','.join(holders)}")
                        log(t, tx.txn_id, "WAIT_FOR_GRAPH",
                            f"{tx.txn_id} waits for: {','.join(holders) or 'none'}")
                        cycles = locks.detect_deadlocks()
                        if cycles:
                            log(t, tx.txn_id, "DEADLOCK_DETECTED", f"wait-for cycles: {cycles}")
                    continue

                # A blocked transaction becomes eligible again as soon as all its
                # required locks are free; it remains in the ready queue.
                if tx.status == Status.BLOCKED.value:
                    tx.transition_to(Status.QUEUED)
                    locks.waiting.pop(tx.txn_id, None)
                    locks.wait_order = [item for item in locks.wait_order if item != tx.txn_id]
                    log(t, tx.txn_id, "UNBLOCKED",
                        f"resources available: {','.join(accts) or 'no account locks'}")

                decision = admission(tx, {
                    "time": t,
                    "locks": dict(locks.owner),
                    "running": [running_txn for _, running_txn in running],
                    "ready": list(ready),
                }) if admission else True
                if decision in ("REJECT", "REJECTED"):
                    tx.transition_to(Status.FAILED)
                    tx.finish_time = t
                    tx.note = "Rejected by admission control: unsafe resource state"
                    ready.remove(tx)
                    locks.waiting.pop(tx.txn_id, None)
                    locks.wait_order = [item for item in locks.wait_order if item != tx.txn_id]
                    log(t, tx.txn_id, "ADMISSION_REJECTED", tx.note)
                    log(t, tx.txn_id, "FAILED", tx.note)
                    # Continue looking at the remaining ready transactions.
                    continue
                if decision is False:
                    log(t, tx.txn_id, "ADMISSION_WAIT", "admission callback deferred this transaction")
                    continue
                picked = tx
                break
            if picked is None:
                break
            ready.remove(picked)
            w = free_workers.pop(0)
            eff = scheduler.effective_priority(picked, t)
            picked.transition_to(Status.RUNNING)
            picked.worker, picked.start_time = w, t
            picked.dispatch_priority = eff
            picked.waiting_time = t - picked.arrival_time
            locks.acquire(picked.txn_id, picked.accounts())
            running.append((t + picked.duration, picked))
            log(t, picked.txn_id, "RUNNING",
                f"worker {w}, base={picked.base_priority}, effective={eff}, waited={picked.waiting_time}")

        # 4) advance the logical clock to the next event
        nxt = []
        if pi < len(pending):
            nxt.append(pending[pi].arrival_time)
        if running:
            nxt.append(min(r[0] for r in running))
        if not nxt:
            for tx in ready:  # only possible if admission never lets them in
                tx.transition_to(Status.FAILED)
                tx.note = "No eligible transaction could be admitted"
                tx.finish_time = t
                log(t, tx.txn_id, "FAILED", tx.note)
                locks.waiting.pop(tx.txn_id, None)
            ready.clear()
            locks.wait_order.clear()
            break
        t = max(t + 1, min(nxt)) if min(nxt) <= t else min(nxt)

    makespan = max([x.finish_time for x in txns if x.finish_time >= 0], default=0)
    return SimResult(txns=sorted(txns, key=lambda x: x.seq_no), events=events,
                     balances=balances, makespan=makespan,
                     policy=scheduler.policy.value, workers=workers,
                     locks_final=dict(locks.owner))
