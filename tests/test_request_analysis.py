"""F3005 원문 근거·누락·오류와 실제 LangGraph 분석 노드를 오프라인 검증한다."""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from fuelroute.__main__ import main
from fuelroute.agent import analyze_request, make_graph
from fuelroute.config import Settings
from fuelroute.contracts import ToolResult
from fuelroute.request_analysis import ExtractedRequest

TODAY = date(2026, 11, 19)
USER = (
    "대구(128.6014,35.8714)에서 서울(126.9780,37.5665)로 내일 오후3시에 출발하고 "
    "오후8시까지 도착할 거야. 휘발유이고 현재 연료15L, 안전 잔량3L, 연비12km/L야. "
    "우회15분, 추가거리10km 이내에서 싼 곳으로 주유 추천해줘."
)


def full_extraction(**changes) -> ExtractedRequest:
    """명시적 원문 구절을 근거로 한 합성 모델 출력을 만든다. 외부 호출·실제 추출은 없다."""
    values = {
        "intent": "recommend",
        "reason": "주유소 추천을 요청했습니다.",
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
        "trip_date": {"value": "2026-11-20", "quote": "내일"},
        "departure_time": {"value": "15:00", "quote": "오후3시"},
        "arrival_deadline": {"value": "20:00", "quote": "오후8시"},
        "fuel_type": {"value": "gasoline", "quote": "휘발유"},
        "sort_by": {"value": "price", "quote": "싼 곳"},
        "current_fuel_l": {"value": 15, "quote": "현재 연료15L"},
        "reserve_fuel_l": {"value": 3, "quote": "안전 잔량3L"},
        "avg_efficiency_kmpl": {"value": 12, "quote": "연비12km/L"},
        "max_detour_min": {"value": 15, "quote": "우회15분"},
        "max_detour_km": {"value": 10, "quote": "추가거리10km"},
    }
    return ExtractedRequest.model_validate({**values, **changes})


def analyze_fake(user: str, extracted: object) -> ToolResult:
    """대역 분석 함수를 그래프에 주입한다. GMS 키·외부 HTTP 없이 공통 결과를 반환한다."""

    async def fake(message: str) -> ExtractedRequest:
        """호출 원문이 유지되는지 확인하고 미리 정한 합성 출력을 반환한다."""
        assert message == user.strip()
        return extracted

    return asyncio.run(
        analyze_request(user, Settings(_env_file=None), analyzer=fake, reference_date=TODAY)
    )


def test_full_analysis_uses_reference_graph_structure_and_groups() -> None:
    """완전한 조건을 코드로 분류하고 분석 함수만 1회 호출하는 실제 그래프를 확인한다."""
    calls = []

    async def fake(user: str) -> ExtractedRequest:
        """원문을 기록하며 순수한 합성 추출 결과만 제공한다."""
        calls.append(user)
        return full_extraction()

    graph = make_graph(fake, TODAY)
    assert set(graph.get_graph().nodes) == {"__start__", "analyze", "__end__"}
    state = asyncio.run(graph.ainvoke({"user": USER}))
    result = state["analysis"]
    assert calls == [USER]
    assert result["ready_for_planning"] and not result["needs_user_input"]
    assert result["conditions"]["trip_date"] == "2026-11-20"
    assert result["conditions"]["departure_time"] == "15:00:00"
    assert result["conditions"]["arrival_deadline"] == "20:00:00"
    assert result["conditions"]["current_fuel_l"] == 15
    assert result["condition_groups"]["preferences"] == ["fuel_type", "sort_by"]
    assert "current_fuel_l" in result["condition_groups"]["constraints"]


