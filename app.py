"""Streamlit front-end:  streamlit run app.py"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from executor import simulate
from generator import (DEFAULT_MIX, PRIORITY_PRESETS, generate_starvation,
                       generate_workload)
from metrics import compute_metrics
from models import REQUEST_FIELDS, TXN_TYPES
from repository import OracleRepository
from scheduler import AgingConfig, Policy, Scheduler

st.set_page_config(page_title="Priority Scheduler Simulator", layout="wide")
load_dotenv()  # Load local Oracle settings without committing credentials to source control.
try:
    repo = OracleRepository()
except Exception as exc:
    st.error(f"Oracle database connection failed: {exc}")
    st.info("Configure ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN in your .env file, then run the SQL setup script.")
    st.stop()

POLICY_LABELS = {Policy.FIFO: "FIFO (baseline)", Policy.PRIORITY: "Priority (no aging)",
                 Policy.PRIORITY_AGING: "Priority + Aging"}

# ---------------- sidebar: scheduler configuration ----------------
st.sidebar.header("Scheduler settings")
policy = st.sidebar.selectbox("Policy", list(Policy), index=2, format_func=POLICY_LABELS.get)
workers = st.sidebar.number_input("Workers (parallel executors)", 1, 8, 2)
st.sidebar.caption("Use 1 worker to see priority/aging effect most clearly.")
aging = AgingConfig(
    interval=st.sidebar.number_input("Aging interval (ticks)", 1, 100, 5),
    step=st.sidebar.number_input("Aging step (+priority)", 1, 4, 1),
    cap=st.sidebar.slider("Aging cap (max effective priority)", 1, 5, 5),
)
starve_thr = st.sidebar.number_input("Starvation threshold (ticks waited)", 1, 500, 20)
use_banker = st.sidebar.checkbox(
    "Enable Banker's safety admission", value=False,
    help="Optional. Uses Oracle resource claims and ACCOUNT_RESOURCE claims. Off by default to preserve the existing scheduling behaviour."
)
st.sidebar.caption("Priority 5 = highest, 1 = lowest. Scheduling is non-preemptive. "
                   "Time is a logical clock (ticks).")

st.title("Priority Scheduler + Aging: Banking Transaction Simulator")
st.caption("Flow: Streamlit -> Oracle DBMS -> Priority Scheduler + Aging -> Execution simulator "
           "(locks / queued / blocked / unblocked / running / completed)")

tab_req, tab_run, tab_cmp, tab_acc = st.tabs(
    ["1. Requests", "2. Run scheduler", "3. Compare policies", "Oracle records"])


def requests_df():
    reqs = repo.load_requests()
    return pd.DataFrame([t.to_row_request() for t in reqs], columns=REQUEST_FIELDS)


# ---------------- tab 1: requests ----------------
with tab_req:
    accounts = repo.load_accounts()
    ids = sorted(accounts)
    st.subheader("Generate concurrent workload")
    st.caption("This project demonstrates concurrent arrivals through a workload batch; individual one-by-one submission controls are hidden.")
    left, right = st.columns(2)

    with left:
        st.subheader("Concurrent workload settings")
        n = st.number_input("Number of requests", 1, 500, 10)
        mc = st.columns(4)
        mix = {k: mc[i].number_input(k.title(), 0, 100, int(DEFAULT_MIX[k] * 100))
               for i, k in enumerate(DEFAULT_MIX)}
        pc, ac, sc = st.columns(3)
        preset = pc.selectbox("Priority distribution", list(PRIORITY_PRESETS))
        arrival_mode = ac.selectbox("Arrival pattern", ["burst", "staggered"])
        seed = sc.number_input("Seed", 0, 10_000, 42)
        if st.button("Generate (async submit)"):
            if sum(mix.values()) == 0:
                st.error("Mix cannot be all zeros")
            else:
                made = generate_workload(repo, n, mix, PRIORITY_PRESETS[preset], arrival_mode, seed)
                st.success(f"Submitted {len(made)} requests concurrently")
        st.markdown("**Starvation demo preset**: 1 low-priority request among a stream of "
                    "high-priority ones. Run with 1 worker.")
        if st.button("Add starvation scenario"):
            made = generate_starvation(repo)
            st.success(f"Added {len(made)} requests (low-priority one is LOW-1)")
        if st.button("Clear pending requests"):
            repo.clear_requests()
            st.session_state.pop("result", None)
            st.rerun()

    st.subheader("Request queue (loaded from Oracle)")
    st.dataframe(requests_df(), width="stretch", hide_index=True)


# ---------------- helpers for result display ----------------
def gantt(result):
    fig, ax = plt.subplots(figsize=(10, 1.0 + 0.6 * result.workers))
    cmap = plt.cm.viridis
    for t in result.txns:
        if t.start_time < 0:
            continue
        ax.barh(t.worker, t.finish_time - t.start_time, left=t.start_time,
                color=cmap((t.base_priority - 1) / 4), edgecolor="white",
                hatch="//" if t.status == "FAILED" else None)
        ax.text(t.start_time + (t.finish_time - t.start_time) / 2, t.worker, t.txn_id,
                ha="center", va="center", fontsize=6, color="white")
    ax.set_yticks(range(result.workers))
    ax.set_yticklabels([f"Worker {i}" for i in range(result.workers)])
    ax.set_xlabel("logical time (ticks)")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(1, 5))
    fig.colorbar(sm, ax=ax, label="base priority", pad=0.01)
    fig.tight_layout()
    return fig


def results_df(result):
    return pd.DataFrame([{
        "txn_id": t.txn_id, "type": t.txn_type, "src": t.src, "dst": t.dst,
        "amount": t.amount, "base_prio": t.base_priority,
        "eff_prio@dispatch": t.dispatch_priority, "arrival": t.arrival_time,
        "start": t.start_time, "finish": t.finish_time, "waited": t.waiting_time,
        "worker": t.worker, "blocked_times": t.blocked_count,
        "status": t.status, "note": t.note} for t in result.txns])


# ---------------- tab 2: run ----------------
with tab_run:
    if st.button("Run scheduler", type="primary"):
        reqs = repo.load_queued_requests()
        if not reqs:
            st.warning("No queued requests in Oracle. Add requests in tab 1.")
        else:
            initial_balances = repo.load_balances()
            admission = repo.make_banker_admission(reqs) if use_banker else None
            res = simulate(reqs, initial_balances, Scheduler(policy, aging), workers,
                           admission=admission)
            repo.persist_run(res, initial_balances)
            st.session_state["result"] = res
            st.success("Run results, account balances, ledger entries, and lifecycle events saved to Oracle.")

    res = st.session_state.get("result")
    if res:
        m = compute_metrics(res, starve_thr)
        st.subheader(f"Result: {POLICY_LABELS[Policy(res.policy)]}, {res.workers} worker(s)")
        cols = st.columns(6)
        cols[0].metric("Completed / Failed", f"{m['completed']} / {m['failed']}")
        cols[1].metric("Avg wait", m["avg_wait"])
        cols[2].metric("Max wait", m["max_wait"])
        cols[3].metric("Avg turnaround", m["avg_turnaround"])
        cols[4].metric("Throughput (txn/tick)", m["throughput"])
        cols[5].metric(f"Waited > {starve_thr}", m["starved"])
        st.caption("Avg wait by base priority: " +
                   ", ".join(f"P{p}: {w}" for p, w in m["avg_wait_by_priority"].items()))
        st.subheader("Process scheduling Gantt chart")
        st.pyplot(gantt(res))
        result_table = results_df(res)
        st.subheader("Full transaction execution table")
        st.dataframe(result_table, width="stretch", hide_index=True)
        st.subheader("Lifecycle events — queue, block, wake-up, dispatch, completion/failure")
        event_df = pd.DataFrame(res.events)
        st.dataframe(event_df, width="stretch", hide_index=True)
        if not event_df.empty:
            counts = event_df.groupby("event").size().sort_values(ascending=False)
            st.subheader("Scheduler event counts")
            st.bar_chart(counts)
        st.subheader("Priority and waiting-time analysis")
        priority_df = result_table[["txn_id", "base_prio", "eff_prio@dispatch", "waited", "status"]].copy()
        st.dataframe(priority_df, width="stretch", hide_index=True)
        if not priority_df.empty:
            st.bar_chart(priority_df.set_index("txn_id")[["base_prio", "eff_prio@dispatch", "waited"]])
        with st.expander("Balances after this run (persisted to Oracle)"):
            st.dataframe(pd.DataFrame({"account": list(res.balances),
                                       "balance": list(res.balances.values())}),
                         hide_index=True)
    else:
        st.info("Press **Run scheduler** to process the queued requests.")


# ---------------- tab 3: compare ----------------
with tab_cmp:
    st.write("Replays the same queued Oracle requests under all three policies. Comparison runs are simulations only and are not saved as executed transactions.")
    if st.button("Compare FIFO vs Priority vs Priority + Aging"):
        reqs = repo.load_queued_requests()
        if not reqs:
            st.warning("No requests yet.")
        else:
            bal = repo.load_balances()
            rows = []
            for p in Policy:
                r = simulate(reqs, bal, Scheduler(p, aging), workers)
                m = compute_metrics(r, starve_thr)
                low = [t.waiting_time for t in r.txns if t.base_priority == min(x.base_priority for x in reqs)]
                rows.append({"policy": POLICY_LABELS[p], "avg_wait": m["avg_wait"],
                             "max_wait": m["max_wait"], "avg_turnaround": m["avg_turnaround"],
                             "throughput": m["throughput"], "makespan": m["makespan"],
                             f"waited>{starve_thr}": m["starved"],
                             "lowest-priority max wait": max(low, default=0)})
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch", hide_index=True)
            st.bar_chart(df.set_index("policy")[["avg_wait", "max_wait", "lowest-priority max wait"]])


# ---------------- tab 4: live Oracle records ----------------
with tab_acc:
    st.subheader("Oracle connection")
    connection_message = repo.connection_status()
    if connection_message == "Connected":
        st.success("Connected to Oracle")
    else:
        st.error(connection_message)

    left, right = st.columns(2)
    with left:
        st.subheader("Users")
        st.dataframe(pd.DataFrame(repo.load_users()), width="stretch", hide_index=True)
    with right:
        st.subheader("Accounts and persisted balances")
        account_rows = [{"account_id": account_id, **details}
                        for account_id, details in repo.load_accounts().items()]
        st.dataframe(pd.DataFrame(account_rows), width="stretch", hide_index=True)

    st.subheader("Transaction history with user/account details (JOIN / transaction_history_v)")
    try:
        st.dataframe(pd.DataFrame(repo.load_transaction_history()), width="stretch", hide_index=True)
    except Exception as exc:
        st.error(f"Could not load joined transaction history: {exc}")

    st.subheader("Pending request view (pending_transactions_v)")
    try:
        st.dataframe(pd.DataFrame(repo.load_pending_view()), width="stretch", hide_index=True)
    except Exception as exc:
        st.error(f"Could not load pending view: {exc}")

    st.subheader("Transactions from Oracle")
    all_transactions = repo.load_requests()
    transaction_rows = [{
        "txn_id": t.txn_id, "user": t.user_id, "type": t.txn_type, "src": t.src,
        "dst": t.dst, "amount": t.amount, "base_priority": t.base_priority,
        "effective_priority": t.dispatch_priority, "arrival": t.arrival_time,
        "start": t.start_time, "finish": t.finish_time, "waiting": t.waiting_time,
        "worker": t.worker, "blocked_count": t.blocked_count, "status": t.status,
        "notes": t.note
    } for t in all_transactions]
    txn_df = pd.DataFrame(transaction_rows)
    if not txn_df.empty:
        selected_status = st.multiselect("Filter by status", sorted(txn_df["status"].dropna().unique()),
                                         default=sorted(txn_df["status"].dropna().unique()))
        txn_df = txn_df[txn_df["status"].isin(selected_status)]
        search_id = st.text_input("Search transaction ID")
        if search_id:
            txn_df = txn_df[txn_df["txn_id"].str.contains(search_id, case=False, na=False)]
    st.dataframe(txn_df, width="stretch", hide_index=True)

    with st.expander("Resource types and Banker claims"):
        try:
            resource_totals, claims, resource_names = repo.load_resource_totals_and_claims(all_transactions)
            st.dataframe(pd.DataFrame([
                {"resource": name, "total_units": resource_totals[i]}
                for i, name in enumerate(resource_names)
            ]), width="stretch", hide_index=True)
            st.caption("Claims stored in TRANSACTION_RESOURCES are used when present. For transactions without explicit claims, the optional admission check derives ACCOUNT_RESOURCE claims from the number of account locks.")
            claim_rows = []
            for txn_id, vector in claims.items():
                claim_rows.append({"transaction_id": txn_id, **{
                    resource_names[i]: vector[i] for i in range(len(resource_names))
                }})
            st.dataframe(pd.DataFrame(claim_rows), width="stretch", hide_index=True)
        except Exception as exc:
            st.error(f"Could not load resource matrix: {exc}")

    with st.expander("Ledger entries"):
        st.dataframe(pd.DataFrame(repo.load_ledger()), width="stretch", hide_index=True)
    with st.expander("Persistent audit / event history"):
        st.dataframe(pd.DataFrame(repo.load_events()), width="stretch", hide_index=True)
    with st.expander("Database status-change audit (trigger-backed)"):
        try:
            st.dataframe(pd.DataFrame(repo.load_status_audit()), width="stretch", hide_index=True)
        except Exception as exc:
            st.error(f"Could not load status audit: {exc}")

    with st.expander("DBMS operations covered"):
        st.markdown("""
        - **INSERT:** generated requests are registered in `TRANSACTIONS` with a `REGISTERED` event.
        - **UPDATE:** scheduler status, effective priority, start/finish times, wait time, worker and blocked count are persisted.
        - **SELECT / search / JOIN:** transaction filters plus joined user/account history are displayed above.
        - **Views:** `TRANSACTION_HISTORY_V` and `PENDING_TRANSACTIONS_V`.
        - **Constraints / keys / indexes:** primary keys, foreign keys, amount/account/status checks and query indexes are defined in the schema.
        - **Atomicity:** successful balance changes, ledger rows, statuses and lifecycle events commit together; failures roll back.
        - **Audit:** `EVENT_LOG` stores lifecycle events; `TRANSACTION_STATUS_AUDIT` records status changes via a database trigger.
        """)
        with st.expander("SQL examples for viva / DBMS demonstration"):
            st.code("""-- INSERT example (use unique IDs and valid account/user references)
