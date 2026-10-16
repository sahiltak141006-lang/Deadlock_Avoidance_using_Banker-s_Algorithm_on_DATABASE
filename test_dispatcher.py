import unittest

from executor import simulate, LockManager
from generator import build_specs, generate_starvation, generate_workload
from metrics import compute_metrics
from models import Transaction, TXN_TYPES
from bankers import safety_check, banker_admission_callback
from deadlock import find_cycles, has_cycle
from scheduler import AgingConfig, Policy, Scheduler

BAL = {"A": 1000.0, "B": 1000.0, "C": 1000.0, "D": 1000.0}


def tx(i, prio, arrival=0, dur=3, typ="DEPOSIT", src="", dst="A", amount=10):
    return Transaction(txn_id=f"T{i}", seq_no=i, user_id="u", txn_type=typ, src=src, dst=dst,
                       amount=amount, base_priority=prio, arrival_time=arrival, duration=dur)


def order(res):
    return [t.txn_id for t in sorted(res.txns, key=lambda t: t.start_time)]


class SchedulerTests(unittest.TestCase):
    def test_priority_order(self):
        reqs = [tx(1, 1), tx(2, 3), tx(3, 5)]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=1)
        self.assertEqual(order(res), ["T3", "T2", "T1"])

    def test_fifo_policy_ignores_priority(self):
        reqs = [tx(1, 1), tx(2, 3), tx(3, 5)]
        res = simulate(reqs, BAL, Scheduler(Policy.FIFO), workers=1)
        self.assertEqual(order(res), ["T1", "T2", "T3"])

    def test_fifo_tie_break(self):
        reqs = [tx(1, 4), tx(2, 4), tx(3, 4)]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=1)
        self.assertEqual(order(res), ["T1", "T2", "T3"])

    def test_non_preemptive(self):
        reqs = [tx(1, 1, arrival=0, dur=5), tx(2, 5, arrival=1, dur=2)]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=1)
        by = {t.txn_id: t for t in res.txns}
        self.assertEqual(by["T1"].finish_time, 5)   # low txn was NOT interrupted
        self.assertEqual(by["T2"].start_time, 5)

    def test_effective_priority_formula(self):
        s = Scheduler(Policy.PRIORITY_AGING, AgingConfig(interval=5, step=1, cap=5))
        t = tx(1, 1, arrival=0)
        self.assertEqual(s.effective_priority(t, 4), 1)
        self.assertEqual(s.effective_priority(t, 5), 2)
        self.assertEqual(s.effective_priority(t, 100), 5)   # capped

    def _starvation(self, policy):
        reqs = [tx(0, 5, arrival=0, dur=3, typ="WITHDRAW", src="A", dst="")]
        reqs.append(tx(99, 1, arrival=1, dur=2))
        for i in range(1, 15):
            reqs.append(tx(i, 5, arrival=i * 2, dur=3, typ="WITHDRAW", src="B", dst=""))
        res = simulate(reqs, BAL, Scheduler(policy, AgingConfig(5, 1, 5)), workers=1)
        return {t.txn_id: t for t in res.txns}

    def test_starvation_without_aging(self):
        by = self._starvation(Policy.PRIORITY)
        self.assertEqual(by["T99"].finish_time, max(t.finish_time for t in by.values()))

    def test_aging_prevents_starvation(self):
        by = self._starvation(Policy.PRIORITY_AGING)
        last = max(t.finish_time for t in by.values())
        self.assertLess(by["T99"].finish_time, last)
        self.assertLess(by["T99"].waiting_time, self._starvation(Policy.PRIORITY)["T99"].waiting_time)


