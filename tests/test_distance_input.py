"""계기판 km 입력의 계산·추출·계획·Tool·CLI와 기존 실행기 연결을 검증한다."""

import asyncio
import json
from datetime import date

import httpx
import pytest

from fuelroute.agent.analysis import analyze_conditions
from fuelroute.agent.graph import plan_request
from fuelroute.agent.planning import STEP_SPECS
from fuelroute.agent.replanning import recommend_route
from fuelroute.calculations.driving_range import calculate_safe_range
from fuelroute.cli.commands import main
from fuelroute.cli.route_spike import run_route_spike
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolError, ToolMeta, ToolResult, utc_now
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis
from fuelroute.schemas.planning import PlanProposal
from fuelroute.schemas.requests import PlanningRequest, RouteSpikeRequest
from fuelroute.tools.registry import ToolRegistry

TODAY = date(2026, 11, 19)
USER = (
    "대구(128.6014,35.8714)에서 서울(126.9780,37.5665)로 휘발유 주유 추천해줘. "
    "주행 가능180km, 안전 여유30km야."
)


def extracted(**changes: object) -> ExtractedRequest:
    """km 두 값과 좌표를 가진 합성 모델 출력을 만든다. L/연비는 생성하지 않는다."""
    return ExtractedRequest.model_validate(
        {
            "intent": "recommend",
            "reason": "주유 추천 요청",
            "origin": {
                "value": {"longitude": 128.6014, "latitude": 35.8714},
                "quote": "대구(128.6014,35.8714)",
            },
            "destination": {
                "value": {"longitude": 126.978, "latitude": 37.5665},
                "quote": "서울(126.9780,37.5665)",
            },
            "fuel_type": {"value": "gasoline", "quote": "휘발유"},
            "remaining_range_km": {"value": 180, "quote": "주행 가능180km"},
            "reserve_range_km": {"value": 30, "quote": "안전 여유30km"},
            **changes,
        }
    )


@pytest.mark.parametrize(
    ("remaining", "reserve", "expected"), [(180, 30, 150), (180, 0, 180), (0.5, 0.25, 0.25)]
)
def test_dashboard_distance_subtraction(remaining: float, reserve: float, expected: float) -> None:
    """km 차감식과 안전 여유0·소수 경계를 검증한다. 연비나 L 값은 필요하지 않다."""
    assert calculate_safe_range(remaining_range_km=remaining, reserve_range_km=reserve) == expected


@pytest.mark.parametrize(
    "values",
    [
        {"remaining_range_km": 0, "reserve_range_km": 0},
        {"remaining_range_km": -1, "reserve_range_km": 0},
        {"remaining_range_km": 180, "reserve_range_km": -1},
        {"remaining_range_km": 180, "reserve_range_km": 180},
        {"remaining_range_km": 180, "reserve_range_km": 200},
        {"remaining_range_km": float("nan"), "reserve_range_km": 0},
        {"remaining_range_km": float("inf"), "reserve_range_km": 0},
        {"remaining_range_km": 180},
        {"reserve_range_km": 30},
        {},
        {"remaining_range_km": 180, "reserve_range_km": 30, "current_fuel_l": 15},
    ],
)
def test_incomplete_invalid_or_mixed_input_is_rejected(values: dict) -> None:
    """누락·비유한 값·안전 여유 침범·L 혼용을 임의 보정/환산 없이 거절한다."""
    with pytest.raises(ValueError):
        calculate_safe_range(**values)


