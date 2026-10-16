"""Priority scheduler with aging (non-preemptive).

The scheduler only answers one question: "in what order should the ready
transactions be considered right now?"  It knows nothing about locks,
databases or Streamlit, so Banker's Algorithm can be plugged in later at the
executor's admission hook without touching this file.
"""
from dataclasses import dataclass
from enum import Enum


class Policy(str, Enum):
    FIFO = "fifo"                      # baseline: ignore priority
    PRIORITY = "priority"              # static priority, no aging
    PRIORITY_AGING = "priority_aging"  # priority + aging


@dataclass
class AgingConfig:
    interval: int = 5   # every `interval` ticks of waiting ...
    step: int = 1       # ... effective priority goes up by `step`
    cap: int = 5        # ... but never above `cap` (5 = highest priority)


class Scheduler:
    def __init__(self, policy=Policy.PRIORITY_AGING, aging=None):
        self.policy = Policy(policy)
        self.aging = aging or AgingConfig()
        # Invalid configuration is rejected early instead of failing during sorting.
        if int(self.aging.interval) < 1:
            raise ValueError("Aging interval must be at least 1")
        if int(self.aging.step) < 1:
            raise ValueError("Aging step must be at least 1")
        if not 1 <= int(self.aging.cap) <= 5:
            raise ValueError("Aging cap must be between 1 and 5")

    def effective_priority(self, txn, now):
        """effective = min(cap, base + step * floor(waiting / interval))"""
        base = int(txn.base_priority)
        if self.policy != Policy.PRIORITY_AGING:
            return base
        waited = max(0, int(now) - int(txn.arrival_time))
        bonus = (waited // self.aging.interval) * self.aging.step
        # Never lower a base priority when a lower cap was configured.
        return min(5, max(base, min(self.aging.cap, base + bonus)))

    def sort_key(self, txn, now):
        # Smaller key = scheduled first.
        # Ties on effective priority are broken FIFO: arrival time, then seq_no.
        fifo = (txn.arrival_time, txn.seq_no)
        if self.policy == Policy.FIFO:
            return fifo
        return (-self.effective_priority(txn, now),) + fifo

    def order(self, ready, now):
        """Ready transactions in the order they should be considered.
        Effective priority changes with time, so a plain scan/sort at each
        decision is used instead of a static heap (fine for 10-1000 txns)."""
        return sorted(ready, key=lambda t: self.sort_key(t, now))
