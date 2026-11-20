"""F3015 저장 계획의 입력·추천 근거·충돌·조회 계약."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fuelroute.schemas.analysis import TripConditions
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.response import ResponseFact
from fuelroute.schemas.stations import RestAreaInfo, StationDetails


class PlanKey(BaseModel):
    """URL·SQLite에서 사용할 저장 계획 ID를 검증하는 값 객체."""

    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class PlanLocation(BaseModel):
    """계획 저장 시점의 표시 이름과 확인된 WGS84 좌표."""

    model_config = ConfigDict(extra="forbid")
    label: str = Field(
        min_length=1, max_length=300, description="사용자가 입력하거나 검색으로 확인한 위치명"
    )
    coordinate: Coordinate = Field(description="확인된 WGS84 경도·위도(도)")


class PlanInputSnapshot(BaseModel):
    """추천에 실제 사용한 출발·목적지와 주행 조건의 불변 스냅샷.

    계획 저장 전에 Agent가 검증한 `TripConditions`를 전달한다. 출발·목적지 좌표, 유종,
    계획 날짜와 거리 또는 기존 연료량 방식의 필수값이 있어야 한다. 이 모델은 누락값을
    추측하거나 좌표/API를 다시 조회하지 않으며, 잘못된 범위는 ValidationError로 거절한다.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    origin: PlanLocation
    destination: PlanLocation
    conditions: TripConditions

    @model_validator(mode="after")
    def complete_recommendation_input(self) -> "PlanInputSnapshot":
        """저장 조건이 위치·날짜·유종·한 가지 거리 입력 방식을 완성했는지 검사한다.

        `conditions`의 좌표/라벨은 중복 저장하지 않으므로 있어도 `origin`/`destination`과
        같아야 한다. km 방식은 주행 가능 거리>0 및 0<=안전 여유<주행 가능 거리, 기존 L
        방식은 연료량·안전 잔량·연비에 같은 경계를 적용한다. 두 방식을 섞거나 둘 다
        누락하면 ValueError다. 저장·외부 호출·시간 생성 부작용은 없다.
        """
        conditions = self.conditions
        if conditions.trip_date is None or conditions.fuel_type is None:
            raise ValueError("계획 날짜와 유종이 필요합니다.")
        if conditions.origin is not None and conditions.origin != self.origin.coordinate:
            raise ValueError("출발 좌표 스냅샷이 입력 조건과 다릅니다.")
        if (
            conditions.destination is not None
            and conditions.destination != self.destination.coordinate
        ):
            raise ValueError("목적 좌표 스냅샷이 입력 조건과 다릅니다.")
        distance_values = (conditions.remaining_range_km, conditions.reserve_range_km)
        fuel_values = (
            conditions.current_fuel_l,
            conditions.reserve_fuel_l,
            conditions.avg_efficiency_kmpl,
        )
        has_distance = any(value is not None for value in distance_values)
        has_fuel = any(value is not None for value in fuel_values)
        if has_distance == has_fuel:
            raise ValueError("거리 또는 연료량 입력 방식 하나만 사용하세요.")
        if has_distance:
            remaining, reserve = distance_values
            if (
                remaining is None
                or reserve is None
                or remaining <= 0
                or not 0 <= reserve < remaining
            ):
                raise ValueError("주행 가능 거리와 안전 여유 거리 범위를 확인하세요.")
        else:
            current, reserve, efficiency = fuel_values
            if (
                current is None
                or reserve is None
                or efficiency is None
                or current <= 0
                or efficiency <= 0
                or not 0 <= reserve < current
            ):
                raise ValueError("연료량·안전 잔량·연비 범위를 확인하세요.")
        return self


