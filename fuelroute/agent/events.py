"""F3012 이벤트 발행/구독과 LangGraph 노드·Tool 결과 관찰 어댑터."""

import inspect
from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import uuid4

from fuelroute.core.contracts import utc_now
from fuelroute.schemas.events import EventExecutionKey, EventType, ExecutionEvent
from fuelroute.schemas.execution import PlanExecution

EventSubscriber = Callable[[ExecutionEvent], Awaitable[None]]
NodeResult = TypeVar("NodeResult", bound=dict)
NodeCallable = Callable[[dict], NodeResult | Awaitable[NodeResult]]


def public_result_size(data: object) -> int:
    """정규화 Tool 결과에서 화면에 표시할 논리 결과 건수를 계산한다.

    Tool 성공 이벤트를 만들 때 호출하며 ``data``는 이미 검증된 내부 반환값이다. 최상위
    list는 원소 수, ``items`` list를 가진 dict는 후보·휴게소 항목 수를 반환한다. 단일 상세·
    경로 dict와 스칼라는 필드 수가 아니라 결과 1건으로 처리하고 ``None``은 0건이다. 원본
    값·좌표·가격은 기록하지 않으며 입력 수정, 외부 호출, DB 부작용은 없다.

    예: ``{"items": [a, b]}``는 2, 주유소 상세 dict는 key가 여러 개여도 1이다. 제공사
    total count를 추정하지 않는 화면용 요약이라는 한계가 있다.
    """
    if data is None:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return len(data["items"])
    return 1


class EventBus:
    """실행 이벤트를 등록 순서대로 비동기 구독자에게 전달하는 요청 외부 버스.

    subscribe()로 EventSubscriber를 등록한 뒤 publish()를 호출한다. 구독자가 없으면 즉시
    끝나며 Agent 실행과 독립적이다. 각 구독자에는 immutable ExecutionEvent가 전달된다.
    한 구독자의 예외는 실패 수에 기록하고 다음 구독자를 계속 호출해 로깅 장애가 메인
    Agent를 막지 않게 한다. 재시도·병렬 전달·DB·LLM·전역 싱글턴은 없다. 같은 버스를 여러
    실행에서 재사용할 수 있으나 실행별 sequence는 ExecutionEventStream이 관리한다.
    """

    def __init__(self) -> None:
        """빈 구독자 목록과 실패 카운터를 만든다. 외부 호출·파일·DB 부작용은 없다."""
        self._subscribers: list[EventSubscriber] = []
        self.delivery_failures = 0

    def subscribe(self, subscriber: EventSubscriber) -> Callable[[], None]:
        """비동기 구독자를 등록하고 같은 등록 하나를 해제하는 함수를 반환한다.

        subscriber는 ExecutionEvent를 받아 Awaitable[None]을 반환해야 한다. 중복 등록은
        별도 구독으로 취급한다. 반환한 unsubscribe는 멱등이며 다른 구독을 제거하지 않는다.
        네트워크·DB·LLM은 호출하지 않는다. 예: unsubscribe() 뒤 이벤트는 전달되지 않는다.
        """
        self._subscribers.append(subscriber)
        active = True

        def unsubscribe() -> None:
            """현재 등록만 한 번 제거한다. 이미 제거됐으면 아무 작업도 하지 않는다."""
            nonlocal active
            if active:
                active = False
                self._subscribers.remove(subscriber)

        return unsubscribe

    async def publish(self, event: ExecutionEvent) -> None:
        """현재 구독자 스냅샷에 이벤트를 순서대로 전달하고 구독 실패를 격리한다.

        event는 이미 실행 ID·sequence·시간대를 검증한 객체다. 구독 도중 추가/해제된 등록은
        다음 이벤트부터 적용한다. 구독자 예외는 다시 발생시키거나 payload에 원문을 넣지
        않고 delivery_failures만 증가시킨다. 반환값은 없고 DB/LLM/재시도는 없다.
        """
        for subscriber in tuple(self._subscribers):
            try:
                await subscriber(event)
            except Exception:
                self.delivery_failures += 1


