"""F3011 최종 설명의 사실·누락/미확인·출처·모델 오류·전체 그래프/CLI를 검증한다."""

import asyncio
import json
from datetime import datetime, time

import httpx
import pytest
from test_execution import TODAY, USER, Provider, extracted, proposal, settings
from test_graph_replanning import ReplanProvider
from test_judgement import context as judgement_context
from test_judgement import cost

from fuelroute.agent.analysis import refresh_analysis
from fuelroute.agent.graph import execute_request
from fuelroute.agent.llm import llm_response_outline
from fuelroute.agent.response import build_response_facts, finalize_response
from fuelroute.calculations.constraints import apply_judgement, judge_execution
from fuelroute.cli.commands import main
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolError, ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis, TripConditions
from fuelroute.schemas.memory import ConversationMemory
from fuelroute.schemas.planning import PlanProposal
from fuelroute.schemas.replanning import ReplanState
from fuelroute.schemas.requests import PlanningRequest
from fuelroute.schemas.response import (
    ExplanationOutline,
    ResponseContext,
    ResponseFacts,
)
from fuelroute.schemas.stations import RestAreaInfo, RestAreasOutput


def response_context(**conditions: object) -> ResponseContext:
    """사용자 근거와 정상 비용을 가진 F3008 합성 결과를 최종 설명 선행 상태로 만든다.

    A1700원/31km/우회2km·10분,B1710원/36km/우회3km·5분이다. 계산·판정은실제함수를
    사용하고네트워크/모델은없다. 반환입력은응답생성중수정하면안된다.
    """
    _, _, analysis, plan, execution = judgement_context(**conditions)
    judgement = judge_execution(analysis, plan, execution)
    return ResponseContext(
        analysis=analysis,
        plan=plan,
        execution=apply_judgement(execution, judgement),
        judgement=judgement,
        replanning=ReplanState(
            stop_reason="recommendation_found"
            if judgement.status == "recommended"
            else "evaluation_budget_exhausted"
        ),
    )


def rest_info() -> RestAreasOutput:
    """실제운영과구분할시설True/False/None의합성보조자료를반환한다. 주유소매칭은미확인이다."""
    return RestAreasOutput(
        catalog_count=1,
        skipped_invalid_count=0,
        items=[
            RestAreaInfo(
                name="참고휴게소",
                longitude=127.01,
                latitude=37,
                route_name="합성고속도로",
                direction="상행",
                distance_m=123.4,
                reference_date="2026-11-01",
                facilities={"toiletYn": True, "shopYn": False, "nrsgYn": None},
            )
        ],
    )


def outline(facts: ResponseFacts) -> ExplanationOutline:
    """전체사실을정확히한번포함하는기본순서의문단을만든다. 사실본문/값은수정하지않는다."""
    return ExplanationOutline(paragraphs=[{"fact_ids": [fact.fact_id]} for fact in facts.facts])


def test_recommendation_contains_real_values_limits_sources_and_unknowns() -> None:
    """조회가격·실제접근/우회·가격시각·시설3상태·부분평가한계가입력값대로나오는지검사한다."""
    context = response_context(max_detour_min=15)
    context.execution.results.rest_area_info = rest_info()
    before = context.model_dump(mode="json")
    result = asyncio.run(finalize_response(context))
    assert result.status == "recommended" and result.recommended_station_id == "A"
    assert "1,700원/L" in result.answer and "31km" in result.answer
    assert "2km" in result.answer and "10분 0초" in result.answer
    assert "150km" in result.answer and "15분" in result.answer
    assert "2026-11-19 09:00:00 KST" in result.answer
    assert "화장실 있음" in result.answer and "매점 없음" in result.answer
    assert "수유실 미확인" in result.answer and "2026-11-01" in result.answer
    assert "직선거리 약 123m" in result.answer
    assert "미확인(unverified)" in result.answer and "실시간 운영 여부" in result.answer
    assert "전체 경로 최저가" in result.answer and "보장하지 않습니다" in result.answer
    assert result.scope == "evaluated_candidates_only" and result.mode == "template"
    assert context.model_dump(mode="json") == before


