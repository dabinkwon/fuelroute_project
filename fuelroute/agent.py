"""State·make_graph·모델 함수 주입으로 구성한 F3005 분석·F3006 작업 계획 그래프."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fuelroute.agent_gms import llm_analyze, llm_plan
from fuelroute.config import Settings
from fuelroute.contracts import ToolResult
from fuelroute.geocoding import LocationMatchSet, resolve_location
from fuelroute.probes import Coordinate, ProbeFailure, run_probe
from fuelroute.request_analysis import (
    ExtractedRequest,
    RequestAnalysis,
    analyze_conditions,
    refresh_analysis,
)
from fuelroute.task_plan import PlanProposal, WorkPlan, finalize_plan, prepare_plan

Analyzer = Callable[[str], Awaitable[ExtractedRequest]]
LocationResolver = Callable[[str], Awaitable[ToolResult[dict]]]
Planner = Callable[[RequestAnalysis], Awaitable[PlanProposal]]


class State(TypedDict, total=False):
    """현재 원문·분석·작업 계획을 전달한다. 조회 실행·Memory·이벤트는 후속 작업이다."""

    user: str
    analysis: dict
    plan: dict


class AnalysisInput(BaseModel):
    """이번 요청 원문과 테스트·재현에 사용할 선택 기준일."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str = Field(min_length=1, max_length=5000, description="이번 요청의 원문")
    reference_date: date | None = None


