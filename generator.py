"""Workload generator. Requests are created from a seeded RNG (reproducible)
and then SUBMITTED concurrently with asyncio tasks into the repository."""
import asyncio
import random

DEFAULT_MIX = {"DEPOSIT": 0.30, "WITHDRAW": 0.30, "TRANSFER": 0.35, "BALANCE": 0.05}

PRIORITY_PRESETS = {
    "Uniform":     {1: 1, 2: 1, 3: 1, 4: 1, 5: 1},
    "Mostly low":  {1: 5, 2: 3, 3: 2, 4: 1, 5: 1},
    "Mostly high": {1: 1, 2: 1, 3: 2, 4: 3, 5: 5},
}


def build_specs(n, accounts, mix=None, prio_weights=None, arrival="burst",
                seed=42, start_arrival=0, max_duration=5):
    """Deterministic list of request dicts (no I/O)."""
    rng = random.Random(seed)
    mix = mix or DEFAULT_MIX
    prio_weights = prio_weights or PRIORITY_PRESETS["Uniform"]
    ids = sorted(accounts)
    types, tw = list(mix), list(mix.values())
    prios, pw = list(prio_weights), list(prio_weights.values())
    specs, clock = [], start_arrival
    for _ in range(n):
        ttype = rng.choices(types, tw)[0]
        spec = {"txn_type": ttype, "base_priority": rng.choices(prios, pw)[0],
                "duration": rng.randint(1, max_duration), "arrival_time": clock,
                "amount": rng.randrange(1, 40) * 100, "src": "", "dst": ""}
        if ttype == "DEPOSIT":
            spec["dst"] = rng.choice(ids)
        elif ttype in ("WITHDRAW", "BALANCE"):
            spec["src"] = rng.choice(ids)
        else:
            spec["src"], spec["dst"] = rng.sample(ids, 2)
        specs.append(spec)
        if arrival == "staggered":
            clock += rng.randint(0, 3)
    return specs


def starvation_specs(accounts, n_high=20, seed=1):
    """One low-priority request surrounded by a stream of high-priority ones.
    Run it with 1 worker: without aging the low request waits for everything."""
    rng = random.Random(seed)
    ids = sorted(accounts)
    specs = []
    for i in range(n_high):
        a, b = rng.sample(ids, 2)
        specs.append({"txn_type": "TRANSFER", "src": a, "dst": b, "amount": 100,
                      "base_priority": 5, "duration": 3, "arrival_time": i * 2})
        if i == 0:   # the victim arrives right after the first high-priority txn
            v = rng.choice(ids)
            specs.append({"txn_type": "DEPOSIT", "dst": v, "src": "", "amount": 500,
                          "base_priority": 1, "duration": 2, "arrival_time": 1,
                          "txn_id": "LOW-1"})
    return specs


async def _submit(repo, spec, lock):
    await asyncio.sleep(0)            # yield: lets the other submit tasks interleave
    async with lock:                   # one writer at a time -> unique seq_no
        return repo.add_request(spec)


async def submit_concurrently(repo, specs):
    lock = asyncio.Lock()
    results = await asyncio.gather(*(_submit(repo, s, lock) for s in specs))
    return [r for r in results if r is not None]


def generate_workload(repo, n=10, mix=None, prio_weights=None, arrival="burst",
                      seed=42, max_duration=5):
    specs = build_specs(n, repo.load_accounts().keys(), mix, prio_weights, arrival,
                        seed, start_arrival=repo.last_arrival(), max_duration=max_duration)
    return asyncio.run(submit_concurrently(repo, specs))


def generate_starvation(repo, n_high=20, seed=1):
    specs = starvation_specs(repo.load_accounts().keys(), n_high, seed)
    return asyncio.run(submit_concurrently(repo, specs))
