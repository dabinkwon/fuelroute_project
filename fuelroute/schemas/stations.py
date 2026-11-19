"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: stations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fuelroute.schemas.coordinates import Coordinate


class OpinetSearch(BaseModel):
    """좌표 변환 검증 전 인증 확인용 KATEC 직접 입력. WGS84 Tool 입력이 아니다."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    x: float = Field(gt=0, description="공식 예시 등에서 확보한 KATEC X(m)")
    y: float = Field(gt=0, description="공식 예시 등에서 확보한 KATEC Y(m)")
    radius_m: int = Field(default=3000, ge=1, le=5000, description="검색 반경(m), 최대 5000")
    fuel_type: Literal["gasoline", "diesel"] = Field(
        default="gasoline", description="휘발유 gasoline 또는 경유 diesel"
    )


FuelType = Literal["gasoline", "diesel"]


class CandidateSearch(Coordinate):
    """한 경로 검색점의 WGS84 좌표·유종·반경 계약."""

    fuel_type: FuelType = Field(description="gasoline=휘발유, diesel=경유")
    radius_m: int = Field(default=5000, ge=1, le=5000, description="검색점 반경(m), 1~5000")


class StationLookup(BaseModel):
    """후보 조회에서 받은 ID로만 상세를 조회하는 독립 입력."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    station_id: str = Field(min_length=1, max_length=30, description="Opinet 후보의 UNI_ID")
    fuel_type: FuelType = Field(description="상세 가격에서 선택할 유종: gasoline/diesel")


class RestAreaSearch(Coordinate):
    """전체 휴게소 중 반경·선택 노선/방향으로 참고 정보를 찾는 입력."""

    radius_m: int = Field(default=5000, ge=1, le=50000, description="직선 검색거리(m), 최대 50000")
    route_name: str | None = Field(
        default=None, max_length=100, description="노선명 정확 일치 필터"
    )
    direction: str | None = Field(default=None, max_length=100, description="방향 정확 일치 필터")


class StationCandidate(Coordinate):
    """출발지 도달 판정 전의 후보. center_distance_m은 검색점 기준 거리다."""

    station_id: str
    name: str
    fuel_type: FuelType
    price_krw_per_l: float | None = Field(ge=0, allow_inf_nan=False)
    center_distance_m: float = Field(ge=0, allow_inf_nan=False)


class StationDetails(Coordinate):
    """가격 시각과 원본에서 확인한 주소·브랜드·시설만 포함하는 상세 정보."""

    station_id: str
    name: str
    fuel_type: FuelType
    price_krw_per_l: float | None = Field(ge=0, allow_inf_nan=False)
    price_observed_at: datetime | None
    address: str | None
    brand: str | None
    facilities: dict[str, bool | None]


class RestAreaInfo(Coordinate):
    """주유소와 동일 시설임을 입증하지 않은 주변 휴게소 참고 정보."""

    name: str
    route_name: str | None
    direction: str | None
    distance_m: float = Field(ge=0, allow_inf_nan=False)
    facilities: dict[str, bool | None]
    reference_date: str | None
    match_status: Literal["unverified"] = "unverified"
    source: str = "전국휴게소정보표준데이터"


class CandidatesOutput(BaseModel):
    """Opinet 주유소 ID와 다음 상세·경로 조회에 전달할 정규화 후보."""

    items: list[StationCandidate]


class RestAreasOutput(BaseModel):
    """주유소와 동일 시설이라고 단정하지 않는 휴게소 참고 정보."""

    items: list[RestAreaInfo]
    catalog_count: int = Field(ge=0)
    skipped_invalid_count: int = Field(ge=0)