@pytest.mark.parametrize("sort_by,station", [("price", "A"), ("detour_time", "B")])
def test_response_keeps_user_ranking(sort_by: str, station: str) -> None:
    """가격/시간우선정렬은판정결과를그대로설명하며최종모델이추천을다시선택하지않는다."""
    result = asyncio.run(finalize_response(response_context(sort_by=sort_by)))
    assert result.recommended_station_id == station
    assert ("가격 → 추가 시간" if sort_by == "price" else "추가 시간 → 추가 거리") in result.answer


@pytest.mark.parametrize("timestamp", [None, datetime(2026, 11, 19, 12)])
def test_unknown_price_timestamp_is_not_labeled_kst(timestamp: datetime | None) -> None:
    """가격기준시각없음/시간대없음은현재시각으로보충하거나KST로추측하지않는다."""
    context = response_context()
    for item in context.execution.results.station_details:
        item.price_observed_at = timestamp
    context.judgement = judge_execution(context.analysis, context.plan, context.execution)
    context.execution = apply_judgement(context.execution, context.judgement)
    result = asyncio.run(finalize_response(context))
    assert "가격 기준시각: 미확인" in result.answer and "시간대가 확인되지" in result.answer
    assert "2026-11-19 12:00:00 KST" not in result.answer


def test_meter_boundary_is_not_rounded_into_safe_reach() -> None:
    """1m미만표현반올림으로안전거리경계를숨기지않고149999m를149.999km로표시한다."""
    context = response_context()
    context.execution.results.candidate_routes["A"].approach = cost(149999, 1000)
    context.judgement = judge_execution(context.analysis, context.plan, context.execution)
    context.execution = apply_judgement(context.execution, context.judgement)
    result = asyncio.run(finalize_response(context))
    assert result.recommended_station_id == "A" and "149.999km" in result.answer


def test_unreachable_cheapest_candidate_is_excluded_in_answer() -> None:
    """최저가A가안전거리를1m넘으면추천B와A의제외사유를구분해설명한다."""
    context = response_context()
    context.execution.results.candidate_routes["A"].approach = cost(150001, 1000)
    context.judgement = judge_execution(context.analysis, context.plan, context.execution)
    context.execution = apply_judgement(context.execution, context.judgement)
    result = asyncio.run(finalize_response(context))
    assert result.recommended_station_id == "B" and "1,710원/L" in result.answer
    assert any(
        fact.category == "exclusion" and "안전 주행거리 초과" in fact.text
        for fact in result.evidence
    )


def test_time_response_has_only_explicit_dates_and_stop_duration() -> None:
    """정차10분을포함한날짜없는도착시각은날짜를만들지않고판정기준시각으로설명한다."""
    context = response_context(
        departure_time=time(15), arrival_deadline=time(16, 20), refuel_stop_min=10
    )
    result = asyncio.run(finalize_response(context))
    arrival = next(fact.text for fact in result.evidence if fact.fact_id == "arrival")
    assert "16:20:00" in arrival and "날짜 미지정" in arrival and "2026-" not in arrival
    assert "10분 0초" in arrival


def test_negative_detour_reports_clamping_without_free_detour_claim() -> None:
    """음수원차이를0으로비교한경우실제로우회비용이없다고보장하지않는한계를표시한다."""
    context = response_context()
    context.execution.results.base_route = cost(110000, 5000)
    context.judgement = judge_execution(context.analysis, context.plan, context.execution)
    context.execution = apply_judgement(context.execution, context.judgement)
    result = asyncio.run(finalize_response(context))
    assert "비용 차이가 음수" in result.answer and "추가 비용이0이라고 보장하지" in result.answer


def test_no_recommendation_includes_major_reasons_and_bounded_search() -> None:
    """전체제외는확정추천없이주요사유·예산종료·다음입력을주며전구간후보없음으로단정하지않는다."""
    context = response_context(remaining_range_km=31, reserve_range_km=1)
    context.replanning = ReplanState(
        replan_count=2,
        stop_reason="evaluation_budget_exhausted",
        next_input=["현재 주행 가능거리와 상세 위치를 확인해주세요."],
    )
    result = asyncio.run(finalize_response(context))
    assert result.status == "no_recommendation" and result.recommended_station_id is None
    assert "안전 주행거리 초과" in result.answer and "평가 예산 소진" in result.answer
    assert "추가 탐색은 2회" in result.answer and result.questions
    assert "전체 경로에 주유소가 없다는 뜻은 아닙니다" in result.answer


