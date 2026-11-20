"""F3013 Logging Agent의 SQLite 순서 저장·정제·세션 격리·장애 격리를 검증한다."""

import asyncio
from pathlib import Path

import pytest
from test_execution import settings

from fuelroute.agent.events import EventBus, ExecutionEventStream
from fuelroute.agent.graph import execute_request
from fuelroute.agent.logging import LoggingAgent
from fuelroute.core.contracts import utc_now
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis
from fuelroute.schemas.events import ExecutionEvent
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.planning import PlanProposal, WorkPlan
from fuelroute.storage.logs import SQLiteExecutionLogStore


async def greeting_run(
    store: SQLiteExecutionLogStore, execution_id: str, *, bus: EventBus | None = None
):
    """인사 전체 그래프의 이벤트를 지정 SQLite 저장소에 기록하고 공통 결과를 반환한다.

    store/execution_id는 테스트별 파일/실행 키다. 선택 bus가 없으면 새 버스를 만들고
    LoggingAgent 한 개를 연결한다. 계획/Tool은 호출하지 않고 외부 네트워크·실제 키·LLM도
    사용하지 않는다. 반환은 execute_request의 ToolResult다.
    """
    active_bus = bus or EventBus()
    LoggingAgent(store, settings().secret_values()).connect(active_bus)

    async def analyze(_message: str) -> ExtractedRequest:
        """인사 의도를 반환해 계획 모델과 데이터 Tool을 생략한다."""
        return ExtractedRequest(intent="greeting", reason="인사")

    async def forbidden_plan(_analysis: RequestAnalysis) -> PlanProposal:
        """인사에서 계획 함수가 호출되면 테스트를 실패시킨다."""
        raise AssertionError("plan must be skipped")

    async def forbidden_execute(_plan: WorkPlan) -> PlanExecution:
        """인사에서 실행 함수가 호출되면 테스트를 실패시킨다."""
        raise AssertionError("execution must be skipped")

    return await execute_request(
        "안녕",
        settings(),
        analyzer=analyze,
        planner=forbidden_plan,
        executor=forbidden_execute,
        event_bus=active_bus,
        execution_id=execution_id,
    )


def test_logging_agent_persists_complete_execution_in_sequence(tmp_path: Path) -> None:
    """인사 전체 실행의 모든 이벤트와 completed 세션 상태를 같은 sequence로 저장한다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")
    result = asyncio.run(greeting_run(store, "run-one"))
    session = asyncio.run(store.load_session("run-one"))
    events = asyncio.run(store.load_events("run-one"))
    assert result.ok and result.data["event_stream"]["delivery_failures"] == 0
    assert session.status == "completed" and session.finished_at is not None
    assert session.event_count == len(events) == result.data["event_stream"]["event_count"]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].step == events[-1].step == "execution"
    assert events[-1].payload["status"] == "completed"


def test_two_execution_sessions_do_not_mix_events(tmp_path: Path) -> None:
    """같은 DB의 두 실행 ID가 각각 sequence1부터 시작하고 상대 이벤트를 조회하지 않는다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")

    async def scenario() -> None:
        """서로 다른 실행을 차례로 저장해 SQLite 파일 재연결/세션 분리를 확인한다."""
        await greeting_run(store, "run-a")
        await greeting_run(store, "run-b")

    asyncio.run(scenario())
    first = asyncio.run(store.load_events("run-a"))
    second = asyncio.run(store.load_events("run-b"))
    assert first and second and first[0].sequence == second[0].sequence == 1
    assert {event.execution_id for event in first} == {"run-a"}
    assert {event.execution_id for event in second} == {"run-b"}
    assert {event.event_id for event in first}.isdisjoint(event.event_id for event in second)


def test_logging_agent_redacts_secret_before_sqlite_write(tmp_path: Path) -> None:
    """허용 payload 문자열에 비밀값이 섞여도 저장 전에 원문/URL 인코딩 값을 제거한다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")
    bus = EventBus()
    LoggingAgent(store, ["secret key"]).connect(bus)
    stream = ExecutionEventStream(bus, "redacted")

    async def scenario() -> None:
        """비밀 문자열이 든 시작 이벤트와 정상 종료 이벤트를 발행한다."""
        await stream.emit("STEP_STARTED", "execution", payload={"note": "secret key"})
        await stream.emit(
            "STEP_COMPLETED", "execution", payload={"status": "completed", "encoded": "secret+key"}
        )

    asyncio.run(scenario())
    events = asyncio.run(store.load_events("redacted"))
    assert events[0].payload["note"] == "[REDACTED]"
    assert events[1].payload["encoded"] == "[REDACTED]"
    assert "secret" not in (tmp_path / "logs.sqlite3").read_bytes().decode("utf-8", errors="ignore")


def test_repository_rejects_gap_duplicate_and_after_completion(tmp_path: Path) -> None:
    """첫 sequence 간격·중복·종료 뒤 append를 LOG_STORE_ERROR로 거절한다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")

    def event(sequence: int, event_type: str = "STEP_STARTED", status: str | None = None):
        """한 실행의 지정 sequence 경계 이벤트를 시간대 있는 현재 시각으로 만든다."""
        return ExecutionEvent(
            event_id=f"ordered-{sequence}-{event_type}",
            execution_id="ordered",
            sequence=sequence,
            event_type=event_type,
            step="execution",
            payload={"status": status} if status else {},
            created_at=utc_now(),
        )

    with pytest.raises(ProbeFailure) as gap:
        asyncio.run(store.record(event(2)))
    assert gap.value.error.code == "LOG_STORE_ERROR"
    asyncio.run(store.record(event(1)))
    with pytest.raises(ProbeFailure) as duplicate:
        asyncio.run(store.record(event(1)))
    assert duplicate.value.error.code == "LOG_STORE_ERROR"
    asyncio.run(store.record(event(2, "STEP_COMPLETED", "completed")))
    with pytest.raises(ProbeFailure) as ended:
        asyncio.run(store.record(event(3)))
    assert ended.value.error.code == "LOG_STORE_ERROR"
    assert asyncio.run(store.load_session("ordered")).event_count == 2


def test_real_sqlite_failure_does_not_block_main_agent(tmp_path: Path) -> None:
    """DB 경로가 디렉터리여서 모든 저장이 실패해도 인사 응답과 이벤트 발행을 완료한다."""
    invalid_path = tmp_path / "directory"
    invalid_path.mkdir()
    store = SQLiteExecutionLogStore(invalid_path)
    bus = EventBus()
    result = asyncio.run(greeting_run(store, "store-failure", bus=bus))
    assert result.ok and result.data["final_response"]["status"] == "not_required"
    metadata = result.data["event_stream"]
    assert metadata["event_count"] > 0
    assert metadata["delivery_failures"] == metadata["event_count"]
    assert bus.delivery_failures == metadata["event_count"]


def test_empty_store_reads_are_explicit_and_do_not_create_fake_events(tmp_path: Path) -> None:
    """알려진 형식의 미저장 실행은 session None/events 빈 목록이며 값을 생성하지 않는다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")
    assert asyncio.run(store.load_session("missing")) is None
    assert asyncio.run(store.load_events("missing")) == []