class ExecutionEventStream:
    """한 요청의 이벤트 ID·sequence와 새 Tool 호출 식별자를 관리하는 발행기."""

    def __init__(self, bus: EventBus | None = None, execution_id: str | None = None) -> None:
        """선택 버스와 검증한 실행 ID로 sequence 0의 스트림을 준비한다.

        bus가 None이면 이벤트 객체/순서만 생성하고 외부 전달은 생략한다. execution_id를
        생략하면 UUID hex를 생성하며, 지정값은 EventExecutionKey 규칙(영숫자/_/-, 64자)을
        검사한다. DB·LLM·HTTP는 없고 seen request ID는 현재 요청 안에서만 유지한다.
        """
        generated = uuid4().hex if execution_id is None else execution_id
        self.execution_id = EventExecutionKey(execution_id=generated).execution_id
        self.bus = bus
        self._delivery_failure_start = bus.delivery_failures if bus is not None else 0
        self.sequence = 0
        self._seen_request_ids: set[str] = set()

    @property
    def delivery_failures(self) -> int:
        """현재 실행이 시작된 뒤 발생한 구독 전달 실패 수를 반환한다.

        같은 EventBus를 다른 실행에서 재사용해도 이전 실패를 포함하지 않는다. 버스가 없으면
        0이다. 값을 초기화하거나 저장하지 않으며 외부 호출·LLM·DB 부작용은 없다.
        """
        return (
            self.bus.delivery_failures - self._delivery_failure_start if self.bus is not None else 0
        )

    async def emit(
        self,
        event_type: EventType,
        step: str,
        *,
        tool_name: str | None = None,
        request_id: str | None = None,
        elapsed_ms: float | None = None,
        payload: dict[str, str | int | float | bool | None | list[str]] | None = None,
    ) -> ExecutionEvent:
        """다음 sequence의 immutable 이벤트를 만들고 선택 EventBus에 한 번 발행한다.

        event_type/step은 현재 실행 단계, Tool 메타는 선택값, payload는 호출자가 구성한
        비밀값 없는 요약이다. event_id는 실행ID-sequence이고 created_at은 현재 UTC다.
        반환값은 발행한 ExecutionEvent다. bus가 없어도 sequence는 증가한다. 구독 실패는
        EventBus에서 격리하며 이 함수는 저장·재시도·LLM을 수행하지 않는다.
        """
        self.sequence += 1
        event = ExecutionEvent(
            event_id=f"{self.execution_id}-{self.sequence}",
            execution_id=self.execution_id,
            sequence=self.sequence,
            event_type=event_type,
            step=step,
            tool_name=tool_name,
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            payload=payload or {},
            created_at=utc_now(),
        )
        if self.bus is not None:
            await self.bus.publish(event)
        return event

    async def emit_new_tool_calls(self, step: str, result: dict) -> None:
        """노드 출력의 새 ToolCallRecord를 요청→성공/실패 이벤트 쌍으로 발행한다.

        result.execution이 없거나 호출 목록이 비어 있으면 아무 일도 하지 않는다. 동일
        request_id는 재계획 누적 결과에 다시 나타나도 재발행하지 않는다. payload에는 action,
        round_index, station_id 존재 여부, 공개 입력 필드명과 결과 개수/오류 코드만 넣는다.
        좌표값·가격·경로·응답 원문·키는 기록하지 않는다. 반환은 없고 실제 Tool/DB는 호출하지
        않는다. 예: TIMEOUT 호출은 TOOL_REQUESTED 다음 TOOL_FAILED(error_code=TIMEOUT)다.
        """
        raw = result.get("execution")
        if raw is None:
            return
        execution = PlanExecution.model_validate(raw)
        for call in execution.calls:
            request_id = call.result.meta.request_id
            if request_id in self._seen_request_ids:
                continue
            self._seen_request_ids.add(request_id)
            common: dict[str, str | int | float | bool | None | list[str]] = {
                "action": call.action,
                "round_index": call.round_index,
                "station_scoped": call.station_id is not None,
                "input_fields": sorted(call.inputs),
                "provider": call.result.meta.provider,
            }
            await self.emit(
                "TOOL_REQUESTED",
                step,
                tool_name=call.result.tool_name,
                request_id=request_id,
                payload=common,
            )
            if call.result.ok:
                payload = {**common, "result_size": public_result_size(call.result.data)}
                event_type: EventType = "TOOL_SUCCEEDED"
            else:
                payload = {
                    **common,
                    "error_code": call.result.error.code,
                    "retryable": call.result.error.retryable,
                    "replan_allowed": call.result.error.replan_allowed,
                }
                event_type = "TOOL_FAILED"
            await self.emit(
                event_type,
                step,
                tool_name=call.result.tool_name,
                request_id=request_id,
                elapsed_ms=call.result.meta.elapsed_ms,
                payload=payload,
            )

    async def _after_node(self, name: str, result: dict) -> None:
        """노드 결과에서 공개 상태·판정·재계획·최종 응답 이벤트를 추가 발행한다.

        name과 result의 검증된 공개 필드만 읽는다. LLM_DECISION은 분석 의도/계획 상태/
        최종 생성 모드처럼 모델이 반환한 짧은 결론만 기록하며 내부 추론은 기록하지 않는다.
        Tool 호출은 emit_new_tool_calls로 중복 제거한다. 반환·저장·HTTP·LLM은 없다.
        """
        await self.emit_new_tool_calls(name, result)
        if name == "analyze" and "analysis" in result:
            analysis = result["analysis"]
            await self.emit(
                "LLM_DECISION",
                name,
                payload={
                    "intent": analysis["intent"],
                    "ready_for_planning": analysis["ready_for_planning"],
                    "missing_fields": analysis["missing_fields"],
                },
            )
        elif name == "build_plan" and "plan" in result:
            plan = result["plan"]
            await self.emit(
                "LLM_DECISION",
                name,
                payload={"status": plan["status"], "step_count": len(plan["steps"])},
            )
        elif name == "judge" and "judgement" in result:
            judgement = result["judgement"]
            await self.emit(
                "CONSTRAINT_EVALUATED",
                name,
                payload={
                    "status": judgement["status"],
                    "candidate_count": len(judgement["candidates"]),
                    "replan_needed": judgement["replan_needed"],
                },
            )
        elif name == "replan" and "replanning" in result:
            replanning = result["replanning"]
            decision = replanning.get("decision") or {}
            stop_reason = replanning.get("stop_reason")
            event_type = (
                "REPLAN_LIMIT_REACHED"
                if stop_reason
                in {
                    "evaluation_budget_exhausted",
                    "replan_limit_reached",
                    "search_space_exhausted",
                }
                else "REPLAN_TRIGGERED"
                if stop_reason is None
                else None
            )
            if event_type is not None:
                await self.emit(
                    event_type,
                    name,
                    payload={
                        "action": decision.get("action"),
                        "reason": decision.get("reason"),
                        "replan_count": replanning["replan_count"],
                        "stop_reason": stop_reason,
                    },
                )
        elif name == "respond" and "final_response" in result:
            final = result["final_response"]
            await self.emit(
                "LLM_DECISION",
                name,
                payload={"status": final["status"], "mode": final["mode"]},
            )
            await self.emit(
                "FINAL_RESPONSE_CREATED",
                name,
                payload={
                    "status": final["status"],
                    "mode": final["mode"],
                    "evidence_count": len(final["evidence"]),
                    "has_recommendation": final["recommended_station_id"] is not None,
                },
            )

    def observe(self, name: str, node: NodeCallable) -> NodeCallable:
        """LangGraph 노드를 단계 시작/결과 이벤트로 감싸고 같은 호출 계약을 반환한다.

        name은 고정 노드명, node는 동기/비동기 dict→dict 함수다. 실행 전에 STEP_STARTED,
        정상 결과 뒤 공개 요약/Tool/STEP_COMPLETED를 발행한다. 예외면 코드나 메시지를 payload에
        복사하지 않고 STEP_COMPLETED(status=failed, error_type) 후 원 예외를 다시 발생시킨다.
        반환 wrapper는 원 result를 변경하지 않는다. 저장·재시도·추가 LLM/Tool 호출은 없다.
        """

        async def observed(state: dict) -> dict:
            """한 노드의 이벤트 경계를 적용한다. 입력 State와 반환 dict는 수정하지 않는다."""
            await self.emit("STEP_STARTED", name)
            try:
                value = node(state)
                result = await value if inspect.isawaitable(value) else value
                await self._after_node(name, result)
                await self.emit("STEP_COMPLETED", name, payload={"status": "completed"})
                return result
            except Exception as exc:
                await self.emit(
                    "STEP_COMPLETED",
                    name,
                    payload={"status": "failed", "error_type": type(exc).__name__},
                )
                raise

        return observed