def test_missing_stop_time_skips_final_model_and_asks_only_missing_input() -> None:
    """정차시간만없으면최종모델을호출하지않고해당질문만반환하며주유소를확정하지않는다."""
    context = response_context(departure_time=time(15), arrival_deadline=time(20))

    async def forbidden(facts: ResponseFacts) -> ExplanationOutline:
        """입력보완분기에서모델호출이있으면실패한다."""
        raise AssertionError("final model forbidden")

    result = asyncio.run(finalize_response(context, forbidden))
    assert result.status == "needs_user_input" and result.mode == "template"
    assert len(result.questions) == 1 and "정차" in result.questions[0]
    assert result.recommended_station_id is None


@pytest.mark.parametrize("intent", ["greeting", "explain", "other"])
def test_non_tool_conversation_uses_no_final_model(intent: str) -> None:
    """인사/설명/범위밖은조회모델없이안내하며세션이있으면과거조건보존을알린다."""
    context = response_context()
    context.analysis.intent = intent
    context.memory = ConversationMemory(session_id="trip", has_trip=True)

    async def forbidden(facts: ResponseFacts) -> ExplanationOutline:
        """서비스안내에데이터API나최종모델을추가하지않는다."""
        raise AssertionError("narration forbidden")

    result = asyncio.run(finalize_response(context, forbidden))
    assert result.status == "not_required" and result.mode == "template"
    assert "기존 주행 조건은 유지" in result.answer


@pytest.mark.parametrize("code", ["AUTH_FAILED", "RATE_LIMITED", "CONFIG_ERROR"])
def test_core_failure_has_no_recommendation_or_final_model(code: str) -> None:
    """핵심실패는원인안내만하고확정추천·최종모델호출없이error상태를만든다."""
    context = response_context()
    context.execution.error = ToolError(code=code, message="정제된 오류")

    async def forbidden(facts: ResponseFacts) -> ExplanationOutline:
        """핵심실패의고정안내에는GMS가필요없다."""
        raise AssertionError("narration forbidden")

    result = asyncio.run(finalize_response(context, forbidden))
    assert result.status == "error" and result.recommended_station_id is None
    assert result.questions and "확정하지 않습니다" in result.answer


def test_valid_model_groups_verified_facts_and_cannot_mutate_them() -> None:
    """모델은문단구성만선택하고넘긴복사본을변조해도실제가격·추천은바뀌지않는다."""
    calls = []

    async def model(facts: ResponseFacts) -> ExplanationOutline:
        """일부문단순서를바꾸고복사본본문을고의변경해렌더러의사실격리를확인한다."""
        calls.append(1)
        facts.facts[0].text = "가격은1원이며무료세차입니다"
        ids = [facts.facts[0].fact_id] + [fact.fact_id for fact in reversed(facts.facts[1:])]
        return ExplanationOutline(
            paragraphs=[{"fact_ids": ids[i : i + 3]} for i in range(0, len(ids), 3)]
        )

    result = asyncio.run(finalize_response(response_context(), model))
    assert result.mode == "model" and calls == [1]
    assert "1,700원/L" in result.answer and "가격은1원" not in result.answer
    assert "무료세차" not in result.answer