def test_missing_conditions_and_labels_do_not_create_fuel_or_coordinates() -> None:
    """지명만 있는 요청에서 지명은 유지하고 좌표·연료·연비·잔량·유종을 요구한다."""
    user = "대구에서 서울 가는 길에 주유 추천해줘"
    raw = ExtractedRequest(
        intent="recommend",
        reason="주유 추천 요청",
        origin_label={"value": "대구", "quote": "대구"},
        destination_label={"value": "서울", "quote": "서울"},
    )
    result = analyze_fake(user, raw)
    assert result.ok
    data = result.data["analysis"]
    assert data["conditions"]["origin_label"] == "대구"
    assert data["conditions"]["origin"] is None and data["conditions"]["current_fuel_l"] is None
    assert set(data["missing_fields"]) == {
        "origin",
        "destination",
        "fuel_type",
        "current_fuel_l",
        "reserve_fuel_l",
        "avg_efficiency_kmpl",
    }
    assert data["needs_user_input"] and not data["ready_for_planning"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_fuel_l", 0),
        ("avg_efficiency_kmpl", -1),
        ("reserve_fuel_l", -1),
        ("reserve_fuel_l", 15),
        ("max_detour_min", -1),
        ("max_detour_km", -1),
        ("origin", {"longitude": 181, "latitude": 95}),
    ],
)
def test_invalid_user_values_are_input_corrections(field: str, value: object) -> None:
    """원문에 있는 잘못된 값은 외부 장애와 구분해 보완할 입력으로 반환한다."""
    units = {
        "current_fuel_l": "L",
        "reserve_fuel_l": "L",
        "avg_efficiency_kmpl": "km/L",
        "max_detour_min": "분",
        "max_detour_km": "km",
    }
    quote = f"별도 입력 {value}{units.get(field, '')}"
    result = analyze_fake(
        USER + quote, full_extraction(**{field: {"value": value, "quote": quote}})
    )
    assert result.ok
    assert field in result.data["analysis"]["invalid_fields"]
    assert result.data["analysis"]["needs_user_input"]


@pytest.mark.parametrize(
    ("field", "value", "quote"),
    [
        ("current_fuel_l", 99, "현재 연료15L"),
        ("reserve_fuel_l", 3, "존재하지 않는 원문"),
        ("trip_date", "2026-11-21", "내일"),
        ("departure_time", "17:00", "오후3시"),
        ("origin", {"longitude": 127, "latitude": 37}, "대구"),
    ],
)
def test_hallucinated_values_and_modified_quotes_are_not_accepted(
    field: str, value: object, quote: str
) -> None:
    """값·근거·상대 날짜가 원문과 다르면 제거하고 입력 확인을 요청한다."""
    result = analyze_fake(USER, full_extraction(**{field: {"value": value, "quote": quote}}))
    data = result.data["analysis"]
    assert data["conditions"][field] is None and field in data["unverified_fields"]
    assert data["needs_user_input"] and not data["ready_for_planning"]
    assert len(data["questions"]) == 1


@pytest.mark.parametrize("intent", ["greeting", "explain", "other"])
def test_non_planning_intents_do_not_require_trip_fields(intent: str) -> None:
    """인사·설명 요청은 주행 필수값을 되묻지 않고 이후 응답 단계에 분류만 전달한다."""
    result = analyze_fake(
        "안녕, 서비스 설명해줘", ExtractedRequest(intent=intent, reason="서비스 관련 대화")
    )
    data = result.data["analysis"]
    assert data["missing_fields"] == [] and not data["needs_user_input"]
    assert not data["ready_for_planning"] and data["questions"] == []


@pytest.mark.parametrize(
    ("field", "value", "quote"),
    [
        ("current_fuel_l", 15, "현재 연료15갤런"),
        ("avg_efficiency_kmpl", 12, "연비12mpg"),
        ("max_detour_min", 1, "우회1시간"),
        ("max_detour_km", 10, "추가거리10m"),
        ("current_fuel_l", 15, "현재 연료15"),
    ],
)
def test_unsupported_or_missing_units_are_not_silently_reinterpreted(
    field: str, value: float, quote: str
) -> None:
    """같은 숫자라도 단위가 다르거나 없으면 값으로 채택하지 않고 확인을 요청한다."""
    result = analyze_fake(
        USER + quote, full_extraction(**{field: {"value": value, "quote": quote}})
    )
    data = result.data["analysis"]
    assert data["conditions"][field] is None and field in data["unverified_fields"]
    assert data["needs_user_input"]


