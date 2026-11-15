"""Generate random but valid sample banking transactions."""

import random


def generate_workload(repo, count=5, seed=None):
    accounts = repo.load_accounts()
    ids = list(accounts)
    if not ids:
        raise ValueError("No accounts found in Oracle")

    rng = random.Random(seed)
    created = []

    for _ in range(count):
        options = ["DEPOSIT", "WITHDRAW", "BALANCE"]
        if len(ids) > 1:
            options.append("TRANSFER")
        kind = rng.choice(options)

        src = ""
        dst = ""
        if kind == "DEPOSIT":
            dst = rng.choice(ids)
        elif kind == "WITHDRAW" or kind == "BALANCE":
            src = rng.choice(ids)
        elif kind == "TRANSFER":
            src = rng.choice(ids)
            dst = rng.choice([x for x in ids if x != src])

        amount = 0 if kind == "BALANCE" else rng.choice([100, 200, 300, 500])
        txn = repo.add_request({
            "txn_type": kind, "src": src, "dst": dst,
            "amount": amount, "base_priority": 1
        })
        if txn:
            created.append(txn)

    return created