@pytest.mark.parametrize("bad", ["unknown", "missing", "duplicate", "reverse", "free_text"])
def test_model_schema_or_evidence_violation_falls_back(bad: str) -> None:
    """새내용/누락/중복/결과순서위반/자유본문은다시호출하지않고확인한사실로복구한다."""
    calls = []

    async def model(facts: ResponseFacts) -> ExplanationOutline:
        """고의의잘못된출력을반환한다. 유효성검사는실제최종응답기가수행한다."""
        calls.append(1)
        ids = [item.fact_id for item in facts.facts]
        if bad == "unknown":
            ids[0] = "가격은100원"
        elif bad == "missing":
            ids.pop()
        elif bad == "duplicate":
            ids.append(ids[-1])
        elif bad == "reverse":
            ids.reverse()
        else:
            return {"answer": "새 문장"}
        return ExplanationOutline(
            paragraphs=[{"fact_ids": ids[i : i + 8]} for i in range(0, len(ids), 8)]
        )

    result = asyncio.run(finalize_response(response_context(), model))
    assert (
        calls == [1]
        and result.mode == "fallback"
        and result.generation_error_code == "INVALID_RESPONSE"
    )
    assert result.recommended_station_id == "A" and "1,700원/L" in result.answer
    assert "가격은100원" not in result.answer


@pytest.mark.parametrize("code", ["TIMEOUT", "AUTH_FAILED", "RATE_LIMITED", "UPSTREAM_ERROR"])
def test_final_model_failure_preserves_valid_recommendation(code: str) -> None:
    """설명모델오류만으로유효추천을실패처리하지않고코드만남기며자동재시도하지않는다."""
    calls = []

    async def fail(facts: ResponseFacts) -> ExplanationOutline:
        """정제된모델실패만합성한다. 실제GMS나데이터API는호출하지않는다."""
        calls.append(1)
        raise ProbeFailure(code, "정제된 오류")

    result = asyncio.run(finalize_response(response_context(), fail))
    assert result.mode == "fallback" and result.generation_error_code == code
    assert result.status == "recommended" and calls == [1]


def test_semantic_state_mismatch_is_not_hidden_by_fallback() -> None:
    """원비용이변경돼이전판정과다르면기본추천으로감추지않고내부계약오류를알린다."""
    context = response_context()
    context.execution.results.candidate_routes["A"].approach = cost(150001, 1000)
    context.execution.results.candidate_routes["B"].approach = cost(150001, 1000)
    with pytest.raises(ProbeFailure) as caught:
        build_response_facts(context)
    assert caught.value.error.code == "INVALID_RESPONSE"


def test_untrusted_provider_name_is_literal_text_not_markdown_or_instruction() -> None:
    """제공사이름의마크다운/HTML은일반문자로표시하고모델본문으로실행하지않는다."""
    context = response_context()
    for item in context.execution.results.station_details:
        item.name = "![image](https://example.test) <script>bad()</script>"
    context.judgement = judge_execution(context.analysis, context.plan, context.execution)
    context.execution = apply_judgement(context.execution, context.judgement)
    result = asyncio.run(finalize_response(context))
    assert "<script>" not in result.answer and "![image](" not in result.answer
    assert "&lt;script&gt;" in result.answer and "1,700원/L" in result.answer


