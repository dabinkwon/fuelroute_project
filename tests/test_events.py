"""F3012 이벤트 순서·Tool 연결·판정/응답·구독 실패 격리와 실행 ID를 검증한다."""

import asyncio
import json

import pytest
from test_execution import TODAY, USER, extracted, proposal, run_plan, settings

from fuelroute.agent.events import EventBus, ExecutionEventStream, public_result_size
from fuelroute.agent.graph import execute_request, make_graph
from fuelroute.agent.response import finalize_response
from fuelroute.calculations.constraints import judge_execution
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis
from fuelroute.schemas.events import ExecutionEvent
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.planning import PlanProposal, WorkPlan
from fuelroute.schemas.response import FinalResponse, ResponseContext


def test_public_result_size_counts_items_without_counting_detail_fields() -> None:
    """이벤트 건수는 items 원소를 세고 단일 상세 dict의 필드 수를 건수로 오인하지 않는다."""
    assert public_result_size({"items": [{"id": "A"}, {"id": "B"}]}) == 2
    assert public_result_size({"station_id": "A", "name": "주유소", "price": 1500}) == 1
    assert public_result_size([1, 2, 3]) == 3
    assert public_result_size(None) == 0


def test_graph_publishes_ordered_steps_tools_judgement_and_response() -> None:
    """전체 합성 추천에서 단계·각 Tool 쌍·판정·최종 응답을 연속 sequence로 발행한다."""
    execution, _ = run_plan("normal")
    bus = EventBus()
    events: list[ExecutionEvent] = []

    async def collect(event: ExecutionEvent) -> None:
        """전달된 immutable 이벤트를 순서대로 메모리에 추가한다. 저장·외부 호출은 없다."""
        events.append(event)

    bus.subscribe(collect)
    stream = ExecutionEventStream(bus, "event-test")

    async def analyze(_message: str) -> ExtractedRequest:
        """검증된 합성 추천 추출을 반환한다. GMS·HTTP·DB는 사용하지 않는다."""
        return extracted()

    async def plan(_analysis: RequestAnalysis) -> PlanProposal:
        """아홉 단계 합성 계획 제안을 반환한다. 모델·Tool 실행은 없다."""
        return proposal()

    async def execute(_plan: WorkPlan) -> PlanExecution:
        """이미 모의 제공사로 만든 실행 결과를 반환한다. 이 호출에서 외부 I/O는 없다."""
        return execution.model_copy(deep=True)

    async def enrich(_plan: WorkPlan, value: PlanExecution) -> PlanExecution:
        """기존 합성 휴게소 결과를 그대로 반환한다. 추가 조회·변경은 없다."""
        return value

    async def respond(context: ResponseContext) -> FinalResponse:
        """검증 사실의 템플릿 응답을 반환한다. GMS·DB·데이터 API는 호출하지 않는다."""
        return await finalize_response(context)

    async def scenario() -> dict:
        """이벤트 관찰기를 연결한 실제 LangGraph를 끝까지 실행하고 State를 반환한다."""
        graph = make_graph(
            analyze,
            TODAY,
            llm_plan_fn=plan,
            executor=execute,
            judge=judge_execution,
            enricher=enrich,
            responder=respond,
            event_stream=stream,
        )
        return await graph.ainvoke({"user": USER})

    state = asyncio.run(scenario())
    assert state["final_response"]["status"] == "recommended"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].event_type == "STEP_STARTED" and events[0].step == "analyze"
    assert {event.step for event in events if event.event_type == "STEP_STARTED"} == {
        "analyze",
        "build_plan",
        "execute_plan",
        "judge",
        "enrich",
        "respond",
    }
    requested = [event for event in events if event.event_type == "TOOL_REQUESTED"]
    finished = [event for event in events if event.event_type in {"TOOL_SUCCEEDED", "TOOL_FAILED"}]
    assert len(requested) == len(finished) == len(execution.calls)
    assert [event.request_id for event in requested] == [event.request_id for event in finished]
    assert any(event.event_type == "CONSTRAINT_EVALUATED" for event in events)
    assert any(event.event_type == "FINAL_RESPONSE_CREATED" for event in events)
    serialized = json.dumps([event.model_dump(mode="json") for event in events], ensure_ascii=False)
    assert "fake-kakao" not in serialized and USER not in serialized
    assert "vertexes" not in serialized and "http" not in serialized


