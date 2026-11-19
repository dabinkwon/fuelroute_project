"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: recommendation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fuelroute.schemas.routes import DrivingRoute
from fuelroute.schemas.stations import (
    FuelType,
    StationDetails,
)


class CandidateEvaluation(BaseModel):
    """조회·경로 평가 한 건. 비용 미확인이나 제공사 실패도 같은 구조로 보존한다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    station_id: str = Field(min_length=1, description="Opinet 주유소 ID")
    station: StationDetails | None = Field(default=None, description="실제 상세 조회 결과")
    eligible: bool = Field(default=False, description="입력의 과거 판정값; 추천 시 다시 검사함")
    reason_codes: list[str] = Field(default_factory=list, description="조회 실패·제외 사유")
    reach_distance_m: float | None = Field(default=None, ge=0, description="실제 접근 거리(m)")
    detour_distance_m: float | None = Field(default=None, ge=0, description="추가 거리(m)")
    detour_duration_s: float | None = Field(default=None, ge=0, description="추가 시간(초)")
    raw_detour_distance_m: float | None = Field(
        default=None, description="보정 전 경유-기본 거리(m)"
    )
    raw_detour_duration_s: float | None = Field(
        default=None, description="보정 전 경유-기본 시간(초)"
    )
    negative_delta_observed: bool = Field(default=False, description="음수 차이 관찰 여부")


class RecommendationRequest(BaseModel):
    """가격 가중치 없이 비교할 명시적 제약과 후보별 실제 평가 데이터."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    candidates: list[CandidateEvaluation] = Field(
        max_length=1000, description="ID가 중복되지 않는 평가·제외 후보 목록"
    )
    fuel_type: FuelType = Field(description="사용자가 요구한 유종")
    safe_range_km: float = Field(gt=0, description="안전 잔량을 남기는 추정 거리(km)")
    max_detour_min: float | None = Field(default=None, ge=0, description="허용 추가 시간(분)")
    max_detour_km: float | None = Field(default=None, ge=0, description="허용 추가 거리(km)")
    sort_by: Literal["price", "detour_time"] = Field(
        default="price", description="기본 price=가격 우선, detour_time=추가 시간 우선"
    )

    @model_validator(mode="after")
    def unique_stations(self) -> "RecommendationRequest":
        """동일 ID의 상충하는 평가를 임의 선택하지 않도록 중복 입력을 거절한다.

        후보 병합은 검색 단계의 책임이다. 이 검증은 외부 호출 없이 중복 시 ValueError를
        발생시킨다. 예: 같은 A1을 두 번 전달하면 추천을 시작하지 않는다.
        """
        ids = [item.station_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("추천 입력의 주유소 ID가 중복됐습니다.")
        return self


class RecommendationResult(BaseModel):
    """평가한 집합 안의 추천·정렬·제외 근거. 전체 경로 최저가를 주장하지 않는다."""

    recommended: CandidateEvaluation | None
    ranked_candidates: list[CandidateEvaluation]
    excluded_candidates: list[CandidateEvaluation]
    sort_by: Literal["price", "detour_time"]
    ranking_fields: list[str]
    scope: Literal["evaluated_candidates_only"] = "evaluated_candidates_only"


class AssessmentRequest(BaseModel):
    """명시적 실제 자동차 비용 세 종류와 안전·우회 상한의 평가 입력."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    base: DrivingRoute = Field(description="출발→목적지 기본 경로 비용")
    approach: DrivingRoute = Field(description="출발→주유소 접근 경로 비용")
    via: DrivingRoute = Field(description="출발→주유소→목적지 경로 비용, 두 구간 필수")
    safe_range_km: float = Field(gt=0, description="안전 주행거리(km)")
    max_detour_min: float | None = Field(default=None, ge=0, description="허용 추가 시간(분)")
    max_detour_km: float | None = Field(default=None, ge=0, description="허용 추가 거리(km)")

    @model_validator(mode="after")
    def single_waypoint(self) -> "AssessmentRequest":
        """경유 경로가 두 구간인지 HTTP 없이 확인한다. 틀린 입력은 ValueError다."""
        if len(self.via.legs) != 2:
            raise ValueError("단일 경유 경로의 구간은 두 개여야 합니다.")
        return self


class AssessmentOutput(BaseModel):
    """비용 판정·제외 이유와 교통 변동으로 발생할 수 있는 원래 차이."""

    eligible: bool
    reason_codes: list[str]
    reach_distance_m: float = Field(ge=0, allow_inf_nan=False)
    detour_distance_m: float = Field(ge=0, allow_inf_nan=False)
    detour_duration_s: float = Field(ge=0, allow_inf_nan=False)
    raw_detour_distance_m: float = Field(allow_inf_nan=False)
    raw_detour_duration_s: float = Field(allow_inf_nan=False)
    negative_delta_observed: bool