def test_model_prompt_contains_only_fact_text_not_raw_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GMS프롬프트에사실ID/분류/본문만주고원본실행·좌표기하·출처메타는주지않는다."""
    captured = []
    facts = build_response_facts(response_context())

    async def structured(
        schema: type, messages: list, config: Settings, stage: str
    ) -> ExplanationOutline:
        """실제SDK대신입력스키마/메시지/모델단계를검사한다. HTTP는호출하지않는다."""
        captured.append(messages)
        assert schema is ExplanationOutline and stage == "최종 설명 구성"
        return outline(facts)

    monkeypatch.setattr("fuelroute.agent.llm._structured_response", structured)
    asyncio.run(llm_response_outline(facts, Settings(_env_file=None)))
    payload = json.loads(captured[0][1].content)
    assert all(set(item) == {"fact_id", "category", "text"} for item in payload["facts"])
    assert "vertexes" not in captured[0][1].content and "request_id" not in captured[0][1].content


def test_graph_responds_after_enrichment_and_keeps_source_call_ids() -> None:
    """생산진입점이보조조회뒤최종노드를한번실행하고모든근거ID가실제기록에연결되는지검사한다."""
    provider = Provider()
    calls = []

    async def analyze(user: str) -> ExtractedRequest:
        """분석모델만대체하고원문과km조건을유지한다."""
        return extracted()

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """실제계획검증용아홉작업을제안한다."""
        return proposal()

    async def model(facts: ResponseFacts) -> ExplanationOutline:
        """보조정보가먼저끝났는지확인한뒤문단한번만반환한다."""
        assert provider.calls[-1] == "rest"
        calls.append(1)
        assert any(fact.category == "rest_area" for fact in facts.facts)
        return outline(facts)

    async def scenario() -> None:
        """열린클라이언트는호출자가소유하며전체그래프와공통최종출력을확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            result = await execute_request(
                USER,
                settings(),
                analyzer=analyze,
                planner=plan,
                narrator=model,
                client=client,
                reference_date=TODAY,
            )
            assert not client.is_closed
        assert result.ok and result.data["answer"] == result.data["final_response"]["answer"]
        assert result.data["final_response"]["mode"] == "model" and calls == [1]
        assert (
            "respond" in result.data["graph_nodes"] and result.data["narrator_mode"] == "injected"
        )
        actual_ids = {
            item["result"]["meta"]["request_id"] for item in result.data["execution"]["calls"]
        }
        assert set(result.data["final_response"]["source_request_ids"]) <= actual_ids
        assert len(result.data["final_response"]["source_request_ids"]) > 0

    asyncio.run(scenario())


