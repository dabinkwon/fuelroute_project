"""F3013 실행 세션·immutable 이벤트를 순서대로 저장하는 SQLite Repository."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.events import EventExecutionKey, ExecutionEvent, ExecutionSessionRecord

LOG_SCHEMA = """
PRAGMA foreign_keys = ON;
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
CREATE INDEX IF NOT EXISTS execution_events_session_sequence
ON execution_events(session_id, sequence);
"""


class ExecutionLogStore(Protocol):
    """Logging Agent가 이벤트 저장과 검증용 읽기에 사용하는 비동기 Repository 계약."""

    async def record(self, event: ExecutionEvent) -> None:
        """다음 sequence 이벤트 하나를 저장한다. 순서/중복/종료 후 쓰기는 오류다."""
        ...

    async def load_session(self, execution_id: str) -> ExecutionSessionRecord | None:
        """실행 세션 상태 또는 없는 경우 None을 반환한다. 실패는 ProbeFailure다."""
        ...

    async def load_events(self, execution_id: str) -> list[ExecutionEvent]:
        """한 실행의 이벤트를 sequence 오름차순으로 반환한다. 실패는 ProbeFailure다."""
        ...


class SQLiteExecutionLogStore:
    """파일 SQLite에 실행 경계와 immutable 이벤트를 연속 sequence로 저장한다."""

    def __init__(self, path: Path | str) -> None:
        """DB 파일 경로만 보관하고 연결/테이블은 첫 작업에서 만든다.

        path는 Settings.database_path 또는 테스트 파일 경로다. 스레드마다 연결을 새로 여는
        구조라 `:memory:`는 거절한다. 생성자는 파일·DB·HTTP·LLM을 건드리지 않는다.
        예: SQLiteExecutionLogStore(Path('data/fuelroute.sqlite3')).
        """
        if str(path) == ":memory:":
            raise ValueError("실행 로그 저장소에는 SQLite 파일 경로가 필요합니다.")
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        """부모 폴더와 로그 스키마를 준비한 timeout5초 SQLite 연결을 반환한다.

        작업 스레드에서만 호출한다. foreign key·immutable trigger·순서 index를 적용한다.
        연결 종료는 호출자 책임이며 파일/스키마 쓰기 외 HTTP·LLM 부작용은 없다. sqlite/OSError는
        공개 비동기 메서드가 LOG_STORE_ERROR로 정제한다.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.executescript(LOG_SCHEMA)
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def _record(self, event: ExecutionEvent) -> None:
        """쓰기 잠금에서 세션을 시작하고 정확한 다음 sequence 이벤트를 append한다.

        첫 이벤트는 sequence1이어야 한다. 기존 event_count+1만 허용하며 완료/실패 세션에는
        추가하지 않는다. execution 단계의 STEP_COMPLETED payload.status에 따라 세션을
        completed/failed로 끝낸다. payload는 JSON으로 저장하고 이벤트 행은 수정하지 않는다.
        반환은 없고 파일 쓰기 외 외부 호출·LLM·재시도는 없다.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM execution_sessions WHERE session_id = ?",
                (event.execution_id,),
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM execution_events WHERE session_id = ?",
                (event.execution_id,),
            ).fetchone()[0]
            if row is None:
                if event.sequence != 1:
                    raise ValueError("첫 실행 이벤트 sequence는 1이어야 합니다.")
                connection.execute(
                    "INSERT INTO execution_sessions(session_id, started_at, finished_at, status) "
                    "VALUES (?, ?, NULL, 'running')",
                    (event.execution_id, event.created_at.isoformat()),
                )
            elif row[0] != "running":
                raise ValueError("종료된 실행 세션에는 이벤트를 추가할 수 없습니다.")
            if event.sequence != count + 1:
                raise ValueError("실행 이벤트 sequence가 연속적이지 않습니다.")
            connection.execute(
                "INSERT INTO execution_events(id, session_id, sequence, event_type, step, "
                "tool_name, request_id, elapsed_ms, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.execution_id,
                    event.sequence,
                    event.event_type,
                    event.step,
                    event.tool_name,
                    event.request_id,
                    event.elapsed_ms,
                    json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                    event.created_at.isoformat(),
                ),
            )
            if event.event_type == "STEP_COMPLETED" and event.step == "execution":
                status = "completed" if event.payload.get("status") == "completed" else "failed"
                connection.execute(
                    "UPDATE execution_sessions SET status = ?, finished_at = ? "
                    "WHERE session_id = ?",
                    (status, event.created_at.isoformat(), event.execution_id),
                )

    async def record(self, event: ExecutionEvent) -> None:
        """검증한 이벤트를 작업 스레드에서 순서대로 append한다.

        event는 Logging Agent가 정제한 immutable ExecutionEvent다. 첫/연속 sequence와 실행
        종료 상태를 확인한다. 중복·간격·종료 후 쓰기·DB/파일/스키마 문제는 경로/원문 없는
        ProbeFailure(LOG_STORE_ERROR)다. 자동 재시도·HTTP·LLM은 없다.
        """
        checked = ExecutionEvent.model_validate(event.model_dump())
        try:
            await asyncio.to_thread(self._record, checked)
        except (sqlite3.Error, OSError, ValueError, ValidationError) as exc:
            raise ProbeFailure("LOG_STORE_ERROR", "실행 로그를 저장할 수 없습니다.") from exc

    def _load_session(self, execution_id: str) -> ExecutionSessionRecord | None:
        """한 실행 행과 이벤트 개수를 읽어 조회 모델로 반환한다.

        execution_id는 이미 검증됐다. 없음은 None이며 시간대/상태 손상은 Pydantic 오류다.
        연결은 항상 닫고 파일 읽기 외 부작용은 없다.
        """
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT started_at, finished_at, status, "
                "(SELECT COUNT(*) FROM execution_events e WHERE e.session_id = s.session_id) "
                "FROM execution_sessions s WHERE session_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        return ExecutionSessionRecord(
            execution_id=execution_id,
            started_at=row[0],
            finished_at=row[1],
            status=row[2],
            event_count=row[3],
        )

    async def load_session(self, execution_id: str) -> ExecutionSessionRecord | None:
        """안전한 실행 ID의 세션 상태를 작업 스레드에서 읽는다.

        저장 전/없는 ID는 None이다. ID 형식 오류는 ValidationError, 파일/DB/저장 데이터
        손상은 경로 없는 LOG_STORE_ERROR다. HTTP·LLM·자동 재시도는 없다.
        """
        key = EventExecutionKey(execution_id=execution_id)
        try:
            return await asyncio.to_thread(self._load_session, key.execution_id)
        except (sqlite3.Error, OSError, ValueError, ValidationError) as exc:
            raise ProbeFailure("LOG_STORE_ERROR", "실행 로그를 읽을 수 없습니다.") from exc

    def _load_events(self, execution_id: str) -> list[ExecutionEvent]:
        """한 실행의 이벤트 행을 sequence 오름차순으로 읽고 typed 목록으로 복원한다.

        payload JSON과 시각을 ExecutionEvent가 재검증한다. 없음은 빈 목록이다. 연결은 닫고
        원본 행을 수정하지 않으며 파일 읽기 외 HTTP·LLM 부작용은 없다.
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, sequence, event_type, step, tool_name, request_id, elapsed_ms, "
                "payload_json, created_at FROM execution_events WHERE session_id = ? "
                "ORDER BY sequence",
                (execution_id,),
            ).fetchall()
        return [
            ExecutionEvent(
                event_id=row[0],
                execution_id=execution_id,
                sequence=row[1],
                event_type=row[2],
                step=row[3],
                tool_name=row[4],
                request_id=row[5],
                elapsed_ms=row[6],
                payload=json.loads(row[7]),
                created_at=row[8],
            )
            for row in rows
        ]

    async def load_events(self, execution_id: str) -> list[ExecutionEvent]:
        """안전한 실행 ID의 immutable 이벤트를 sequence 오름차순으로 반환한다.

        이벤트가 없으면 빈 목록이다. ID 형식 오류는 ValidationError, DB/JSON/스키마 손상은
        경로·원문 없는 LOG_STORE_ERROR다. 반환 객체는 frozen이며 수정 API는 제공하지 않는다.
        """
        key = EventExecutionKey(execution_id=execution_id)
        try:
            return await asyncio.to_thread(self._load_events, key.execution_id)
        except (sqlite3.Error, OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise ProbeFailure("LOG_STORE_ERROR", "실행 이벤트를 읽을 수 없습니다.") from exc
