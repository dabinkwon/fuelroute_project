"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: locations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fuelroute.schemas.coordinates import Coordinate


class LocationQuery(BaseModel):
    """LLM이 확인한 원문 지명 또는 사용자가 입력한 주소. 실행 명령으로 쓰지 않는다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=200, description="검색할 지명·주소·장소명")


class LocationCandidate(Coordinate):
    """제공사에서 실제 반환한 위치. 지역/도로 수준 좌표와 상세 위치를 구분한다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    name: str = Field(min_length=1, description="주소명 또는 장소명")
    address: str | None = Field(default=None, description="제공사가 반환한 지번/지역 주소")
    road_address: str | None = Field(default=None, description="도로명 주소, 없으면 null")
    place_id: str | None = Field(default=None, description="장소 검색 ID, 주소 검색은 null")
    place_url: str | None = Field(default=None, description="제공사 장소 링크, 없으면 null")
    source: Literal["kakao_address", "kakao_keyword"] = Field(description="실제 조회한 API")
    match_level: Literal["area", "address", "place"] = Field(
        description="지역/도로 범위, 구체 주소, 장소 중 제공사 결과 유형"
    )


class LocationMatchSet(BaseModel):
    """최대 다섯 개의 후보와 유일한 결과일 때만 채운 선택 위치."""

    model_config = ConfigDict(extra="forbid")
    query: str
    status: Literal["resolved", "ambiguous"]
    search_type: Literal["address", "keyword"]
    total_count: int = Field(ge=1, strict=True)
    candidates: list[LocationCandidate] = Field(min_length=1, max_length=5)
    selected: LocationCandidate | None
    api_call_count: int = Field(ge=1, le=2, strict=True)

    @model_validator(mode="after")
    def validate_selection(self) -> "LocationMatchSet":
        """유일한 응답만 선택했는지 확인한다. 여러 결과 중 순위로 고르면 거절한다.

        네트워크·보정은 없고 계약 위반은 ValueError다. 예: 총 10건 중 한 건만 받은
        첫 페이지도 ambiguous여야 하며 첫 결과를 자동 선택하지 않는다.
        """
        if self.total_count < len(self.candidates):
            raise ValueError("후보 개수와 전체 건수가 맞지 않습니다.")
        if self.status == "resolved":
            if (
                self.total_count != 1
                or len(self.candidates) != 1
                or self.selected != self.candidates[0]
            ):
                raise ValueError("유일한 결과만 선택할 수 있습니다.")
        elif self.selected is not None:
            raise ValueError("모호한 결과는 선택하지 않습니다.")
        return self