@pytest.mark.parametrize("user", ["", " \n ", "x" * 5001])
def test_invalid_message_stops_before_analyzer(user: str) -> None:
    """빈 입력·길이 초과는 모델을 호출하지 않고 공통 INVALID_INPUT으로 반환한다."""

    async def forbidden(message: str) -> ExtractedRequest:
        """입력 오류를 모델에 보내면 즉시 테스트를 실패시킨다."""
        raise AssertionError("분석 함수 호출 금지")

    result = asyncio.run(analyze_request(user, Settings(_env_file=None), analyzer=forbidden))
    assert not result.ok and result.error.code == "INVALID_INPUT"


def test_invalid_extractor_output_and_missing_config_are_common_failures() -> None:
    """모델 출력 계약과 설정 누락을 사용자 조건 누락 성공 상태와 구분한다."""
    invalid = analyze_fake("안녕", {"unexpected": True})
    assert not invalid.ok and invalid.error.code == "INVALID_RESPONSE"
    missing = asyncio.run(analyze_request("안녕", Settings(_env_file=None)))
    assert not missing.ok and missing.error.code == "CONFIG_ERROR"


@pytest.mark.parametrize("failure", [None, 401, 429, "timeout"])
def test_gms_analyzer_configuration_errors_and_client_cleanup(monkeypatch, failure) -> None:
    """GMS 전용 모델 설정·시스템/사용자 분리·실패 정제와 클라이언트 종료를 검증한다."""
    calls = []

    async def extract(messages) -> ExtractedRequest:
        """합성 구조화 응답 또는 민감한 원문이 있는 SDK 예외를 제공한다. HTTP는 없다."""
        assert messages[-1].content == USER
        assert "2026-11-19" in messages[0].content
        request = httpx.Request("POST", "https://gms.ssafy.io/test")
        if failure == "timeout":
            raise APITimeoutError(request=request)
        if failure:
            raise APIStatusError(
                "private-test-key",
                response=httpx.Response(failure, request=request),
                body={"api_key": "private-test-key"},
            )
        return full_extraction()

    def structured(schema, method):
        """프로덕션 추출 스키마와 구조화 출력 방식을 확인한다."""
        assert schema is ExtractedRequest and method == "json_schema"
        return SimpleNamespace(ainvoke=extract)

    fake = SimpleNamespace(
        with_structured_output=structured,
        root_async_client=SimpleNamespace(close=AsyncMock()),
        root_client=SimpleNamespace(close=Mock()),
    )

    def factory(**kwargs):
        """호출 인자를 기록하고 네트워크 없는 SDK 대역을 반환한다."""
        calls.append(kwargs)
        return fake

    monkeypatch.setattr("fuelroute.agent_gms.ChatOpenAI", factory)
    settings = Settings(
        _env_file=None,
        gms_api_key="private-test-key",
        gms_base_url="https://gms.ssafy.io/test",
        gms_model="test-model",
    )
    result = asyncio.run(analyze_request(USER, settings, reference_date=TODAY))
    assert result.ok == (failure is None)
    if failure:
        assert (
            result.error.code
            == {401: "AUTH_FAILED", 429: "RATE_LIMITED", "timeout": "TIMEOUT"}[failure]
        )
    assert "private-test-key" not in result.model_dump_json()
    assert calls[0]["model"] == "test-model" and calls[0]["base_url"] == settings.gms_base_url
    assert calls[0]["max_retries"] == 0 and not calls[0]["use_responses_api"]
    fake.root_async_client.close.assert_awaited_once()
    fake.root_client.close.assert_called_once()
    assert calls[0]["http_client"].is_closed and calls[0]["http_async_client"].is_closed


def test_analyze_cli_rejects_bad_date_without_external_call(capsys) -> None:
    """CLI의 재현 기준일 형식 오류가 공통 입력 실패·종료 1인지 확인한다."""
    assert main(["analyze", "--message", "안녕", "--reference-date", "not-a-date"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_INPUT"