class ExecutorTests(unittest.TestCase):
    def test_lock_contention_blocks_then_runs(self):
        reqs = [tx(1, 5, dur=4, dst="A"), tx(2, 5, dur=2, dst="A")]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=2)
        by = {t.txn_id: t for t in res.txns}
        self.assertEqual(by["T2"].blocked_count, 1)
        self.assertEqual(by["T2"].start_time, 4)       # waited for T1's lock
        self.assertEqual(by["T2"].status, "COMPLETED")
        self.assertTrue(any(e["txn_id"] == "T2" and e["event"] == "UNBLOCKED"
                            for e in res.events))


    def test_blocked_transaction_has_consistent_wakeup_and_dispatch_events(self):
        reqs = [tx(1, 3, dur=4, dst="A"), tx(2, 5, arrival=1, dur=1, dst="A")]
        result = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=2)
        events = [e["event"] for e in result.events if e["txn_id"] == "T2"]
        self.assertLess(events.index("BLOCKED"), events.index("UNBLOCKED"))
        self.assertLess(events.index("UNBLOCKED"), events.index("RUNNING"))
        self.assertIn("RESOURCE_AVAILABLE", events)

    def test_invalid_negative_amount_fails_before_simulation(self):
        bad = tx(1, 3, amount=-10)
        with self.assertRaises(ValueError):
            simulate([bad], BAL, Scheduler(Policy.PRIORITY), workers=1)

    def test_independent_accounts_run_in_parallel(self):
        reqs = [tx(1, 5, dst="A"), tx(2, 5, dst="B")]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=2)
        self.assertTrue(all(t.start_time == 0 for t in res.txns))

    def test_blocked_top_priority_does_not_stall_others(self):
        reqs = [tx(1, 3, dur=5, dst="A"), tx(2, 5, arrival=1, dst="A"), tx(3, 2, arrival=1, dst="B")]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=2)
        by = {t.txn_id: t for t in res.txns}
        self.assertEqual(by["T3"].start_time, 1)       # ran while T2 was blocked
        self.assertEqual(by["T2"].start_time, 5)

    def test_insufficient_funds_fails(self):
        reqs = [tx(1, 5, typ="WITHDRAW", src="A", dst="", amount=5000)]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=1)
        self.assertEqual(res.txns[0].status, "FAILED")
        self.assertEqual(res.balances["A"], 1000.0)

    def test_transfer_conserves_money(self):
        reqs = [tx(i, 3, typ="TRANSFER", src="A", dst="B", amount=100) for i in range(1, 6)]
        res = simulate(reqs, BAL, Scheduler(Policy.PRIORITY), workers=3)
        self.assertEqual(sum(res.balances.values()), sum(BAL.values()))
        self.assertEqual(res.balances["A"], 500.0)

    def test_input_balances_not_mutated(self):
        bal = dict(BAL)
        simulate([tx(1, 5, dst="A", amount=50)], bal, Scheduler(Policy.PRIORITY))
        self.assertEqual(bal, BAL)

    def test_no_locks_left_and_all_finish(self):
        repo_specs = build_specs(60, ["A", "B", "C", "D"], arrival="staggered", seed=3)
        reqs = [Transaction(txn_id=f"T{i}", seq_no=i, user_id="u", **s) for i, s in enumerate(repo_specs, 1)]
        for pol in Policy:
            res = simulate(reqs, BAL, Scheduler(pol), workers=3)
            self.assertEqual(res.locks_final, {})
            self.assertTrue(all(t.status in ("COMPLETED", "FAILED") for t in res.txns))


