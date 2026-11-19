"""F3008 경계·재판정·날짜/시각·미확인 입력·그래프의 확정 후보 연결을 검증한다."""

import asyncio
from datetime import date, datetime, time, timezone

import httpx
import pytest

from fuelroute.agent.analysis import analyze_conditions
from fuelroute.agent.graph import execute_request
from fuelroute.agent.planning import finalize_plan
from fuelroute.calculations.constraints import apply_judgement, judge_execution, time_constraint
from fuelroute.calculations.ranking import rank_candidates
from fuelroute.core.config import Settings
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis, TripConditions
from fuelroute.schemas.driving import SafeRangeOutput
from fuelroute.schemas.execution import CandidateRoutes, PlanExecution, WorkResults
from fuelroute.schemas.planning import PlanProposal, WorkPlan
from fuelroute.schemas.recommendation import CandidateEvaluation, RecommendationRequest
from fuelroute.schemas.routes import DrivingRoute, RouteLeg
from fuelroute.schemas.stations import StationDetails

TODAY = date(2026, 11, 19)
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


def cost(distance: int, duration: int, *, first: int | None = None) -> DrivingRoute:
    """거리m·시간초의 합성 비용을 만든다. first가 있으면 단일 경유의 두 구간을 구성한다."""
    lengths = [distance] if first is None else [first, distance - first]
    return DrivingRoute(
        distance_m=distance,
        duration_s=duration,
        legs=[RouteLeg(distance_m=value, duration_s=duration // len(lengths)) for value in lengths],
    )


def context(
    **conditions: object,
) -> tuple[str, ExtractedRequest, RequestAnalysis, WorkPlan, PlanExecution]:
    """원문 근거를 가진 km 조건과 실제 형태의 비용을 합성한다. HTTP/LLM은 사용하지 않는다.

    기본 A는가격1700/도달31km/우회2km·10분/주행70분, B는1710/도달36km/우회3km·5분/
    주행65분이다. 과거 eligible와 계산 비용은 일부러 잘못 둬 새 판정이 원 비용을 쓰게 한다.
    """
    values = {"remaining_range_km": 180, "reserve_range_km": 30, **conditions}
    units = {
        "remaining_range_km": "km",
        "reserve_range_km": "km",
        "avg_efficiency_kmpl": "km/L",
        "max_detour_min": "분",
        "max_detour_km": "km",
        "refuel_stop_min": "분",
    }
    raw = {
        "intent": "recommend",
        "reason": "주유 추천",
        "origin": {"value": {"longitude": 127, "latitude": 37}, "quote": "출발(127,37)"},
        "destination": {"value": {"longitude": 128, "latitude": 37}, "quote": "목적(128,37)"},
        "fuel_type": {"value": "gasoline", "quote": "휘발유"},
    }
    user = "출발(127,37)에서 목적(128,37)으로 휘발유 주유 추천해줘."
    for name, value in values.items():
        if value is None:
            continue
        display = value.isoformat() if isinstance(value, (date, time)) else str(value)
        quote = name + " " + display + units.get(name, "")
        raw[name] = {"value": display if isinstance(value, (date, time)) else value, "quote": quote}
        user += " " + quote
    extracted = ExtractedRequest.model_validate(raw)
    analysis = analyze_conditions(user, extracted, TODAY)
    proposal = PlanProposal(
        reason="작업 계획", steps=[{"action": name, "reason": "필요한 단계"} for name in ORDER]
    )
    plan = finalize_plan(analysis, proposal)
    stations = [
        StationDetails(
            station_id=name,
            name=name,
            longitude=127.01 + i * 0.01,
            latitude=37,
            fuel_type="gasoline",
            price_krw_per_l=1700 + i * 10,
            price_observed_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
            address=None,
            brand=None,
            facilities={"CAR_WASH_YN": None},
        )
        for i, name in enumerate(["A", "B"])
    ]
    evaluations = [
        CandidateEvaluation(
            station_id=station.station_id,
            station=station,
            eligible=True,
            reach_distance_m=0,
            detour_distance_m=0,
            detour_duration_s=0,
        )
        for station in stations
    ]
    pairs = {
        name: CandidateRoutes(
            approach=cost(30000 + i * 5000, 1800),
            via=cost(102000 + i * 1000, 4200 - i * 300, first=31000 + i * 5000),
            approach_request_id=name + "-approach",
            via_request_id=name + "-via",
        )
        for i, name in enumerate(["A", "B"])
    }
    results = WorkResults(
        base_route=cost(100000, 3600),
        safe_range=SafeRangeOutput(safe_range_km=999),
        station_details=stations,
        candidate_routes=pairs,
        evaluate_constraints=evaluations,
        rank_candidates=rank_candidates(
            RecommendationRequest(candidates=evaluations, fuel_type="gasoline", safe_range_km=150)
        ),
    )
    return user, extracted, analysis, plan, PlanExecution(status="recommended", results=results)


def test_actual_costs_are_recomputed_and_source_inputs_are_unchanged() -> None:
    """과거 eligible/0비용/999km 캐시를 신뢰하지 않고 실제 비용과 원 입력으로 재판정한다."""
    _, _, analysis, plan, execution = context()
    before = execution.model_dump_json()
    result = judge_execution(analysis, plan, execution)
    assert result.safe_range_km == 150 and result.recommendation.recommended.station_id == "A"
    a = result.candidates[0]
    assert a.evaluation.reach_distance_m == 31000 and a.range_margin_km == 119
    assert a.evaluation.detour_distance_m == 2000 and a.evaluation.detour_duration_s == 600
    assert a.estimated_fuel_to_station_l is None
    assert a.tool_request_ids == ["A-approach", "A-via"]
    assert all(check.sources for check in a.checks)
    assert execution.model_dump_json() == before


@pytest.mark.parametrize(("limit", "expected"), [(10, "A"), (9.99, "B"), (0, None)])
def test_detour_time_boundary_and_failure(limit: float, expected: str | None) -> None:
    """우회 분/초 경계는 같은 값이면 통과하고 초과 후보를 가격과 관계없이 제외한다."""
    _, _, analysis, plan, execution = context(max_detour_min=limit)
    result = judge_execution(analysis, plan, execution)
    assert (
        result.recommendation.recommended.station_id if result.recommendation.recommended else None
    ) == expected


@pytest.mark.parametrize(("limit", "expected"), [(3, "A"), (2, "A"), (1.99, None)])
def test_detour_distance_boundary(limit: float, expected: str | None) -> None:
    """우회km 상한과 실제m를 비교하며 같은 값·0건을 정확히 처리한다."""
    _, _, analysis, plan, execution = context(max_detour_km=limit)
    result = judge_execution(analysis, plan, execution)
    assert (
        result.recommendation.recommended.station_id if result.recommendation.recommended else None
    ) == expected


@pytest.mark.parametrize(("reach", "expected"), [(150000, "A"), (150001, "B")])
def test_safe_range_boundary_uses_actual_approach_and_first_leg(reach: int, expected: str) -> None:
    """안전150km 경계는 허용하고 1m 초과면 가장 싼 후보도 제외한다."""
    _, _, analysis, plan, execution = context()
    execution.results.candidate_routes["A"] = CandidateRoutes(
        approach=cost(reach, 3000), via=cost(200000, 4200, first=reach)
    )
    result = judge_execution(analysis, plan, execution)
    assert result.recommendation.recommended.station_id == expected


@pytest.mark.parametrize(
    "mode", ["missing_route", "missing_details", "missing_price", "lookup_failure", "invalid_legs"]
)
def test_incomplete_data_is_unverified_and_never_filled_from_old_judgement(mode: str) -> None:
    """상세/가격/경로가 불충분하면 미확인으로 남기고 과거 캐시 값으로 보완하지 않는다."""
    _, _, analysis, plan, execution = context()
    if mode == "missing_route":
        execution.results.candidate_routes["A"].via = None
    if mode == "missing_details":
        execution.results.station_details = execution.results.station_details[1:]
    if mode == "missing_price":
        execution.results.station_details[0].price_krw_per_l = None
    if mode == "lookup_failure":
        execution.results.evaluate_constraints[0].reason_codes = ["TIMEOUT"]
    if mode == "invalid_legs":
        execution.results.candidate_routes["A"].via = cost(102000, 4200)
    result = judge_execution(analysis, plan, execution)
    assert result.recommendation.recommended.station_id == "B"
    assert (
        result.candidates[0].status == "unverified" and not result.candidates[0].evaluation.eligible
    )


def test_negative_detour_is_preserved_but_comparison_uses_zero() -> None:
    """경유 비용이 작게 조회됐을 때 원 차이는 유지하고 우회 상한 비교는0을 사용한다."""
    _, _, analysis, plan, execution = context(max_detour_min=0, max_detour_km=0)
    execution.results.candidate_routes["A"].via = cost(90000, 3500, first=31000)
    a = judge_execution(analysis, plan, execution).candidates[0].evaluation
    assert a.eligible and a.raw_detour_distance_m == -10000 and a.raw_detour_duration_s == -100
    assert a.detour_distance_m == a.detour_duration_s == 0


def test_user_preference_and_optional_efficiency_are_preserved() -> None:
    """시간 우선 정렬과 선택 연비의 추정 소비량을 보존하며 L 잔량을 역산하지 않는다."""
    _, _, analysis, plan, execution = context(sort_by="detour_time", avg_efficiency_kmpl=12)
    result = judge_execution(analysis, plan, execution)
    assert result.recommendation.recommended.station_id == "B"
    assert result.candidates[0].estimated_fuel_to_station_l == pytest.approx(31 / 12)
    assert plan.request.current_fuel_l is None


@pytest.mark.parametrize(("stop", "expected"), [(10, "B"), (0, "A"), (20, None)])
def test_arrival_deadline_includes_explicit_stop_time_and_equal_boundary(
    stop: float, expected: str | None
) -> None:
    """15시→16:15의75분에 주행+명시 정차를 합산하고 같으면 허용한다."""
    _, _, analysis, plan, execution = context(
        departure_time=time(15), arrival_deadline=time(16, 15), refuel_stop_min=stop
    )
    result = judge_execution(analysis, plan, execution)
    assert (
        result.recommendation.recommended.station_id if result.recommendation.recommended else None
    ) == expected
    assert (
        result.time_constraint.available_s == 4500
        and result.time_constraint.basis == "same_day_clock"
    )
    assert analysis.conditions.trip_date is None and analysis.conditions.arrival_date is None
    if stop == 10:
        assert result.candidates[1].estimated_arrival_clock == "16:15:00"
        assert result.candidates[1].estimated_arrival_at is None


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"arrival_deadline": time(17)}, {"departure_time", "refuel_stop_min"}),
        ({"departure_time": time(15), "arrival_deadline": time(17)}, {"refuel_stop_min"}),
        (
            {"departure_time": time(23), "arrival_deadline": time(1), "refuel_stop_min": 5},
            {"trip_date", "arrival_date"},
        ),
        (
            {
                "trip_date": date(2026, 11, 19),
                "departure_time": time(23),
                "arrival_deadline": time(1),
                "refuel_stop_min": 5,
            },
            {"arrival_date"},
        ),
        (
            {"arrival_date": date(2026, 11, 20)},
            {"arrival_deadline", "departure_time", "refuel_stop_min", "trip_date"},
        ),
    ],
)
def test_missing_time_inputs_are_not_invented(changes: dict, missing: set[str]) -> None:
    """날짜/시각/정차시간을 임의 채우지 않고 필요한 항목을 정확히 질문한다."""
    _, _, analysis, plan, execution = context(**changes)
    result = judge_execution(analysis, plan, execution)
    assert result.status == "needs_user_input" and set(result.missing_fields) == missing
    assert result.recommendation.recommended is None and not result.replan_needed


