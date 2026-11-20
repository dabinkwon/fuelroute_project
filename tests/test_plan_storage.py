"""F3015 계획 스냅샷 저장·조회·시간 충돌·영속성을 검증한다."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from fuelroute.schemas.analysis import TripConditions
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.plans import (
    PlanCreate,
    PlanInputSnapshot,
    PlanLocation,
    PlanRecommendationSnapshot,
)
from fuelroute.schemas.response import EvidenceSource, ResponseFact
from fuelroute.schemas.stations import RestAreaInfo, StationDetails
from fuelroute.storage.plans import SQLitePlanStore

KST = timezone(timedelta(hours=9))


def create_command(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> PlanCreate:
    """테스트용 검증 추천 저장 입력을 생성한다.

    start/end는 선택적인 KST 계획 시각이며 None은 사용자가 시각을 입력하지 않은 경우다.
    실제 외부 API·LLM·DB를 호출하지 않고 고정 좌표·가격·근거를 반환한다.
    """
    origin = Coordinate(longitude=128.6014, latitude=35.8714)
    destination = Coordinate(longitude=126.978, latitude=37.5665)
    conditions = TripConditions(
        origin_label="대구",
        destination_label="서울",
        origin=origin,
        destination=destination,
        trip_date=date(2026, 11, 20),
        fuel_type="gasoline",
        remaining_range_km=180,
        reserve_range_km=30,
        max_detour_min=15,
    )
    station = StationDetails(
        station_id="A-1",
        name="테스트주유소",
        fuel_type="gasoline",
        price_krw_per_l=1650,
        price_observed_at=datetime(2026, 11, 20, 8, 0, tzinfo=KST),
        address="경상북도 테스트로 1",
        brand="알뜰",
        facilities={"car_wash": True},
        longitude=128.1,
        latitude=36.1,
    )
    evidence = ResponseFact(
        fact_id="result-1",
        category="result",
        text="테스트주유소는 실제 자동차 경로로 안전거리 안에 있습니다.",
        sources=[
            EvidenceSource(
                kind="tool_result",
                fields=["station.price_krw_per_l", "reach_distance_km"],
                provider="opinet",
                tool_name="get_station_details",
                request_id="request-1",
                fetched_at=datetime(2026, 11, 20, 8, 1, tzinfo=KST),
            )
        ],
    )
    rest_area = RestAreaInfo(
        name="테스트휴게소",
        route_name="경부고속도로",
        direction="서울",
        distance_m=350,
        facilities={"restroom": True},
        reference_date="2026-11-01",
        longitude=128.11,
        latitude=36.11,
    )
    return PlanCreate(
        trip_date=date(2026, 11, 20),
        input_snapshot=PlanInputSnapshot(
            origin=PlanLocation(label="대구", coordinate=origin),
            destination=PlanLocation(label="서울", coordinate=destination),
            conditions=conditions,
        ),
        recommendation=PlanRecommendationSnapshot(
            station=station,
            safe_range_km=150,
            reach_distance_km=120.5,
            detour_distance_km=2.4,
            detour_duration_min=5.2,
            evidence=[evidence],
            rest_area_info=[rest_area],
        ),
        planned_start_at=start,
        planned_end_at=end,
    )


def test_create_and_restart_preserve_full_snapshot(tmp_path: Path) -> None:
    """새 저장소 객체로 재접속해도 가격시각·비용·근거·보조정보와 감사값이 유지된다."""
    path = tmp_path / "plans.sqlite3"
    start = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
    created = asyncio.run(
        SQLitePlanStore(path).create(create_command(start=start, end=start + timedelta(hours=3)))
    )
    assert created.conflict_check == "checked" and created.conflicts == []
    assert created.plan.revision == 1 and created.plan.validation_status == "valid"
    restarted = SQLitePlanStore(path)
    loaded = asyncio.run(restarted.get(created.plan.plan_id))
    assert loaded == created.plan
    assert loaded is not None
    assert loaded.recommendation.station.price_krw_per_l == 1650
    assert loaded.recommendation.station.price_observed_at is not None
    assert loaded.recommendation.reach_distance_km == 120.5
    assert loaded.recommendation.evidence[0].sources[0].request_id == "request-1"
    assert loaded.recommendation.rest_area_info is not None
    assert loaded.recommendation.rest_area_info[0].match_status == "unverified"


def test_overlapping_plan_is_saved_and_reported(tmp_path: Path) -> None:
    """같은 날짜의 겹친 시간 구간도 저장하고 기존 계획 ID를 충돌 목록에 반환한다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    first_start = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
    first = asyncio.run(
        store.create(create_command(start=first_start, end=first_start + timedelta(hours=3)))
    )
    second_start = datetime(2026, 11, 20, 11, 0, tzinfo=KST)
    second = asyncio.run(
        store.create(create_command(start=second_start, end=second_start + timedelta(hours=2)))
    )
    assert [item.plan_id for item in second.conflicts] == [first.plan.plan_id]
    assert asyncio.run(store.get(second.plan.plan_id)) == second.plan
    assert len(asyncio.run(store.list(date(2026, 11, 20)))) == 2


def test_touching_ranges_do_not_conflict(tmp_path: Path) -> None:
    """첫 계획 종료와 다음 시작이 같은 반개구간은 겹침으로 판단하지 않는다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    start = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
    asyncio.run(store.create(create_command(start=start, end=start + timedelta(hours=2))))
    next_plan = asyncio.run(
        store.create(
            create_command(start=start + timedelta(hours=2), end=start + timedelta(hours=4))
        )
    )
    assert next_plan.conflict_check == "checked" and next_plan.conflicts == []


def test_unknown_times_remain_none_and_are_not_comparable(tmp_path: Path) -> None:
    """입력하지 않은 계획 시각을 생성하지 않고 충돌 판정 불가 상태를 명시한다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    result = asyncio.run(store.create(create_command()))
    assert result.conflict_check == "not_comparable" and result.conflicts == []
    assert result.plan.planned_start_at is None and result.plan.planned_end_at is None


def test_invalid_or_naive_plan_times_are_rejected() -> None:
    """시간대 없는 시각과 종료가 시작보다 빠른 입력은 저장 전에 거절한다."""
    with pytest.raises(ValidationError):
        create_command(start=datetime(2026, 11, 20, 9), end=datetime(2026, 11, 20, 10))
    start = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
    with pytest.raises(ValidationError):
        create_command(start=start, end=start - timedelta(minutes=1))


def test_list_filters_trip_date_and_missing_detail_is_none(tmp_path: Path) -> None:
    """날짜 필터는 다른 날짜를 만들지 않으며 없는 안전한 ID 상세는 None이다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    asyncio.run(store.create(create_command()))
    assert asyncio.run(store.list(date(2026, 11, 19))) == []
    assert asyncio.run(store.get("missing-plan")) is None