INSERT INTO transactions
    (transaction_id, seq_no, user_id, transaction_type, destination_account_id,
     amount, base_priority, arrival_time, duration, transaction_status,
     effective_priority, blocked_count)
VALUES
    (:transaction_id, :seq_no, :user_id, 'DEPOSIT', :destination_account_id,
     :amount, 3, 0, 2, 'QUEUED', 0, 0);

-- UPDATE example (the application updates these execution fields after scheduling)
UPDATE transactions
SET notes = :notes, updated_at = SYSTIMESTAMP
WHERE transaction_id = :transaction_id;

-- Retrieve and search transactions
SELECT transaction_id, transaction_type, amount, transaction_status
FROM transactions
WHERE transaction_status IN ('QUEUED', 'BLOCKED')
ORDER BY arrival_time, seq_no;

-- Join transactions with users and account owners
SELECT t.transaction_id, u.user_name, t.transaction_type,
       t.source_account_id, su.user_name AS source_owner,
       t.destination_account_id, du.user_name AS destination_owner,
       t.amount, t.transaction_status
FROM transactions t
JOIN users u ON u.user_id = t.user_id
LEFT JOIN accounts sa ON sa.account_id = t.source_account_id
LEFT JOIN users su ON su.user_id = sa.user_id
LEFT JOIN accounts da ON da.account_id = t.destination_account_id
LEFT JOIN users du ON du.user_id = da.user_id
ORDER BY t.seq_no;

-- Views and audit
SELECT * FROM transaction_history_v;
SELECT * FROM pending_transactions_v;
SELECT * FROM ledger ORDER BY created_at DESC;
SELECT * FROM event_log ORDER BY event_time DESC;
SELECT * FROM transaction_status_audit ORDER BY changed_at DESC;""", language="sql")
    st.warning("Reset sample accounts changes account balances. Use it only when you intentionally want to reset the demo balances.")
    if st.button("Reset sample account balances"):
        repo.reset_accounts()
        st.success("Demo balances reset in Oracle.")
        st.rerun()