def test_km_analysis_and_plan_do_not_require_or_invent_fuel() -> None:
    """분석→계획 그래프가 km 두 값만으로 준비되고 연비/L를 만들지 않는지 검증한다."""

    async def analyze(user: str) -> ExtractedRequest:
        """확인된 원문만 가진 추출 결과를 제공한다. 실제 GMS는 호출하지 않는다."""
        return extracted()

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """km 조건이 준비된 뒤 허용 작업 순서만 반환한다. 수치는 계산하지 않는다."""
        assert analysis.range_input_mode == "distance" and not analysis.missing_fields
        return PlanProposal(
            reason="안전 범위를 기준으로 조회",
            steps=[{"action": name, "reason": "다음 단계에 필요한 작업"} for name in STEP_SPECS],
        )

    result = asyncio.run(
        plan_request(
            USER,
            Settings(_env_file=None),
            analyzer=analyze,
            planner=plan,
            reference_date=TODAY,
        )
    )
    assert result.ok and result.data["plan"]["status"] == "ready"
    request = result.data["plan"]["request"]
    assert request["remaining_range_km"] == 180 and request["reserve_range_km"] == 30
    assert request["current_fuel_l"] is None and request["avg_efficiency_kmpl"] is None
    step = next(step for step in result.data["plan"]["steps"] if step["action"] == "safe_range")
    assert step["inputs_from"] == ["request.remaining_range_km", "request.reserve_range_km"]


@pytest.mark.parametrize(
    "mode", ["reserve_missing", "remaining_missing", "bad_unit", "bad_reserve", "mixed"]
)
def test_km_input_corrections_ask_only_relevant_values(mode: str) -> None:
    """부족한 km 값·단위 오류·안전 여유 침범·혼용을 구분하고 불필요한 연비를 요구하지 않는다."""
    change = {
        "reserve_missing": {"reserve_range_km": None},
        "remaining_missing": {"remaining_range_km": None},
        "bad_unit": {"remaining_range_km": {"value": 180, "quote": "주행 가능180km/L"}},
        "bad_reserve": {"reserve_range_km": {"value": 180, "quote": "안전 여유180km"}},
        "mixed": {"current_fuel_l": {"value": 15, "quote": "연료15L"}},
    }[mode]
    analysis = analyze_conditions(
        USER + " 주행 가능180km/L 안전 여유180km 연료15L", extracted(**change), TODAY
    )
    assert not analysis.ready_for_planning and analysis.needs_user_input
    assert "avg_efficiency_kmpl" not in analysis.missing_fields
    if mode == "bad_unit":
        assert "remaining_range_km" in analysis.unverified_fields
    elif mode == "bad_reserve":
        assert "reserve_range_km" in analysis.invalid_fields
    elif mode == "mixed":
        assert "range_input" in analysis.invalid_fields
    else:
        assert analysis.missing_fields == [
            "reserve_range_km" if mode == "reserve_missing" else "remaining_range_km"
        ]


def test_optional_efficiency_does_not_change_dashboard_range() -> None:
    """km 모드에서 별도 연비를 입력해도 도달 범위를 재환산하지 않는지 확인한다."""
    assert (
        calculate_safe_range(remaining_range_km=180, reserve_range_km=30, avg_efficiency_kmpl=12)
        == 150
    )


