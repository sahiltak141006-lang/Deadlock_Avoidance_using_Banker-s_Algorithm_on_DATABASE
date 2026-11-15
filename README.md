# BankBuddy Transaction Dispatcher

## Setup
1. Install Oracle Database and create/run `new_dispatcher_schema.sql` while connected as `BANKBUDDY`.
2. Create a Python virtual environment.
3. Install dependencies: `pip install -r requirements.txt`.
4. Copy `.env.example` to `.env` and enter the BANKBUDDY password and correct Oracle service name.
5. Start the app: `streamlit run app.py`.

## Important schema limitation
The six-table schema records per-transaction maximum claims in `TRANSACTION_RESOURCES`, but it does not define resource capacities or current allocations. Therefore the repository currently returns empty resource vectors to the safety checker. The queue can be ordered and executed, but the Banker's check is not yet testing contention over named resource types. To enable a full resource-based Banker's Algorithm, add a resource-capacity table and define how allocations are tracked.

## Transaction semantics
- New transactions start at base priority 1.
- Priority rises by 1 after each completed transaction, capped at 5.
- Banker's safety takes precedence; priority chooses only among safe candidates.
- FIFO breaks equal-priority ties.
- Database balance updates, ledger rows, transaction history, and dispatch-run summary are committed in one database transaction.

## Security
Never commit `.env` or share its password. `.env` is intentionally not included in this package.
