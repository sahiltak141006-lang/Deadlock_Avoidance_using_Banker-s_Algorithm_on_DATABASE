-- Run this ONLY if the original dispatcher schema already exists and
-- TRANSACTION_STATUS_AUDIT / TRG_TXN_STATUS_AUDIT do not exist yet.
-- This migration does not drop or alter existing project tables/data.
SET ECHO ON
SET SQLBLANKLINES ON
WHENEVER SQLERROR EXIT SQL.SQLCODE

CREATE TABLE transaction_status_audit (
    audit_id       NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_id VARCHAR2(50) NOT NULL,
    old_status     VARCHAR2(20),
    new_status     VARCHAR2(20) NOT NULL,
    changed_at     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT fk_status_audit_txn FOREIGN KEY (transaction_id)
        REFERENCES transactions(transaction_id)
);

CREATE OR REPLACE TRIGGER trg_txn_status_audit
BEFORE UPDATE OF transaction_status ON transactions
FOR EACH ROW
WHEN (OLD.transaction_status <> NEW.transaction_status)
BEGIN
    :NEW.updated_at := SYSTIMESTAMP;
    INSERT INTO transaction_status_audit
        (transaction_id, old_status, new_status, changed_at)
    VALUES
        (:OLD.transaction_id, :OLD.transaction_status, :NEW.transaction_status, SYSTIMESTAMP);
END;
/

CREATE INDEX idx_status_audit_txn_time
    ON transaction_status_audit(transaction_id, changed_at);

COMMIT;
SELECT COUNT(*) AS status_audit_rows FROM transaction_status_audit;
