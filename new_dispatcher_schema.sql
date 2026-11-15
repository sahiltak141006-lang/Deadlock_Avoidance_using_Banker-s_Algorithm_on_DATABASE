SET SQLBLANKLINES ON;
SET SERVEROUTPUT ON;
SET LINESIZE 150;
SET PAGESIZE 100;
-- =========================================================
-- TRANSACTION DISPATCHER
-- BANKER'S ALGORITHM + PRIORITY AGING
-- NEW ORACLE DATABASE SCHEMA
-- =========================================================

SET SERVEROUTPUT ON;
SET LINESIZE 150;
SET PAGESIZE 100;

PROMPT ==========================================
PROMPT CREATING TRANSACTION DISPATCHER TABLES
PROMPT ==========================================


-- =========================================================
-- 1. ACCOUNTS
-- Stores customer accounts and their current balances
-- =========================================================

CREATE TABLE ACCOUNTS (
    ACCOUNT_ID NUMBER GENERATED ALWAYS AS IDENTITY,
    ACCOUNT_NAME VARCHAR2(100) NOT NULL,
    BALANCE NUMBER(12,2) DEFAULT 0 NOT NULL,

    CONSTRAINT PK_ACCOUNTS PRIMARY KEY (ACCOUNT_ID),
    CONSTRAINT CHK_ACCOUNT_BALANCE CHECK (BALANCE >= 0)
);


-- =========================================================
-- 2. DISPATCH_RUNS
-- Stores every execution batch
-- =========================================================

CREATE TABLE DISPATCH_RUNS (
    RUN_ID NUMBER GENERATED ALWAYS AS IDENTITY,
    START_TIME TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    END_TIME TIMESTAMP,
    TOTAL_TRANSACTIONS NUMBER DEFAULT 0,
    COMPLETED_TRANSACTIONS NUMBER DEFAULT 0,
    FAILED_TRANSACTIONS NUMBER DEFAULT 0,
    RUN_STATUS VARCHAR2(20) DEFAULT 'RUNNING',

    CONSTRAINT PK_DISPATCH_RUNS PRIMARY KEY (RUN_ID),
    CONSTRAINT CHK_RUN_STATUS
        CHECK (RUN_STATUS IN ('RUNNING', 'COMPLETED', 'FAILED'))
);


-- =========================================================
-- 3. TRANSACTIONS
-- Stores manual and generated transactions
-- =========================================================

CREATE TABLE TRANSACTIONS (
    TRANSACTION_ID NUMBER GENERATED ALWAYS AS IDENTITY,
    TRANSACTION_TYPE VARCHAR2(30) NOT NULL,
    SOURCE_ACCOUNT_ID NUMBER,
    DESTINATION_ACCOUNT_ID NUMBER,
    AMOUNT NUMBER(12,2) DEFAULT 0 NOT NULL,

    BASE_PRIORITY NUMBER DEFAULT 1 NOT NULL,
    TRANSACTION_STATUS VARCHAR2(20) DEFAULT 'QUEUED',

    CREATED_AT TIMESTAMP DEFAULT SYSTIMESTAMP,
    COMPLETED_AT TIMESTAMP,
    ERROR_MESSAGE VARCHAR2(500),

    CONSTRAINT PK_TRANSACTIONS PRIMARY KEY (TRANSACTION_ID),

    CONSTRAINT FK_TXN_SOURCE
        FOREIGN KEY (SOURCE_ACCOUNT_ID)
        REFERENCES ACCOUNTS(ACCOUNT_ID),

    CONSTRAINT FK_TXN_DESTINATION
        FOREIGN KEY (DESTINATION_ACCOUNT_ID)
        REFERENCES ACCOUNTS(ACCOUNT_ID),

    CONSTRAINT CHK_TXN_TYPE
        CHECK (TRANSACTION_TYPE IN (
            'DEPOSIT',
            'WITHDRAWAL',
            'TRANSFER',
            'BALANCE_INQUIRY'
        )),

    CONSTRAINT CHK_TXN_PRIORITY
        CHECK (BASE_PRIORITY BETWEEN 1 AND 5),

    CONSTRAINT CHK_TXN_AMOUNT
        CHECK (AMOUNT >= 0),

    CONSTRAINT CHK_TXN_STATUS
        CHECK (TRANSACTION_STATUS IN (
            'QUEUED',
            'COMPLETED',
            'FAILED'
        ))
);


-- =========================================================
-- 4. TRANSACTION_RESOURCES
-- Stores maximum resource claims for Banker's Algorithm
-- =========================================================

CREATE TABLE TRANSACTION_RESOURCES (
    TRANSACTION_ID NUMBER NOT NULL,
    RESOURCE_NAME VARCHAR2(50) NOT NULL,
    MAX_CLAIM NUMBER DEFAULT 0 NOT NULL,

    CONSTRAINT PK_TXN_RESOURCES
        PRIMARY KEY (TRANSACTION_ID, RESOURCE_NAME),

    CONSTRAINT FK_RESOURCE_TXN
        FOREIGN KEY (TRANSACTION_ID)
        REFERENCES TRANSACTIONS(TRANSACTION_ID)
        ON DELETE CASCADE,

    CONSTRAINT CHK_MAX_CLAIM
        CHECK (MAX_CLAIM >= 0)
);


