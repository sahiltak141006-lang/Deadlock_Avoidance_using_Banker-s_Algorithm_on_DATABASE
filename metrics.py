"""Scheduling metrics computed from a SimResult."""
from collections import defaultdict


def compute_metrics(result, starvation_threshold=20):
    done = [t for t in result.txns if t.start_time >= 0]
    n = len(done)
    waits = [t.waiting_time for t in done]
    turn = [t.finish_time - t.arrival_time for t in done if t.finish_time >= 0]
    by_prio = defaultdict(list)
    for t in done:
        by_prio[t.base_priority].append(t.waiting_time)
    completed = sum(1 for t in result.txns if t.status == "COMPLETED")
    failed = sum(1 for t in result.txns if t.status == "FAILED")
    return {
        "policy": result.policy,
        "workers": result.workers,
        "transactions": len(result.txns),
        "completed": completed,
        "failed": failed,
        "avg_wait": round(sum(waits) / n, 2) if n else 0.0,
        "max_wait": max(waits, default=0),
        "avg_turnaround": round(sum(turn) / len(turn), 2) if turn else 0.0,
        "throughput": round(completed / result.makespan, 3) if result.makespan else 0.0,
        "makespan": result.makespan,
        "starved": sum(1 for w in waits if w > starvation_threshold),
        "avg_wait_by_priority": {p: round(sum(v) / len(v), 2) for p, v in sorted(by_prio.items(), reverse=True)},
        "blocked_events": sum(t.blocked_count for t in result.txns),
    }
