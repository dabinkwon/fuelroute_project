"""F3006 계획 순서·입력 보존·호출 생략과 GMS 계약을 실제 그래프에서 검증한다."""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError
from pydantic import ValidationError

from fuelroute.agent.analysis import analyze_conditions
from fuelroute.agent.graph import make_graph, plan_request
from fuelroute.agent.llm import llm_plan
from fuelroute.agent.planning import finalize_plan
from fuelroute.cli.commands import main
from fuelroute.clients.kakao_local import resolve_location
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis
from fuelroute.schemas.planning import PlanProposal

TODAY = date(2026, 11, 19)
USER = (
    "대구(128.6014,35.8714)에서 서울(126.9780,37.5665)로 휘발유 주유 추천해줘. "
    "현재 연료15L, 안전 잔량3L, 연비12km/L이고 우회0분, 추가거리0km야. "
    "내일 오후3시 출발해서 오후8시까지 가고 싶어."
)
ORDER = [
    "base_route",
    "safe_range",
    "search_points",
    "search_candidates",
    "station_details",
    "candidate_routes",
    "evaluate_constraints",
    "rank_candidates",
    "rest_area_info",
]


def extracted(**changes: object) -> ExtractedRequest:
    """명시적 좌표와 연료·0 우회 상한을 가진 합성 추출을 만든다. GMS·HTTP는 없다."""
    values = {
        "intent": "recommend",
        "reason": "주유 추천 요청",
        "origin_label": {"value": "대구", "quote": "대구"},
        "destination_label": {"value": "서울", "quote": "서울"},
        "origin": {
            "value": {"longitude": 128.6014, "latitude": 35.8714},
            "quote": "대구(128.6014,35.8714)",
        },
        "destination": {
            "value": {"longitude": 126.978, "latitude": 37.5665},
            "quote": "서울(126.9780,37.5665)",
        },
        "fuel_type": {"value": "gasoline", "quote": "휘발유"},
        "current_fuel_l": {"value": 15, "quote": "현재 연료15L"},
        "reserve_fuel_l": {"value": 3, "quote": "안전 잔량3L"},
        "avg_efficiency_kmpl": {"value": 12, "quote": "연비12km/L"},
        "max_detour_min": {"value": 0, "quote": "우회0분"},
        "max_detour_km": {"value": 0, "quote": "추가거리0km"},
        "trip_date": {"value": "2026-11-20", "quote": "내일"},
        "departure_time": {"value": "15:00", "quote": "오후3시"},
        "arrival_deadline": {"value": "20:00", "quote": "오후8시"},
    }
    return ExtractedRequest.model_validate({**values, **changes})


def proposal(order: list[str] | None = None) -> PlanProposal:
    """단계 순서를 명시한 모델 대역을 만든다. 실제 판단·수치·외부 조회를 생성하지 않는다."""
    return PlanProposal.model_validate(
        {
            "reason": "안전 조건을 먼저 판정한 뒤 후보를 정렬하고 보조정보를 확인합니다.",
            "steps": [
                {"action": action, "reason": f"{action} 결과가 다음 단계에 필요합니다."}
                for action in (ORDER if order is None else order)
            ],
        }
    )


