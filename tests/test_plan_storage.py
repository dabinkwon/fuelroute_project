"""F3015 계획 스냅샷 저장·조회·시간 충돌·영속성을 검증한다."""

import asyncio
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import TripConditions
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.plans import (
    PlanCreate,
    PlanInputSnapshot,
    PlanLocation,
    PlanRecommendationSnapshot,
    PlanUpdate,
)
from fuelroute.schemas.response import EvidenceSource, ResponseFact
from fuelroute.schemas.stations import RestAreaInfo, StationDetails
from fuelroute.storage.plans import SQLitePlanStore

KST = timezone(timedelta(hours=9))


def create_command(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    memo: str = "",
) -> PlanCreate:
    """테스트용 검증 추천 저장 입력을 생성한다.

    start/end는 선택적인 KST 계획 시각이며 None은 사용자가 시각을 입력하지 않은 경우다.
    memo는 최대 1000자 사용자 메모다. 실제 외부 API·LLM·DB를 호출하지 않고 고정
    좌표·가격·근거를 반환한다.
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
        memo=memo,
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


def test_update_conditions_marks_revalidation_and_preserves_recommendation(tmp_path: Path) -> None:
    """주행 조건 변경은 revision을 올리고 기존 추천을 재검증 필요 상태로 보존한다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    created = asyncio.run(store.create(create_command())).plan
    changed_conditions = created.input_snapshot.conditions.model_copy(
        update={"remaining_range_km": 160.0}
    )
    changed_input = created.input_snapshot.model_copy(update={"conditions": changed_conditions})
    updated = asyncio.run(
        store.update(
            created.plan_id,
            PlanUpdate(expected_revision=created.revision, input_snapshot=changed_input),
        )
    )
    assert updated.plan.revision == 2
    assert updated.plan.validation_status == "revalidation_required"
    assert updated.plan.recommendation == created.recommendation
    assert updated.plan.input_snapshot.conditions.remaining_range_km == 160
    assert updated.plan.created_at == created.created_at
    assert updated.plan.updated_at >= created.updated_at


def test_update_time_rechecks_conflict_and_can_clear_time(tmp_path: Path) -> None:
    """수정한 시간 구간의 충돌을 알리고 명시적 null 삭제는 판정 불가로 바꾼다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    start = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
    existing = asyncio.run(
        store.create(create_command(start=start, end=start + timedelta(hours=2)))
    ).plan
    target = asyncio.run(store.create(create_command())).plan
    overlapped = asyncio.run(
        store.update(
            target.plan_id,
            PlanUpdate(
                expected_revision=target.revision,
                planned_start_at=start + timedelta(hours=1),
                planned_end_at=start + timedelta(hours=3),
            ),
        )
    )
    assert [item.plan_id for item in overlapped.conflicts] == [existing.plan_id]
    assert overlapped.plan.validation_status == "valid"
    cleared = asyncio.run(
        store.update(
            target.plan_id,
            PlanUpdate(expected_revision=overlapped.plan.revision, planned_end_at=None),
        )
    )
    assert cleared.conflict_check == "not_comparable"
    assert cleared.plan.planned_end_at is None


def test_memo_create_and_update_preserve_recommendation_validation(tmp_path: Path) -> None:
    """메모 생성·수정·삭제는 텍스트를 보존하되 기존 추천 검증 상태를 바꾸지 않는다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    created = asyncio.run(store.create(create_command(memo="  도착 전에 연락  "))).plan
    assert created.memo == "도착 전에 연락" and created.validation_status == "valid"
    updated = asyncio.run(
        store.update(
            created.plan_id,
            PlanUpdate(expected_revision=created.revision, memo="셀프 주유 확인"),
        )
    ).plan
    assert updated.memo == "셀프 주유 확인"
    assert updated.revision == 2 and updated.validation_status == "valid"
    cleared = asyncio.run(
        store.update(
            created.plan_id,
            PlanUpdate(expected_revision=updated.revision, memo=""),
        )
    ).plan
    assert cleared.memo == "" and cleared.validation_status == "valid"


def test_existing_plan_database_adds_empty_memo_column(tmp_path: Path) -> None:
    """memo 열이 없던 기존 SQLite 계획 테이블은 재연결 시 빈 메모 열로 안전하게 이관한다."""
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE fuel_plans(id TEXT PRIMARY KEY, session_id TEXT, revision INTEGER, "
        "validation_status TEXT, trip_date TEXT, input_snapshot_json TEXT, "
        "recommendation_json TEXT, planned_start_at TEXT, planned_end_at TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    connection.commit()
    connection.close()
    store = SQLitePlanStore(path)
    asyncio.run(store.list())
    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(fuel_plans)")}
    connection.close()
    assert "memo" in columns


def test_stale_revision_and_same_value_are_rejected(tmp_path: Path) -> None:
    """오래된 revision과 실제 변경이 없는 PATCH는 현재 계획을 덮어쓰지 않는다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    created = asyncio.run(store.create(create_command())).plan
    with pytest.raises(ProbeFailure) as stale:
        asyncio.run(
            store.update(
                created.plan_id,
                PlanUpdate(expected_revision=created.revision + 1, planned_start_at=None),
            )
        )
    assert stale.value.error.code == "PLAN_CONFLICT"
    with pytest.raises(ProbeFailure) as unchanged:
        asyncio.run(
            store.update(
                created.plan_id,
                PlanUpdate(expected_revision=created.revision, planned_start_at=None),
            )
        )
    assert unchanged.value.error.code == "INVALID_INPUT"
    assert asyncio.run(store.get(created.plan_id)) == created


def test_delete_removes_only_selected_plan(tmp_path: Path) -> None:
    """계획 삭제는 선택 ID만 제거하며 다른 계획은 유지하고 재삭제는 False다."""
    store = SQLitePlanStore(tmp_path / "plans.sqlite3")
    first = asyncio.run(store.create(create_command())).plan
    second = asyncio.run(store.create(create_command())).plan
    assert asyncio.run(store.delete(first.plan_id)) is True
    assert asyncio.run(store.get(first.plan_id)) is None
    assert asyncio.run(store.get(second.plan_id)) == second
    assert asyncio.run(store.delete(first.plan_id)) is False
