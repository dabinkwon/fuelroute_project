"""함수 주입으로 구성한 F3005~F3010 분석·계획·판정·재계획·대화 맥락 그래프."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from fuelroute.agent.analysis import analyze_conditions, refresh_analysis
from fuelroute.agent.execution import PlanExecutionSession, enrich_rest_area
from fuelroute.agent.llm import llm_analyze, llm_plan
from fuelroute.agent.memory import merge_conditions, remember_turn
from fuelroute.agent.planning import finalize_plan, prepare_plan
from fuelroute.agent.replanning import replan_stop_reason, stop_replanning
from fuelroute.calculations.constraints import apply_judgement, judge_execution
from fuelroute.clients.kakao_local import resolve_location
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.core.redaction import redact
from fuelroute.core.results import run_probe
from fuelroute.schemas.analysis import AnalysisInput, ExtractedRequest, RequestAnalysis
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.judgement import TripJudgement
from fuelroute.schemas.locations import LocationMatchSet
from fuelroute.schemas.memory import ConversationMemory, SessionKey
from fuelroute.schemas.planning import PlanProposal, WorkPlan
from fuelroute.schemas.replanning import ReplanOutcome, ReplanRound, ReplanState
from fuelroute.storage.conversations import ConversationStore, SQLiteConversationStore

Analyzer = Callable[[str], Awaitable[ExtractedRequest]]


LocationResolver = Callable[[str], Awaitable[ToolResult[dict]]]


Planner = Callable[[RequestAnalysis], Awaitable[PlanProposal]]


Executor = Callable[[WorkPlan], Awaitable[PlanExecution]]


Judge = Callable[[RequestAnalysis, WorkPlan, PlanExecution], TripJudgement]


Enricher = Callable[[WorkPlan, PlanExecution], Awaitable[PlanExecution]]

Replanner = Callable[[WorkPlan, PlanExecution, TripJudgement, int], Awaitable[ReplanOutcome]]


class State(TypedDict, total=False):
    """원문·검증 조건·짧은 대화 맥락·계획·실행·판정·재계획 근거. 이벤트 저장은 후속이다."""

    user: str
    analysis: dict
    plan: dict
    results: dict
    execution: dict
    judgement: dict
    replanning: dict
    session_id: str | None
    memory: dict


def make_graph(
    llm_analyze_fn: Analyzer,
    reference_date: date,
    location_resolver: LocationResolver | None = None,
    *,
    llm_plan_fn: Planner | None = None,
    executor: Executor | None = None,
    judge: Judge | None = None,
    enricher: Enricher | None = None,
    replanner: Replanner | None = None,
    memory: ConversationMemory | None = None,
) -> CompiledStateGraph:
    """분석·선택 위치 검색·선택 계획 함수를 주입받아 실행 전 그래프를 만든다.

    첨부 step3_agent.make_graph와 같은 State·노드·엣지 구성이다. 주입 함수는 원문을
    받아 ExtractedRequest를 비동기로 반환한다. 테스트 대역도 같은 계약이다. 등록 시
    호출·DB·이벤트는 없고 실행 시에만 분석 함수를 1회 호출한다. llm_plan_fn이 있으면
    build_plan에서 입력을 재검사해 준비된 추천만 계획 함수를 1회 호출한다. 없으면 F3005
    분석 전용 동작을 유지한다. 위치 검색 함수가 있으면 추천 요청만 resolve_locations에
    연결한다. 이미 입력한 좌표·잘못된 좌표 입력은 덮어쓰지 않는다. 예:
    await make_graph(fake, today, resolver).ainvoke({'user': ...}).
    잘못된 요청은 ValidationError, 모델 출력/계획 순서 오류는 INVALID_RESPONSE다.
    executor가 있으면 계획 뒤 execute_plan을 연결하고 준비된 계획만 한 회차 실행한다.
    executor를 쓰려면 llm_plan_fn도 필요하다. 구성 오류는ValueError이며 실행 실패는
    State.execution에 보존한다. judge가 있으면 실행 뒤 순수 제약 판정과 선택 보조 조회를
    연결한다. judge에는executor, enricher에는judge가 필요하다. memory는 같은 ID의 검증 조건·요약이며
    병합 노드에서만 읽는다. DB 저장은 상위 진입점이 담당하고 이벤트·최종 문장은 후속이다.
    추가 판정은 API/LLM을 사용하지 않는다. replanner에는judge가
    필요하며 judge→replan→judge로 추가 회차를최대2번 실행한다. 최초실행기는반복하지
    않고 주입 재계획기만 한 회차씩 호출한다. 인증/입력/평가/회차 상한은호출전에검사한다.
    State.replanning에회차·종료사유·입력안내를남기며 최종판정뒤에만enrich한다.
    """

    async def analyze(state: State) -> dict:
        """원문을 검증해 주입 분석기를 호출하고 조건·누락·오류를 State에 갱신한다.

        현재 원문만 근거로 추출값을 검증하고 같은 세션의 memory가 있으면 미언급 조건만
        병합한다. 잘못된 원문은 모델 호출 전에 거절하며 새 지명/무효 값이 이전 정상값에
        가려지지 않게 한다. 반환 analysis는 범위·필수값·출처를 재검사한다. 이 노드에서는
        Tool·DB 저장을 하지 않는다. 예:우회10분만 바꿔도 기존 출발/목적을 이어받는다.
        """
        query = AnalysisInput(user=state.get("user", ""))
        if not query.user.strip():
            raise ProbeFailure("INVALID_INPUT", "요청 문장을 입력하세요.")
        try:
            extracted = ExtractedRequest.model_validate(await llm_analyze_fn(query.user))
        except ValidationError as exc:
            raise ProbeFailure("INVALID_RESPONSE", "요청 분석 출력 계약을 확인하세요.") from exc
        current = analyze_conditions(query.user, extracted, reference_date)
        result = merge_conditions(query.user, extracted, current, memory)
        return {
            "analysis": result.model_dump(mode="json"),
            "session_id": memory.session_id if memory else None,
            "memory": {
                "revision": memory.revision,
                "last_request_summary": memory.last_request_summary,
                "last_response_summary": memory.last_response_summary,
            }
            if memory
            else {},
        }

    if executor is not None and llm_plan_fn is None:
        raise ValueError("실행 노드에는 계획 함수가 필요합니다.")
    if judge is not None and executor is None or enricher is not None and judge is None:
        raise ValueError("판정에는 실행기, 보조 연결에는 판정 함수가 필요합니다.")
    if replanner is not None and judge is None:
        raise ValueError("재계획에는 제약 판정 함수가 필요합니다.")
    graph = StateGraph(State)
    graph.add_node("analyze", analyze)
    graph.add_edge(START, "analyze")
    finish = "build_plan" if llm_plan_fn is not None else END
    if llm_plan_fn is not None:

        async def build_plan(state: State) -> dict:
            """위치 해석 후 입력을 재검증해 계획을 보관하거나 빈 계획으로 보류/생략한다.

            state.analysis만 읽고 준비된 추천에서 주입 계획기를 1회 호출한다. 모델의 작업
            ID·중복·선행조건은 코드로 검증하고 고정 입력 출처와 실패 정책을 붙인다.
            잘못된 모델 출력은 INVALID_RESPONSE이며 자동 복구·실제 조회·DB·이벤트는 없다.
            반환 plan은 후속 실행 노드의 입력으로 사용한다. 인사/누락 입력은 계획 호출0회다.
            """
            analysis = RequestAnalysis.model_validate(state["analysis"])
            prepared = prepare_plan(analysis)
            if isinstance(prepared, WorkPlan):
                result = prepared
            else:
                try:
                    proposal = PlanProposal.model_validate(await llm_plan_fn(analysis))
                except ValidationError as exc:
                    raise ProbeFailure(
                        "INVALID_RESPONSE", "작업 계획 출력 계약을 확인하세요."
                    ) from exc
                result = finalize_plan(analysis, proposal)
            return {"plan": result.model_dump(mode="json")}

        graph.add_node("build_plan", build_plan)
        graph.add_edge("build_plan", "execute_plan" if executor is not None else END)
    if executor is not None:

        async def execute_plan(state: State) -> dict:
            """ready 계획만 실행기에 전달하고 작업별 결과·부분 실패·실행 상태를 덮어쓴다.

            State.plan을 WorkPlan으로 검사한다. 보류/비추천은 실행기 호출0회·빈 결과이며
            이전 요청의 results/execution을 재사용하지 않는다. 주입 실행기 출력 스키마가
            틀리면 INVALID_RESPONSE다. 실제 조회/계산·오류 정책은 실행기 계약을 따르고
            이 노드는 DB·이벤트·추가LLM·재계획을 실행하지 않는다.
            """
            plan = WorkPlan.model_validate(state["plan"])
            try:
                result = (
                    PlanExecution.model_validate(await executor(plan))
                    if plan.status == "ready"
                    else PlanExecution(
                        status=plan.status,
                        warnings=list(plan.warnings),
                    )
                )
            except ValidationError as exc:
                raise ProbeFailure("INVALID_RESPONSE", "작업 실행 출력 계약을 확인하세요.") from exc
            return {
                "results": result.results.model_dump(mode="json"),
                "execution": result.model_dump(mode="json"),
                "replanning": ReplanState().model_dump(mode="json"),
            }

        graph.add_node("execute_plan", execute_plan)
        graph.add_edge("execute_plan", "judge" if judge is not None else END)
    if judge is not None:

        def judge_plan(state: State) -> dict:
            """실제 비용과 입력으로 F3008 제약을 재판정해 근거·확정 추천을 State에 보관한다.

            분석/계획/실행 결과만 사용하며 호출·DB·추가LLM은 없다. 미확인 시간 입력은
            보완 질문으로 반환하고 과거 판정값을 맹신하지 않는다. 출력 스키마 오류는
            INVALID_RESPONSE, 입력/실행 모순은 판정 함수의 ProbeFailure로 전달한다.
            반환 results/execution은 확정 판정을 반영해 이전 추천/보조정보를 잘못 붙이지 않는다.
            """
            analysis = RequestAnalysis.model_validate(state["analysis"])
            plan = WorkPlan.model_validate(state["plan"])
            execution = PlanExecution.model_validate(state["execution"])
            try:
                judgement = TripJudgement.model_validate(judge(analysis, plan, execution))
            except ValidationError as exc:
                raise ProbeFailure("INVALID_RESPONSE", "제약 판정 출력 계약을 확인하세요.") from exc
            updated = apply_judgement(execution, judgement)
            values = {
                "judgement": judgement.model_dump(mode="json"),
                "results": updated.results.model_dump(mode="json"),
                "execution": updated.model_dump(mode="json"),
            }
            if replanner is not None and plan.status == "ready":
                progress = ReplanState.model_validate(state["replanning"])
                previous_ids = {
                    station_id
                    for row in progress.rounds
                    for station_id in row.evaluated_station_ids
                }
                previous_points = {
                    point.model_dump_json()
                    for row in progress.rounds
                    for point in row.search_points
                }
                progress.rounds.append(
                    ReplanRound(
                        round_index=progress.replan_count,
                        trigger=progress.decision.action if progress.decision else "initial",
                        search_points=[
                            point
                            for point in updated.results.search_points
                            if point.model_dump_json() not in previous_points
                        ],
                        evaluated_station_ids=[
                            item.station_id
                            for item in updated.results.evaluate_constraints
                            if item.station_id not in previous_ids
                        ],
                        eligible_count=len(judgement.recommendation.ranked_candidates)
                        if judgement.recommendation
                        else 0,
                        tool_calls_added=len(updated.calls)
                        - sum(row.tool_calls_added for row in progress.rounds),
                    )
                )
                values["replanning"] = progress.model_dump(mode="json")
            return values

        graph.add_node("judge", judge_plan)
        graph.add_edge(
            "judge", "replan" if replanner is not None else ("enrich" if enricher else END)
        )
    if replanner is not None:

        async def replan(state: State) -> dict:
            """판정 뒤 종료 조건을 먼저 검사하고 필요한 추가 회차만 재계획기에 전달한다.

            누적State의 고정계획·실행·판정·횟수만 사용한다. 최초회차는세지않고추가실행이
            실제 반환됐을때만count를1증가시킨다. 최대2회·전체평가예산은주입함수호출전에도
            검사한다. 결과/기본경로/누적호출/기존ID를보존하지않는주입출력은INVALID_RESPONSE다.
            종료면다음입력안내와stop_reason을남기고보조연결/종료로,추가면다시judge로보낸다.
            이노드는추가LLM·제약완화·DB·동일요청재시도를하지않으며 실제조회는주입함수책임이다.
            예:인증실패와정차시간누락은추가회차0회다.
            """
            plan = WorkPlan.model_validate(state["plan"])
            execution = PlanExecution.model_validate(state["execution"])
            judgement = TripJudgement.model_validate(state["judgement"])
            progress = ReplanState.model_validate(state["replanning"])
            reason = replan_stop_reason(plan, execution, judgement, progress.replan_count)
            try:
                outcome = (
                    ReplanOutcome(decision=stop_replanning(reason, judgement))
                    if reason
                    else ReplanOutcome.model_validate(
                        await replanner(plan, execution, judgement, progress.replan_count)
                    )
                )
            except ValidationError as exc:
                raise ProbeFailure("INVALID_RESPONSE", "재계획 출력 계약을 확인하세요.") from exc
            progress.decision = outcome.decision
            if outcome.execution is None:
                progress.stop_reason = outcome.decision.reason
                progress.next_input = outcome.decision.next_input
                execution.replan_needed = False
            else:
                advanced = outcome.execution
                old_ids = {item.station_id for item in execution.results.evaluate_constraints}
                new_ids = {item.station_id for item in advanced.results.evaluate_constraints}
                old_points = {point.model_dump_json() for point in execution.results.search_points}
                new_points = {point.model_dump_json() for point in advanced.results.search_points}
                if (
                    not old_ids.issubset(new_ids)
                    or len(new_ids) > plan.request.max_evaluations
                    or advanced.results.base_route != execution.results.base_route
                    or advanced.calls[: len(execution.calls)] != execution.calls
                    or not (new_ids - old_ids or new_points - old_points)
                ):
                    raise ProbeFailure(
                        "INVALID_RESPONSE", "재계획의 누적 결과·진행·예산을 확인하세요."
                    )
                progress.replan_count += 1
                execution = advanced
            return {
                "replanning": progress.model_dump(mode="json"),
                "execution": execution.model_dump(mode="json"),
                "results": execution.results.model_dump(mode="json"),
            }

        def after_replan(state: State) -> str:
            """종료사유가 없으면 새 결과의judge로, 있으면최종보조연결 또는END로 보낸다.

            재계획노드의구조화상태만읽고 API·변경·LLM·저장은없다. 예:상한종료는judge를
            다시실행하지않아회차기록을중복하지않는다. 상태누락은KeyError로숨기지않는다.
            """
            if state["replanning"]["stop_reason"] is None:
                return "judge"
            return "enrich" if enricher is not None else END

        graph.add_node("replan", replan)
        finish_replan = "enrich" if enricher is not None else END
        graph.add_conditional_edges(
            "replan", after_replan, {"judge": "judge", finish_replan: finish_replan}
        )
    if enricher is not None:

        async def enrich_plan(state: State) -> dict:
            """판정 뒤 확정 후보의 보조정보만 연결한다. 미확정/실패는 추가 조회하지 않는다.

            선택 enricher는 열린 클라이언트를 소유한 상위 함수가 주입한다. 보조 실패는
            추천을 유지하고 반환 호출/경고에 남긴다. 추가LLM·제약 완화·DB·재계획은 없다.
            결과 타입 불일치는INVALID_RESPONSE이며 이전 세션의 상태를 재사용하지 않는다.
            """
            try:
                result = PlanExecution.model_validate(
                    await enricher(
                        WorkPlan.model_validate(state["plan"]),
                        PlanExecution.model_validate(state["execution"]),
                    )
                )
            except ValidationError as exc:
                raise ProbeFailure(
                    "INVALID_RESPONSE", "보조정보 연결 출력 계약을 확인하세요."
                ) from exc
            values = {
                "results": result.results.model_dump(mode="json"),
                "execution": result.model_dump(mode="json"),
            }
            if replanner is not None:
                progress = ReplanState.model_validate(state["replanning"])
                if progress.rounds:
                    before = len(state["execution"]["calls"])
                    progress.rounds[-1].tool_calls_added += len(result.calls) - before
                values["replanning"] = progress.model_dump(mode="json")
            return values

        graph.add_node("enrich", enrich_plan)
        graph.add_edge("enrich", END)
    if location_resolver is None:
        graph.add_edge("analyze", finish)
    else:

        async def resolve_locations(state: State) -> dict:
            """빠진 좌표의 원문 지명만 검색해 유일한 위치를 채운다. 여러 후보는 선택을 요청한다.

            같은 요청의 동일 지명은 성공/실패 모두 재사용한다. 빈 결과 외 제공사 오류가
            나면 다른 위치에도 동일 실패를 전달해 반복 호출하지 않는다. 저장·재시도·LLM
            추가 호출은 없으며 지역 결과는 상세 주소와 다르다는 안내를 남긴다.
            """
            analysis = RequestAnalysis.model_validate(state["analysis"])
            cache = {}
            blocked = None
            for field in ("origin", "destination"):
                label = getattr(analysis.conditions, field + "_label")
                if getattr(analysis.conditions, field) is not None or not label:
                    continue
                if field in analysis.invalid_fields or field in analysis.unverified_fields:
                    continue
                key = " ".join(label.split()).casefold()
                if blocked is not None:
                    lookup = blocked
                elif key in cache:
                    lookup = cache[key]
                else:
                    raw = await location_resolver(label)
                    try:
                        lookup = ToolResult[LocationMatchSet].model_validate(raw.model_dump())
                    except ValidationError as exc:
                        raise ProbeFailure(
                            "INVALID_RESPONSE", "위치 검색 출력 계약을 확인하세요."
                        ) from exc
                    cache[key] = lookup
                    if lookup.error and lookup.error.code not in {"EMPTY_RESULT", "INVALID_INPUT"}:
                        blocked = lookup
                analysis.location_results[field] = lookup
                if lookup.ok and lookup.data.status == "resolved":
                    selected = lookup.data.selected
                    setattr(
                        analysis.conditions,
                        field,
                        Coordinate(longitude=selected.longitude, latitude=selected.latitude),
                    )
                    analysis.coordinate_sources[field] = selected.source
                    analysis.field_sources[field] = "current"
                    if selected.match_level == "area":
                        role = "출발" if field == "origin" else "도착"
                        analysis.warnings.append(
                            f"{role} 위치는 '{label}'의 지역·도로 단위 검색 좌표입니다. "
                            "정확한 위치가 필요하면 상세 주소나 장소명을 입력해주세요."
                        )
            return {"analysis": refresh_analysis(analysis).model_dump(mode="json")}

        def after_analysis(state: State) -> str:
            """추천에만 위치 검색을 연결하고 그 밖은 계획 생략 기록 또는 종료로 보낸다.

            state의 검증된 intent만 읽는다. 조회·부작용·예외는 없고 실행 경로 이름을 반환한다.
            예: 계획 노드가 있으면 인사는 build_plan에서 not_required로 기록한다.
            """
            return "resolve_locations" if state["analysis"]["intent"] == "recommend" else finish

        graph.add_node("resolve_locations", resolve_locations)
        graph.add_conditional_edges(
            "analyze", after_analysis, {"resolve_locations": "resolve_locations", finish: finish}
        )
        graph.add_edge("resolve_locations", finish)
    return graph.compile()


async def analyze_request(
    user: str,
    settings: Settings,
    *,
    analyzer: Analyzer | None = None,
    reference_date: date | None = None,
    location_resolver: LocationResolver | None = None,
    session_id: str | None = None,
    memory_store: ConversationStore | None = None,
) -> ToolResult[dict]:
    """분석·위치 해석·선택 세션 병합을 실행하고 성공/예상 실패를 공통 형식으로 반환한다.

    user는1~5000자, reference_date는 선택 date이며 기본은 실행일KST다. analyzer가 없으면
    GMS1회, 주입이면 해당 분석 함수를 사용한다. 실제 GMS 경로의 지명은 Local로 최대4회
    해석하며 주입 분석기만 있으면 자동 위치 검색을 하지 않는다. location_resolver도
    주입할 수 있다. 누락/무효 입력은 data.analysis의 보완 상태이고 인증/출력은 공통 오류다.
    session_id가 있으면 최대64자 ID를 검사한 뒤 memory_store 또는 기본 SQLite에서 조건을
    읽고 이번에 바뀐 값만 병합한다. 미지정은 기존 단독 요청이며 DB를 만들지 않는다.
    같은 ID의 완료한 분석은 정제된 조건/짧은 요약으로 저장하고 data.session에 버전을 준다.
    저장소 손상은MEMORY_ERROR, 동시 갱신은MEMORY_CONFLICT이며 자동 재시도는 없다.
    길찾기·주유소 조회·추천·로그 파일·추가 요약 LLM은 없다. 예:같은 ID의 두 번째
    '안전 여유30km'는 이전 출발/목적·주행거리와 합쳐 누락 여부를 다시 확인한다.
    """
    return await _run_request(
        user,
        settings,
        analyzer=analyzer,
        reference_date=reference_date,
        location_resolver=location_resolver,
        include_plan=False,
        planner=None,
        session_id=session_id,
        memory_store=memory_store,
    )


async def plan_request(
    user: str,
    settings: Settings,
    *,
    analyzer: Analyzer | None = None,
    planner: Planner | None = None,
    reference_date: date | None = None,
    location_resolver: LocationResolver | None = None,
    session_id: str | None = None,
    memory_store: ConversationStore | None = None,
) -> ToolResult[dict]:
    """분석·선택 맥락 병합·위치 해석 뒤 검증된 작업 계획을 공통 결과로 반환한다.

    user는1~5000자, 선택 기준일은 date이며 기본 KST다. GMS는 분석1회와 준비된 추천의
    계획1회, Local은 최대4회다. analyzer/planner/location_resolver를 각각 주입할 수 있다.
    성공 data.analysis/plan의 ready는 입력·순서 준비이며 실제 추천 성공이 아니다.
    session_id와 memory_store는 analyze_request와 같은 세션/저장 계약이다. 현재 발언에
    없는 검증 조건은 이어받되 무효 새 값은 이전 정상값으로 숨기지 않는다. 준비 안 된
    요청은 계획 모델을 호출하지 않는다. 계획까지 그래프가 완료되면 조건/요약을 저장하고
    버전을 반환한다. 분석/계획 모델 오류로 그래프가 실패하면 저장본을 변경하지 않는다.
    설정/인증/출력/메모리 실패는 공통 오류다. 경로·가격 조회·추천·이벤트·자동 재시도는
    없다. 예:두 번째 우회10분 요청은 기존 출발·목적을 포함한 새 계획을 만든다.
    """
    return await _run_request(
        user,
        settings,
        analyzer=analyzer,
        reference_date=reference_date,
        location_resolver=location_resolver,
        include_plan=True,
        planner=planner,
        session_id=session_id,
        memory_store=memory_store,
    )


async def _run_request(
    user: str,
    settings: Settings,
    *,
    analyzer: Analyzer | None,
    reference_date: date | None,
    location_resolver: LocationResolver | None,
    include_plan: bool,
    planner: Planner | None,
    include_execution: bool = False,
    executor: Executor | None = None,
    client: httpx.AsyncClient | None = None,
    replanner: Replanner | None = None,
    session_id: str | None = None,
    memory_store: ConversationStore | None = None,
) -> ToolResult[dict]:
    """분석/계획/실행 진입점의 요청별 그래프·메모리·클라이언트 수명을 관리한다.

    원문1~5000자·선택 date를 검사한다. include_plan은 계획기를, include_execution은 최초
    실행·판정·최대2회 재계획·마지막 보조를 활성화한다. 세션 ID가 있을 때만 SQLite 또는
    주입 저장소를 읽고 정제한 맥락을 분석 모델·병합 노드에 전달한다. ID 없는 단독 요청은
    DB에 접근하지 않는다. 그래프 완료 후 조건·짧은 요약만 CAS로 저장한다. 실제 Tool
    캐시·경로·가격은 다음 발언에 저장/재사용하지 않고 항상 현재 요청에서 조회한다.
    주입 executor에는 명시 replanner만 연결한다. 주입 HTTP client는 닫지 않고 내부 생성
    client만 닫는다. 핵심 조회 실패는 data=None/error이며 그래프가 반환한 확인 조건은
    저장한다. 분석/계획 실패는 이전 메모리를 유지한다. 메모리 손상/충돌은 전용 공통
    오류이고 프로그래밍 오류는 숨기지 않는다. 로그·계획 DB CRUD·최종 문장·자동 재시도는
    없다. 예:같은 세션이라도 새 요청의 경로·주유 가격은 이전 Tool 응답을 재사용하지 않는다.
    """

    async def operation() -> dict:
        """검증한 원문·기준일로 요청별 그래프를 실행하고 분석/계획 JSON을 만든다.

        분석/계획 함수 주입과 자동 Local 클라이언트 소유권을 정한다. 반환에는 실제 데이터와
        구성한 노드·의존성 모드를 담으며 노드 목록은 실행 이력이 아니다. 누락/외부 오류는
        바깥 run_probe에서 정제하고 전역 상태·DB·로그·재시도는 만들지 않는다.
        """
        query = AnalysisInput(user=user, reference_date=reference_date)
        today = query.reference_date or datetime.now(timezone(timedelta(hours=9))).date()
        previous = None
        store = None
        if session_id is None and memory_store is not None:
            raise ProbeFailure("INVALID_INPUT", "맥락 저장소를 사용하려면 session_id가 필요합니다.")
        if session_id is not None:
            key = SessionKey(session_id=session_id)
            store = memory_store or SQLiteConversationStore(settings.database_path)
            previous = await store.load(key.session_id) or ConversationMemory(
                session_id=key.session_id
            )
            if previous.session_id != key.session_id:
                raise ProbeFailure("MEMORY_ERROR", "다른 세션의 대화 맥락을 사용할 수 없습니다.")
            previous = ConversationMemory.model_validate(
                redact(previous.model_dump(mode="json"), settings.secret_values())
            )

        async def gms_analyze(message: str) -> ExtractedRequest:
            """현재 설정·기준일만 캡처해 GMS 호출 함수를 주입 가능한 계약으로 맞춘다."""
            return await llm_analyze(message, settings, today, memory=previous)

        async def gms_plan(analysis: RequestAnalysis) -> PlanProposal:
            """현재 요청의 정규화 분석을 GMS 계획기에 전달한다. 실행 Tool은 호출하지 않는다."""
            return await llm_plan(analysis, settings)

        async def execute(active_client: httpx.AsyncClient | None) -> tuple[State, list[str]]:
            """현재 분석/선택 계획기를 연결한 그래프를 실행하고 State와 구성 노드를 반환한다.

            테스트 분석기만 주입하면 자동 위치 조회를 생략한다. 필요할 때만 같은 요청의
            클라이언트를 위치 검색/실행기에 전달하며 종료는 호출자 책임이다. 노드 목록은
            구성 정보이고 실제 실행 이력이 아니다. 예외는 상위 공통 응답으로 전달한다.
            """
            resolver = location_resolver
            session = PlanExecutionSession(settings, active_client)
            if resolver is None and analyzer is None:

                async def kakao_resolver(label: str) -> ToolResult[dict]:
                    """원문 지명을 요청 클라이언트로 조회한다. 추가LLM·저장은 없다."""
                    return await resolve_location(label, settings, active_client)

                resolver = kakao_resolver

            async def tools_execute(plan: WorkPlan) -> PlanExecution:
                """요청별 최초 회차를 실행하고 경로/캐시는추가회차까지유지한다. 보조는판정뒤다.

                plan은고정WorkPlan,반환은누적실행이며클라이언트는상위소유다. 추가LLM·DB·
                반복은없다. 예:빈후보도판정/재계획노드로넘기고이함수는다시호출하지않는다.
                """
                return await session.start(plan)

            async def enrich(plan: WorkPlan, execution: PlanExecution) -> PlanExecution:
                """확정 후보의 보조정보를 연결한다. 같은 ID의 과거 보조 호출은 재사용한다."""
                return await enrich_rest_area(execution, plan.request, settings, active_client)

            graph = make_graph(
                analyzer or gms_analyze,
                today,
                resolver,
                llm_plan_fn=(planner or gms_plan) if include_plan else None,
                executor=(executor or tools_execute) if include_execution else None,
                judge=judge_execution if include_execution else None,
                enricher=enrich if include_execution else None,
                replanner=(replanner or session.replan)
                if include_execution and (executor is None or replanner is not None)
                else None,
                memory=previous,
            )
            output = await graph.ainvoke({"user": query.user})
            return output, [name for name in graph.get_graph().nodes if not name.startswith("__")]

        needs_client = (analyzer is None and location_resolver is None) or (include_execution)
        if client is not None:
            output, nodes = await execute(client)
        elif needs_client:
            async with httpx.AsyncClient() as owned_client:
                output, nodes = await execute(owned_client)
        else:
            output, nodes = await execute(None)
        data = {
            "analysis": output["analysis"],
            "reference_date": today.isoformat(),
            "graph_nodes": nodes,
            "analyzer_mode": "injected" if analyzer else "gms",
        }
        if include_plan:
            data.update(plan=output["plan"], planner_mode="injected" if planner else "gms")
        if store is not None:
            remembered = remember_turn(
                previous,
                RequestAnalysis.model_validate(output["analysis"]),
                execution=PlanExecution.model_validate(output["execution"])
                if include_execution
                else None,
                judgement=TripJudgement.model_validate(output["judgement"])
                if include_execution
                else None,
            )
            remembered = ConversationMemory.model_validate(
                redact(remembered.model_dump(mode="json"), settings.secret_values())
            )
            await store.save(remembered, previous.revision)
            data["session"] = {
                "session_id": remembered.session_id,
                "revision": remembered.revision,
                "memory_loaded": previous.revision > 0,
                "inherited_fields": [
                    name
                    for name, source in output["analysis"]["field_sources"].items()
                    if source == "memory"
                ],
                "last_request_summary": remembered.last_request_summary,
                "last_response_summary": remembered.last_response_summary,
                "memory_saved": True,
            }
        if include_execution:
            execution = PlanExecution.model_validate(output["execution"])
            if execution.error:
                error = execution.error
                raise ProbeFailure(
                    error.code,
                    error.message,
                    retryable=error.retryable,
                    replan_allowed=error.replan_allowed,
                )
            data.update(
                results=output["results"],
                execution=output["execution"],
                executor_mode="injected" if executor else "tools",
                judgement=output["judgement"],
                replanning=output["replanning"],
                replanner_mode="injected"
                if replanner
                else ("disabled" if executor else "bounded_tools"),
            )
        return data

    return await run_probe(
        settings,
        "local" if analyzer else "gms",
        "execute_request"
        if include_execution
        else ("plan_request" if include_plan else "analyze_request"),
        operation,
    )


async def execute_request(
    user: str,
    settings: Settings,
    *,
    analyzer: Analyzer | None = None,
    planner: Planner | None = None,
    reference_date: date | None = None,
    location_resolver: LocationResolver | None = None,
    executor: Executor | None = None,
    client: httpx.AsyncClient | None = None,
    replanner: Replanner | None = None,
    session_id: str | None = None,
    memory_store: ConversationStore | None = None,
) -> ToolResult[dict]:
    """조건 맥락을 이어 분석·계획·실제 조회·판정·최대2회 재계획과 보조정보를 반환한다.

    user는1~5000자, 기준일은 선택 date이며 기본KST다. GMS는 분석1회+준비된 추천의 계획1회,
    Local은 최대4회다. 재계획은 미평가 후보 우선이며 기본 회차3/전체9곳·추가2회에서 멈춘다.
    안전 여유·유종·우회/시간 상한은 완화하지 않는다. 전체 조회 상한은Opinet14/Mobility19,
    휴게소1논리조회/최대10HTTP다. 입력 부족·핵심 인증/한도는 추가 회차를 하지 않는다.
    data는analysis/plan/results/execution/judgement/replanning과 의존성 모드를 포함한다.
    session_id가 있으면 같은 세션의 검증 조건과 요약만 이어받고 data.session에 ID/버전/
    상속 필드를 준다. 기본 SQLite는 settings.database_path, 주입은 memory_store다.
    현재 발언의 경로·가격은 새로 조회하고 이전 요청의 Tool 캐시를 다음 발언에 쓰지 않는다.
    모델/resolver/executor/client/replanner는 오프라인 주입용이며 주입 executor의 재계획은
    replanner를 함께 명시해야 한다. 주입 client는 닫지 않는다. 핵심 오류는공통data=None,
    보조 실패는 확정 추천을 유지한다. DB 맥락 실패/충돌도 공통 코드로 알리며 이벤트·계획
    CRUD·최종 문장·동일 실패 재시도는 후속이다. 예:두 번째 '우회10분'은 같은 ID의 위치·
    km 조건을 유지해 현재 데이터로 다시 추천한다.
    """
    return await _run_request(
        user,
        settings,
        analyzer=analyzer,
        reference_date=reference_date,
        location_resolver=location_resolver,
        include_plan=True,
        planner=planner,
        include_execution=True,
        executor=executor,
        client=client,
        replanner=replanner,
        session_id=session_id,
        memory_store=memory_store,
    )
