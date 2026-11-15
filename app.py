
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from repository import OracleRepository
from scheduler import AgingScheduler
from executor import run_transactions
from generator import generate_workload


# =====================================================
# 1. PAGE CONFIGURATION
# =====================================================

load_dotenv()

st.set_page_config(
    page_title="Bank Transaction Manager",
    page_icon="🏦",
    layout="wide"
)


# =====================================================
# 2. FRONTEND DESIGN
# =====================================================

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

.stApp {
    background: linear-gradient(135deg, #edf4ff, #f5f9ff, #e9f7f7);
    font-family: 'Inter', sans-serif;
    color: #19324d;
}

.block-container {
    max-width: 1150px;
    padding-top: 2rem;
    padding-bottom: 3rem;
}

h1, h2, h3 {
    color: #19345a !important;
    font-weight: 700 !important;
}

.hero {
    background: linear-gradient(110deg, #173b68, #087e8b);
    color: white;
    padding: 27px 30px;
    border-radius: 18px;
    margin-bottom: 25px;
    box-shadow: 0 8px 22px rgba(23,59,104,0.15);
}

.hero h1 {
    color: white !important;
    font-size: 32px;
    margin: 0;
}

.hero p {
    color: #d9edff;
    margin: 8px 0 0 0;
    font-size: 14px;
}

.section-label {
    color: #087e8b;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    margin-bottom: 5px;
}

.field-label {
    color: #173b68 !important;
    font-size: 14px;
    font-weight: 700;
    margin: 12px 0 6px 0;
}

div[data-testid="stWidgetLabel"] p {
    color: #173b68 !important;
    font-weight: 700 !important;
}

div[data-baseweb="select"] > div {
    background-color: white !important;
    border: 1px solid #c5d7e9 !important;
    border-radius: 9px !important;
    color: #19324d !important;
}

div[data-baseweb="input"] input {
    background-color: white !important;
    border: 1px solid #c5d7e9 !important;
    border-radius: 9px !important;
    color: #19324d !important;
}

div[data-baseweb="select"] span {
    color: #19324d !important;
}

.stButton > button,
.stFormSubmitButton > button {
    background: linear-gradient(100deg, #087e8b, #0799a5);
    color: white !important;
    border: none;
    border-radius: 10px;
    padding: 11px 16px;
    font-weight: 700;
}

.stButton > button:hover,
.stFormSubmitButton > button:hover {
    background: linear-gradient(100deg, #066773, #087e8b);
    box-shadow: 0 5px 12px rgba(8,126,139,0.2);
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(255,255,255,0.95);
    border: 1px solid #d5e3f1;
    border-radius: 16px;
    box-shadow: 0 5px 16px rgba(30,70,110,0.05);
    padding: 10px;
}

div[data-testid="stMetric"] {
    background: white;
    border: 1px solid #d5e3f1;
    border-radius: 12px;
    padding: 15px;
}

div[data-testid="stDataFrame"] {
    border: 1px solid #d5e3f1;
    border-radius: 12px;
    overflow: hidden;
}

hr {
    border-color: #d5e3f1;
}

div[data-testid="stAlert"] {
    border-radius: 12px;
}
</style>
""", unsafe_allow_html=True)


# =====================================================
# 3. HEADER
# =====================================================

st.markdown("""
<div class="hero">
    <h1>🏦 Bank Transaction Manager</h1>
    <p>
        Manage banking transactions, generate test workloads
        and execute transactions using Banker's Algorithm.
    </p>
</div>
""", unsafe_allow_html=True)


# =====================================================
# 4. DATABASE CONNECTION
# =====================================================

try:
    repo = OracleRepository()
    accounts = repo.load_accounts()

except Exception as exc:
    st.error(f"Oracle connection failed: {exc}")
    st.stop()

if not accounts:
    st.warning("No accounts found in the database.")
    st.stop()

account_ids = list(accounts)

scheduler = AgingScheduler(every=1, cap=5)


# =====================================================
# 5. SESSION TRACKING
# =====================================================

# Store IDs of transactions created during this app session.

if "new_txn_ids" not in st.session_state:
    st.session_state["new_txn_ids"] = []

if "last_sequence" not in st.session_state:
    st.session_state["last_sequence"] = ""

if "last_generated_mix" not in st.session_state:
    st.session_state["last_generated_mix"] = {}


def remember_new_transactions(items):
    """Remember newly created transaction IDs."""

    ids = st.session_state["new_txn_ids"]

    for item in items:
        if item is None:
            continue

        txn_id = getattr(item, "txn_id", None)

        if txn_id is not None:
            ids.append(str(txn_id))

    st.session_state["new_txn_ids"] = list(dict.fromkeys(ids))


def get_value(row, *names, default=None):
    """Read a value using the first available dictionary key."""

    for name in names:
        if isinstance(row, dict) and name in row:
            return row[name]

    return default


def get_transaction_id(row):
    return str(get_value(
        row, "transaction_id", "txn_id", default=""
    ))


def remember_queue_difference(before_ids):
    """Find newly inserted queued requests, even if add_request returns None."""

    after = repo.load_queued_requests()

    new_items = [
        txn for txn in after
        if str(txn.txn_id) not in before_ids
    ]

    remember_new_transactions(new_items)


# =====================================================
# 6. ADD TRANSACTION AND GENERATE LOAD
# =====================================================

left, right = st.columns([1.15, 0.85], gap="large")


# -------------------- ADD TRANSACTION --------------------

with left:

    with st.container(border=True):

        st.markdown(
            '<div class="section-label">01 · Banking operation</div>',
            unsafe_allow_html=True
        )

        st.subheader("Add transaction")

        types = ["DEPOSIT", "WITHDRAW", "BALANCE"]

        if len(account_ids) > 1:
            types.append("TRANSFER")

        st.markdown(
            '<div class="field-label">Transaction type</div>',
            unsafe_allow_html=True
        )

        kind = st.selectbox(
            "Transaction type",
            types,
            key="txn_kind",
            label_visibility="collapsed"
        )

        with st.form("add_transaction"):

            src = ""
            dst = ""

            # Source account

            if kind in ("WITHDRAW", "TRANSFER", "BALANCE"):

                st.markdown(
                    '<div class="field-label">From account</div>',
                    unsafe_allow_html=True
                )

                src = st.selectbox(
                    "From account",
                    account_ids,
                    format_func=lambda a:
                        f"{a} — {accounts[a]['holder']}",
                    label_visibility="collapsed"
                )

            # Destination account

            if kind in ("DEPOSIT", "TRANSFER"):

                st.markdown(
                    '<div class="field-label">To account</div>',
                    unsafe_allow_html=True
                )

                choices = (
                    [a for a in account_ids if a != src]
                    if kind == "TRANSFER"
                    else account_ids
                )

                dst = st.selectbox(
                    "To account",
                    choices,
                    format_func=lambda a:
                        f"{a} — {accounts[a]['holder']}",
                    label_visibility="collapsed"
                )

            # Amount

            if kind != "BALANCE":

                st.markdown(
                    '<div class="field-label">Amount (₹)</div>',
                    unsafe_allow_html=True
                )

                amount = st.number_input(
                    "Amount",
                    min_value=100.0,
                    value=100.0,
                    step=100.0,
                    label_visibility="collapsed"
                )

                st.caption(
                    "Use + or − to change the amount in ₹100 steps."
                )

            else:
                amount = 0

            submitted = st.form_submit_button(
                "＋ Add transaction to queue",
                use_container_width=True
            )

            if submitted:

                try:
                    before_ids = {
                        str(t.txn_id)
                        for t in repo.load_queued_requests()
                    }

                    repo.add_request({
                        "txn_type": kind,
                        "src": src,
                        "dst": dst,
                        "amount": amount,
                        "base_priority": 1,
                        "arrival_time": 0,
                        "duration": 1
                    })

                    remember_queue_difference(before_ids)

                    st.success("Transaction added successfully!")
                    st.rerun()

                except Exception as exc:
                    st.error(str(exc))


# -------------------- GENERATE LOAD --------------------

with right:

    with st.container(border=True):

        st.markdown(
            '<div class="section-label">02 · Testing</div>',
            unsafe_allow_html=True
        )

        st.subheader("Generate load")

        st.write(
            "Create sample banking transactions automatically "
            "to test the scheduling algorithm."
        )

        with st.form("generate_load"):

            st.markdown(
                '<div class="field-label">Number of transactions</div>',
                unsafe_allow_html=True
            )

            count = st.number_input(
                "Number of transactions",
                min_value=1,
                max_value=30,
                value=5,
                step=1,
                label_visibility="collapsed"
            )

            make_load = st.form_submit_button(
                "⚡ Generate sample load",
                use_container_width=True
            )

            if make_load:

                try:
                    before_ids = {
                        str(t.txn_id)
                        for t in repo.load_queued_requests()
                    }

                    made = generate_workload(repo, int(count))

                    remember_new_transactions(made)

                    # Also detect inserted transactions in case
                    # the generator does not return all objects.
                    remember_queue_difference(before_ids)

                    mix = {}

                    for txn in made:
                        mix[txn.txn_type] = (
                            mix.get(txn.txn_type, 0) + 1
                        )

                    st.session_state["last_generated_mix"] = mix

                    st.success(
                        f"{len(made)} sample transactions generated!"
                    )

                    st.rerun()

                except Exception as exc:
                    st.error(str(exc))

        # Explain automatic aging

        st.markdown("---")

        st.markdown("""
        <div style="
            background:#eaf5ff;
            padding:14px;
            border-radius:10px;
            color:#28547a;
            font-size:13px;
        ">
            <b>Automatic priority aging</b><br>
            Every transaction starts at priority 1. While it waits,
            it gains 1 priority point after each completed transaction.
            Maximum priority: <b>5</b>.
        </div>
        """, unsafe_allow_html=True)


# =====================================================
# 7. GENERATED LOAD SUMMARY
# =====================================================

mix = st.session_state["last_generated_mix"]

if mix:

    st.markdown("---")
    st.subheader("Last generated load")

    st.caption(
        "This shows the types of transactions created by your last Generate Load action."
    )

    cols = st.columns(len(mix))

    for col, (kind, total) in zip(cols, mix.items()):
        col.metric(kind, total)


# =====================================================
# 8. WAITING TRANSACTION QUEUE
# =====================================================

st.markdown("---")

st.markdown(
    '<div class="section-label">03 · Transaction management</div>',
    unsafe_allow_html=True
)

st.subheader("Waiting transactions")

queued = repo.load_queued_requests()

if queued:

    st.dataframe(
        pd.DataFrame([{
            "Transaction ID": t.txn_id,
            "Type": t.txn_type,
            "From": t.src or "—",
            "To": t.dst or "—",
            "Amount (₹)": t.amount,
            "Base priority": t.base_priority
        } for t in queued]),
        hide_index=True,
        use_container_width=True
    )

    st.write("")

    if st.button(
        "▶ Run Banker's Algorithm",
        type="primary",
        use_container_width=True
    ):

        try:
            totals, claims, _ = (
                repo.load_resource_totals_and_claims(queued)
            )

            before = repo.load_balances()

            result = run_transactions(
                queued,
                before,
                totals,
                claims,
                scheduler
            )

            if not result.safe:

                st.error(
                    "Unsafe state. No transaction was executed. "
                    + result.message
                )

            else:

                repo.persist_run(result, before)

                st.session_state["last_sequence"] = " → ".join(
                    x["txn_id"] for x in result.sequence
                )

                st.success("Transactions processed!")
                st.rerun()

        except Exception as exc:
            st.error(f"Execution failed: {exc}")

else:

    st.info(
        "Your transaction queue is empty. Add a transaction "
        "or generate a sample load."
    )


# =====================================================
# 9. LAST EXECUTED SAFE SEQUENCE
# =====================================================

if st.session_state["last_sequence"]:

    st.markdown("---")
    st.subheader("Last executed safe sequence")

    st.markdown(
        f"""
        <div style="
            background:linear-gradient(100deg,#e4f5f5,#eaf2ff);
            padding:18px;
            border-radius:12px;
            border-left:5px solid #087e8b;
            color:#173b68;
            font-weight:700;
            font-size:15px;
        ">
            {st.session_state["last_sequence"]}
        </div>
        """,
        unsafe_allow_html=True
    )


# =====================================================
# 10. TRANSACTION HISTORY
# =====================================================

st.markdown("---")

st.markdown(
    '<div class="section-label">04 · Results</div>',
    unsafe_allow_html=True
)

st.subheader("Transaction history")

try:

    history = repo.load_transaction_history()
    ledger = repo.load_ledger()

    ledger_by_txn = {}

    for entry in ledger:

        tid = str(entry["transaction_id"])

        ledger_by_txn.setdefault(tid, []).append(entry)

    rows = []

    for txn in history:

        tid = get_transaction_id(txn)

        entries = ledger_by_txn.get(tid, [])

        changes = []

        for entry in entries:

            account = entry["account_id"]
            amount = float(entry["amount"])
            after = float(entry["balance_after"])
            entry_type = entry["entry_type"]

            if entry_type in ("WITHDRAWAL", "TRANSFER_OUT"):

                before = after + amount
                sign = "-"

            else:

                before = after - amount
                sign = "+"

            changes.append(
                f"{account}: ₹{before:,.2f} → "
                f"₹{after:,.2f} ({sign}₹{amount:,.2f})"
            )

        status = str(
            get_value(txn, "status", default="UNKNOWN")
        ).upper()

        txn_type = str(
            get_value(txn, "type", "txn_type", default="UNKNOWN")
        ).upper()

        if changes:

            impact = " | ".join(changes)

        elif status == "FAILED":

            impact = "Not applied"

        elif txn_type == "BALANCE" and status == "COMPLETED":

            impact = "Balance checked; no money moved"

        elif status == "QUEUED":

            impact = "Waiting to execute"

        elif status == "RUNNING":

            impact = "Currently executing"

        else:

            impact = "No balance change recorded"

        if status == "COMPLETED":
            display_status = "✅ Completed"

        elif status == "FAILED":
            display_status = "❌ Failed"

        elif status == "QUEUED":
            display_status = "🕒 Waiting"

        else:
            display_status = status

        base_priority = get_value(
            txn, "base_priority", default="—"
        )

        effective_priority = get_value(
            txn, "effective_priority",
            "dispatch_priority",
            default=None
        )

        if effective_priority is not None:
            priority = f"{base_priority} → {effective_priority}"
        else:
            priority = str(base_priority)

        rows.append({

            "Transaction ID": tid,

            "Created": str(get_value(
                txn, "created_at", "arrival_time", default="—"
            )),

            "Type": txn_type,

            "From account": get_value(
                txn, "source_account", "src", default="—"
            ) or "—",

            "To account": get_value(
                txn, "destination_account", "dst", default="—"
            ) or "—",

            "Amount": (
                "—"
                if txn_type == "BALANCE"
                else f"₹{float(get_value(txn, 'amount', default=0)):,.2f}"
            ),

            "Status": display_status,

            "Priority (base → effective)": priority,

            "Balance changes": impact,

            "Notes": get_value(
                txn, "notes", "note", default="—"
            ) or "—"
        })

    # Separate current-session transactions from older records.

    new_ids = set(st.session_state["new_txn_ids"])

    new_rows = [
        row for row in rows
        if row["Transaction ID"] in new_ids
    ]

    old_rows = [
        row for row in rows
        if row["Transaction ID"] not in new_ids
    ]

    # -------------------- NEW TRANSACTIONS --------------------

    st.markdown("### 🟢 New transactions")

    st.caption(
        "Transactions added during your current app session, "
        "including manually added and generated requests."
    )

    st.metric("New transactions", len(new_rows))

    if new_rows:

        st.dataframe(
            pd.DataFrame(new_rows),
            hide_index=True,
            use_container_width=True
        )

    else:

        st.info(
            "No new transactions yet. Add a transaction "
            "or generate a load."
        )

    # -------------------- PREVIOUS TRANSACTIONS --------------------

    st.markdown("### 📁 Previous transactions")

    st.caption(
        "Transactions already stored in Oracle before "
        "your current app session."
    )

    with st.expander(
        f"View previous transactions ({len(old_rows)})",
        expanded=False
    ):

        if old_rows:

            st.dataframe(
                pd.DataFrame(old_rows),
                hide_index=True,
                use_container_width=True
            )

        else:

            st.info("No previous transactions found.")

except Exception as exc:

    st.warning(
        f"Could not load transaction history: {exc}"
    )


# =====================================================
# 11. CURRENT ACCOUNT BALANCES
# =====================================================

st.markdown("---")

st.subheader("Current account balances")

try:

    current = repo.load_accounts()

    st.dataframe(
        pd.DataFrame([{
            "Account ID": account,
            "Account holder": details["holder"],
            "Available balance (₹)": details["balance"]
        } for account, details in current.items()]),
        hide_index=True,
        use_container_width=True
    )

except Exception as exc:

    st.warning(f"Could not load account balances: {exc}")


# =====================================================
# 12. FOOTER
# =====================================================

st.markdown("""
<div style="
    text-align:center;
    color:#66809b;
    font-size:12px;
    padding:20px 0 5px 0;
">
    Bank Transaction Manager · Banker's Algorithm · Priority Aging
</div>
""", unsafe_allow_html=True)