class PlanRecommendationSnapshot(BaseModel):
    """추천 당시 주유소·가격·자동차 경로 비용·근거·보조정보 스냅샷.

    `station`은 Opinet 상세 결과, 거리는 km, 시간은 분 단위다. `evidence`에는 F3011에서
    코드가 생성한 검증 사실만 넣는다. 휴게소 정보는 선택이며 동일 시설 확정으로 바꾸지
    않는다. 모델은 값을 재계산하거나 최신 가격을 조회하지 않는다.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    station: StationDetails
    safe_range_km: float = Field(gt=0, description="안전 여유를 남긴 추정 주행 가능 거리(km)")
    reach_distance_km: float = Field(
        ge=0, description="출발지에서 주유소까지 실제 자동차 경로 거리(km)"
    )
    detour_distance_km: float = Field(ge=0, description="기본 경로 대비 추가 거리(km)")
    detour_duration_min: float = Field(ge=0, description="기본 경로 대비 추가 시간(분)")
    evidence: list[ResponseFact] = Field(
        min_length=1, max_length=64, description="추천 근거와 출처"
    )
    rest_area_info: list[RestAreaInfo] | None = Field(
        default=None, description="확인된 주변 휴게소 참고정보; 없거나 실패하면 null"
    )


class PlanCreate(BaseModel):
    """검증된 추천 하나를 저장하기 위한 F3015 입력 계약.

    `trip_date`는 입력 스냅샷과 같아야 한다. `planned_start_at`/`planned_end_at`은 사용자가
    구체적으로 입력해 시간대까지 확정된 경우에만 전달하며 둘 중 하나만 저장할 수 있다.
    둘 다 있으면 종료가 시작보다 늦어야 한다. `execution_id`는 선택 로그 참조이며 없다고
    생성하지 않는다. 생성시각·ID·revision은 Repository가 만든다.
    """

    model_config = ConfigDict(extra="forbid")
    execution_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    trip_date: date
    input_snapshot: PlanInputSnapshot
    recommendation: PlanRecommendationSnapshot
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None

    @field_validator("planned_start_at", "planned_end_at")
    @classmethod
    def aware_plan_time(cls, value: datetime | None) -> datetime | None:
        """입력된 계획 시각에 UTC offset이 있는지 검사하고 그대로 반환한다.

        None은 알 수 없는 시각으로 보존한다. naive datetime은 시간대를 추측하지 않고
        ValueError로 거절한다. 저장·KST 변환·외부 호출 부작용은 없다.
        """
        if value is not None and value.utcoffset() is None:
            raise ValueError("계획 시각에는 시간대가 필요합니다.")
        return value

    @model_validator(mode="after")
    def consistent_plan_time(self) -> "PlanCreate":
        """계획 날짜·시각 순서가 저장 입력과 일치하는지 검사한다.

        입력 스냅샷의 `trip_date`와 최상위 날짜가 달라지면 거절한다. 시작/종료가 모두
        있으면 시작 날짜는 계획 날짜이고 종료는 시작보다 늦어야 한다. 한 시각만 있으면
        알려진 값만 보존해 충돌 판정 불가 상태로 둔다. 값을 보정하거나 저장하지 않는다.
        """
        if self.input_snapshot.conditions.trip_date != self.trip_date:
            raise ValueError("계획 날짜가 입력 스냅샷과 다릅니다.")
        if self.planned_start_at is not None and self.planned_start_at.date() != self.trip_date:
            raise ValueError("계획 시작 시각의 날짜가 계획 날짜와 다릅니다.")
        if (
            self.planned_start_at is not None
            and self.planned_end_at is not None
            and self.planned_end_at <= self.planned_start_at
        ):
            raise ValueError("계획 종료 시각은 시작 시각보다 늦어야 합니다.")
        return self


class StoredPlan(PlanCreate):
    """SQLite에서 복원한 저장 계획과 버전·검증 상태·감사 시각."""

    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    revision: int = Field(ge=1)
    validation_status: Literal["valid", "revalidation_required"] = "valid"
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_audit_time(cls, value: datetime) -> datetime:
        """Repository 감사 시각이 timezone-aware인지 확인하고 그대로 반환한다.

        저장소가 만든 UTC 시각만 예상하지만 다른 offset도 허용한다. naive 값은 데이터
        손상으로 ValueError이며 외부 호출·현재시각 생성·DB 쓰기는 없다.
        """
        if value.utcoffset() is None:
            raise ValueError("계획 감사 시각에는 시간대가 필요합니다.")
        return value

    @model_validator(mode="after")
    def ordered_audit_time(self) -> "StoredPlan":
        """수정 시각이 생성 시각보다 빠르지 않은지 검사하고 자기 자신을 반환한다.

        잘못된 순서는 ValueError이며 값을 보정하지 않는다. DB·LLM·외부 API 부작용은 없다.
        """
        if self.updated_at < self.created_at:
            raise ValueError("계획 수정 시각은 생성 시각보다 빠를 수 없습니다.")
        return self


class PlanConflict(BaseModel):
    """새 계획과 실제 시각 구간이 겹치는 기존 계획의 최소 식별 정보."""

    plan_id: str
    planned_start_at: datetime
    planned_end_at: datetime
    reason: Literal["time_overlap"] = "time_overlap"


class PlanWriteResult(BaseModel):
    """저장 성공 계획과 비차단 충돌 판정 결과."""

    plan: StoredPlan
    conflicts: list[PlanConflict]
    conflict_check: Literal["checked", "not_comparable"]


class PlanUpdate(BaseModel):
    """저장 계획의 핵심 입력을 예상 revision에만 변경하는 F3016 계약.

    `expected_revision`은 목록/상세에서 읽은 현재 버전이다. 날짜, 입력 스냅샷, 시작/종료
    시각 중 최소 하나를 명시해야 하며 시각 필드는 명시적 null로 지울 수 있다. 추천 가격·
    비용·근거는 직접 덮어쓰지 않고, 핵심 입력이 실제로 바뀌면 Repository가 기존 추천을
    `revalidation_required`로 표시한다. 이 모델은 DB를 읽거나 값을 병합하지 않는다.
    """

    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    trip_date: date | None = None
    input_snapshot: PlanInputSnapshot | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None

    @field_validator("planned_start_at", "planned_end_at")
    @classmethod
    def aware_updated_time(cls, value: datetime | None) -> datetime | None:
        """수정 요청의 비어 있지 않은 계획 시각에 UTC offset이 있는지 검사한다.

        None은 필드 미지정 또는 명시적 삭제 양쪽에 쓰며 `model_fields_set`으로 구분한다.
        naive 시각은 시간대를 추측하지 않고 ValueError다. 저장·외부 호출 부작용은 없다.
        """
        if value is not None and value.utcoffset() is None:
            raise ValueError("계획 시각에는 시간대가 필요합니다.")
        return value

    @model_validator(mode="after")
    def editable_field_present(self) -> "PlanUpdate":
        """expected_revision 외 수정 필드가 하나 이상 명시됐는지 검사한다.

        날짜와 새 스냅샷을 함께 보냈으면 두 날짜가 같아야 한다. 시작/종료의 전체 순서는
        기존 계획과 병합한 Repository가 재검사한다. 빈 PATCH는 ValueError이며 부작용은 없다.
        """
        editable = {
            "trip_date",
            "input_snapshot",
            "planned_start_at",
            "planned_end_at",
        }
        if not self.model_fields_set.intersection(editable):
            raise ValueError("수정할 계획 필드가 필요합니다.")
        if (
            self.trip_date is not None
            and self.input_snapshot is not None
            and self.input_snapshot.conditions.trip_date != self.trip_date
        ):
            raise ValueError("계획 날짜가 입력 스냅샷과 다릅니다.")
        return self


class PlanList(BaseModel):
    """계획 목록 API/Repository가 반환하는 계획과 건수."""

    items: list[StoredPlan]
    count: int = Field(ge=0)
