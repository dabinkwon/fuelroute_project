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
from fuelroute.schemas.plans import (
    PlanConflict,
    PlanCreate,
    PlanKey,
    PlanUpdate,
    PlanWriteResult,
    StoredPlan,
)

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
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fuel_plans_trip_date ON fuel_plans(trip_date);
CREATE INDEX IF NOT EXISTS fuel_plans_time_range
ON fuel_plans(trip_date, planned_start_at, planned_end_at);
"""


class PlanStore(Protocol):
    """계획 저장·조회와 revision 기반 수정·삭제를 제공하는 비동기 Repository 계약."""

    async def create(self, command: PlanCreate) -> PlanWriteResult:
        """검증된 추천 스냅샷을 저장하고 겹치는 계획을 함께 반환한다."""
        ...

    async def list(self, trip_date: date | None = None) -> list[StoredPlan]:
        """선택 날짜의 계획을 시작/생성 순서로 반환한다."""
        ...

    async def get(self, plan_id: str) -> StoredPlan | None:
        """검증한 ID의 계획 또는 없는 경우 None을 반환한다."""
        ...

    async def update(self, plan_id: str, command: PlanUpdate) -> PlanWriteResult:
        """예상 revision의 계획 핵심값·일정·메모를 바꾸고 검증 상태·충돌을 반환한다."""
        ...

    async def delete(self, plan_id: str) -> bool:
        """계획 한 건만 삭제하고 기존 존재 여부를 반환한다."""
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

        foreign key와 계획 날짜/시간 index를 적용한다. 기존 테이블에 memo 열이 없으면 빈
        문자열 기본값으로 한 번 추가한다. 연결 종료는 호출자 책임이며 파일·스키마 쓰기 외
        부작용은 없다. 오류는 공개 비동기 경계에서 정제한다.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(PLAN_SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(fuel_plans)").fetchall()
            }
            if "memo" not in columns:
                connection.execute(
                    "ALTER TABLE fuel_plans ADD COLUMN memo TEXT NOT NULL DEFAULT ''"
                )
                connection.commit()
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
            memo=row["memo"],
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
                "input_snapshot_json, recommendation_json, planned_start_at, planned_end_at, memo, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    plan.memo,
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

    @staticmethod
    def _merge_update(current: StoredPlan, command: PlanUpdate) -> PlanCreate:
        """현재 계획과 PATCH 필드를 병합해 전체 `PlanCreate` 계약으로 다시 검증한다.

        명시하지 않은 필드는 현재 값을 유지하고, 명시적 null 계획 시각은 삭제한다. 날짜만
        바꾸면 입력 스냅샷의 조건 날짜도 같은 명시값으로 바꾼다. 새 스냅샷만 주어지면 그
        날짜를 사용한다. 메모는 명시했을 때만 교체하고 빈 문자열이면 삭제한다. 추천
        스냅샷·실행 참조는 유지하며 DB·시간·외부 호출 부작용은 없다.
        """
        fields = command.model_fields_set
        input_snapshot = (
            command.input_snapshot
            if "input_snapshot" in fields and command.input_snapshot is not None
            else current.input_snapshot
        )
        if "trip_date" in fields:
            if command.trip_date is None:
                raise ProbeFailure("INVALID_INPUT", "계획 날짜는 비울 수 없습니다.")
            trip_date = command.trip_date
            conditions = input_snapshot.conditions.model_copy(update={"trip_date": trip_date})
            input_snapshot = input_snapshot.model_copy(update={"conditions": conditions})
        elif "input_snapshot" in fields:
            trip_date = input_snapshot.conditions.trip_date
            if trip_date is None:
                raise ProbeFailure("INVALID_INPUT", "입력 스냅샷의 계획 날짜가 필요합니다.")
        else:
            trip_date = current.trip_date
        start = (
            command.planned_start_at if "planned_start_at" in fields else current.planned_start_at
        )
        end = command.planned_end_at if "planned_end_at" in fields else current.planned_end_at
        memo = command.memo if "memo" in fields else current.memo
        return PlanCreate(
            execution_id=current.execution_id,
            trip_date=trip_date,
            input_snapshot=input_snapshot,
            recommendation=current.recommendation,
            planned_start_at=start,
            planned_end_at=end,
            memo=memo,
        )

    def _update(self, plan_id: str, command: PlanUpdate) -> PlanWriteResult:
        """쓰기 잠금에서 예상 revision을 확인하고 핵심값 변경을 원자적으로 저장한다.

        없는 ID는 PLAN_NOT_FOUND, 다른 revision은 PLAN_CONFLICT다. 병합 결과가 현재 핵심값과
        같으면 INVALID_INPUT이며 revision을 올리지 않는다. 실제 변경은 revision+1과 UTC
        updated_at을 기록한다. 날짜·추천 입력이 바뀌면 `revalidation_required`, 일정 시각·
        메모만 바뀌면 기존 검증 상태를 유지한다. 시간 충돌은 저장 후 조회하되 변경을
        취소하지 않는다.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM fuel_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise ProbeFailure("PLAN_NOT_FOUND", "계획을 찾을 수 없습니다.")
            current = self._row_to_plan(row)
            if current.revision != command.expected_revision:
                raise ProbeFailure(
                    "PLAN_CONFLICT",
                    "다른 요청이 계획을 먼저 변경했습니다. 최신 계획을 다시 확인하세요.",
                )
            merged = self._merge_update(current, command)
            before = (
                current.trip_date,
                current.input_snapshot,
                current.planned_start_at,
                current.planned_end_at,
                current.memo,
            )
            after = (
                merged.trip_date,
                merged.input_snapshot,
                merged.planned_start_at,
                merged.planned_end_at,
                merged.memo,
            )
            if before == after:
                raise ProbeFailure("INVALID_INPUT", "계획의 변경된 값이 없습니다.")
            recommendation_changed = (
                current.trip_date != merged.trip_date
                or current.input_snapshot != merged.input_snapshot
            )
            updated = StoredPlan(
                **merged.model_dump(),
                plan_id=current.plan_id,
                revision=current.revision + 1,
                validation_status=(
                    "revalidation_required" if recommendation_changed else current.validation_status
                ),
                created_at=current.created_at,
                updated_at=utc_now(),
            )
            cursor = connection.execute(
                "UPDATE fuel_plans SET revision = ?, validation_status = ?, trip_date = ?, "
                "input_snapshot_json = ?, planned_start_at = ?, planned_end_at = ?, memo = ?, "
                "updated_at = ? "
                "WHERE id = ? AND revision = ?",
                (
                    updated.revision,
                    updated.validation_status,
                    updated.trip_date.isoformat(),
                    updated.input_snapshot.model_dump_json(),
                    updated.planned_start_at.isoformat() if updated.planned_start_at else None,
                    updated.planned_end_at.isoformat() if updated.planned_end_at else None,
                    updated.memo,
                    updated.updated_at.isoformat(),
                    updated.plan_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ProbeFailure("PLAN_CONFLICT", "계획 수정 충돌을 확인하세요.")
            comparable = updated.planned_start_at is not None and updated.planned_end_at is not None
            conflicts = self._conflicts(connection, updated) if comparable else []
        return PlanWriteResult(
            plan=updated,
            conflicts=conflicts,
            conflict_check="checked" if comparable else "not_comparable",
        )

    async def update(self, plan_id: str, command: PlanUpdate) -> PlanWriteResult:
        """ID·PATCH 계약을 검증하고 작업 스레드에서 revision 기반 수정을 수행한다.

        성공 시 기존 추천 본문을 보존한다. 날짜·추천 입력 변경만 재검증 필요로 바꾸고 일정
        시각·메모 변경은 상태를 유지한다. 없는 계획, revision 충돌, 빈/무효 변경은 각
        ProbeFailure 코드로 구분한다. DB/파일/손상은 PLAN_STORE_ERROR이며 외부 API·LLM·
        자동 재추천/재시도는 없다.
        """
        key = PlanKey(plan_id=plan_id)
        checked = PlanUpdate.model_validate(command.model_dump(exclude_unset=True))
        try:
            return await asyncio.to_thread(self._update, key.plan_id, checked)
        except ProbeFailure:
            raise
        except (sqlite3.Error, OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise ProbeFailure("PLAN_STORE_ERROR", "계획을 수정할 수 없습니다.") from exc

    def _delete(self, plan_id: str) -> bool:
        """쓰기 트랜잭션에서 계획 ID 한 건만 삭제하고 존재 여부를 반환한다.

        로그·대화·다른 계획은 삭제하지 않는다. plan_id는 검증됐고 파라미터 바인딩한다.
        DB 파일 쓰기 외 외부 호출·LLM 부작용은 없다.
        """
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM fuel_plans WHERE id = ?", (plan_id,))
            return cursor.rowcount == 1

    async def delete(self, plan_id: str) -> bool:
        """안전한 ID의 계획 한 건을 작업 스레드에서 삭제한다.

        있으면 True, 없으면 False다. 계획에 연결된 로그를 역으로 삭제하지 않는다. ID 형식은
        ValidationError, DB/파일 문제는 PLAN_STORE_ERROR이며 외부 API·LLM·재시도는 없다.
        """
        key = PlanKey(plan_id=plan_id)
        try:
            return await asyncio.to_thread(self._delete, key.plan_id)
        except (sqlite3.Error, OSError, ValueError, ValidationError) as exc:
            raise ProbeFailure("PLAN_STORE_ERROR", "계획을 삭제할 수 없습니다.") from exc