def test_explicit_overnight_dates_produce_timezone_aware_arrival() -> None:
    """명시된 익일 날짜로23시→익일1시를 비교하고KST 예상 도착을 계산한다."""
    _, _, analysis, plan, execution = context(
        trip_date=date(2026, 11, 19),
        arrival_date=date(2026, 11, 20),
        departure_time=time(23),
        arrival_deadline=time(1),
        refuel_stop_min=5,
    )
    result = judge_execution(analysis, plan, execution)
    assert result.status == "recommended" and result.time_constraint.available_s == 7200
    assert result.candidates[0].estimated_arrival_at.isoformat() == "2026-11-20T00:15:00+09:00"


def test_reversed_explicit_dates_and_unbounded_stop_are_rejected() -> None:
    """도착 날짜가 과거면 날짜를 보완하고 비유한 계산 범위 정차시간은 거절한다."""
    value = TripConditions(
        trip_date=date(2026, 11, 20),
        arrival_date=date(2026, 11, 19),
        departure_time=time(15),
        arrival_deadline=time(17),
        refuel_stop_min=10,
    )
    assert "arrival_date" in time_constraint(value).missing_fields
    value.refuel_stop_min = 1e308
    with pytest.raises(ProbeFailure):
        time_constraint(value)


def test_context_mismatch_and_duplicate_results_are_not_silent_successes() -> None:
    """다른 출발 조건/중복ID의 결과를 재사용하지 않고 명시 오류로 구분한다."""
    _, _, analysis, plan, execution = context()
    analysis.conditions.remaining_range_km = 200
    with pytest.raises(ProbeFailure) as mismatch:
        judge_execution(analysis, plan, execution)
    assert mismatch.value.error.code == "INVALID_INPUT"
    analysis.conditions.remaining_range_km = 180
    execution.results.evaluate_constraints.append(execution.results.evaluate_constraints[0])
    with pytest.raises(ProbeFailure) as duplicate:
        judge_execution(analysis, plan, execution)
    assert duplicate.value.error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize(
    ("field", "value", "quote", "category"),
    [
        ("arrival_date", date(2026, 11, 21), "내일 도착", "unverified_fields"),
        ("refuel_stop_min", -1, "주유-1분", "invalid_fields"),
        ("refuel_stop_min", 1, "주유1시간", "unverified_fields"),
    ],
)
def test_new_time_fields_require_grounded_dates_units_and_valid_values(
    field: str, value: object, quote: str, category: str
) -> None:
    """도착 날짜·정차시간은 원문 근거/분 단위/비음수 검사를 통과해야 채택한다."""
    user, raw, _, _, _ = context()
    values = raw.model_dump()
    values[field] = {"value": value, "quote": quote}
    analysis = analyze_conditions(
        user + " " + quote, ExtractedRequest.model_validate(values), TODAY
    )
    assert field in getattr(analysis, category) and not analysis.ready_for_planning