def test_cli_text_prints_answer_and_default_keeps_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """run --text는한국어본문을,기본run은기존공통JSON을출력하며정상종료0을유지한다."""
    from fuelroute.core.contracts import ToolMeta, utc_now

    async def entry(user: str, config: Settings, **kwargs: object) -> ToolResult[dict]:
        """CLI출력만검사하도록구조화된최종답변을대입한다. API/DB/모델은호출하지않는다."""
        return ToolResult(
            ok=True,
            tool_name="execute_request",
            data={"answer": "추천 결과입니다."},
            error=None,
            meta=ToolMeta(provider="local", request_id="cli", elapsed_ms=0, fetched_at=utc_now()),
        )

    monkeypatch.setattr("fuelroute.cli.commands.execute_request", entry)
    monkeypatch.setattr("fuelroute.cli.commands.Settings", lambda: Settings(_env_file=None))
    assert main(["run", "--message", USER, "--text"]) == 0
    assert capsys.readouterr().out.strip() == "추천 결과입니다."
    assert main(["run", "--message", USER]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["answer"] == "추천 결과입니다."


def test_final_model_runs_once_after_two_replans() -> None:
    """추가2회가있어도최종설명은모든판정뒤한번만호출하며상한·주요제외근거를반환한다."""
    provider = ReplanProvider("limit")
    calls = []

    async def analyze(user: str) -> ExtractedRequest:
        """명시좌표/km를추출하고후보가전부도달불가인모의응답까지실제흐름을연결한다."""
        return extracted()

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """기본평가9곳·추가2회정책을그대로사용한다."""
        return proposal()

    async def model(facts: ResponseFacts) -> ExplanationOutline:
        """평가9곳을완료한뒤에만문단을구성한다. 추가LLM이나재시도는없다."""
        assert len([call for call in provider.calls if call.startswith("detail:")]) == 9
        calls.append(1)
        return outline(facts)

    async def scenario() -> None:
        """생산진입점의최종응답/재계획관계를모의전송과모델로검사한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            result = await execute_request(
                USER,
                settings(),
                analyzer=analyze,
                planner=plan,
                narrator=model,
                client=client,
                reference_date=TODAY,
            )
        assert result.ok and calls == [1]
        assert result.data["replanning"]["replan_count"] == 2
        assert "추가 탐색은 2회" in result.data["answer"]
        assert "전체 평가 예산 소진" in result.data["answer"]
        assert result.data["final_response"]["status"] == "no_recommendation"

    asyncio.run(scenario())


def test_optional_rest_failure_is_visible_and_keeps_recommendation() -> None:
    """휴게소503은보조미확인으로설명하되주유추천과해당실패출처를유지한다."""
    provider = Provider("rest_error")

    async def analyze(user: str) -> ExtractedRequest:
        """모델통신을생략할명시요청을반환한다."""
        return extracted()

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """실제조회/판정/보조까지진행할정상계획을반환한다."""
        return proposal()

    async def scenario() -> None:
        """최종설명이실패를누락하거나추천자체의실패로표현하지않는지확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            result = await execute_request(
                USER,
                settings(),
                analyzer=analyze,
                planner=plan,
                client=client,
                reference_date=TODAY,
            )
        assert result.ok and result.data["final_response"]["status"] == "recommended"
        assert "휴게소 보조정보는 미확인" in result.data["answer"]
        facts = result.data["final_response"]["evidence"]
        missing = next(item for item in facts if item["fact_id"] == "rest_unavailable")
        assert any(
            source["provider"] == "rest_area" and source["request_id"]
            for source in missing["sources"]
        )
        assert provider.calls.count("rest") == 1

    asyncio.run(scenario())


def test_final_prompt_redacts_configured_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """데이터문자열에키가섞여도최종모델로넘기기전에환경설정의비밀값을정제한다."""
    context = response_context()
    for station in context.execution.results.station_details:
        station.name = "fake-secret"
    context.judgement = judge_execution(context.analysis, context.plan, context.execution)
    context.execution = apply_judgement(context.execution, context.judgement)
    facts = build_response_facts(context)

    async def structured(
        schema: type, messages: list, config: Settings, stage: str
    ) -> ExplanationOutline:
        """모델대신직렬화된데이터의비밀값부재를검사한다. 실제HTTP는없다."""
        assert "fake-secret" not in messages[1].content
        assert "[REDACTED]" in messages[1].content
        return outline(facts)

    monkeypatch.setattr("fuelroute.agent.llm._structured_response", structured)
    asyncio.run(llm_response_outline(facts, Settings(_env_file=None, opinet_api_key="fake-secret")))


def test_legacy_fuel_units_are_explained_without_inventing_km_inputs() -> None:
    """기존L/연비요청도실제입력식으로설명하며계기판km입력을역산하지않는다."""
    context = response_context()
    values = dict(
        remaining_range_km=None,
        reserve_range_km=None,
        current_fuel_l=15,
        reserve_fuel_l=3,
        avg_efficiency_kmpl=12,
    )
    context.analysis.conditions = TripConditions.model_validate(
        {**context.analysis.conditions.model_dump(), **values}
    )
    context.analysis.range_input_mode = "fuel"
    context.analysis = refresh_analysis(context.analysis)
    context.plan.request = PlanningRequest.model_validate(
        {**context.plan.request.model_dump(), **values}
    )
    context.judgement = judge_execution(context.analysis, context.plan, context.execution)
    context.execution = apply_judgement(context.execution, context.judgement)
    result = asyncio.run(finalize_response(context))
    safe = next(fact.text for fact in result.evidence if fact.fact_id == "safe_range")
    assert "15L" in safe and "3L" in safe and "12km/L" in safe and "144km" in safe
    assert "입력한 주행 가능거리" not in safe


def test_text_cli_preserves_failure_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--text실패도공통오류의한국어메시지와종료1을유지하며성공문장으로감추지않는다."""
    from fuelroute.core.contracts import ToolMeta, utc_now

    async def entry(user: str, config: Settings, **kwargs: object) -> ToolResult[dict]:
        """정제된인증실패만반환한다. HTTP·환경파일·실제키는사용하지않는다."""
        return ToolResult(
            ok=False,
            tool_name="execute_request",
            data=None,
            error=ToolError(code="AUTH_FAILED", message="인증키와 API 권한을 확인하세요."),
            meta=ToolMeta(
                provider="local", request_id="cli-error", elapsed_ms=0, fetched_at=utc_now()
            ),
        )

    monkeypatch.setattr("fuelroute.cli.commands.execute_request", entry)
    monkeypatch.setattr("fuelroute.cli.commands.Settings", lambda: Settings(_env_file=None))
    assert main(["run", "--message", USER, "--text"]) == 1
    assert capsys.readouterr().out.strip() == "인증키와 API 권한을 확인하세요."