class InMemoryRepository:
    """Test double for repository contracts; unit tests do not require Oracle."""

    def __init__(self):
        self.accounts = {a: {"holder": f"user-{a}", "balance": b} for a, b in BAL.items()}
        self.requests = []
        self.results = []
        self.events = []

    def load_accounts(self):
        return {a: dict(v) for a, v in self.accounts.items()}

    def load_balances(self):
        return {a: v["balance"] for a, v in self.accounts.items()}

    def last_arrival(self):
        return max((t.arrival_time for t in self.requests), default=0)

    def add_request(self, data):
        defaults = {"src": "", "dst": "", "amount": 0, "duration": 1,
                    "user_id": "", "arrival_time": 0, **data}
        txn_type = defaults["txn_type"]
        if txn_type not in TXN_TYPES:
            raise ValueError(f"Unknown transaction type: {txn_type}")
        if not 1 <= int(defaults["base_priority"]) <= 5:
            raise ValueError("Priority must be between 1 and 5")
        required = {"DEPOSIT": ("dst",), "WITHDRAW": ("src",),
                    "TRANSFER": ("src", "dst"), "BALANCE": ("src",)}[txn_type]
        for key in required:
            if not defaults.get(key) or defaults[key] not in self.accounts:
                raise ValueError(f"Invalid {key} account")
        if txn_type == "TRANSFER" and defaults["src"] == defaults["dst"]:
            raise ValueError("Source and destination must differ")
        if txn_type != "BALANCE" and float(defaults["amount"]) <= 0:
            raise ValueError("Amount must be positive")
        if int(defaults["duration"]) < 1:
            raise ValueError("Duration must be at least 1")
        seq = len(self.requests) + 1
        txn_id = defaults.get("txn_id") or f"T{seq:04d}"
        if any(t.txn_id == txn_id for t in self.requests):
            return None
        account = defaults["src"] or defaults["dst"]
        txn = Transaction(txn_id=txn_id, seq_no=seq,
                          user_id=defaults["user_id"] or self.accounts[account]["holder"],
                          txn_type=txn_type, src=defaults["src"], dst=defaults["dst"],
                          amount=float(0 if txn_type == "BALANCE" else defaults["amount"]),
                          base_priority=int(defaults["base_priority"]),
                          arrival_time=int(defaults["arrival_time"]),
                          duration=int(defaults["duration"]))
        self.requests.append(txn)
        return txn

    def load_requests(self):
        return list(self.requests)

    def save_results(self, txns):
        self.results = list(txns)

    def load_results(self):
        return list(self.results)

    def save_events(self, events):
        self.events = list(events)

    def load_events(self):
        return list(self.events)


