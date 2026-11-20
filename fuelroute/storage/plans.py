"""F3015 검증된 주유 계획의 SQLite 저장·목록·상세 Repository."""

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from fuelroute.core.contracts import utc_now
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.plans import PlanConflict, PlanCreate, PlanKey, PlanWriteResult, StoredPlan

PLAN_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS execution_sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed'))
);
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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fuel_plans_trip_date ON fuel_plans(trip_date);
CREATE INDEX IF NOT EXISTS fuel_plans_time_range
ON fuel_plans(trip_date, planned_start_at, planned_end_at);
"""


class PlanStore(Protocol):
    """계획 저장·목록·상세 조회를 제공하는 비동기 F3015 Repository 계약."""

    async def create(self, command: PlanCreate) -> PlanWriteResult:
        """검증된 추천 스냅샷을 저장하고 겹치는 계획을 함께 반환한다."""
        ...

    async def list(self, trip_date: date | None = None) -> list[StoredPlan]:
        """선택 날짜의 계획을 시작/생성 순서로 반환한다."""
        ...

    async def get(self, plan_id: str) -> StoredPlan | None:
        """검증한 ID의 계획 또는 없는 경우 None을 반환한다."""
        ...


class SQLitePlanStore:
    """파일 SQLite에 추천 스냅샷을 저장하고 시간 구간 충돌을 비차단 탐지한다."""

    def __init__(self, path: Path | str) -> None:
        """DB 경로만 보관하며 첫 작업 전에는 파일·테이블을 만들지 않는다.

        path는 Settings.database_path 또는 테스트 파일이다. 작업 스레드마다 연결을 열므로
        `:memory:`는 거절한다. 생성자는 외부 API·LLM·DB I/O를 수행하지 않는다.
        """
        if str(path) == ":memory:":
            raise ValueError("계획 저장소에는 SQLite 파일 경로가 필요합니다.")
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        """부모 폴더와 계획 스키마를 준비한 timeout 5초 SQLite 연결을 반환한다.

        foreign key와 계획 날짜/시간 index를 적용한다. 연결 종료는 호출자 책임이며 파일·
        스키마 쓰기 외 부작용은 없다. 오류는 공개 비동기 경계에서 정제한다.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(PLAN_SCHEMA)
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> StoredPlan:
        """한 DB 행의 JSON·시각·버전을 `StoredPlan`으로 재검증해 복원한다.

        row는 계획 SELECT 결과이며 필수 별칭을 포함해야 한다. JSON/시간 손상은 예외로
        상위에서 PLAN_STORE_ERROR가 된다. DB 쓰기·현재시각 생성·외부 호출은 없다.
        """
        return StoredPlan(
            plan_id=row["id"],
            execution_id=row["session_id"],
            revision=row["revision"],
            validation_status=row["validation_status"],
            trip_date=row["trip_date"],
            input_snapshot=json.loads(row["input_snapshot_json"]),
            recommendation=json.loads(row["recommendation_json"]),
            planned_start_at=row["planned_start_at"],
            planned_end_at=row["planned_end_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _conflicts(connection: sqlite3.Connection, plan: StoredPlan) -> list[PlanConflict]:
        """같은 날짜에서 반개구간 `[start,end)`이 겹치는 기존 계획을 조회한다.

        새 계획의 시작/종료가 모두 있을 때만 호출한다. 끝과 다른 시작이 같은 연속 계획은
        충돌이 아니다. 현재 계획 ID는 제외하며 조회 결과를 기존 시작 순서로 반환한다.
        DB 읽기 외 부작용·저장 차단·시간 추측은 없다.
        """
        rows = connection.execute(
            "SELECT id, planned_start_at, planned_end_at FROM fuel_plans "
            "WHERE trip_date = ? AND id <> ? AND planned_start_at IS NOT NULL "
            "AND planned_end_at IS NOT NULL AND planned_start_at < ? AND planned_end_at > ? "
            "ORDER BY planned_start_at, id",
            (
                plan.trip_date.isoformat(),
                plan.plan_id,
                plan.planned_end_at.isoformat(),
                plan.planned_start_at.isoformat(),
            ),
        ).fetchall()
        return [
            PlanConflict(
                plan_id=row["id"],
                planned_start_at=row["planned_start_at"],
                planned_end_at=row["planned_end_at"],
            )
            for row in rows
        ]

    def _create(self, command: PlanCreate) -> PlanWriteResult:
        """쓰기 트랜잭션에서 계획을 삽입하고 저장 후 시간 충돌을 조회한다.

        command는 공개 메서드가 검증한 추천이다. UUID hex ID와 UTC 생성/수정 시각을 만들며
        revision=1, validation_status=valid로 저장한다. 실행 ID가 실제 로그에 없으면 FK 오류로
        실패하고 자동으로 참조를 만들지 않는다. 충돌은 저장을 취소하지 않는다.
        """
        now = utc_now()
        plan = StoredPlan(
            **command.model_dump(),
            plan_id=uuid4().hex,
            revision=1,
            validation_status="valid",
            created_at=now,
            updated_at=now,
        )
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO fuel_plans(id, session_id, revision, validation_status, trip_date, "
                "input_snapshot_json, recommendation_json, planned_start_at, planned_end_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.plan_id,
                    plan.execution_id,
                    plan.revision,
                    plan.validation_status,
                    plan.trip_date.isoformat(),
                    plan.input_snapshot.model_dump_json(),
                    plan.recommendation.model_dump_json(),
                    plan.planned_start_at.isoformat() if plan.planned_start_at else None,
                    plan.planned_end_at.isoformat() if plan.planned_end_at else None,
                    plan.created_at.isoformat(),
                    plan.updated_at.isoformat(),
                ),
            )
            comparable = plan.planned_start_at is not None and plan.planned_end_at is not None
            conflicts = self._conflicts(connection, plan) if comparable else []
        return PlanWriteResult(
            plan=plan,
            conflicts=conflicts,
            conflict_check="checked" if comparable else "not_comparable",
        )

    async def create(self, command: PlanCreate) -> PlanWriteResult:
        """입력을 재검증한 뒤 작업 스레드에서 계획을 저장하고 충돌 결과를 반환한다.

        유효한 전체 추천만 받으며 알 수 없는 시각은 None으로 보존한다. 같은 날짜의 겹침은
        성공 결과 `conflicts`에 포함한다. FK/DB/파일/손상은 비밀값 없는 PLAN_STORE_ERROR다.
        외부 API·LLM·자동 재시도는 없다.
        """
        checked = PlanCreate.model_validate(command.model_dump())
        try:
            return await asyncio.to_thread(self._create, checked)
        except (sqlite3.Error, OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise ProbeFailure("PLAN_STORE_ERROR", "계획을 저장할 수 없습니다.") from exc

    def _list(self, trip_date: date | None) -> list[StoredPlan]:
        """선택 날짜 또는 전체 계획 행을 계획 시각·생성 시각 순서로 읽는다.

        시각 없는 계획은 날짜 안에서 생성 순서로 정렬한다. 날짜는 파라미터 바인딩하며
        원본 행을 수정하지 않는다. JSON/타입 손상은 상위 공개 메서드에서 정제한다.
        """
        query = "SELECT * FROM fuel_plans"
        params: tuple[str, ...] = ()
        if trip_date is not None:
            query += " WHERE trip_date = ?"
            params = (trip_date.isoformat(),)
        query += " ORDER BY trip_date, COALESCE(planned_start_at, created_at), created_at, id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_plan(row) for row in rows]

    async def list(self, trip_date: date | None = None) -> list[StoredPlan]:
        """SQLite 재연결 뒤 선택 날짜의 계획 목록을 반환한다.

        날짜가 None이면 전체이며 없는 날짜는 빈 목록이다. 서버 재시작과 같은 새 저장소
        객체에서도 파일 데이터를 읽는다. 실패는 PLAN_STORE_ERROR이고 외부 API·LLM·쓰기는 없다.
        """
        try:
            return await asyncio.to_thread(self._list, trip_date)
        except (sqlite3.Error, OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise ProbeFailure("PLAN_STORE_ERROR", "계획 목록을 읽을 수 없습니다.") from exc

    def _get(self, plan_id: str) -> StoredPlan | None:
        """파라미터 바인딩으로 계획 한 건을 읽어 복원하며 없으면 None을 반환한다.

        plan_id는 공개 메서드에서 검증됐다. DB 읽기 외 부작용은 없고 손상은 예외다.
        """
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM fuel_plans WHERE id = ?", (plan_id,)).fetchone()
        return None if row is None else self._row_to_plan(row)

    async def get(self, plan_id: str) -> StoredPlan | None:
        """안전한 ID의 계획 상세 또는 없는 경우 None을 반환한다.

        ID는 영문·숫자·밑줄·하이픈 최대64자다. 형식 오류는 Pydantic ValidationError,
        DB/파일/저장 데이터 문제는 PLAN_STORE_ERROR다. 외부 API·LLM·수정 부작용은 없다.
        """
        key = PlanKey(plan_id=plan_id)
        try:
            return await asyncio.to_thread(self._get, key.plan_id)
        except (sqlite3.Error, OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise ProbeFailure("PLAN_STORE_ERROR", "계획을 읽을 수 없습니다.") from exc
