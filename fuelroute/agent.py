"""State·make_graph·모델 함수 주입으로 구성한 F3005 분석 전용 그래프."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fuelroute.agent_gms import llm_analyze
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

Analyzer = Callable[[str], Awaitable[ExtractedRequest]]
LocationResolver = Callable[[str], Awaitable[ToolResult[dict]]]


class State(TypedDict, total=False):
    """현재는 user→analysis만 전달한다. 계획·실행·Memory·이벤트는 후속 작업이다."""

    user: str
    analysis: dict


class AnalysisInput(BaseModel):
    """이번 요청 원문과 테스트·재현에 사용할 선택 기준일."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str = Field(min_length=1, max_length=5000, description="이번 요청의 원문")
    reference_date: date | None = None


def make_graph(
    llm_analyze_fn: Analyzer,
    reference_date: date,
    location_resolver: LocationResolver | None = None,
) -> CompiledStateGraph:
    """분석 함수와 선택 위치 검색 함수를 주입받아 F3005 그래프를 만든다.

    첨부 step3_agent.make_graph와 같은 State·노드·엣지 구성이다. 주입 함수는 원문을
    받아 ExtractedRequest를 비동기로 반환한다. 테스트 대역도 같은 계약이다. 등록 시
    호출·DB·이벤트는 없고 실행 시에만 분석 함수를 1회 호출한다. 아직 F3006~F3014의
    노드를 포함하지 않는다. 위치 검색 함수가 있으면 추천 요청만 resolve_locations에
    연결한다. 이미 입력한 좌표·잘못된 좌표 입력은 덮어쓰지 않는다. 예:
    await make_graph(fake, today, resolver).ainvoke({'user': ...}).
    잘못된 요청은 ValidationError, 모델 출력 스키마 오류는 INVALID_RESPONSE다.
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
    if location_resolver is None:
        graph.add_edge("analyze", END)
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
            """추천 의도에만 위치 검색을 연결한다. 인사·설명은 조회 없이 끝낸다."""
            return "resolve_locations" if state["analysis"]["intent"] == "recommend" else END

        graph.add_node("resolve_locations", resolve_locations)
        graph.add_conditional_edges(
            "analyze", after_analysis, {"resolve_locations": "resolve_locations", END: END}
        )
        graph.add_edge("resolve_locations", END)
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

    async def operation() -> dict:
        """요청별 분석기·그래프를 만들고 정규화 결과를 반환한다. 전역 상태는 공유하지 않는다."""
        query = AnalysisInput(user=user, reference_date=reference_date)
        today = query.reference_date or datetime.now(timezone(timedelta(hours=9))).date()

        async def gms_analyze(message: str) -> ExtractedRequest:
            """현재 설정·기준일만 캡처해 GMS 호출 함수를 주입 가능한 계약으로 맞춘다."""
            return await llm_analyze(message, settings, today)

        async def execute(resolver: LocationResolver | None) -> tuple[State, list[str]]:
            """그래프를 실행하고 구성한 노드 이름을 반환한다. 클라이언트 종료는 호출자 책임이다."""
            graph = make_graph(analyzer or gms_analyze, today, resolver)
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
        return {
            "analysis": output["analysis"],
            "reference_date": today.isoformat(),
            "graph_nodes": nodes,
            "analyzer_mode": "injected" if analyzer else "gms",
        }

    return await run_probe(settings, "local" if analyzer else "gms", "analyze_request", operation)