-- =========================================================
-- 5. TRANSACTION_HISTORY
-- Stores the result of each transaction execution
-- =========================================================

CREATE TABLE TRANSACTION_HISTORY (
    HISTORY_ID NUMBER GENERATED ALWAYS AS IDENTITY,
    RUN_ID NUMBER NOT NULL,
    TRANSACTION_ID NUMBER NOT NULL,

    EFFECTIVE_PRIORITY NUMBER NOT NULL,
    EXECUTION_STATUS VARCHAR2(20) NOT NULL,

    WAITING_TIME NUMBER DEFAULT 0,
    TURNAROUND_TIME NUMBER DEFAULT 0,

    EXECUTION_START TIMESTAMP,
    EXECUTION_END TIMESTAMP,

    CONSTRAINT PK_TRANSACTION_HISTORY
        PRIMARY KEY (HISTORY_ID),

    CONSTRAINT FK_HISTORY_RUN
        FOREIGN KEY (RUN_ID)
        REFERENCES DISPATCH_RUNS(RUN_ID),

    CONSTRAINT FK_HISTORY_TXN
        FOREIGN KEY (TRANSACTION_ID)
        REFERENCES TRANSACTIONS(TRANSACTION_ID),

    CONSTRAINT CHK_EFFECTIVE_PRIORITY
        CHECK (EFFECTIVE_PRIORITY BETWEEN 1 AND 5),

    CONSTRAINT CHK_HISTORY_STATUS
        CHECK (EXECUTION_STATUS IN (
            'COMPLETED',
            'FAILED'
        ))
);


-- =========================================================
-- 6. TRANSACTION_LEDGER
-- Records account balance changes
-- =========================================================

CREATE TABLE TRANSACTION_LEDGER (
    LEDGER_ID NUMBER GENERATED ALWAYS AS IDENTITY,
    TRANSACTION_ID NUMBER NOT NULL,
    ACCOUNT_ID NUMBER NOT NULL,

    OPERATION_TYPE VARCHAR2(30) NOT NULL,

    BALANCE_BEFORE NUMBER(12,2) NOT NULL,
    BALANCE_AFTER NUMBER(12,2) NOT NULL,

    CHANGE_AMOUNT NUMBER(12,2) NOT NULL,
    ENTRY_TIME TIMESTAMP DEFAULT SYSTIMESTAMP,

    CONSTRAINT PK_TRANSACTION_LEDGER
        PRIMARY KEY (LEDGER_ID),

    CONSTRAINT FK_LEDGER_TXN
        FOREIGN KEY (TRANSACTION_ID)
        REFERENCES TRANSACTIONS(TRANSACTION_ID),

    CONSTRAINT FK_LEDGER_ACCOUNT
        FOREIGN KEY (ACCOUNT_ID)
        REFERENCES ACCOUNTS(ACCOUNT_ID)
);


-- =========================================================
-- 7. INDEXES
-- Improve common database searches
-- =========================================================

CREATE INDEX IDX_TXN_STATUS
ON TRANSACTIONS(TRANSACTION_STATUS);

CREATE INDEX IDX_TXN_CREATED
ON TRANSACTIONS(CREATED_AT);

CREATE INDEX IDX_HISTORY_RUN
ON TRANSACTION_HISTORY(RUN_ID);

CREATE INDEX IDX_LEDGER_ACCOUNT
ON TRANSACTION_LEDGER(ACCOUNT_ID);


-- =========================================================
-- 8. SAMPLE ACCOUNTS
-- =========================================================

INSERT INTO ACCOUNTS (ACCOUNT_NAME, BALANCE)
VALUES ('Sahil', 10000);

INSERT INTO ACCOUNTS (ACCOUNT_NAME, BALANCE)
VALUES ('Rahul', 7500);

INSERT INTO ACCOUNTS (ACCOUNT_NAME, BALANCE)
VALUES ('Aman', 5000);

INSERT INTO ACCOUNTS (ACCOUNT_NAME, BALANCE)
VALUES ('Priya', 12000);


COMMIT;


-- =========================================================
-- 9. VERIFY TABLES AND SAMPLE DATA
-- =========================================================

PROMPT ==========================================
PROMPT ACCOUNTS CREATED
PROMPT ==========================================

SELECT * FROM ACCOUNTS;

PROMPT ==========================================
PROMPT TABLE CREATION COMPLETED
PROMPT ==========================================

SELECT TABLE_NAME
FROM USER_TABLES
WHERE TABLE_NAME IN (
    'ACCOUNTS',
    'DISPATCH_RUNS',
    'TRANSACTIONS',
    'TRANSACTION_RESOURCES',
    'TRANSACTION_HISTORY',
    'TRANSACTION_LEDGER'
)
ORDER BY TABLE_NAME;