def test_graph_judges_before_enriching_the_new_selected_candidate() -> None:
    """판정에서A→B로 바뀌면B만 보조 조회하며 원 실행/분석 데이터는 수정하지 않는다."""
    user, raw, analysis, plan, execution = context(
        departure_time=time(15), arrival_deadline=time(16, 15), refuel_stop_min=10
    )
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        """정차/시간 판정 뒤 휴게소 페이지 요청만 허용한다. 다른API를 호출하면 실패다."""
        assert request.url.host == "api.data.go.kr"
        captured.append("rest")
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {
                        "totalCount": 1,
                        "items": [{"entrpsNm": "B주변", "longitude": "127.02", "latitude": "37"}],
                    },
                }
            },
        )

    async def analyze(message: str) -> ExtractedRequest:
        """테스트 원문에 대응하는 구조화 결과를 주입한다. GMS는 호출하지 않는다."""
        assert message == user
        return raw

    async def propose(value: RequestAnalysis) -> PlanProposal:
        """계획 검증을 실제로 거치게 하며 허용된 순서만 제안한다."""
        return PlanProposal(
            reason="대역계획", steps=[{"action": name, "reason": "필요"} for name in ORDER]
        )

    async def execute(value: WorkPlan) -> PlanExecution:
        """실제 형태의 조회 비용을 주입한다. 이전 추천A의 판정값은 신뢰 대상이 아니다."""
        return execution

    async def scenario() -> None:
        """실제 그래프의 judge/enrich 노드와 보조 조회 어댑터만 모의 HTTP로 실행한다."""
        config = Settings(_env_file=None, data_go_kr_service_key="fake-key")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await execute_request(
                user,
                config,
                analyzer=analyze,
                planner=propose,
                reference_date=TODAY,
                executor=execute,
                client=client,
            )
        assert result.ok and result.data["judgement"]["status"] == "recommended"
        assert result.data["results"]["rank_candidates"]["recommended"]["station_id"] == "B"
        assert result.data["execution"]["calls"][-1]["station_id"] == "B"
        assert result.data["results"]["rest_area_info"] is not None and captured == ["rest"]
        assert execution.results.rank_candidates.recommended.station_id == "A"

    asyncio.run(scenario())


def test_confirmed_judgement_does_not_reuse_another_candidates_rest_info() -> None:
    """확정 후보가 바뀌면 기존 보조정보를 지워 다른 후보의 시설로 오해하지 않게 한다."""
    _, _, analysis, plan, execution = context(
        departure_time=time(15), arrival_deadline=time(16, 15), refuel_stop_min=10
    )
    result = apply_judgement(execution, judge_execution(analysis, plan, execution))
    assert result.results.rank_candidates.recommended.station_id == "B"
    assert result.results.rest_area_info is None
    assert result.results.safe_range.safe_range_km == 150