def make_graph(
    llm_analyze_fn: Analyzer,
    reference_date: date,
    location_resolver: LocationResolver | None = None,
    *,
    llm_plan_fn: Planner | None = None,
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
    시간·연료·거리 계산과 실제 Tool 실행·Memory·저장은 하지 않는다.
    """

    async def analyze(state: State) -> dict:
        """원문을 검증해 주입 분석기를 호출하고 조건·누락·오류를 State에 갱신한다.

        현재 요청만 읽으며 이전 analysis를 참고하지 않는다. 잘못된 원문은 모델 호출
        전에 거절한다. 모델의 값은 원문 근거와 다시 대조하며 Tool 실행·저장은 없다.
        """
        query = AnalysisInput(user=state.get("user", ""))
        if not query.user.strip():
            raise ProbeFailure("INVALID_INPUT", "요청 문장을 입력하세요.")
        try:
            extracted = ExtractedRequest.model_validate(await llm_analyze_fn(query.user))
        except ValidationError as exc:
            raise ProbeFailure("INVALID_RESPONSE", "요청 분석 출력 계약을 확인하세요.") from exc
        result = analyze_conditions(query.user, extracted, reference_date)
        return {"analysis": result.model_dump(mode="json")}

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
        graph.add_edge("build_plan", END)
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
) -> ToolResult[dict]:
    """분석 전용 그래프를 실행하고 성공·예상 가능한 실패를 공통 JSON으로 반환한다.

    user는 1~5000자 원문이다. reference_date 기본값은 실행 시점 KST 날짜다. analyzer가
    없으면 설정된 GMS를 1회 호출하고, 테스트 주입이면 GMS 설정·클라이언트를 사용하지
    않는다. 실제 GMS 경로에서는 지명만 있는 위치를 Kakao Local로 자동 검색하며 위치당
    주소 1회·필요할 때 키워드 1회, 두 위치 최대 4회다. 테스트 분석기만 주입하면 자동
    네트워크 검색을 하지 않으며 location_resolver도 주입해 검증할 수 있다.
    누락/범위 오류는 성공 data.analysis의 입력 보완 상태로, 설정·인증·출력
    오류는 공통 error로 구분한다. 아직 추천을 실행하거나 대화 맥락을 유지하지 않는다.
    예: await analyze_request('서울 가는 길에 주유', settings)는 누락 목록을 반환한다.
    DB·로그 파일·길찾기·주유소 API·재시도는 없다. 비밀값은 run_probe에서 정제한다.
    """
    return await _run_request(
        user,
        settings,
        analyzer=analyzer,
        reference_date=reference_date,
        location_resolver=location_resolver,
        include_plan=False,
        planner=None,
    )


async def plan_request(
    user: str,
    settings: Settings,
    *,
    analyzer: Analyzer | None = None,
    planner: Planner | None = None,
    reference_date: date | None = None,
    location_resolver: LocationResolver | None = None,
) -> ToolResult[dict]:
    """F3005 분석·위치 해석 뒤 F3006 작업 계획까지 실행해 공통 결과를 반환한다.

    user는1~5000자, 기준일은 선택 date이며 기본 실행일 KST다. 실제 경로는 GMS 분석1회,
    필요한 위치 조회 최대4회, 준비된 추천에 한해 GMS 계획1회다. analyzer/planner와 선택
    location_resolver를 각각 주입하면 같은 그래프를 오프라인 검증한다. 테스트 분석기를
    주입한 경우 자동 위치 조회는 없으며 계획기 미주입이면 준비된 요청은 GMS를 사용한다.
    성공 data.analysis와 data.plan을 반환하고 계획 보류/생략은 성공 상태 안에서 구분한다.
    ready는 순서 준비이며 조회·추천 성공이 아니다. 모델 출력/순서/인증 오류는 공통 실패다.
    예: 연료가 없으면 plan.status=needs_user_input이고 계획 모델 호출은0회다.
    길찾기·주유소·휴게소 조회 실행·계산·DB·Memory·이벤트·자동 재시도는 없다.
    """
    return await _run_request(
        user,
        settings,
        analyzer=analyzer,
        reference_date=reference_date,
        location_resolver=location_resolver,
        include_plan=True,
        planner=planner,
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
) -> ToolResult[dict]:
    """분석/계획 진입점이 공유하는 요청별 그래프·의존성과 클라이언트 수명을 관리한다.

    입력 범위와 기준일은 AnalysisInput으로 검사한다. include_plan이 참일 때만 주입 또는
    GMS 계획기를 연결하고, 결과에 plan/planner_mode를 추가한다. 나머지 입력/분석기/위치
    검색 계약은 analyze_request와 같다. 설정/예상 실패는 run_probe의 공통 응답이며
    프로그래밍 오류는 숨기지 않는다. 외부 호출은 주입 함수 또는 GMS/Local만 가능하고
    요청 사이 상태·캐시는 공유하지 않는다. DB·실행 Tool·자동 재시도는 없다.
    예: include_plan=False이면 기존 분석 CLI의 출력과 호출 횟수를 유지한다.
    """

    async def operation() -> dict:
        """검증한 원문·기준일로 요청별 그래프를 실행하고 분석/계획 JSON을 만든다.

        분석/계획 함수 주입과 자동 Local 클라이언트 소유권을 정한다. 반환에는 실제 데이터와
        구성한 노드·의존성 모드를 담으며 노드 목록은 실행 이력이 아니다. 누락/외부 오류는
        바깥 run_probe에서 정제하고 전역 상태·DB·로그·재시도는 만들지 않는다.
        """
        query = AnalysisInput(user=user, reference_date=reference_date)
        today = query.reference_date or datetime.now(timezone(timedelta(hours=9))).date()

        async def gms_analyze(message: str) -> ExtractedRequest:
            """현재 설정·기준일만 캡처해 GMS 호출 함수를 주입 가능한 계약으로 맞춘다."""
            return await llm_analyze(message, settings, today)

        async def gms_plan(analysis: RequestAnalysis) -> PlanProposal:
            """현재 요청의 정규화 분석을 GMS 계획기에 전달한다. 실행 Tool은 호출하지 않는다."""
            return await llm_plan(analysis, settings)

        async def execute(resolver: LocationResolver | None) -> tuple[State, list[str]]:
            """현재 분석/선택 계획기를 연결한 그래프를 실행하고 State와 구성 노드를 반환한다.

            resolver=None은 자동 위치 검색을 하지 않는다. include_plan에 따라 계획 노드를
            포함하고 그래프의 예상 오류는 상위로 전달한다. 주입 함수 외 추가 호출·저장은
            없으며 HTTP 클라이언트 종료는 호출자 책임이다. 노드 이름은 실행 이력이 아니다.
            """
            graph = make_graph(
                analyzer or gms_analyze,
                today,
                resolver,
                llm_plan_fn=(planner or gms_plan) if include_plan else None,
            )
            output = await graph.ainvoke({"user": query.user})
            return output, [name for name in graph.get_graph().nodes if not name.startswith("__")]

        if analyzer is not None or location_resolver is not None:
            output, nodes = await execute(location_resolver)
        else:
            async with httpx.AsyncClient() as client:

                async def kakao_resolver(label: str) -> ToolResult[dict]:
                    """지명 한 건을 요청 소유 HTTP 클라이언트로 검색한다. 추가 LLM 호출은 없다."""
                    return await resolve_location(label, settings, client)

                output, nodes = await execute(kakao_resolver)
        data = {
            "analysis": output["analysis"],
            "reference_date": today.isoformat(),
            "graph_nodes": nodes,
            "analyzer_mode": "injected" if analyzer else "gms",
        }
        if include_plan:
            data.update(plan=output["plan"], planner_mode="injected" if planner else "gms")
        return data

    return await run_probe(
        settings,
        "local" if analyzer else "gms",
        "plan_request" if include_plan else "analyze_request",
        operation,
    )