def test_execute_request_reports_stream_and_ignores_subscriber_failure() -> None:
    """인사 전체 실행은 나쁜 구독자가 있어도 성공하고 실행 ID·건수·실패 수를 반환한다."""
    bus = EventBus()
    received: list[ExecutionEvent] = []

    async def broken(_event: ExecutionEvent) -> None:
        """로깅 저장 장애를 합성한다. 매 이벤트에서 RuntimeError를 발생시킨다."""
        raise RuntimeError("synthetic logging failure")

    async def collect(event: ExecutionEvent) -> None:
        """실패 구독자 다음에도 전달되는 이벤트를 메모리에 모은다."""
        received.append(event)

    bus.subscribe(broken)
    bus.subscribe(collect)

    async def analyze(_message: str) -> ExtractedRequest:
        """인사 의도를 반환해 위치·계획 모델·실행 Tool을 모두 생략한다."""
        return ExtractedRequest(intent="greeting", reason="인사")

    async def forbidden_plan(_analysis: RequestAnalysis) -> PlanProposal:
        """인사에서 호출되면 실패한다. 정상 경로에는 부작용이 없다."""
        raise AssertionError("plan must be skipped")

    async def forbidden_execute(_plan: WorkPlan) -> PlanExecution:
        """인사에서 호출되면 실패한다. 정상 경로에는 부작용이 없다."""
        raise AssertionError("execution must be skipped")

    result = asyncio.run(
        execute_request(
            "안녕",
            settings(),
            analyzer=analyze,
            planner=forbidden_plan,
            executor=forbidden_execute,
            event_bus=bus,
            execution_id="greeting-run",
        )
    )
    assert result.ok and result.data["final_response"]["status"] == "not_required"
    metadata = result.data["event_stream"]
    assert metadata == {
        "execution_id": "greeting-run",
        "event_count": len(received),
        "delivery_failures": len(received),
    }
    assert received[-1].step == "execution" and received[-1].payload["status"] == "completed"
    assert not any(event.event_type.startswith("TOOL_") for event in received)


def test_subscribe_unsubscribe_and_no_subscriber_are_safe() -> None:
    """구독 해제는 현재 등록만 제거하고 버스가 없는 스트림도 sequence를 계속 만든다."""
    bus = EventBus()
    received: list[ExecutionEvent] = []

    async def collect(event: ExecutionEvent) -> None:
        """구독 유지 여부를 확인할 이벤트만 메모리에 추가한다."""
        received.append(event)

    unsubscribe = bus.subscribe(collect)
    stream = ExecutionEventStream(bus, "subscription")

    async def scenario() -> None:
        """등록 전후 이벤트와 버스 없는 이벤트를 발행한다. 외부 I/O는 없다."""
        await stream.emit("STEP_STARTED", "first")
        unsubscribe()
        unsubscribe()
        await stream.emit("STEP_COMPLETED", "first")
        isolated = ExecutionEventStream(execution_id="isolated")
        event = await isolated.emit("STEP_STARTED", "only")
        assert event.sequence == 1

    asyncio.run(scenario())
    assert len(received) == 1 and received[0].step == "first"


def test_failed_node_emits_sanitized_completion_and_reraises() -> None:
    """노드 오류는 원문 메시지 없이 error_type만 발행하고 원 ProbeFailure를 유지한다."""
    bus = EventBus()
    events: list[ExecutionEvent] = []

    async def collect(event: ExecutionEvent) -> None:
        """실패 경계 이벤트를 메모리에 보관한다."""
        events.append(event)

    bus.subscribe(collect)
    stream = ExecutionEventStream(bus, "failed-node")

    async def fail(_message: str) -> ExtractedRequest:
        """민감할 수 있는 합성 메시지를 가진 예상 오류를 발생시킨다."""
        raise ProbeFailure("AUTH_FAILED", "secret value must not be logged")

    async def scenario() -> None:
        """분석 노드 실패를 실행해 원 오류와 이벤트를 함께 확인한다."""
        graph = make_graph(fail, TODAY, event_stream=stream)
        with pytest.raises(ProbeFailure, match="secret value"):
            await graph.ainvoke({"user": "요청"})

    asyncio.run(scenario())
    assert [event.event_type for event in events] == ["STEP_STARTED", "STEP_COMPLETED"]
    assert events[-1].payload == {"status": "failed", "error_type": "ProbeFailure"}
    assert "secret value" not in json.dumps(events[-1].model_dump(mode="json"))


