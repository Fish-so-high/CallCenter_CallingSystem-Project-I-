CREATE TABLE employees (
    employee_id    VARCHAR(20)  PRIMARY KEY,
    employee_name  TEXT         NOT NULL,
    status         VARCHAR(16)  NOT NULL DEFAULT 'AVAILABLE',
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    
    CONSTRAINT ck_employees_status CHECK (status IN ('AVAILABLE', 'BUSY', 'BREAK', 'OFFLINE'))
);

CREATE TABLE call_records (
    call_id           VARCHAR(64)   PRIMARY KEY,          
    phone_number      VARCHAR(10)   NOT NULL,
    employee_id       VARCHAR(20)   REFERENCES employees (employee_id), 
    received_at       TIMESTAMPTZ   NOT NULL,
    started_at        TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ,
    duration_seconds  NUMERIC(10,3) NOT NULL DEFAULT 0,
    tag               VARCHAR(16)   NOT NULL,
    recorded_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    
    CONSTRAINT ck_call_records_phone CHECK (phone_number ~ '^(09|08|02|03)[0-9]{8}$'),
    CONSTRAINT ck_call_records_tag CHECK (tag IN ('Success', 'Miss', 'Break', 'Rejected', 'Abandoned')),
    CONSTRAINT ck_call_records_duration CHECK (duration_seconds >= 0),
    CONSTRAINT ck_call_records_time_order CHECK (started_at IS NULL OR ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT ck_call_records_employee_required CHECK (tag IN ('Rejected', 'Abandoned') OR employee_id IS NOT NULL)
);

CREATE INDEX ix_call_records_employee_id ON call_records (employee_id);

CREATE INDEX ix_call_records_day_key ON call_records ((COALESCE(ended_at, received_at)), call_id);

CREATE TABLE employee_monthly_stats (
    employee_id         VARCHAR(20)   NOT NULL REFERENCES employees (employee_id),
    year_month          VARCHAR(7)    NOT NULL,           
    successful_calls    INTEGER       NOT NULL DEFAULT 0,
    total_talk_seconds  NUMERIC(14,3) NOT NULL DEFAULT 0,
    
    PRIMARY KEY (employee_id, year_month),
    CONSTRAINT ck_stats_year_month CHECK (year_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT ck_stats_calls      CHECK (successful_calls >= 0),
    CONSTRAINT ck_stats_seconds    CHECK (total_talk_seconds >= 0)
);
