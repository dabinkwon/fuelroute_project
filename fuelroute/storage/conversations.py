"""세션별 최신 대화 맥락의 SQLite 저장소. Agent·LLM·외부 API에 의존하지 않는다."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.memory import ConversationMemory, SessionKey

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_memory (
    session_id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    context_json TEXT NOT NULL CHECK (json_valid(context_json))
);
"""


class ConversationStore(Protocol):
    """동일 세션의 조건을 읽고 예상 revision에만 쓰는 비동기 저장 계약."""

    async def load(self, session_id: str) -> ConversationMemory | None:
        """검증한 ID의 최신 맥락 또는 없는 경우None을 반환한다. 실패는ProbeFailure다."""
        ...

    async def save(self, memory: ConversationMemory, expected_revision: int) -> None:
        """예상 버전일 때만 새 맥락을 저장한다. 충돌은MEMORY_CONFLICT이며 자동 재시도하지 않는다."""
        ...


class SQLiteConversationStore:
    """파일 기반 SQLite에 최신 상태 하나만 저장하며 요청 간 덮어쓰기 충돌을 검출한다."""

    def __init__(self, path: Path | str) -> None:
        """DB 파일 경로만 보관한다. 생성 시 파일·연결·HTTP·LLM 부작용은 없다.

        기본 경로는 호출자가 Settings.database_path에서 전달한다. :memory:는 작업 스레드별
        연결이 달라져 영속성이 없어 거절한다(ValueError). 예:Path('data/fuelroute.sqlite3').
        """
        if str(path) == ":memory:":
            raise ValueError("대화 저장소에는 SQLite 파일 경로가 필요합니다.")
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        """부모 폴더·대화 테이블을 만들고 timeout5초의 짧은 연결을 반환한다.

        작업 스레드에서만 호출한다. 로그/계획 테이블은 만들지 않으며 연결 종료는 호출자
        책임이다. 파일/스키마 쓰기 외 부작용은 없고 sqlite/OSError는 비동기 경계에서 정제한다.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.executescript(MEMORY_SCHEMA)
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def _load(self, session_id: str) -> ConversationMemory | None:
        """파라미터 바인딩으로 한 ID만 읽고 JSON의 ID·revision과 DB 열이 같은지 검사한다.

        인증·SQL을 문자열로 조합하지 않는다. 없음은None, 손상·다른ID/버전은ValueError다.
        파일 생성/읽기 외 HTTP·LLM·자동 재시도는 없고 연결은 항상 닫는다.
        """
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT revision, context_json FROM conversation_memory WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        result = ConversationMemory.model_validate_json(row[1])
        if result.session_id != session_id or result.revision != row[0]:
            raise ValueError("대화 상태의 ID·버전이 일치하지 않습니다.")
        return result

    async def load(self, session_id: str) -> ConversationMemory | None:
        """ID를 먼저 검증하고 SQLite 읽기를 작업 스레드에서 수행한다.

        session_id는SessionKey의최대64자ID다. 데이터가없으면None, 손상/파일/DB실패는
        비밀값·경로없는MEMORY_ERROR다. 외부모델호출전에호출하며HTTP·LLM·재시도는없다.
        예:await store.load('trip-1').
        """
        key = SessionKey(session_id=session_id)
        try:
            return await asyncio.to_thread(self._load, key.session_id)
        except (sqlite3.Error, OSError, ValueError, ValidationError) as exc:
            raise ProbeFailure("MEMORY_ERROR", "대화 맥락 저장소를 읽을 수 없습니다.") from exc

    def _save(self, memory: ConversationMemory, expected_revision: int) -> None:
        """쓰기 잠금 안에서 새 ID를 삽입하거나 예상 버전과 같은 행만 갱신한다.

        memory는정제·검증된최신스냅샷이며revision은expected+1이다. 기존행을임의덮어쓰지
        않고CAS실패는MEMORY_CONFLICT다. 연결·트랜잭션은항상종료하고파일쓰기외부작용은없다.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            values = (memory.revision, memory.model_dump_json(), memory.session_id)
            if expected_revision == 0:
                cursor = connection.execute(
                    "INSERT INTO conversation_memory(revision, context_json, session_id) "
                    "VALUES (?, ?, ?) ON CONFLICT(session_id) DO NOTHING",
                    values,
                )
            else:
                cursor = connection.execute(
                    "UPDATE conversation_memory SET revision = ?, context_json = ? "
                    "WHERE session_id = ? AND revision = ?",
                    (*values, expected_revision),
                )
            if cursor.rowcount != 1:
                raise ProbeFailure(
                    "MEMORY_CONFLICT",
                    "같은 대화가 동시에 변경됐습니다. 최신 조건을 확인하세요.",
                    retryable=True,
                )

    async def save(self, memory: ConversationMemory, expected_revision: int) -> None:
        """검증·정제된 맥락을 예상 버전에만 저장하고 파일 I/O는 작업 스레드로 넘긴다.

        호출자는비밀값을정제해야한다. memory.revision=expected+1,expected>=0이어야하며
        모순은INVALID_INPUT이다. 충돌은MEMORY_CONFLICT,파일/DB실패는MEMORY_ERROR다.
        자동재시도·HTTP·LLM·전체Tool응답저장은없다. 예:revision0을읽은후revision1을저장한다.
        """
        checked = ConversationMemory.model_validate(memory.model_dump())
        if (
            type(expected_revision) is not int
            or expected_revision < 0
            or checked.revision != expected_revision + 1
        ):
            raise ProbeFailure("INVALID_INPUT", "대화 맥락의 저장 버전을 확인하세요.")
        try:
            await asyncio.to_thread(self._save, checked, expected_revision)
        except (sqlite3.Error, OSError) as exc:
            raise ProbeFailure("MEMORY_ERROR", "대화 맥락을 저장할 수 없습니다.") from exc