def test_failed_tool_event_keeps_code_without_provider_message() -> None:
    """보조 Tool 실패는 요청/실패 쌍과 오류 코드만 발행하고 제공사 오류 문장은 남기지 않는다."""
    execution, _ = run_plan("rest_error")
    bus = EventBus()
    events: list[ExecutionEvent] = []

    async def collect(event: ExecutionEvent) -> None:
        """합성 실패 Tool 이벤트를 메모리에 보관한다."""
        events.append(event)

    async def node(_state: dict) -> dict:
        """휴게소 실패를 포함한 검증 실행 결과만 반환한다. 외부 호출은 없다."""
        return {"execution": execution.model_dump(mode="json")}

    bus.subscribe(collect)
    stream = ExecutionEventStream(bus, "failed-tool")
    asyncio.run(stream.observe("execute_plan", node)({}))
    failed = [event for event in events if event.event_type == "TOOL_FAILED"]
    assert len(failed) == 1 and failed[0].payload["error_code"] == "UPSTREAM_ERROR"
    assert failed[0].tool_name == "search_rest_area_info"
    assert "휴게소" not in json.dumps(failed[0].model_dump(mode="json"), ensure_ascii=False)


def test_replan_events_distinguish_actual_round_and_limit_stop() -> None:
    """추가 회차와 평가 예산 종료를 REPLAN_TRIGGERED/REPLAN_LIMIT_REACHED로 구분한다."""
    bus = EventBus()
    events: list[ExecutionEvent] = []

    async def collect(event: ExecutionEvent) -> None:
        """재계획 관찰 이벤트를 순서대로 메모리에 추가한다."""
        events.append(event)

    bus.subscribe(collect)
    stream = ExecutionEventStream(bus, "replan-events")

    async def triggered(_state: dict) -> dict:
        """검색점 확장 한 회차가 실행된 공개 재계획 상태를 반환한다."""
        return {
            "replanning": {
                "replan_count": 1,
                "rounds": [],
                "decision": {
                    "action": "expand_search",
                    "reason": "새 검색점",
                    "search_point": {"longitude": 127, "latitude": 37, "route_distance_m": 1000},
                    "next_input": [],
                },
                "stop_reason": None,
                "next_input": [],
            }
        }

    async def limited(_state: dict) -> dict:
        """전체 평가 예산에서 종료한 공개 재계획 상태를 반환한다."""
        return {
            "replanning": {
                "replan_count": 2,
                "rounds": [],
                "decision": {
                    "action": "stop",
                    "reason": "평가 예산 소진",
                    "search_point": None,
                    "next_input": ["조건을 조정해주세요."],
                },
                "stop_reason": "evaluation_budget_exhausted",
                "next_input": ["조건을 조정해주세요."],
            }
        }

    async def scenario() -> None:
        """같은 실행에서 추가 회차와 상한 종료 노드를 차례로 관찰한다."""
        await stream.observe("replan", triggered)({})
        await stream.observe("replan", limited)({})

    asyncio.run(scenario())
    assert [
        event.event_type
        for event in events
        if event.event_type in {"REPLAN_TRIGGERED", "REPLAN_LIMIT_REACHED"}
    ] == ["REPLAN_TRIGGERED", "REPLAN_LIMIT_REACHED"]


@pytest.mark.parametrize("execution_id", ["", "space id", "한글", "a" * 65])
def test_execution_id_rejects_unsafe_storage_keys(execution_id: str) -> None:
    """빈값·공백·비ASCII·64자 초과 실행 ID는 이벤트 생성 전에 거절한다."""
    with pytest.raises(ValueError):
        ExecutionEventStream(execution_id=execution_id)
