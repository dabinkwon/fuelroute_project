-- 저장 설계 기준. F3010 대화 맥락과 F3013 실행 로그 Repository를 실제 연결했다.
-- F3014 로그 HTTP 조회·삭제와 F3015 계획 저장·목록·상세 Repository를 연결했다.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversation_memory (
    session_id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    context_json TEXT NOT NULL CHECK (json_valid(context_json))
);

CREATE TABLE IF NOT EXISTS execution_sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS execution_events (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES execution_sessions(session_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'STEP_STARTED', 'STEP_COMPLETED', 'TOOL_REQUESTED', 'TOOL_SUCCEEDED', 'TOOL_FAILED',
        'CONSTRAINT_EVALUATED', 'REPLAN_TRIGGERED', 'REPLAN_LIMIT_REACHED',
        'LLM_DECISION', 'FINAL_RESPONSE_CREATED'
    )),
    step TEXT NOT NULL,
    tool_name TEXT,
    request_id TEXT,
    elapsed_ms REAL CHECK (elapsed_ms >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE (session_id, sequence)
);

CREATE TRIGGER IF NOT EXISTS execution_events_immutable
BEFORE UPDATE ON execution_events
BEGIN
    SELECT RAISE(ABORT, 'execution events cannot be updated');
END;

CREATE TABLE IF NOT EXISTS fuel_plans (
    id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT REFERENCES execution_sessions(session_id) ON DELETE SET NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('valid', 'revalidation_required')),
    trip_date TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL CHECK (json_valid(input_snapshot_json)),
    recommendation_json TEXT NOT NULL CHECK (json_valid(recommendation_json)),
    planned_start_at TEXT,
    planned_end_at TEXT,
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS execution_events_session_sequence
ON execution_events(session_id, sequence);

CREATE INDEX IF NOT EXISTS fuel_plans_trip_date ON fuel_plans(trip_date);

CREATE INDEX IF NOT EXISTS fuel_plans_time_range
ON fuel_plans(trip_date, planned_start_at, planned_end_at);
