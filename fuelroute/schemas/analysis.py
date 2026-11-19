"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: analysis."""

from datetime import date, time
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from fuelroute.core.contracts import ToolResult
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.locations import LocationMatchSet


class AnalysisInput(BaseModel):
    """이번 요청 원문과 테스트·재현에 사용할 선택 기준일."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user: str = Field(min_length=1, max_length=5000, description="이번 요청의 원문")
    reference_date: date | None = None


T = TypeVar("T")


class Mention(BaseModel, Generic[T]):
    """모델이 추출한 값과 사용자 원문에서 그대로 복사한 짧은 근거."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    value: T = Field(description="사용자가 실제 언급한 값; 추측 금지")
    quote: str = Field(min_length=1, max_length=1000, description="값의 근거인 사용자 원문 구절")


class RawCoordinate(BaseModel):
    """원문 좌표. 범위 오류는 사용자 입력 보완 대상으로 이후 검사한다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    longitude: float = Field(description="사용자가 명시한 WGS84 경도(도)")
    latitude: float = Field(description="사용자가 명시한 WGS84 위도(도)")


class ExtractedRequest(BaseModel):
    """모델 출력 양식. 모든 미언급 조건은 null이며 기본 연료·좌표를 만들지 않는다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    intent: Literal["recommend", "greeting", "explain", "other"] = Field(
        description="주유 추천/계획 요청, 인사, 서비스 설명, 그 밖의 요청"
    )
    reason: str = Field(min_length=1, max_length=200, description="의도 분류의 짧은 이유")
    origin_label: Mention[str] | None = Field(default=None, description="출발 지명, 없으면 null")
    destination_label: Mention[str] | None = Field(default=None, description="목적 지명")
    origin: Mention[RawCoordinate] | None = Field(default=None, description="명시된 출발 좌표만")
    destination: Mention[RawCoordinate] | None = Field(
        default=None, description="명시된 목적 좌표만"
    )
    trip_date: Mention[date] | None = Field(
        default=None, description="계획 날짜; 상대 날짜는 기준일 사용"
    )
    arrival_date: Mention[date] | None = Field(
        default=None, description="명시된 도착 날짜; 미언급이면 null, 익일을 임의 추측하지 않음"
    )
    departure_time: Mention[str] | None = Field(default=None, description="출발 시각 HH:MM")
    arrival_deadline: Mention[str] | None = Field(default=None, description="도착 상한 시각 HH:MM")
    fuel_type: Mention[Literal["gasoline", "diesel"]] | None = Field(
        default=None, description="명시된 휘발유 또는 경유; 기본 유종을 채우지 않음"
    )
    sort_by: Mention[Literal["price", "detour_time"]] | None = Field(
        default=None, description="명시된 가격 우선 또는 우회시간 우선 선호"
    )
    remaining_range_km: Mention[float] | None = Field(
        default=None, description="계기판의 현재 주행 가능 거리(km); 목적지 거리·우회거리와 구분"
    )
    reserve_range_km: Mention[float] | None = Field(
        default=None, description="남길 안전 여유 거리(km); 미언급 시 null이며 기본값을 만들지 않음"
    )
    current_fuel_l: Mention[float] | None = Field(default=None, description="현재 연료(L)")
    reserve_fuel_l: Mention[float] | None = Field(default=None, description="남길 안전 잔량(L)")
    avg_efficiency_kmpl: Mention[float] | None = Field(default=None, description="평균 연비(km/L)")
    max_detour_min: Mention[float] | None = Field(default=None, description="허용 추가 시간(분)")
    max_detour_km: Mention[float] | None = Field(default=None, description="허용 추가 거리(km)")
    refuel_stop_min: Mention[float] | None = Field(
        default=None, description="사용자가 예상한 주유·정차 시간(분), 0도 명시값이며 기본값 없음"
    )


class TripConditions(BaseModel):
    """원문 또는 위치 API로 확인한 조건. 날짜·시각 추출은 도착 가능성 판정이 아니다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    origin_label: str | None = None
    destination_label: str | None = None
    origin: Coordinate | None = None
    destination: Coordinate | None = None
    trip_date: date | None = None
    arrival_date: date | None = None
    departure_time: time | None = None
    arrival_deadline: time | None = None
    fuel_type: Literal["gasoline", "diesel"] | None = None
    sort_by: Literal["price", "detour_time"] | None = None
    remaining_range_km: float | None = None
    reserve_range_km: float | None = None
    current_fuel_l: float | None = None
    reserve_fuel_l: float | None = None
    avg_efficiency_kmpl: float | None = None
    max_detour_min: float | None = None
    max_detour_km: float | None = None
    refuel_stop_min: float | None = None


class RequestAnalysis(BaseModel):
    """분석 노드의 구조화 결과와 다음 계획 단계에 넘길 입력 보완 상태."""

    intent: Literal["recommend", "greeting", "explain", "other"]
    reason: str
    conditions: TripConditions
    condition_groups: dict[str, list[str]]
    missing_fields: list[str]
    invalid_fields: list[str]
    unverified_fields: list[str]
    needs_user_input: bool
    ready_for_planning: bool
    questions: list[str]
    range_input_mode: Literal["distance", "fuel"] = "distance"
    location_results: dict[str, ToolResult[LocationMatchSet]] = Field(default_factory=dict)
    coordinate_sources: dict[str, Literal["user", "kakao_address", "kakao_keyword"]] = Field(
        default_factory=dict
    )
    warnings: list[str] = Field(default_factory=list)


FIELD_LABELS = {
    "origin": "출발 위치(지명·주소)",
    "destination": "목적 위치(지명·주소)",
    "origin_label": "출발 지명",
    "destination_label": "목적 지명",
    "fuel_type": "유종(휘발유/경유)",
    "remaining_range_km": "계기판의 주행 가능 거리(km)",
    "reserve_range_km": "남겨둘 안전 여유 거리(km, 주행 가능 거리보다 적게)",
    "range_input": "주행 가능 거리(km) 또는 연료량(L) 중 한 가지 입력 방식",
    "current_fuel_l": "현재 연료량(L)",
    "reserve_fuel_l": "안전 잔량(L, 현재 연료보다 적게)",
    "avg_efficiency_kmpl": "평균 연비(km/L)",
    "max_detour_min": "허용 추가 시간(분, 0 이상)",
    "max_detour_km": "허용 추가 거리(km, 0 이상)",
    "trip_date": "계획 날짜(YYYY-MM-DD)",
    "arrival_date": "도착 날짜(YYYY-MM-DD, 자정 이후면 날짜를 명시)",
    "refuel_stop_min": "예상 주유·정차 시간(분, 0 이상)",
    "departure_time": "출발 시각(HH:MM)",
    "arrival_deadline": "도착 상한 시각(HH:MM)",
    "sort_by": "가격/우회시간 우선 선호",
}