def test_ready_graph_preserves_inputs_and_never_executes_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 계획 그래프에서 입력·0 상한·기본 정책·선행조건을 보존하고 HTTP가 없는지 검증한다."""
    calls = []

    async def forbidden(*args: object, **kwargs: object) -> None:
        """주입 함수만 쓰는 계획 단계에서 HTTP를 실행하면 테스트를 실패시킨다."""
        raise AssertionError("계획 중 HTTP 실행 금지")

    async def analyze(user: str) -> ExtractedRequest:
        """원문을 기록해 분석이 정확히 한 번만 호출됐는지 확인한다."""
        calls.append("analyze")
        assert user == USER
        return extracted()

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """정규화 조건만 계획기로 전달되는지 확인하고 합성 순서를 반환한다."""
        calls.append("plan")
        assert analysis.ready_for_planning and analysis.conditions.current_fuel_l == 15
        return proposal()

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    result = asyncio.run(
        plan_request(
            USER,
            Settings(_env_file=None),
            analyzer=analyze,
            planner=plan,
            reference_date=TODAY,
        )
    )
    assert result.ok and result.tool_name == "plan_request" and calls == ["analyze", "plan"]
    assert result.data["graph_nodes"] == ["analyze", "build_plan"]
    work = result.data["plan"]
    assert work["status"] == "ready" and [step["action"] for step in work["steps"]] == ORDER
    request = work["request"]
    assert request["origin"] == {"longitude": 128.6014, "latitude": 35.8714}
    assert request["current_fuel_l"] == 15 and request["reserve_fuel_l"] == 3
    assert request["max_detour_min"] == request["max_detour_km"] == 0
    assert request["radius_m"] == 5000 and request["max_search_points"] == 3
    assert request["max_candidates"] == 3 and request["max_replans"] == 2
    assert request["max_evaluations"] == 9 and request["sort_by"] == "price"
    assert result.data["analysis"]["conditions"]["trip_date"] == "2026-11-20"
    assert any("시각" in warning for warning in work["warnings"])
    assert any("미래 가격" in warning for warning in work["warnings"])
    assert "safe_range_km" not in request
    assert all(step["tool_name"] is None for step in work["steps"] if step["kind"] == "local")
    rest = work["steps"][-1]
    assert rest["run_when"] == "if_recommended" and rest["failure_policy"] == "optional"
    search = next(step for step in work["steps"] if step["action"] == "search_candidates")
    assert search["failure_policy"] == "replan_if_empty"


@pytest.mark.parametrize(
    "mode", ["missing", "invalid", "unverified", "greeting", "explain", "other"]
)
def test_incomplete_or_unrelated_input_skips_planning_model(mode: str) -> None:
    """누락·범위·근거 오류와 비추천 의도에서 계획 모델을 호출하지 않고 상태를 구분한다."""

    async def analyze(user: str) -> ExtractedRequest:
        """시나리오별 입력 상태만 합성한다. 실제 GMS 호출은 없다."""
        if mode in {"greeting", "explain", "other"}:
            return ExtractedRequest(intent=mode, reason="추천이 아닌 요청")
        change = {
            "missing": {"current_fuel_l": None},
            "invalid": {"reserve_fuel_l": {"value": 15, "quote": "안전 잔량15L"}},
            "unverified": {"current_fuel_l": {"value": 99, "quote": "현재 연료15L"}},
        }[mode]
        return extracted(**change)

    async def forbidden(analysis: RequestAnalysis) -> PlanProposal:
        """준비되지 않은 입력이 계획 모델로 넘어가면 테스트를 실패시킨다."""
        raise AssertionError("계획 모델 호출 금지")

    result = asyncio.run(
        plan_request(
            USER + " 안전 잔량15L",
            Settings(_env_file=None),
            analyzer=analyze,
            planner=forbidden,
            reference_date=TODAY,
        )
    )
    assert result.ok
    work = result.data["plan"]
    assert work["status"] == (
        "not_required" if mode in {"greeting", "explain", "other"} else "needs_user_input"
    )
    assert work["steps"] == [] and work["request"] is None


@pytest.mark.parametrize("mode", ["missing", "duplicate", "order", "unknown", "extra", "empty"])
def test_invalid_model_plan_is_a_common_failure(mode: str) -> None:
    """필수/안전 단계 누락·중복·역순·미등록 작업·인자 삽입을 복구 없이 거절한다."""

    async def analyze(user: str) -> ExtractedRequest:
        """유효한 입력을 제공해 계획 검증만 분리한다."""
        return extracted()

    async def plan(analysis: RequestAnalysis) -> dict:
        """계약을 위반하는 모델 JSON을 합성한다. 테스트상 반환 타입은 의도적으로 dict다."""
        data = proposal().model_dump()
        if mode == "missing":
            data["steps"] = [step for step in data["steps"] if step["action"] != "safe_range"]
        elif mode == "duplicate":
            data["steps"][-1] = data["steps"][0]
        elif mode == "order":
            data["steps"] = list(reversed(data["steps"]))
        elif mode == "unknown":
            data["steps"][0]["action"] = "delete_logs"
        elif mode == "extra":
            data["steps"][0]["arguments"] = {"max_replans": 100, "reserve_fuel_l": 0}
        else:
            data["steps"] = []
        return data

    result = asyncio.run(
        plan_request(
            USER,
            Settings(_env_file=None),
            analyzer=analyze,
            planner=plan,
            reference_date=TODAY,
        )
    )
    assert not result.ok and result.error.code == "INVALID_RESPONSE" and result.data is None
    assert not result.error.retryable and not result.error.replan_allowed


def test_independent_steps_can_reorder_and_explicit_preference_survives() -> None:
    """독립 작업 순서 변경은 허용하고 사용자의 시간 우선 정렬을 기본값으로 덮지 않는다."""
    analysis = analyze_conditions(
        USER + " 우회시간 우선",
        extracted(sort_by={"value": "detour_time", "quote": "우회시간 우선"}),
        TODAY,
    )
    order = ["safe_range", "base_route", *ORDER[2:]]
    work = finalize_plan(analysis, proposal(order))
    assert work.request.sort_by == "detour_time" and work.steps[0].action == "safe_range"
    assert not analysis.conditions.sort_by == "price"


@pytest.mark.parametrize("ambiguous", [False, True])
def test_geocoding_finishes_before_planning_or_blocks_ambiguous_location(ambiguous: bool) -> None:
    """실제 위치 해석 노드 뒤에 계획을 연결하고 여러 위치 후보에서 계획 모델 호출을 생략한다."""
    planned = []

    def handler(request: httpx.Request) -> httpx.Response:
        """주소 결과를 합성한다. 실제 Kakao API를 호출하지 않는다."""
        name = request.url.params["query"]
        count = 2 if ambiguous and name == "대구" else 1
        row = {"address_name": name, "address_type": "REGION", "x": "127", "y": "37"}
        return httpx.Response(
            200,
            json={
                "meta": {"total_count": count, "is_end": True},
                "documents": [row] * count,
            },
        )

    async def analyze(user: str) -> ExtractedRequest:
        """사용자가 좌표를 쓰지 않은 경우의 합성 추출 결과를 제공한다."""
        return extracted(origin=None, destination=None)

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """제공사 출처가 채워진 다음에만 계획기를 호출했는지 확인한다."""
        planned.append(analysis)
        assert set(analysis.coordinate_sources.values()) == {"kakao_address"}
        return proposal()

    async def scenario() -> None:
        """모의 위치 검색기를 실제 그래프에 주입해 의존 순서와 계획 보류를 확인한다."""
        settings = Settings(_env_file=None, kakao_rest_api_key="test-key")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

            async def lookup(label: str) -> ToolResult[dict]:
                """모의 전송으로만 Local 조회를 수행하며 클라이언트 소유권은 scenario에 둔다."""
                return await resolve_location(label, settings, client)

            result = await plan_request(
                USER,
                settings,
                analyzer=analyze,
                planner=plan,
                reference_date=TODAY,
                location_resolver=lookup,
            )
        assert result.ok and len(planned) == (0 if ambiguous else 1)
        assert result.data["plan"]["status"] == ("needs_user_input" if ambiguous else "ready")
        if not ambiguous:
            assert result.data["plan"]["warnings"]

    asyncio.run(scenario())


def test_reused_graph_overwrites_old_plan_instead_of_leaking_to_next_input() -> None:
    """그래프를 재사용해도 입력이 부족한 다음 요청에 이전 준비된 계획이 남지 않는지 확인한다."""

    async def analyze(user: str) -> ExtractedRequest:
        """첫 요청은 추천, 두 번째 인사는 비추천으로 분리한다."""
        return extracted() if user == USER else ExtractedRequest(intent="greeting", reason="인사")

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """첫 추천의 합성 계획을 반환한다. 별도 전역 캐시는 없다."""
        return proposal()

    async def scenario() -> None:
        """같은 그래프의 이전 plan을 일부러 입력에 섞어도 새 계획 결과로 덮어쓰는지 본다."""
        graph = make_graph(analyze, TODAY, llm_plan_fn=plan)
        first = await graph.ainvoke({"user": USER})
        second = await graph.ainvoke({"user": "안녕", "plan": first["plan"]})
        assert first["plan"]["status"] == "ready"
        assert second["plan"]["status"] == "not_required" and not second["plan"]["steps"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [None, 401, 429, "timeout", "schema"])
def test_gms_analysis_and_plan_use_separate_schemas_and_close_clients(
    monkeypatch: pytest.MonkeyPatch, failure: int | str | None
) -> None:
    """실제 GMS 어댑터의 두 단계·조회 Tool 설명·수치 변경 금지·실패 정제와 종료를 검증한다."""
    calls = []
    models = []

    def factory(**kwargs: object) -> SimpleNamespace:
        """GMS SDK를 대역으로 교체하며 단계별 구조화 스키마와 설정을 기록한다."""
        calls.append(kwargs)

        async def invoke(messages: list) -> ExtractedRequest | PlanProposal | dict:
            """첫 호출은 추출, 두 번째는 계획 또는 합성 SDK 오류를 제공한다. HTTP는 없다."""
            if len(calls) == 1:
                assert messages[-1].content == USER
                return extracted()
            context = json.loads(messages[-1].content)
            assert set(context["external_tools"]) == {
                "get_driving_route",
                "search_gas_stations",
                "get_station_details",
                "search_rest_area_info",
            }
            assert all(
                tool["description"] and tool["input_schema"]["properties"]
                for tool in context["external_tools"].values()
            )
            assert "steps" not in context and "사용자" not in context
            assert context["request"]["reserve_fuel_l"] == 3
            request = httpx.Request("POST", "https://gms.ssafy.io/test/chat/completions")
            if failure == "timeout":
                raise APITimeoutError(request=request)
            if failure in (401, 429):
                raise APIStatusError(
                    "private-test-key",
                    response=httpx.Response(failure, request=request),
                    body={},
                )
            if failure == "schema":
                return {"steps": "bad output"}
            return proposal()

        def structured(schema: type, method: str) -> SimpleNamespace:
            """분석은 추출, 계획은 PlanProposal을 사용하고 json_schema가 유지되는지 확인한다."""
            assert schema is (ExtractedRequest if len(calls) == 1 else PlanProposal)
            assert method == "json_schema"
            return SimpleNamespace(ainvoke=invoke)

        model = SimpleNamespace(
            with_structured_output=structured,
            root_async_client=SimpleNamespace(close=AsyncMock()),
            root_client=SimpleNamespace(close=Mock()),
        )
        models.append(model)
        return model

    monkeypatch.setattr("fuelroute.clients.gms.ChatOpenAI", factory)
    settings = Settings(
        _env_file=None,
        gms_api_key="private-test-key",
        gms_base_url="https://gms.ssafy.io/test",
        gms_model="test-model",
    )
    result = asyncio.run(plan_request(USER, settings, reference_date=TODAY))
    assert result.ok == (failure is None) and len(calls) == 2
    if failure:
        assert (
            result.error.code
            == {
                401: "AUTH_FAILED",
                429: "RATE_LIMITED",
                "timeout": "TIMEOUT",
                "schema": "INVALID_RESPONSE",
            }[failure]
        )
    assert "private-test-key" not in result.model_dump_json()
    for model, options in zip(models, calls):
        model.root_async_client.close.assert_awaited_once()
        model.root_client.close.assert_called_once()
        assert options["http_client"].is_closed and options["http_async_client"].is_closed
        assert options["max_retries"] == 0 and options["model"] == "test-model"


def test_planner_rejects_missing_settings_or_input_without_gms_call() -> None:
    """직접 계획 모델을 호출해도 누락 설정·미준비 입력을 네트워크보다 먼저 거절한다."""
    ready = analyze_conditions(USER, extracted(), TODAY)
    with pytest.raises(ProbeFailure) as missing:
        asyncio.run(llm_plan(ready, Settings(_env_file=None)))
    assert missing.value.error.code == "CONFIG_ERROR"
    incomplete = analyze_conditions(USER, extracted(current_fuel_l=None), TODAY)
    with pytest.raises(ProbeFailure) as invalid:
        asyncio.run(llm_plan(incomplete, Settings(_env_file=None)))
    assert invalid.value.error.code == "INVALID_INPUT"


def test_plan_cli_dispatch_and_invalid_date(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """새 plan 명령이 기준일을 계획 진입점에 전달하고 잘못된 날짜는 호출 전에 거절한다."""
    calls = []

    async def fake(user: str, settings: Settings, *, reference_date: date) -> ToolResult[dict]:
        """CLI 입력을 기록하고 주입 모델로 오프라인 계획을 만든다. 실제 GMS는 사용하지 않는다."""
        calls.append((user, reference_date))

        async def analyze(message: str) -> ExtractedRequest:
            """CLI 경로 확인용 합성 추출 결과를 반환한다."""
            return extracted()

        async def plan(analysis: RequestAnalysis) -> PlanProposal:
            """CLI 경로 확인용 합성 작업 순서를 반환한다."""
            return proposal()

        return await plan_request(
            user,
            settings,
            analyzer=analyze,
            planner=plan,
            reference_date=reference_date,
        )

    monkeypatch.setattr("fuelroute.cli.commands.plan_request", fake)
    assert main(["plan", "--message", USER, "--reference-date", "2026-11-19"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["plan"]["status"] == "ready"
    assert calls == [(USER, TODAY)]
    assert main(["plan", "--message", USER, "--reference-date", "bad-date"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_INPUT"
    assert len(calls) == 1


def test_serialized_planning_schema_constrains_actions_and_requires_descriptions() -> None:
    """모델에 전달하는 직렬화 스키마에서 필수 필드·작업 enum·설명이 실제로 존재하는지 검증한다."""
    schema = PlanProposal.model_json_schema()
    step = schema["$defs"]["ProposedStep"]
    assert set(step["properties"]["action"]["enum"]) == set(ORDER)
    assert set(step["required"]) == {"action", "reason"} and not step["additionalProperties"]
    assert set(schema["required"]) == {"reason", "steps"}
    assert all(field["description"] for field in step["properties"].values())
    assert all(field["description"] for field in schema["properties"].values())
    with pytest.raises(ValidationError):
        PlanProposal.model_validate({"reason": "추가 실행 인자", "steps": [], "request": {}})