class GeneratorRepoTests(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryRepository()

    def test_same_seed_same_workload(self):
        a = build_specs(20, ["A", "B", "C"], seed=5)
        b = build_specs(20, ["A", "B", "C"], seed=5)
        self.assertEqual(a, b)

    def test_mix_is_not_all_deposits(self):
        types = {s["txn_type"] for s in build_specs(30, ["A", "B", "C"], seed=1)}
        self.assertGreater(len(types), 1)

    def test_concurrent_submission_unique_seq(self):
        txns = generate_workload(self.repo, n=25, seed=9)
        self.assertEqual(len(txns), 25)
        seqs = [t.seq_no for t in self.repo.load_requests()]
        self.assertEqual(seqs, list(range(1, 26)))

    def test_duplicate_txn_id_rejected(self):
        spec = {"txn_id": "X1", "txn_type": "DEPOSIT", "dst": "A", "amount": 10,
                "base_priority": 3, "arrival_time": 0, "duration": 1}
        self.assertIsNotNone(self.repo.add_request(spec))
        self.assertIsNone(self.repo.add_request(spec))
        self.assertEqual(len(self.repo.load_requests()), 1)

    def test_validation(self):
        bad = {"txn_type": "TRANSFER", "src": "A", "dst": "A", "amount": 10,
               "base_priority": 3, "arrival_time": 0, "duration": 1}
        with self.assertRaises(ValueError):
            self.repo.add_request(bad)
        with self.assertRaises(ValueError):
            self.repo.add_request({**bad, "dst": "NOPE"})

    def test_repository_contract_and_full_pipeline(self):
        generate_workload(self.repo, n=15, seed=2)
        reqs = self.repo.load_requests()
        res = simulate(reqs, self.repo.load_balances(), Scheduler(Policy.PRIORITY_AGING), workers=2)
        self.repo.save_results(res.txns)
        self.repo.save_events(res.events)
        self.assertEqual(len(self.repo.load_results()), 15)
        self.assertGreater(len(self.repo.load_events()), 15)

    def test_aging_helps_in_generated_starvation_preset(self):
        generate_starvation(self.repo, n_high=20)
        reqs, bal = self.repo.load_requests(), self.repo.load_balances()
        no_age = simulate(reqs, bal, Scheduler(Policy.PRIORITY), workers=1)
        aged = simulate(reqs, bal, Scheduler(Policy.PRIORITY_AGING, AgingConfig(5, 1, 5)), workers=1)
        low = lambda r: next(t for t in r.txns if t.txn_id == "LOW-1").waiting_time
        self.assertLess(low(aged), low(no_age))
        self.assertGreater(compute_metrics(no_age)["max_wait"], compute_metrics(aged)["max_wait"])

    def test_invalid_aging_config_rejected(self):
        with self.assertRaises(ValueError):
            Scheduler(Policy.PRIORITY_AGING, AgingConfig(interval=0, step=1, cap=5))
        with self.assertRaises(ValueError):
            Scheduler(Policy.PRIORITY_AGING, AgingConfig(interval=5, step=1, cap=6))


class ModelLifecycleTests(unittest.TestCase):
    def test_valid_and_invalid_status_transitions(self):
        txn = tx(1, 3)
        txn.transition_to("BLOCKED")
        txn.transition_to("QUEUED")
        txn.transition_to("RUNNING")
        txn.transition_to("COMPLETED")
        self.assertEqual(txn.status, "COMPLETED")
        with self.assertRaises(ValueError):
            txn.transition_to("RUNNING")


if __name__ == "__main__":
    unittest.main()


class ResourceManagerTests(unittest.TestCase):
    def test_wait_queue_wakes_when_resources_released(self):
        locks = LockManager()
        locks.acquire("T1", ["A"])
        locks.mark_waiting("T2", ["A"])
        self.assertEqual(locks.eligible_waiters(), [])
        newly_eligible = locks.release("T1")
        self.assertEqual(newly_eligible, ["T2"])
        self.assertEqual(locks.wait_for_graph(), {"T2": []})

    def test_wait_for_graph_cycle_detection(self):
        graph = {"T1": ["T2"], "T2": ["T1"], "T3": ["T1"]}
        self.assertTrue(has_cycle(graph))
        self.assertEqual(len(find_cycles(graph)), 1)
        self.assertFalse(has_cycle({"T1": ["T2"], "T2": []}))

    def test_all_or_nothing_locking_has_no_wait_for_cycle(self):
        locks = LockManager()
        locks.acquire("T1", ["A", "B"])
        locks.mark_waiting("T2", ["A"])
        self.assertFalse(has_cycle(locks.wait_for_graph()))


class BankersAlgorithmTests(unittest.TestCase):
    def test_safe_state_and_sequence(self):
        result = safety_check(
            [3, 3, 2],
            [[1, 0, 0], [1, 1, 0], [1, 0, 1]],
            [[1, 1, 0], [2, 1, 1], [3, 1, 1]],
            ["P1", "P2", "P3"],
        )
        self.assertTrue(result.safe)
        self.assertEqual(set(result.safe_sequence), {"P1", "P2", "P3"})

    def test_unsafe_state(self):
        result = safety_check([1], [[1], [0]], [[1], [2]], ["P1", "P2"])
        self.assertFalse(result.safe)

    def test_invalid_resource_matrices(self):
        with self.assertRaises(ValueError):
            safety_check([2, 1], [[0]], [[1]], ["P1"])
        with self.assertRaises(ValueError):
            safety_check([1], [[2]], [[1]], ["P1"])

    def test_admission_rejection_is_terminal_and_logged(self):
        req = tx(1, 3)
        result = simulate([req], BAL, Scheduler(Policy.PRIORITY), workers=1,
                          admission=lambda candidate, context: "REJECT")
        self.assertEqual(result.txns[0].status, "FAILED")
        self.assertTrue(any(e["event"] == "ADMISSION_REJECTED" for e in result.events))

    def test_banker_callback_admits_safe_claim(self):
        req = tx(1, 3)
        callback = banker_admission_callback([2], {"T1": [1]})
        result = simulate([req], BAL, Scheduler(Policy.PRIORITY), workers=1,
                          admission=callback)
        self.assertEqual(result.txns[0].status, "COMPLETED")

    def test_banker_callback_rejects_unsafe_claim(self):
        req = tx(1, 3)
        callback = banker_admission_callback([1], {"T1": [2]})
        result = simulate([req], BAL, Scheduler(Policy.PRIORITY), workers=1,
                          admission=callback)
        self.assertEqual(result.txns[0].status, "FAILED")
        self.assertTrue(any(e["event"] == "ADMISSION_REJECTED" for e in result.events))
