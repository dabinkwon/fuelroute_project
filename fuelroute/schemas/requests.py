"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: requests."""

from typing import Literal

from pydantic import Field

from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.driving import RangeConditions


class RouteSpikeRequest(RangeConditions):
    """WGS84 좌표와 km 기반 주행 가능/안전 여유 또는 기존 L/연비를 받는 실험 입력."""

    origin: Coordinate = Field(description="출발 WGS84 좌표")
    destination: Coordinate = Field(description="목적 WGS84 좌표")
    fuel_type: Literal["gasoline", "diesel"] = Field(description="검증할 유종")
    radius_m: int = Field(default=5000, ge=1, le=5000, description="검색점 반경(m)")
    max_search_points: int = Field(default=3, ge=1, le=3, description="최대 검색점 수")
    max_candidates: int = Field(default=3, ge=1, le=10, description="상세·경로 평가할 후보 상한")
    max_detour_min: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, description="허용 추가 시간(분), null은 미지정"
    )
    max_detour_km: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, description="허용 추가 거리(km), null은 미지정"
    )


class PlanningRequest(RouteSpikeRequest):
    """같은 조건을 유지하며 후보·검색점만 바꾸는 유한한 추천 요청."""

    max_candidates: int = Field(default=3, ge=1, le=10, description="회차별 평가 후보 상한")
    max_replans: int = Field(default=2, ge=0, le=2, description="최초 실행 이후 재계획 상한")
    max_evaluations: int = Field(default=9, ge=1, le=30, description="전체 실행의 후보 평가 상한")
    sort_by: Literal["price", "detour_time"] = Field(
        default="price", description="추천 정렬: 가격 또는 추가 시간 우선"
    )