def test_registered_km_tool_schema_and_common_failure() -> None:
    """실제 등록 Tool에 km 필드·조건부 필수·설명이 전달되고 잘못된 입력은 공통 실패인지 본다."""

    async def scenario() -> None:
        """HTTP 없는 계산 Tool을 등록·실행하고 입력 오류와 단위를 확인한다."""
        async with httpx.AsyncClient() as client:
            registry = ToolRegistry(Settings(_env_file=None), client)
            contract = registry.contracts()["calculate_safe_range"]
            schema = contract["input_schema"]
            assert schema["anyOf"][0]["required"] == ["remaining_range_km", "reserve_range_km"]
            assert schema["properties"]["remaining_range_km"]["description"]
            valid = await registry.invoke(
                "calculate_safe_range", {"remaining_range_km": 180, "reserve_range_km": 30}
            )
            invalid = await registry.invoke("calculate_safe_range", {"remaining_range_km": 180})
        assert valid.ok and valid.data == {"safe_range_km": 150}
        assert not invalid.ok and invalid.error.code == "INVALID_INPUT"

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["route-spike", "recommend"])
def test_cli_km_input_dispatches_without_liters_or_efficiency(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """두 실제 조회 CLI의 km 옵션이 L/연비 필수 인자 없이 실행 요청까지 전달되는지 확인한다."""

    async def fake(
        request: RouteSpikeRequest, settings: Settings, client: httpx.AsyncClient
    ) -> ToolResult[dict]:
        """외부 조회 대신 검증된 km 요청만 확인한다. 실제 네트워크는 없다."""
        assert request.remaining_range_km == 180 and request.reserve_range_km == 30
        assert request.current_fuel_l is None and request.avg_efficiency_kmpl is None
        return ToolResult(
            ok=True,
            tool_name="test",
            data={"safe_range_km": 150},
            error=None,
            meta=ToolMeta(provider="local", request_id="test", elapsed_ms=0, fetched_at=utc_now()),
        )

    target = "recommend_route" if command == "recommend" else "run_route_spike"
    monkeypatch.setattr("fuelroute.cli.commands." + target, fake)
    assert (
        main(
            [
                command,
                "--origin",
                "128.6",
                "35.87",
                "--destination",
                "126.97",
                "37.56",
                "--remaining-range-km",
                "180",
                "--reserve-range-km",
                "30",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["data"]["safe_range_km"] == 150


@pytest.mark.parametrize("replanning", [False, True])
def test_existing_executors_use_km_range_for_actual_search_boundary(
    replanning: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기존 실행기의 검색 경계까지 km 입력이 전달되는지 모의 경로·0건 후보로 검증한다."""
    meta = ToolMeta(provider="test", request_id="test", elapsed_ms=0, fetched_at=utc_now())

    async def route(*args: object, **kwargs: object) -> ToolResult[dict]:
        """1000m 상세 도로를 합성해 검색점 생성까지 실행시킨다. 실제 길찾기는 없다."""
        raw = {
            "result_code": 0,
            "summary": {"distance": 1000, "duration": 60},
            "sections": [
                {
                    "distance": 1000,
                    "duration": 60,
                    "roads": [{"distance": 1000, "vertexes": [127, 37, 127.01, 37]}],
                }
            ],
        }
        return ToolResult(
            ok=True,
            tool_name="get_driving_route",
            data={
                "distance_m": 1000,
                "duration_s": 60,
                "response": {"routes": [raw]},
            },
            error=None,
            meta=meta,
        )

    async def empty(*args: object, **kwargs: object) -> ToolResult[dict]:
        """후보0건을 반환해 실제 가격/경로 조회로 이어지지 않게 한다."""
        return ToolResult(
            ok=False,
            tool_name="search_gas_stations",
            data=None,
            error=ToolError(code="EMPTY_RESULT", message="합성0건", replan_allowed=True),
            meta=meta,
        )

    monkeypatch.setattr("fuelroute.agent.replanning.probe_route", route)
    monkeypatch.setattr("fuelroute.cli.route_spike.probe_route", route)
    monkeypatch.setattr("fuelroute.tools.fuel_tools.FuelTools.search_gas_stations", empty)

    async def scenario() -> None:
        """키는 합성값이며 내부 계산·검색점·공통 결과만 확인한다. 네트워크는 호출하지 않는다."""
        settings = Settings(_env_file=None, kakao_rest_api_key="test", opinet_api_key="test")
        values = {
            "origin": {"longitude": 127, "latitude": 37},
            "destination": {"longitude": 127.01, "latitude": 37},
            "remaining_range_km": 180,
            "reserve_range_km": 30,
            "fuel_type": "gasoline",
            "max_search_points": 1,
        }
        async with httpx.AsyncClient() as client:
            result = (
                await recommend_route(PlanningRequest(**values, max_replans=0), settings, client)
                if replanning
                else await run_route_spike(RouteSpikeRequest(**values), settings, client)
            )
        assert result.ok and result.data["safe_range_km"] == 150

    asyncio.run(scenario())
